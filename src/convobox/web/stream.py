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

# Each subscriber queue is bounded (2026-08-08 review, finding B5): an
# unbounded asyncio.Queue meant a subscriber that stops draining -- a
# backgrounded/suspended browser tab, still TCP-connected but no longer
# running its JS event loop, is the realistic case, not just a bug -- had
# its queue grow for the rest of the session with nothing to cap it. 200 is
# generous for a live-spoken conversation (each event is one transcript/
# response/tool-call, arriving on the order of seconds apart, not a
# high-frequency stream) while still bounding worst-case memory per
# subscriber to a small, fixed number of JSON dicts.
_DEFAULT_MAX_QUEUE_SIZE = 200


class EventBroadcaster:
    def __init__(self, max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._max_queue_size = max_queue_size
        # Drops accumulated per-subscriber since that subscriber's last
        # successfully-delivered "dropped" marker (see _put_dropping_oldest
        # and broadcast() below) -- absent/0 means nothing pending.
        self._dropped: dict[asyncio.Queue[dict[str, Any] | None], int] = {}

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.discard(queue)
        self._dropped.pop(queue, None)

    def _put_dropping_oldest(
        self, queue: asyncio.Queue[dict[str, Any] | None], item: dict[str, Any] | None
    ) -> None:
        """put_nowait(), evicting the oldest queued item to make room
        instead of blocking when full. Blocking here (a plain `await
        queue.put(...)`) would stall broadcast()'s for-loop -- and with it,
        delivery to every OTHER, healthy subscriber -- behind a single
        stalled one; the original unbounded queue avoided that by simply
        never blocking, at the cost of never bounding memory either. Every
        eviction is counted in self._dropped so the subscriber eventually
        learns something was lost (see broadcast()).

        Loops rather than a single try/except: a queue seen as full can
        race with its own consumer briefly emptying it (get_nowait() then
        raises QueueEmpty, not evicting anything) -- retrying the put
        covers that without assuming which way the race went.
        """
        while True:
            try:
                queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    self._dropped[queue] = self._dropped.get(queue, 0) + 1
                except asyncio.QueueEmpty:
                    continue

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            pending = self._dropped.get(queue)
            if pending:
                # Reset before the put (not after): if delivering the
                # marker itself evicts something, that eviction is a NEW
                # drop, counted against the count the NEXT marker reports,
                # not silently folded into the one being sent now.
                self._dropped[queue] = 0
                self._put_dropping_oldest(queue, {"type": "dropped", "count": pending})
            self._put_dropping_oldest(queue, payload)

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

        Uses the same evict-oldest put as broadcast() (not a raw blocking
        `await queue.put(None)`) so a subscriber whose queue happens to be
        full at shutdown still reliably receives the sentinel and closes,
        rather than this call hanging on a full queue nothing is draining.
        """
        for queue in list(self._subscribers):
            self._put_dropping_oldest(queue, None)
