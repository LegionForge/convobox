"""Gives the backend coding agent an explicit tool to push a file from
backend.working_dir into the artifact pane, without writing or editing it
(docs/ARTIFACT-PANE-SCOPE.md's ARTIFACT event previously only ever fired
implicitly, from a detected Write/Edit tool call -- there was no way for
the agent to say "show me this file I only read" or refocus one already
shown). JP's own framing: "the llm should be given a tool to refocus a
document or show a document from the cwd."

Security posture: the tool can never expose anything the browser-facing
GET /api/artifacts/{path} route wouldn't already serve -- both share the
exact same _resolve_artifact() fence (working_dir, path-traversal) and the
same ARTIFACT_MEDIA_TYPES extension allowlist. This is genuinely a NEW
kind of exposure, though: a plain loopback HTTP endpoint any local
process, or any browser tab, could POST to. The app-wide CSRF middleware
(app.py's require_csrf_header) doesn't help here -- it checks for a custom
header only ConvoBox's own frontend JS knows to send, but the MCP client
making these requests is the claude/codex CLI subprocess, not a browser,
and has no way to send it. So this route carries its OWN auth instead: a
random per-session bearer token, generated once in run_convobox.py and
handed to the backend CLI via --mcp-config's own "headers" field (the
same shape --header already accepts on `claude mcp add`, confirmed via
`claude mcp add --help`) -- the same "random per-session token over a
loopback channel" shape already used for the approval-hook TCP server in
adapters/claude_code.py, not a new pattern invented for this.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from mcp.server.mcpserver import MCPServer

from convobox.adapters.base import ARTIFACT_MEDIA_TYPES, BackendEvent, BackendEventType
from convobox.web.artifacts import _resolve_artifact
from convobox.web.bridge import WebEventForwarder

logger = logging.getLogger(__name__)

# Mount path on the main FastAPI app -- also the last path segment of the
# URL run_convobox.py hands the backend CLI via --mcp-config. Kept as one
# constant so the two ends can't drift apart.
MCP_MOUNT_PATH = "/mcp"

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _require_bearer_token(app: ASGIApp, token: str) -> ASGIApp:
    """Wraps an ASGI app so every request needs `Authorization: Bearer
    <token>` -- see this module's docstring for why the browser-UI's own
    CSRF header can't cover this route instead. Checked here, at the ASGI
    level, rather than via the mcp SDK's own `auth`/TokenVerifier system:
    that machinery is built for real OAuth flows (issuer URLs, scopes,
    resource metadata endpoints); a fixed loopback shared secret doesn't
    need any of it, and hand-rolling the OAuth shape just to check one
    constant string would be more code and more to get wrong, not less.
    """
    expected = f"Bearer {token}"

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(b"authorization", b"").decode("latin-1") != expected:
            response_start: dict[str, Any] = {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            }
            await send(response_start)
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await app(scope, receive, send)

    return wrapped


def add_mcp_routes(
    app: FastAPI,
    working_dir: Path | None,
    token: str | None,
    web_forwarder: WebEventForwarder | None,
) -> None:
    """Mounts the show_document MCP server at MCP_MOUNT_PATH, streamable-
    HTTP transport (the modern MCP transport; --mcp-config's own "type":
    "http" per `claude mcp add --help`, not the older SSE shape).

    No-ops (mounts nothing) unless BOTH working_dir and token are set --
    matches run_convobox.py's own condition for generating them
    (config.web.enabled AND backend.working_dir configured). Deliberately
    NOT mounted-but-always-erroring the way GET /api/artifacts is when
    working_dir is unset: that route just 503s on a browser request,
    harmless; an LLM tool that's ALWAYS advertised as available and
    ALWAYS fails wastes a real agent turn discovering that, which is
    worse than the tool not existing at all.
    """
    if working_dir is None or token is None:
        return

    server = MCPServer(name="convobox")

    @server.tool(
        name="show_document",
        description=(
            "Show or refocus a file from the current working directory in "
            "ConvoBox's artifact pane, without writing or editing it. Use "
            "this to bring a file back into view, or to show one you only "
            "read rather than wrote. Only file types ConvoBox already "
            "renders are accepted (images, HTML, and a fixed set of "
            "source-code languages) -- anything else is rejected."
        ),
    )
    async def show_document(path: str) -> str:
        # async, though nothing here awaits anything, is deliberate, not
        # decorative: the mcp SDK runs SYNC tool functions in an anyio
        # worker thread (func_metadata.py's call_fn_with_arg_validation),
        # which has no asyncio event loop of its own -- web_forwarder()
        # below calls asyncio.ensure_future() internally (bridge.py's
        # _broadcast), which requires one. A sync `def` here live-failed
        # with "There is no current event loop in thread 'AnyIO worker
        # thread'" the first time this was run through a real claude CLI
        # session; async keeps this on the main loop instead (the SDK
        # awaits async tool functions directly, no thread offload).
        try:
            candidate = _resolve_artifact(working_dir, path)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        media_type = ARTIFACT_MEDIA_TYPES.get(candidate.suffix.lower())
        if media_type is None:
            raise ValueError(f"{candidate.suffix!r} is not a servable artifact type")
        # working_dir is guaranteed non-None here -- add_mcp_routes()
        # already returned above otherwise, and _resolve_artifact would
        # have raised its own 503 first if it were.
        assert working_dir is not None  # nosec B101
        relative = candidate.relative_to(working_dir.resolve()).as_posix()
        if web_forwarder is not None:
            web_forwarder(BackendEvent(type=BackendEventType.ARTIFACT, artifact_path=relative))
        return f"Showing {relative} in the artifact pane."

    @server.tool(
        name="get_shown_artifact",
        description=(
            "Check which file, if any, is currently displayed in ConvoBox's "
            "artifact pane -- grounded in the real UI state (which tab the "
            "user has selected, or that the pane is closed), not just the "
            "last file this session happened to open. Use this to answer "
            "questions like 'what's showing?' or 'which file am I looking "
            "at?' rather than guessing from conversation history."
        ),
    )
    async def get_shown_artifact() -> str:
        # Purely a read of app.state.active_artifact_path -- see
        # add_artifact_routes()'s own comment on that field for why the
        # BROWSER, not this server, is the source of truth for it (a
        # POST from renderArtifact() on every live event, tab click, and
        # Browse-files open). Sub-second race, accepted rather than
        # engineered around: if this tool is called in the same instant
        # show_document just fired, the browser may not have reported
        # the new value back yet. A human asking a follow-up question
        # takes far longer to speak than that round trip takes to
        # complete in practice.
        current = getattr(app.state, "active_artifact_path", None)
        if current is None:
            return "No artifact is currently shown in the pane."
        return f"Currently showing: {current}"

    mcp_app = server.streamable_http_app(streamable_http_path="/")
    app.mount(MCP_MOUNT_PATH, _require_bearer_token(mcp_app, token))

    # FastAPI/Starlette only runs the OUTER app's own lifespan by default
    # -- a mounted sub-app's lifespan is never entered automatically. The
    # MCP session manager NEEDS its lifespan to run: streamable_http_app()
    # wires it via session_manager.run() as an async context manager that
    # initializes an internal anyio task group, and every request into a
    # session manager whose task group was never started raises
    # "RuntimeError: Task group is not initialized. Make sure to use
    # run()." -- live-confirmed 2026-08-1x: every real POST to /mcp/
    # 500'd with exactly that error before this fix. Wrapping (not
    # replacing) app.router.lifespan_context preserves whatever the
    # caller's own FastAPI(lifespan=...) already does, if anything.
    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def combined_lifespan(started_app: FastAPI) -> Any:
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(previous_lifespan(started_app))
            await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield

    app.router.lifespan_context = combined_lifespan
