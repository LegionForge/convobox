"""In-memory fan-out for the web UI's live event stream (docs/WEB-UI-
ARCHITECTURE.md's "Server-Sent Events (live stream)" section).

A single shared asyncio.Queue (the design doc's original sketch) would only
ever deliver each event to whichever ONE consumer happened to drain it --
fine for one browser tab, silently wrong the moment a second tab opens the
same stream. EventBroadcaster instead gives every subscriber its own queue
and copies each event to all of them.

Broadcasts plain JSON-able dicts, not BackendEvent objects -- not every
live event has one (a user transcript doesn't; it's not something any
backend adapter emits), so the caller (WebEventForwarder) shapes whatever
it's broadcasting into the wire format before handing it here. See
convobox.web.history.event_to_dict for the BackendEvent -> dict shape.
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.discard(queue)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            await queue.put(payload)

    async def close_all(self) -> None:
        """Tells every connected SSE subscriber to end its stream on its
        own (sse_lines() sees None and returns), instead of being force-
        cancelled by uvicorn's shutdown teardown -- which is what was
        producing noisy (if harmless) "Exception in ASGI application"
        log lines on every --web quit, confirmed live 2026-07-29. Called
        once, deliberately, right before the web server's should_exit
        flips true (run_convobox.py's shutdown sequence).

        Doesn't touch subscribe/unsubscribe/broadcast and adds no
        exception handling anywhere -- a genuine error inside sse_lines()
        or the route itself still raises and propagates exactly as
        before; this only gives an already-open connection an explicit,
        additive way to end itself cleanly on request, nothing is ever
        swallowed.
        """
        for queue in list(self._subscribers):
            await queue.put(None)
