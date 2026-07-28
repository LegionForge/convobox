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
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            await queue.put(payload)
