from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Same two-stage shape as MicrophoneStream.stream() (convobox/audio/capture.py)
# and UtteranceSegmenter.feed_async() (convobox/vad/segmenter.py).
_READLINE_STALL_FIRST_WARNING_S = 0.5
_READLINE_STALL_REPEAT_WARNING_S = 5.0


async def readline_with_stall_diagnostic(
    stream: asyncio.StreamReader,
    proc: asyncio.subprocess.Process,
    label: str,
    busy: Callable[[], bool] | None = None,
) -> bytes:
    """await stream.readline(), logging a stall warning instead of blocking
    silently -- and logging proc.returncode on every warning, so a process
    that has already died without EOF'ing its pipe is visible too.

    Uses asyncio.wait() with a timeout in a loop, NOT asyncio.wait_for() --
    wait_for cancels the underlying coroutine on timeout, which would
    discard a line that arrives right after the deadline; asyncio.wait
    just observes the still-running task and is re-awaited next iteration,
    identical to the polling shape the two call sites above already use.

    Added for docs/KNOWN-ISSUES.md's still-open, safety-relevant freeze
    (the app can go totally unresponsive, including both hard-stop paths).
    docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-
    and-a-12-minute-unrecoverable-one.md's leading, not-yet-confirmed
    hypothesis for the severe variant is a blocking read on backend-
    subprocess I/O with no timeout and no "process died" handling -- both
    adapters' readline() calls (codex.py's _read_loop, claude_code.py's
    _read_loop and _drain_stderr) matched that shape exactly and had no
    equivalent instrumentation, unlike capture.py/segmenter.py. This does
    not fix that freeze -- it gives the next recurrence real telemetry
    (queued-vs-running timing, proc.returncode at each check) instead of
    the silence every prior live repro produced.

    ``busy``, if given, is called fresh at each warning to report whether
    a turn is actually in flight right now. Added 2026-08-15 after a live
    capture (docs/field-notes/2026-08-15-*) showed this stall firing
    routinely during ordinary IDLE gaps between turns -- readline() has
    nothing to read whenever the backend has genuinely finished and is
    correctly waiting for the next command, which looks identical in this
    log line to a real stuck-mid-turn hang unless busy state is also
    shown. A long stall with busy=False is very likely harmless; one with
    busy=True is the shape actually worth treating as a freeze.
    """
    task = asyncio.ensure_future(stream.readline())
    start = time.monotonic()
    interval = _READLINE_STALL_FIRST_WARNING_S
    stalled = False
    while True:
        done, _pending = await asyncio.wait({task}, timeout=interval)
        if done:
            break
        stalled = True
        logger.warning(
            "%s: readline() still pending after %.1fs (proc.returncode=%s, "
            "busy=%s) -- not abandoning it, just reporting; see "
            "docs/KNOWN-ISSUES.md's VAD segmenter freeze entry",
            label, time.monotonic() - start, proc.returncode,
            busy() if busy is not None else "unknown",
        )
        interval = _READLINE_STALL_REPEAT_WARNING_S
    if stalled:
        logger.warning(
            "%s: readline() finally returned after %.1fs total "
            "(proc.returncode=%s, busy=%s)",
            label, time.monotonic() - start, proc.returncode,
            busy() if busy is not None else "unknown",
        )
    return task.result()


