from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

import httpx
from httpx_sse import aconnect_sse

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Real OpenCode SSE event type strings (v1.17.18, /api/ versioned surface;
# confirmed live against a real `opencode serve` instance, both a
# single-step text reply and a multi-step tool-calling one -- see
# OPENCODE_API_NOTES.md). The real taxonomy is a ~28-member discriminated
# union (session/step/text/reasoning/tool/shell/compaction/revert); only
# these five map onto our 5-value BackendEventType or drive is_busy()
# tracking, and DONE is deliberately never emitted -- see is_busy()'s
# tracking, driven from _STEP_ENDED below, not a DONE event.
_TEXT_ENDED = "session.next.text.ended"
_TOOL_CALLED = "session.next.tool.called"
_TOOL_SUCCESS = "session.next.tool.success"
_TOOL_FAILED = "session.next.tool.failed"
_STEP_ENDED = "session.next.step.ended"

# step.ended's own `finish` field is the real, precise "is more coming"
# signal (confirmed live: a multi-step tool-calling response's first step
# ends with finish="tool-calls", meaning another step follows; a genuinely
# final step ends with finish="stop"). The OpenAPI spec types `finish` as a
# bare string with no enum, so this is an allowlist of the one confirmed
# "definitely continuing" value, not a denylist of terminal ones -- an
# unrecognized future finish reason is treated as terminal (clears
# is_busy()), not as "definitely continuing". That choice is deliberate:
# an unknown reason mistakenly treated as terminal just means a later
# utterance gets queued instead of steered (harmless, delivery="queue"
# waits behind current work) -- the opposite mistake (latching busy forever
# on an unrecognized value) blocks the user outright. See OPENCODE_API_NOTES.md.
_CONTINUING_FINISH_REASONS = frozenset({"tool-calls"})


class OpenCodeAdapter(BackendAdapter):
    """Adapter for OpenCode's real /api/ (versioned) HTTP+SSE surface.

    CORRECTED (see OPENCODE_API_NOTES.md): this class was previously built
    against an inferred, never-verified API shape (ported from an unrelated
    TypeScript project's docs) -- wrong endpoint paths, wrong request/event
    bodies, and a false claim that no cancel endpoint exists. This version
    is built from a real `opencode serve` (v1.17.18) instance: its live
    OpenAPI spec (`GET /doc`) plus multiple real end-to-end runs captured
    over its real SSE stream.

    Why the /api/ surface specifically, not the unversioned /session one:
    confirmed empirically that /session has no event-stream endpoint of its
    own -- GET /api/session/:id/event is the only place session events are
    published at all. /api/ also has a prompt `delivery` mode
    (steer/queue) that maps directly onto interject vs. fresh-command,
    which the unversioned surface lacks too.

    is_busy() is tracked from step.ended's `finish` field (see
    _CONTINUING_FINISH_REASONS), not OpenCode's own POST .../wait endpoint
    ("wait for a session agent loop to become idle") despite that sounding
    like the obviously-correct mechanism. Confirmed live, repeatedly: a
    concurrent POST .../wait while this adapter's own SSE GET
    .../event connection is open silently kills that connection's event
    delivery (0 events received, even though the server genuinely emits
    them -- confirmed via a raw, wait-free connection receiving them fine).
    Since Orchestrator always keeps events() running continuously
    (start_event_loop() runs before any send_*), /wait is fundamentally
    incompatible with how this adapter is actually used, not just an edge
    case -- see OPENCODE_API_NOTES.md for the full investigation.
    """

    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = url.rstrip("/")
        self._client = client if client is not None else httpx.AsyncClient(base_url=self._base_url)
        self._session_id: str | None = None
        self._busy = False
        self._sse_context: Any = None
        warn_if_insecure(self._base_url)

    async def _ensure_session(self) -> str:
        if self._session_id is None:
            resp = await self._client.post("/api/session", json={})
            resp.raise_for_status()
            self._session_id = resp.json()["data"]["id"]
        return self._session_id

    async def _post_prompt(self, text: str, delivery: str) -> None:
        session_id = await self._ensure_session()
        payload = {"prompt": {"text": text}, "delivery": delivery}
        resp = await self._client.post(f"/api/session/{session_id}/prompt", json=payload)
        resp.raise_for_status()
        self._busy = True

    async def send_text(self, text: str) -> None:
        # Idle -> fresh command. "queue" admits and runs immediately since
        # nothing else is in flight -- the semantically correct choice for
        # "this is a new command", as distinct from "steer" (redirecting
        # something already running, which nothing is).
        await self._post_prompt(text, delivery="queue")

    async def send_interject(self, text: str) -> None:
        # Busy -> soft interject. "steer" is OpenCode's own term for
        # influencing an in-progress agent loop without stopping it --
        # exactly what a soft interject means here. "queue" would instead
        # wait behind the current run before taking effect, which is not
        # what an interject is for.
        await self._post_prompt(text, delivery="steer")

    async def send_hard_stop(self) -> None:
        """Abort the session's in-progress agent loop, for real.

        CORRECTED (see OPENCODE_API_NOTES.md): earlier versions of this
        method claimed OpenCode's API exposed no cancel endpoint. That was
        never true -- POST /api/session/:id/interrupt exists and its own
        description says idle interruption is a no-op, so it's safe to call
        unconditionally here rather than guarding on is_busy() first.
        """
        if self._session_id is not None:
            try:
                resp = await self._client.post(f"/api/session/{self._session_id}/interrupt")
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.warning("OpenCode interrupt request failed", exc_info=True)
        await self._close_sse()
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    async def _close_sse(self) -> None:
        if self._sse_context is not None:
            await self._sse_context.__aexit__(None, None, None)
            self._sse_context = None

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        session_id = await self._ensure_session()
        self._sse_context = aconnect_sse(
            self._client,
            "GET",
            f"/api/session/{session_id}/event",
            # httpx's default read timeout (5s) is fine for the short
            # request/response calls elsewhere in this class, but wrong
            # here: confirmed live, a real multi-step tool-calling response
            # can legitimately go 5+ seconds between SSE frames (the tool
            # itself running, the model "thinking"), and httpx.ReadTimeout
            # would kill this long-lived connection mid-response for no
            # real failure -- see OPENCODE_API_NOTES.md. connect/write/pool
            # keep the default (still want a fast failure if the server is
            # simply unreachable); only read is disabled.
            timeout=httpx.Timeout(5.0, read=None),
        )
        sse_source = await self._sse_context.__aenter__()
        try:
            async for sse in sse_source.aiter_sse():
                outer = _safe_json_loads(sse.data)
                if outer is None:
                    continue
                self._track_busy(outer)
                event = _to_backend_event(outer)
                if event is not None:
                    yield event
        finally:
            # Last-resort safety net, not the primary signal: the normal
            # path clears is_busy() via _track_busy the moment a genuinely
            # terminal step.ended arrives (finish != "tool-calls"), which
            # fires well before the stream itself ends. But this generator
            # can also end for reasons that say nothing about whether the
            # agent loop actually finished -- a dropped connection, an
            # exception, or the consumer closing it early -- and without
            # clearing busy here too, any of those would latch is_busy()
            # True forever (nothing else would ever clear it). Regression
            # protection carried over from the original adapter, which had
            # the identical gap for its own (wrong) DONE-event-based
            # tracking; the failure mode is the same regardless of what
            # clears busy in the normal case.
            self._busy = False
            await self._close_sse()

    def _track_busy(self, outer: dict[str, Any]) -> None:
        if outer.get("type") != _STEP_ENDED:
            return
        finish = (outer.get("data") or {}).get("finish")
        if finish not in _CONTINUING_FINISH_REASONS:
            self._busy = False


def _safe_json_loads(data: str) -> dict[str, Any] | None:
    try:
        parsed: dict[str, Any] = json.loads(data)
        return parsed
    except json.JSONDecodeError:
        return None


def _to_backend_event(outer: dict[str, Any]) -> BackendEvent | None:
    event_type = outer.get("type", "")
    payload = outer.get("data") or {}

    if event_type == _TEXT_ENDED:
        return BackendEvent(type=BackendEventType.TEXT, content=payload.get("text"))
    if event_type == _TOOL_CALLED:
        tool_input = payload.get("input")
        return BackendEvent(
            type=BackendEventType.TOOL_CALL,
            tool=payload.get("tool"),
            tool_input=json.dumps(tool_input) if tool_input is not None else None,
        )
    if event_type == _TOOL_SUCCESS:
        return BackendEvent(
            type=BackendEventType.TOOL_RESULT,
            tool_output=json.dumps(payload.get("structured") or payload.get("content")),
        )
    if event_type == _TOOL_FAILED:
        # Not empirically observed -- no real tool failure occurred during
        # investigation. Shape inferred from the OpenAPI spec's
        # SessionNextToolFailed schema (data.error) -- spec-grounded, not
        # independently re-verified against a live failure, same
        # confidence-tier distinction DEPENDENCY_LICENSE_AUDIT.md uses.
        return BackendEvent(
            type=BackendEventType.ERROR,
            tool_output=json.dumps(payload.get("error")),
        )
    # Every other real event type (session.next.prompt.admitted/.prompted,
    # step.started, step.ended itself (handled by _track_busy, not here),
    # text.started, tool.input.*, reasoning.*, shell.*, compaction.*,
    # revert.*, ...) is intentionally not surfaced as a BackendEvent --
    # our 5-value model has no slot for them. A deliberately narrow
    # mapping, not an oversight.
    return None


def warn_if_insecure(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        # A schemeless "host:port" string (e.g. "somehost:4096") parses with
        # the host mistaken for the scheme and an empty/None hostname —
        # urlparse's documented ambiguity for inputs without "//". Without
        # this branch such a URL would silently skip the check below
        # entirely (parsed.scheme != "http") even though httpx accepts it
        # at client-construction time and would make plaintext requests.
        logger.warning(
            "OpenCode backend URL %r has no recognized http/https scheme; "
            "requests may fail, or this check cannot confirm the connection "
            "is encrypted. Use an explicit http:// or https:// prefix.",
            url,
        )
        return
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        logger.warning(
            "OpenCode backend URL %s uses plaintext http:// to a non-local host; "
            "traffic (including agent commands) is unencrypted.",
            url,
        )
