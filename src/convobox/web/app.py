"""FastAPI app for the local web UI (docs/WEB-UI-ARCHITECTURE.md).

create_app() takes an already-constructed HistoryDB/EventBroadcaster rather
than building them from a startup hook -- the caller (run_convobox.py, or a
test) decides their lifetime; this module has no global state.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from convobox.config import DisplayConfig, resolve_config_path
from convobox.web.artifacts import add_artifact_routes
from convobox.web.bridge import (
    WebApprovalBridge,
    WebListeningBridge,
    WebTextInputBridge,
)
from convobox.web.history import HistoryDB
from convobox.web.settings_api import add_settings_routes
from convobox.web.stream import EventBroadcaster


class ApprovalDecision(BaseModel):
    action: Literal["approve", "deny", "explain"]


class ListeningDecision(BaseModel):
    action: Literal["pause", "resume"]


class TextSubmission(BaseModel):
    text: str


# Loopback-only, any port -- CORSMiddleware's allow_origins does an exact
# string match (no mid-string "*" wildcard support despite what a plain
# allow_origins=["http://127.0.0.1:*"] entry might suggest), so matching
# "whatever port the dev server picked" needs the regex form instead.
_LOCALHOST_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"

# The plain HTML/JS frontend (docs/WEB-UI-ARCHITECTURE.md's "Next Steps":
# start minimal, not a React/Vite toolchain). Path relative to this file,
# not the process cwd, so it resolves correctly regardless of where
# ConvoBox is installed/run from.
_STATIC_DIR = Path(__file__).parent / "static"

# Keep-alive heartbeat cadence for idle SSE connections -- some proxies and
# browsers time out a connection with no bytes for ~30-60s.
_HEARTBEAT_INTERVAL_S = 15.0


async def sse_lines(
    queue: asyncio.Queue[dict[str, Any] | None], heartbeat_interval: float = _HEARTBEAT_INTERVAL_S
) -> AsyncIterator[str]:
    """The wire format for one SSE connection: a queued JSON-able payload
    (already shaped by whoever broadcast it -- see WebEventForwarder)
    becomes a `data: ...` line, an idle gap becomes a `: heartbeat` comment
    line, and a queued `None` (EventBroadcaster.close_all(), server
    shutdown only) ends the generator with a plain `return` instead of
    needing uvicorn to force-cancel this connection.

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
            payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
        except TimeoutError:
            yield ": heartbeat\n\n"
            continue
        if payload is None:
            return
        yield f"data: {json.dumps(payload)}\n\n"


def create_app(
    *,
    db: HistoryDB,
    broadcaster: EventBroadcaster | None = None,
    display: DisplayConfig | None = None,
    approval_bridge: WebApprovalBridge | None = None,
    listening_bridge: WebListeningBridge | None = None,
    text_bridge: WebTextInputBridge | None = None,
    quit_handler: Callable[[], None] | None = None,
    config_path: Path | None = None,
    working_dir: Path | None = None,
) -> FastAPI:
    broadcaster = broadcaster if broadcaster is not None else EventBroadcaster()
    display = display if display is not None else DisplayConfig()
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

    add_settings_routes(app, config_path if config_path is not None else resolve_config_path())
    add_artifact_routes(app, working_dir)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/quit")
    async def quit_convobox() -> dict[str, str]:
        # Confirmation lives client-side (the button arms on the first
        # click, fires on the second) -- this endpoint just does what
        # it's told the moment it's called, same
        # trust boundary as every other mutating route here (no auth,
        # loopback-only). quit_handler signals the real OS process
        # (WebApprovalBridge's sibling primitive, run_convobox.py's
        # _self_signal_interrupt) rather than doing anything itself here --
        # this request's own task is not the one that needs to unwind.
        if quit_handler is None:
            raise HTTPException(
                503,
                "no live session to quit -- this only works during a real "
                "run_convobox.py --web session, not a disconnected preview",
            )
        quit_handler()
        return {"status": "quitting"}

    @app.get("/api/listening")
    async def get_listening_state() -> dict[str, bool]:
        return {"is_paused": listening_bridge is not None and listening_bridge.is_paused}

    @app.post("/api/listening")
    async def set_listening_state(decision: ListeningDecision) -> dict[str, bool]:
        # Same trust boundary as every other mutating route here (no auth,
        # loopback-only). pause() does exactly what a spoken pause phrase
        # does -- hard-stops in-flight playback/backend work, not just a
        # future-transcript gate -- see WebListeningBridge's own docstring.
        if listening_bridge is None or not listening_bridge.is_ready:
            raise HTTPException(
                503,
                "no live session to pause/resume -- this only works during a "
                "real run_convobox.py --web session, not a disconnected preview",
            )
        if decision.action == "pause":
            await listening_bridge.pause()
        else:
            listening_bridge.resume()
        return {"is_paused": listening_bridge.is_paused}

    @app.post("/api/text")
    async def submit_text(submission: TextSubmission) -> dict[str, bool]:
        # Same trust boundary as every other mutating route here (no auth,
        # loopback-only). Goes through WebTextInputBridge, not straight to
        # the orchestrator, so this stays testable without a live session
        # and matches the approval/listening bridges' own shape.
        if text_bridge is None or not text_bridge.is_ready:
            raise HTTPException(
                503,
                "no live session to send text to -- this only works during a "
                "real run_convobox.py --web session, not a disconnected preview",
            )
        accepted = await text_bridge.submit(submission.text)
        if not accepted:
            raise HTTPException(400, "text was empty")
        return {"accepted": True}

    @app.get("/api/config")
    async def get_display_config() -> dict[str, str | None]:
        # Deliberately scoped to display-only fields, not the whole
        # AppConfig -- this endpoint has no auth (same loopback-only trust
        # model as everything else here), and AppConfig carries fields
        # (backend.working_dir, backend.command, ...) that shouldn't be
        # handed to any page this browser happens to load.
        return {
            "user_color": display.user_color,
            "assistant_color": display.assistant_color,
            "user_name": display.user_name,
            "assistant_name": display.assistant_name,
        }

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

    @app.post("/api/sessions/{session_id}/approval")
    async def resolve_approval(session_id: str, decision: ApprovalDecision) -> dict[str, str | None]:
        # session_id is unused: there is exactly one live approval gate per
        # running ConvoBox process (same scope resolve_pending_approval
        # already operates at) -- accepted in the path anyway to match every
        # other /api/sessions/{session_id}/* route's shape, and so a stale
        # tab pointed at a past session gets the same 409 a live tab with a
        # wrong click would, not a silently-ignored no-op.
        del session_id
        if approval_bridge is None or not approval_bridge.is_pending:
            raise HTTPException(409, "no approval is currently pending")
        if decision.action == "explain":
            return {"status": "pending", "explanation": approval_bridge.extend()}
        approved = decision.action == "approve"
        resolved = await approval_bridge.decide(approved)
        if not resolved:
            raise HTTPException(
                409,
                "could not deliver this decision to the backend -- the request may "
                "have just been answered another way (voice) or timed out",
            )
        return {"status": "approved" if approved else "denied", "explanation": None}

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

    # Mounted LAST, after every /api/* route above: Starlette matches
    # routes in registration order, so the explicit /api/* paths always
    # win their own exact matches regardless of this catch-all mount
    # existing. html=True makes "/" serve static/index.html -- the plain
    # HTML/JS frontend (no build step, docs/WEB-UI-ARCHITECTURE.md's own
    # "Next Steps" guidance: start minimal, not a React/Vite toolchain).
    app.mount(
        "/", StaticFiles(directory=_STATIC_DIR, html=True), name="static"
    )

    return app
