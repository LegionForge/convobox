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

_EVENT_TYPE_MAP: dict[str, BackendEventType] = {
    "text": BackendEventType.TEXT,
    "tool_call": BackendEventType.TOOL_CALL,
    "tool_result": BackendEventType.TOOL_RESULT,
    "error": BackendEventType.ERROR,
    "done": BackendEventType.DONE,
}


class OpenCodeAdapter(BackendAdapter):
    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = url.rstrip("/")
        self._client = client if client is not None else httpx.AsyncClient(base_url=self._base_url)
        self._session_id: str | None = None
        self._busy = False
        self._sse_context: Any = None
        warn_if_insecure(self._base_url)

    async def _ensure_session(self) -> str:
        if self._session_id is None:
            resp = await self._client.post("/api/sessions")
            resp.raise_for_status()
            self._session_id = resp.json()["id"]
        return self._session_id

    async def _post_message(self, text: str) -> None:
        session_id = await self._ensure_session()
        payload = {"messages": [{"role": "user", "content": text}]}
        resp = await self._client.post(f"/api/sessions/{session_id}/messages", json=payload)
        resp.raise_for_status()
        self._busy = True

    async def send_text(self, text: str) -> None:
        await self._post_message(text)

    async def send_interject(self, text: str) -> None:
        await self._post_message(text)

    async def send_hard_stop(self) -> None:
        """Best-effort abort.

        OpenCode's documented HTTP API exposes no cancel endpoint, so this can
        only disconnect the local SSE stream; it does not abort work already
        running server-side. A true server-side abort would require OpenCode to
        expose a cancel endpoint.

        CORRECTION (see OPENCODE_API_NOTES.md): this whole class was built
        against an inferred, not real, API shape, and testing against an
        actual `opencode serve` instance found a real `POST
        /session/:id/abort` endpoint — the claim above is false against the
        current OpenCode API. Not yet fixed; this class's paths/bodies/event
        parsing are wrong wholesale, not just this one method.
        """
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
            self._client, "GET", f"/api/sessions/{session_id}/events"
        )
        sse_source = await self._sse_context.__aenter__()
        try:
            async for sse in sse_source.aiter_sse():
                event = _parse_event(sse.data)
                if event is None:
                    continue
                if event.type in (BackendEventType.DONE, BackendEventType.ERROR):
                    self._busy = False
                yield event
        finally:
            # Clear busy on ANY exit from this generator, not just an
            # observed DONE/ERROR above: a dropped connection, an exception
            # from aiter_sse, or the consumer closing the generator early
            # would otherwise latch is_busy() True forever, since nothing
            # else in this class ever clears it back to False.
            self._busy = False
            await self._close_sse()


def _parse_event(data: str) -> BackendEvent | None:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError:
        return None
    event_type = _EVENT_TYPE_MAP.get(raw.get("type", ""))
    if event_type is None:
        return None
    return BackendEvent(
        type=event_type,
        content=raw.get("content") or raw.get("text"),
        tool=raw.get("tool"),
        tool_input=raw.get("toolInput"),
        tool_output=raw.get("toolOutput"),
    )


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
