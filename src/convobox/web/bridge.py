"""Bridges Orchestrator's on_event hook into the web UI (docs/WEB-UI-
ARCHITECTURE.md). Kept as its own small callable, not inlined into
run_convobox.py's already-large on_event chain, so "how a BackendEvent
becomes a web history row / SSE broadcast" is unit-testable without
spinning up the whole voice loop.
"""

from __future__ import annotations

import asyncio

from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.web.history import HistoryDB
from convobox.web.stream import EventBroadcaster

# Mirrors WEB-UI-ARCHITECTURE.md's event_type vocabulary ("tool_call",
# "response", etc.) for the history row's own event_type column.
# APPROVAL_REQUEST is deliberately its own string ("approval_request"), not
# folded into "response" -- a pending decision isn't a spoken reply.
_EVENT_TYPE_NAMES: dict[BackendEventType, str] = {
    BackendEventType.TEXT: "response",
    BackendEventType.TOOL_CALL: "tool_call",
    BackendEventType.TOOL_RESULT: "tool_result",
    BackendEventType.ERROR: "error",
    BackendEventType.DONE: "done",
    BackendEventType.APPROVAL_REQUEST: "approval_request",
}


class WebEventForwarder:
    """Callable Orchestrator ``on_event`` hook.

    ``history``/``broadcaster`` are each independently optional (None means
    "skip that half") -- matches WebConfig's two separate opt-ins:
    ``web.enabled`` alone gets live SSE streaming with no persistence,
    ``web.history_tracking_enabled`` on top of that adds SQLite storage.
    Constructing this with both None is a valid, harmless no-op, matching
    web.enabled=False (the default) making zero behavioral difference.
    """

    def __init__(
        self,
        session_id: str,
        history: HistoryDB | None,
        broadcaster: EventBroadcaster | None,
    ) -> None:
        self._session_id = session_id
        self._history = history
        self._broadcaster = broadcaster

    def __call__(self, event: BackendEvent) -> None:
        if self._history is not None:
            self._history.append_event(
                self._session_id,
                _EVENT_TYPE_NAMES.get(event.type, event.type.value),
                backend_event=event,
            )
        if self._broadcaster is not None:
            # Orchestrator._on_event calls its hook synchronously from
            # inside a running event loop (_consume_events' async-for) --
            # this __call__ is that hook's contract (sync), so the
            # broadcast is scheduled rather than awaited, same reasoning as
            # _events.put_nowait() elsewhere in the adapters.
            asyncio.ensure_future(self._broadcaster.broadcast(event))
