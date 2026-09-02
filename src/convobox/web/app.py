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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from convobox.config import DisplayConfig, load_config, resolve_config_path
from convobox.web.artifacts import add_artifact_routes
from convobox.web.bridge import (
    WebApprovalBridge,
    WebEventForwarder,
    WebListeningBridge,
    WebSafewordBridge,
    WebTextInputBridge,
)
from convobox.web.history import HistoryDB
from convobox.web.mcp_server import MCP_MOUNT_PATH, add_mcp_routes
from convobox.web.settings_api import add_settings_routes
from convobox.web.stream import EventBroadcaster
from convobox.web.uploads import add_upload_routes


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

# CSRF: see require_csrf_header's own docstring/comment below for the
# full reasoning. Any header name not on CORS' safelist works; this one
# just self-documents which app it's for. Starlette's Headers mapping is
# case-insensitive, so a literal lowercase key here matches any casing
# the client actually sends.
_CSRF_HEADER = "x-convobox-client"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

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
    safeword_bridge: WebSafewordBridge | None = None,
    text_bridge: WebTextInputBridge | None = None,
    quit_handler: Callable[[], None] | None = None,
    config_path: Path | None = None,
    working_dir: Path | None = None,
    mcp_token: str | None = None,
    web_forwarder: WebEventForwarder | None = None,
    web_ui_token: str | None = None,
    port: int | None = None,
) -> FastAPI:
    broadcaster = broadcaster if broadcaster is not None else EventBroadcaster()
    display = display if display is not None else DisplayConfig()
    app = FastAPI(title="ConvoBox Web UI")
    app.state.db = db
    app.state.broadcaster = broadcaster

    # Scoped to the ACTUAL bound port when known, not any localhost port --
    # see require_web_ui_token's own docstring below for why the token
    # check is the real control now; this is defense in depth on top of
    # it, closing "any other local process/page that happens to guess or
    # already know the CSRF header's constant value gets treated as
    # trusted" (found via an independently cross-verified security
    # review, 2026-09-01). `port=None` (most existing tests, which don't
    # care about this) keeps the original any-port regex so nothing here
    # forces every test to start passing a port.
    origin_regex = (
        rf"^https?://(127\.0\.0\.1|localhost):{port}$" if port is not None else _LOCALHOST_ORIGIN_REGEX
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_web_ui_token(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Real authentication for the browser-facing web UI, not just the
        CSRF header above (which forces a preflight but checks for a
        constant, public string -- anyone who's read this file knows it,
        so it stops a same-origin-blind cross-origin PAGE but not a local
        process that already knows what to send).

        A random per-session bearer token, generated once in
        run_convobox.py and embedded in the URL it prints
        (`http://host:port/?token=...`) -- the SAME "random per-session
        token over a loopback channel" pattern already used for the MCP
        mount (web/mcp_server.py's _require_bearer_token) and the
        approval-hook TCP server (adapters/claude_code.py), not a new
        pattern invented here. Deliberately NOT a cookie: a cookie gets
        attached by the browser automatically regardless of which page
        triggered the request (subject to SameSite rules, and a DNS-
        rebinding attacker page specifically exploits this) -- a bearer
        token/query param requires the CALLING JS to already know the
        value, which only this app's own frontend (having read it once
        from `location.search` at initial load) does.

        Checked on /api/* only -- the static HTML/JS/CSS shell itself
        isn't sensitive (no secrets embedded in it beyond what an
        attacker would need the token for anyway), matching how a
        Jupyter-style login flow serves its shell unauthenticated but
        gates the actual API. /mcp is exempt: it already carries its own
        independent bearer-token auth (mcp_server.py), and its client is
        a CLI subprocess, not a browser page reading this URL.

        Accepts the token via `Authorization: Bearer <token>` (what
        index.html's fetch() calls send) OR a `?token=` query param
        (for the handful of GET-by-URL cases a browser can't attach a
        custom header to: the EventSource live-event stream, and the
        artifact pane's direct `<img src>`/`<iframe src>`/download-link
        URLs). `web_ui_token=None` (most existing tests, and any
        create_app() caller that doesn't pass one) leaves this check off
        entirely -- run_convobox.py's own real startup path always
        generates one when the web UI is enabled.
        """
        if web_ui_token is None or not request.url.path.startswith("/api"):
            return await call_next(request)
        auth_header = request.headers.get("authorization", "")
        if auth_header == f"Bearer {web_ui_token}" or request.query_params.get("token") == web_ui_token:
            return await call_next(request)
        return Response(status_code=401, content="missing or invalid auth token")

    @app.middleware("http")
    async def require_csrf_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        # CSRF: the three routes below take no request body (/api/quit,
        # /api/stop, /api/sessions/{id}/clear) -- a cross-origin page's
        # `fetch(url, {method:"POST"})` is a CORS "simple request" for
        # those (no body, no custom headers), which the browser sends
        # WITHOUT a preflight. CORSMiddleware above only controls whether
        # the attacking page can READ the response; it never stops the
        # browser from sending a simple request in the first place, so
        # those three routes' real side effects (kill the session,
        # hard-stop, wipe history) were reachable from any tab, not just
        # this app's own loopback origin. The JSON-bodied mutating routes
        # were only protected INCIDENTALLY (a JSON content-type forces a
        # preflight, which _LOCALHOST_ORIGIN_REGEX then rejects) -- an
        # accident of body shape, not a designed control; a future
        # body-less route would silently reopen the same gap. Found via
        # autonomous codebase review, 2026-08-08 (GitHub issue #235,
        # finding A3).
        #
        # Fix: require a header no CORS "simple request" is allowed to
        # carry, uniformly on every mutating route -- this forces a real
        # preflight every time, which the existing CORS middleware then
        # correctly rejects for any non-loopback origin. index.html's own
        # fetch() calls all send this (CSRF_HEADERS, defined once near
        # the top of its script).
        #
        # MCP_MOUNT_PATH is deliberately exempt: its client is the
        # claude/codex CLI subprocess (web/mcp_server.py), not a browser
        # tab -- it has no way to know about or send this header, and
        # isn't the CSRF threat model this check exists for anyway (a
        # malicious cross-origin PAGE riding the user's own browser
        # session). That route carries its own bearer-token auth instead
        # (see mcp_server.py's module docstring for why).
        if (
            request.method in _MUTATING_METHODS
            and _CSRF_HEADER not in request.headers
            and not request.url.path.startswith(MCP_MOUNT_PATH)
        ):
            return Response(status_code=403, content="missing required header")
        return await call_next(request)

    add_settings_routes(app, config_path if config_path is not None else resolve_config_path())
    add_artifact_routes(app, working_dir)
    add_mcp_routes(app, working_dir, mcp_token, web_forwarder)

    async def _notify_backend_of_upload(filename: str) -> None:
        # Best-effort: no live session (text_bridge unset/not ready) means
        # the file still landed in working_dir successfully -- the upload
        # itself must not fail just because there's nothing to tell yet.
        if text_bridge is not None and text_bridge.is_ready:
            await text_bridge.submit(f"[Uploaded file: {filename}]")

    add_upload_routes(app, working_dir, on_uploaded=_notify_backend_of_upload)

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

    @app.post("/api/stop")
    async def stop_now() -> dict[str, bool]:
        # Same trust boundary as every other mutating route here (no auth,
        # loopback-only). Distinct from /api/listening's pause action --
        # this does exactly what saying the safeword does (abort the
        # current turn, keep listening normally afterward), not the
        # pause-until-resume-word behavior. See WebSafewordBridge's own
        # docstring for why these are deliberately separate bridges.
        if safeword_bridge is None or not safeword_bridge.is_ready:
            raise HTTPException(
                503,
                "no live session to stop -- this only works during a "
                "real run_convobox.py --web session, not a disconnected preview",
            )
        await safeword_bridge.trigger()
        return {"stopped": True}

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
        #
        # Re-reads config_path fresh on every call (2026-08-07, fixed
        # live after JP hit this directly): unlike every other section,
        # display.* is never consumed by the mic-loop pipeline
        # run_convobox.py builds once at startup (see
        # scripts/settings_tui.py's SectionSpec.restart_required for the
        # grep confirming that) -- it exists purely to answer THIS route.
        # There was never a real reason to only read it once at
        # create_app() time; that was just an unexamined default, not a
        # deliberate choice, and it's what made a color/name change need
        # a full backend restart instead of a page refresh. Falls back
        # to the closure-captured `display` param when config_path is
        # None (tests that construct DisplayConfig directly, with no
        # real file backing it -- same shape resolve_config_path()
        # itself can't help with).
        if config_path is not None:
            live_display = load_config(config_path).display
            return {
                "user_color": live_display.user_color,
                "assistant_color": live_display.assistant_color,
                "user_name": live_display.user_name,
                "assistant_name": live_display.assistant_name,
            }
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
        session_id: str,
        # Bounded (GitHub issue #235, finding A6): limit/offset used to go
        # straight into the SQL LIMIT/OFFSET clause unbounded -- a client
        # (or a bug in one) could request an arbitrarily large page in one
        # call. 1000 matches HistoryDB.get_session_events's own default.
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
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
        if web_forwarder is not None:
            web_forwarder.forward_approval_resolved(approved)
        return {"status": "approved" if approved else "denied", "explanation": None}

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str) -> StreamingResponse:
        # Streamed (GitHub issue #235, finding A6): this used to call
        # export_session_json, which does one unbounded query
        # (limit=1_000_000) and builds the entire response as one JSON
        # string in memory before writing a single byte -- a genuinely
        # long-lived, high-traffic session's export could be large.
        # HistoryDB.iter_session_events yields rows one at a time (a real
        # cursor iteration, not .fetchall()); this generator writes each
        # event's JSON as it's produced, same net JSON shape
        # export_session_json produces (just without the indent=2
        # pretty-printing, not meaningful for a machine-consumed export).
        async def generate() -> AsyncIterator[str]:
            yield f'{{"session_id": {json.dumps(session_id)}, "events": ['
            first = True
            for event in db.iter_session_events(session_id):
                if not first:
                    yield ","
                first = False
                yield json.dumps(event)
            yield "]}"

        return StreamingResponse(
            generate(),
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