async def anext_with_stall_diagnostic[T](
    aiter: AsyncIterator[T],
    label: str,
) -> T:
    """await anext(aiter), logging the same stall warnings as
    readline_with_stall_diagnostic() -- for adapters whose long-lived read
    is an async-generator/SSE iterator (OpenCodeAdapter.events()) rather
    than a StreamReader with an owning subprocess. No proc.returncode
    equivalent exists here (there is no owned OS process to report on, by
    design -- see OpenCodeAdapter's own docstring), so these warnings carry
    elapsed time only.

    Added because PR #274's readline_with_stall_diagnostic() instrumented
    codex.py's and claude_code.py's own blocking reads (the backends with
    live freeze incidents) but never audited opencode.py's structurally
    similar unbounded wait (its SSE connection is opened with
    ``read=None`` -- no timeout at all, a deliberate choice for long
    multi-step tool calls, see events()' own comment) -- a real
    instrumentation gap found live, 2026-08-15, not yet known to have
    caused an actual incident. Raises StopAsyncIteration exactly like a
    bare ``await anext(aiter)`` would once the stream ends.
    """
    task = asyncio.ensure_future(anext(aiter))
    start = time.monotonic()
    interval = _READLINE_STALL_FIRST_WARNING_S
    stalled = False
    while True:
        done, _pending = await asyncio.wait({task}, timeout=interval)
        if done:
            break
        stalled = True
        logger.warning(
            "%s: anext() still pending after %.1fs -- not abandoning it, "
            "just reporting; see docs/KNOWN-ISSUES.md's VAD segmenter "
            "freeze entry",
            label, time.monotonic() - start,
        )
        interval = _READLINE_STALL_REPEAT_WARNING_S
    if stalled:
        logger.warning(
            "%s: anext() finally returned after %.1fs total",
            label, time.monotonic() - start,
        )
    return task.result()


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
    # A tool call produced a file worth looking at -- an image, a plot, a
    # rendered HTML page (docs/ARTIFACT-PANE-SCOPE.md). Deliberately NOT a
    # heuristic over TOOL_RESULT's tool_output text: no adapter emits this
    # yet (that's each adapter's own opt-in, one at a time, per the scope
    # doc's "First Implementation Slice") -- this is just the primitive
    # existing ahead of any adapter using it. `artifact_path` carries the
    # path an adapter identified; `tool`/`tool_input` reuse the same
    # fields TOOL_CALL/TOOL_RESULT use for which call produced it.
    ARTIFACT = "artifact"


# Shared between adapters that detect an artifact-shaped tool call
# (currently ClaudeCodeAdapter) and convobox.web.artifacts' serving route
# -- kept in this module specifically (core, no fastapi dependency) so
# adapters/*.py never has to import from web/*.py (which pulls in the
# optional "web" extra) just to share this list; web/artifacts.py imports
# it FROM here instead, the correct direction (web depends on adapters,
# adapters never depend on web). Deliberately narrow
# (docs/ARTIFACT-PANE-SCOPE.md's "Rendering" section) -- never treat an
# arbitrary file type as a servable/renderable artifact just because a
# tool happened to write one.
ARTIFACT_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    # Source code -- served as text/plain regardless of language (the
    # frontend fetches the raw text itself and syntax-highlights it
    # client-side; browsers have no native rendering for these anyway,
    # unlike images/HTML/PDF above, so there's no reason to invent
    # non-standard MIME types per language). See index.html's
    # _ARTIFACT_CODE_LANGUAGES for the extension -> highlight.js grammar
    # mapping this list must stay in sync with.
    ".js": "text/plain",
    ".mjs": "text/plain",
    ".cjs": "text/plain",
    ".jsx": "text/plain",
    ".ts": "text/plain",
    ".tsx": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
    ".java": "text/plain",
    ".c": "text/plain",
    ".h": "text/plain",
    ".cpp": "text/plain",
    ".cc": "text/plain",
    ".cxx": "text/plain",
    ".hpp": "text/plain",
    ".hh": "text/plain",
    ".cs": "text/plain",
    ".py": "text/plain",
    ".json": "text/plain",
    ".xml": "text/plain",
    # Added 2026-08-17 (live UAT gap-check, same class as the .py fix
    # above): common source/config/script extensions this allowlist had
    # simply never been extended to cover.
    ".css": "text/plain",
    ".sh": "text/plain",
    ".bash": "text/plain",
    ".ps1": "text/plain",
    ".toml": "text/plain",
    ".sql": "text/plain",
    ".go": "text/plain",
    ".rs": "text/plain",
    ".rb": "text/plain",
    ".php": "text/plain",
    ".kt": "text/plain",
    ".kts": "text/plain",
    ".swift": "text/plain",
    ".scala": "text/plain",
    ".lua": "text/plain",
    ".dart": "text/plain",
    ".vue": "text/plain",
    ".graphql": "text/plain",
    ".gql": "text/plain",
}


