from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from enum import Enum


class BackendEventType(str, Enum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"


class BackendEvent:
    def __init__(
        self,
        type: BackendEventType,
        content: str | None = None,
        tool: str | None = None,
        tool_input: str | None = None,
        tool_output: str | None = None,
    ) -> None:
        self.type = type
        self.content = content
        self.tool = tool
        self.tool_input = tool_input
        self.tool_output = tool_output


class BackendAdapter(ABC):
    """One implementation per target CLI (OpenCode, Claude Code, Codex, ...).

    send_interject and send_hard_stop are distinct: an interject should not
    derail a running task, a hard stop must abort it. Adapters that can't
    express interjection natively may queue it for the next idle point, but
    must never silently downgrade a hard stop to an interject.

    send_hard_stop's ability to deliver on "must abort it" is bounded by
    what the underlying backend actually exposes: if the backend has no
    server-side cancel endpoint, an adapter can only sever its own
    connection to it, not guarantee the backend stops acting. An adapter in
    that position must still fail toward safety at the adapter/orchestrator
    layer (disconnect, clear busy state, never continue routing to the
    stale in-flight task) and must document the gap in its own
    send_hard_stop rather than implying a guarantee it can't keep.

    The principle above is still correct; the example that used to follow
    it (OpenCodeAdapter.send_hard_stop, cited as a case with no cancel
    endpoint) is not — see OPENCODE_API_NOTES.md. A real cancel endpoint
    does exist there; the adapter just wasn't built against the real API.
    """

    @abstractmethod
    async def send_text(self, text: str) -> None: ...

    @abstractmethod
    async def send_interject(self, text: str) -> None: ...

    @abstractmethod
    async def send_hard_stop(self) -> None: ...

    @abstractmethod
    def is_busy(self) -> bool: ...

    @abstractmethod
    def events(self) -> AsyncGenerator[BackendEvent, None]:
        # Typed as AsyncGenerator (not the looser AsyncIterator) because
        # callers rely on .aclose() being available on what this returns —
        # e.g. to cancel a live SSE stream on hard stop/shutdown — which
        # AsyncIterator doesn't guarantee but AsyncGenerator does.
        ...
