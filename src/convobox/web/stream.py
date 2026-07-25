"""In-memory fan-out for the web UI's live event stream (docs/WEB-UI-
ARCHITECTURE.md's "Server-Sent Events (live stream)" section).

A single shared asyncio.Queue (the design doc's original sketch) would only
ever deliver each event to whichever ONE consumer happened to drain it --
fine for one browser tab, silently wrong the moment a second tab opens the
same stream. EventBroadcaster instead gives every subscriber its own queue
and copies each event to all of them.
"""

from __future__ import annotations

import asyncio

from convobox.adapters.base import BackendEvent


class EventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[BackendEvent]] = set()

    def subscribe(self) -> asyncio.Queue[BackendEvent]:
        queue: asyncio.Queue[BackendEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BackendEvent]) -> None:
        self._subscribers.discard(queue)

    async def broadcast(self, event: BackendEvent) -> None:
        for queue in list(self._subscribers):
            await queue.put(event)