class JobState(str, Enum):
    """docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md's core pivot: this
    exists to let an adapter tell the truth about what it can and can't
    confirm, not to pretend confidence it doesn't have. UNKNOWN is a real,
    first-class state (matching Kubernetes' Unknown pod phase / Docker's
    dead) -- never silently upgraded to RUNNING or EXITED just because a
    caller wants a definite answer.
    """

    RUNNING = "running"
    EXITED = "exited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BackgroundJob:
    """One observed backend-spawned process/task, as far as an adapter can
    actually confirm -- see BackendAdapter.background_jobs().

    ``label`` is the ONLY field that may ever reach TTS or a spoken
    confirmation -- never ``command``. ``command`` is raw, potentially
    attacker-influenceable text (the backend may have read it from
    untrusted content) and may contain embedded secrets (see
    docs/SECURITY.md's own "credentials in CLI args" category); it exists
    for a verbose-tier visual display only, per the scope doc's Privacy
    section, and must never be persisted to the history store or spoken
    aloud regardless of config.
    """

    id: str
    state: JobState
    label: str
    command: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    started_at: float | None = None
    observed_at: float = 0.0
    source: str = "protocol"  # "protocol" | "os-scan" | "inferred"


class BackendEvent:
    def __init__(
        self,
        type: BackendEventType,
        content: str | None = None,
        tool: str | None = None,
        tool_input: str | None = None,
        tool_output: str | None = None,
        artifact_path: str | None = None,
    ) -> None:
        self.type = type
        self.content = content
        self.tool = tool
        self.tool_input = tool_input
        self.tool_output = tool_output
        self.artifact_path = artifact_path


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
        return

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
        return

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

    def background_jobs(self) -> Sequence[BackgroundJob]:
        """Return the currently observed snapshot of backend-spawned
        background jobs -- see docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md.

        Synchronous and does NO I/O: this returns whatever this adapter has
        already recorded from events it's already parsed (or a prior OS
        scan another component ran), never a fresh probe. A caller may
        invoke this from the quit path or the eject/kill_phrase path,
        where blocking here would reintroduce the exact "the control path
        rides the stuck channel" failure force_kill()'s own docstring
        already exists to avoid.

        Default: empty. Same "default no-op, override where real" shape as
        wait_listening/resolve_pending_approval -- an adapter with nothing
        to report (or not yet wired up) must not be forced to override
        this. Empty means "nothing observed," never "nothing is running";
        callers must not treat it as a guarantee.
        """
        return ()

    async def stop_background_job(self, job_id: str) -> bool:
        """Ask the backend to stop ONE named background job -- returns
        whether it was actually asked to stop (not whether it confirmed
        stopping).

        Deliberately separate from force_kill(): force_kill() ends the
        whole backend/session; this targets one job a user chose from a
        panel, and must never be reachable from the kill_phrase path (see
        the scope doc's "Eject must NOT be gated" section -- kill_phrase
        is the one lever that must keep working when everything else is
        wedged, and this method has no such guarantee).

        Default: no-op, returns False. Only an adapter with a real,
        live-verified per-job stop channel should override this --
        ClaudeCodeAdapter's ``stop_task`` control request is the only one
        confirmed to exist today.
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
        return

    async def force_kill(self) -> None:
        """Escalate beyond send_hard_stop(): terminate the underlying OS
        process outright (or sever the connection, for adapters with no
        owned process), regardless of whether the backend is responding to
        anything over its own channel.

        Exists for the case send_hard_stop() structurally cannot handle:
        the backend itself is wedged (e.g. a blocking readline() with no
        timeout — see docs/KNOWN-ISSUES.md's VAD/readline freeze entries,
        live-reproduced 2026-08-14), so no message sent over its normal
        channel will ever get a response, including the polite interrupt
        send_hard_stop() sends. This method must NOT go through that
        channel at all — an OS-level kill is the one lever that still
        works when the process itself isn't reading its own stdin.

        Default: delegates to aclose() — the correct behavior for adapters
        with no real subprocess to escalate against (e.g. OpenCodeAdapter's
        HTTP client, where severing the connection IS the strongest
        available action). Subprocess-owning adapters override this with a
        real terminate()/kill() sequence, structured so it does NOT discard
        any resumable session/thread identifier the way a full aclose()
        conceptually could — a future caller may want to reconnect to the
        same conversation on a freshly spawned process rather than
        starting over (not yet built; this method's shape is scaffolded
        for that, not implementing it).

        Must be idempotent and must not raise, same contract as aclose().
        """
        await self.aclose()
