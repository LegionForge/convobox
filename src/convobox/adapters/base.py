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
    # A tool call is blocked awaiting a voice approve/deny decision (Phase 3,
    # docs/DESIGN-0.3.0-interaction-and-safety.md). Only adapters with a
    # runtime-answerable approval channel emit this -- see
    # BackendAdapter.resolve_pending_approval's docstring for why most
    # adapters never do. `tool`/`tool_input` carry what's pending, the same
    # fields TOOL_CALL uses.
    APPROVAL_REQUEST = "approval_request"


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
    endpoint) was not — see OPENCODE_API_NOTES.md. OpenCode does have a
    real cancel endpoint (POST /api/session/:id/interrupt), and
    OpenCodeAdapter now calls it. Kept as the cautionary example anyway:
    the adapter was shipped and passed its own test suite for a while
    before anyone ran it against a real server and found the gap between
    "what the docs/an earlier project claimed" and "what the API actually
    does" — the reason this class's own docstring exists.
    """

    @abstractmethod
    async def send_text(self, text: str) -> None: ...

    @abstractmethod
    async def send_interject(self, text: str) -> None: ...

    @abstractmethod
    async def send_hard_stop(self) -> None: ...

    @abstractmethod
    def is_busy(self) -> bool: ...

    async def wait_listening(self, timeout: float = 2.0) -> None:
        """Best-effort wait until this adapter's event stream is established.

        Default is an immediate no-op: adapters whose transport can't lose
        events to a subscribe-after-send race have nothing to wait for.
        Adapters that CAN (e.g. OpenCode's SSE endpoint, which never
        replays events emitted before the subscriber registered) override
        this so Orchestrator can let the subscription win the race before
        posting a prompt. Implementations must return (not raise) on
        timeout -- a caller that never consumes events() must not deadlock.
        """
        return None

    def set_interactive_approvals(self, enabled: bool) -> None:
        """Opt in to holding a backend approval request for the operator.

        Most backends have no answerable approval channel, so the safe
        default is a no-op. Adapters that do expose one and can toggle it
        at RUNTIME (currently Codex -- see codex.py) override this and emit
        ``APPROVAL_REQUEST`` events while enabled. ClaudeCodeAdapter is
        deliberately NOT one of these: its hook-based mechanism is baked
        into the spawned process's ``--settings``/``--permission-mode`` at
        CONSTRUCTION time (see its own module docstring), so there is no
        live process to toggle -- it's controlled via the
        ``permission_mode`` constructor argument instead (``"approve"``
        wires the hook; ``"plan"``/``"permissive"`` don't), and this
        method stays the inherited no-op for it.
        """
        return None

    async def resolve_pending_approval(self, approved: bool) -> bool:
        """Answer this adapter's currently-pending tool-call approval
        request, if it has one (see BackendEventType.APPROVAL_REQUEST).

        Returns whether there was one to answer. Default (False, no-op):
        most adapters have no runtime-answerable approval channel at all
        (opencode has no concept of one) and must not be forced to
        implement an override just to satisfy this class -- same "default
        no-op, override where real" shape as wait_listening. A caller
        answering when nothing is actually pending (a stale gate after a
        race) also gets False, not an exception -- see ClaudeCodeAdapter's
        and CodexAdapter's own overrides for the real implementations.
        False here must always be treated as "nothing to answer / fail
        closed", never as an implicit approval.
        """
        return False

    @abstractmethod
    def events(self) -> AsyncGenerator[BackendEvent, None]:
        # Typed as AsyncGenerator (not the looser AsyncIterator) because
        # callers rely on .aclose() being available on what this returns —
        # e.g. to cancel a live SSE stream on hard stop/shutdown — which
        # AsyncIterator doesn't guarantee but AsyncGenerator does.
        ...

    async def aclose(self) -> None:
        """Release transport resources (subprocess, sockets, HTTP client).

        Default no-op. Adapters that own a subprocess or client override this
        so shutdown closes them WHILE THE EVENT LOOP IS STILL RUNNING —
        otherwise Python finalizes the pipe transports after the loop has
        closed and spews harmless-but-alarming "Event loop is closed" /
        "unclosed transport" tracebacks (seen on Windows with the subprocess
        adapters). Must be idempotent and must not raise.
        """
        return None
