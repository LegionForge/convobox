"""FastAPI app for the local web UI (docs/WEB-UI-ARCHITECTURE.md).

create_app() takes an already-constructed HistoryDB/EventBroadcaster rather
than building them from a startup hook -- the caller (run_convobox.py, or a
test) decides their lifetime; this module has no global state.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from convobox.adapters.base import BackendEvent
from convobox.web.history import HistoryDB, event_to_dict
from convobox.web.stream import EventBroadcaster

# Loopback-only, any port -- CORSMiddleware's allow_origins does an exact
# string match (no mid-string "*" wildcard support despite what a plain
# allow_origins=["http://127.0.0.1:*"] entry might suggest), so matching
# "whatever port the dev server picked" needs the regex form instead.
_LOCALHOST_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"

# Keep-alive heartbeat cadence for idle SSE connections -- some proxies and
# browsers time out a connection with no bytes for ~30-60s.
_HEARTBEAT_INTERVAL_S = 15.0


async def sse_lines(
    queue: asyncio.Queue[BackendEvent], heartbeat_interval: float = _HEARTBEAT_INTERVAL_S
) -> AsyncIterator[str]:
    """The wire format for one SSE connection: a queued BackendEvent becomes
    a `data: ...` line, an idle gap becomes a `: heartbeat` comment line.

    Split out from stream_events()'s route body so it's testable as a plain
    async generator over a queue -- exercising the *route* end-to-end would
    mean driving an infinite response body through the test client, which
    httpx's ASGITransport can't do (it awaits the whole ASGI call to
    completion before returning anything, so a body that only ends on
    client disconnect hangs forever under it -- confirmed while writing
    this: even the endpoint's response headers never arrived in a test).
    """
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            yield f"data: {json.dumps(event_to_dict(event))}\n\n"
        except TimeoutError:
            yield ": heartbeat\n\n"


def create_app(*, db: HistoryDB, broadcaster: EventBroadcaster | None = None) -> FastAPI:
    broadcaster = broadcaster if broadcaster is not None else EventBroadcaster()
    app = FastAPI(title="ConvoBox Web UI")
    app.state.db = db
    app.state.broadcaster = broadcaster

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_LOCALHOST_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def list_sessions() -> dict[str, list[dict[str, str]]]:
        sessions = db.list_sessions()
        return {
            "sessions": [
                {"id": session_id, "last_activity": last_activity}
                for session_id, last_activity in sessions
            ]
        }

    @app.get("/api/sessions/{session_id}/events")
    async def get_session_events(
        session_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, list[dict[str, Any]]]:
        return {"events": db.get_session_events(session_id, limit=limit, offset=offset)}

    @app.post("/api/sessions/{session_id}/clear")
    async def clear_session(session_id: str) -> dict[str, str]:
        db.clear_session(session_id)
        return {"status": "cleared"}

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str) -> Response:
        data = db.export_session_json(session_id)
        return Response(
            content=data,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'},
        )

    @app.get("/api/events/stream")
    async def stream_events(request: Request) -> StreamingResponse:
        # request is unused directly: real client disconnects cancel this
        # generator's own task (Starlette tears down the streaming
        # response's task when the transport closes), which raises inside
        # the pending queue.get()/sleep and hits the `finally` below --
        # no explicit is_disconnected() poll needed. Kept as a parameter
        # anyway since FastAPI route functions taking Request is the
        # normal shape, and a future version of this endpoint may want it.
        del request
        assert broadcaster is not None  # nosec B101 -- set unconditionally above
        queue = broadcaster.subscribe()

        async def generate() -> AsyncIterator[str]:
            try:
                async for line in sse_lines(queue):
                    yield line
            finally:
                broadcaster.unsubscribe(queue)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app
