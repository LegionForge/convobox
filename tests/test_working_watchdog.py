"""Direct tests for _working_watchdog (GitHub issue #235, finding C3: "no
direct test... it owns approval-timeout resolution, queued-interjection
delivery, and the whole status-computation ladder"). Its constituent gates
(WorkingIndicator, QueuedInterjection, ApprovalPromptGate, ContinuePromptGate)
already have direct tests elsewhere -- these exercise the watchdog's own
orchestration of them, which none of those cover.

asyncio.sleep is monkeypatched and counts its own calls: the Nth call
(1-indexed) is where tick N's body starts (a real event-loop yield happens
first so other ready callbacks -- e.g. a test-driven state mutation between
ticks -- get a chance to run), and the (n+1)th call raises CancelledError,
ending the watchdog's `while True` deterministically after exactly n body
iterations rather than relying on a fixed number of bare `asyncio.sleep(0)`
scheduler round-trips in the test (which undercounts, since the loop's own
top-of-body `await asyncio.sleep(interval)` already consumes one). The
function's own `interval = 1.0` literal (used for WorkingIndicator.observe's
dt_s and ContinuePromptGate/ApprovalPromptGate's timeout math) is untouched
by any of this, so timing-sensitive assertions stay meaningful.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine

import pytest

from convobox.tui.state import ConversationTuiState
from scripts.run_convobox import (
    ApprovalPromptGate,
    ContinuePromptGate,
    QueuedInterjection,
    WorkingIndicator,
    _working_watchdog,
)

_REAL_SLEEP = asyncio.sleep


class FakeAdapter:
    def __init__(self, busy: bool = False) -> None:
        self.busy = busy

    def is_busy(self) -> bool:
        return self.busy


class FakePlayer:
    def __init__(self, playing: bool = False) -> None:
        self.playing = playing

    def is_playing(self) -> bool:
        return self.playing


class FakeOrchestrator:
    def __init__(self, has_more: bool = False, resolve_result: bool = True) -> None:
        self.has_more = has_more
        self.resolve_result = resolve_result
        self.resolve_calls: list[bool] = []
        self.handled: list[str] = []

    def has_more_to_reveal(self) -> bool:
        return self.has_more

    async def resolve_pending_approval(self, decision: bool) -> bool:
        self.resolve_calls.append(decision)
        return self.resolve_result

    async def handle_transcript(self, text: str) -> None:
        self.handled.append(text)


class FakeWebForwarder:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str | None]] = []
        self.transcripts: list[str] = []

    def forward_status(self, status: str, detail: str | None) -> None:
        self.statuses.append((status, detail))

    def forward_transcript(self, text: str) -> None:
        self.transcripts.append(text)


class FakeListeningGate:
    def __init__(self, is_paused: bool) -> None:
        self.is_paused = is_paused


class FakeSegmenter:
    def __init__(self, in_speech: bool = False, discarded_forced_runs: int = 0) -> None:
        self.in_speech = in_speech
        self.discarded_forced_runs = discarded_forced_runs


async def _run_ticks(
    monkeypatch: pytest.MonkeyPatch,
    coro_factory: Callable[[], Coroutine[object, object, object]],
    n: int,
    pre_tick: dict[int, Callable[[], None]] | None = None,
) -> None:
    """Run the watchdog for exactly n body iterations, then let it end via
    a CancelledError raised from its own (n+1)th `await asyncio.sleep(...)`
    call -- deterministic regardless of however many raw event-loop turns
    each iteration's own internal awaits (on fake, non-suspending
    coroutines) actually take.

    pre_tick maps a 1-indexed tick number to a callback run right as that
    tick's sleep call returns, i.e. immediately before that tick's body
    executes -- for tests that need to mutate fake state BETWEEN ticks
    (e.g. "playback just finished" only becomes true partway through).
    """
    calls = 0

    async def counted_sleep(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > n:
            raise asyncio.CancelledError()
        await _REAL_SLEEP(0)
        if pre_tick and calls in pre_tick:
            pre_tick[calls]()

    # scripts.run_convobox's own `import asyncio` is the same module object
    # as this file's -- no need for a second import of that module just to
    # reach it through an attribute.
    monkeypatch.setattr(asyncio, "sleep", counted_sleep)
    task = asyncio.ensure_future(coro_factory())
    with pytest.raises(asyncio.CancelledError):
        # The awaited result is intentionally discarded -- this line's
        # purpose IS the CancelledError it raises (caught by the context
        # manager above), not a return value.
        await task


# --- approval-timeout resolution: silence on a pending approval must
# auto-decline, never be treated as consent (ApprovalPromptGate's own
# central invariant -- the watchdog is what actually calls it) ---


@pytest.mark.asyncio
async def test_expired_approval_wait_auto_declines_through_the_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator(resolve_result=True)
    approval_gate = ApprovalPromptGate(detector=None, timeout_s=5.0)  # type: ignore[arg-type]
    approval_gate.start_waiting(time.monotonic() - 10.0)  # already expired

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            approval_gate=approval_gate,
        ),
        n=1,
    )

    assert orchestrator.resolve_calls == [False]
    assert not approval_gate.is_waiting


@pytest.mark.asyncio
async def test_a_still_open_approval_wait_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator()
    approval_gate = ApprovalPromptGate(detector=None, timeout_s=60.0)  # type: ignore[arg-type]
    approval_gate.start_waiting(time.monotonic())  # fresh, nowhere near timeout

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            approval_gate=approval_gate,
        ),
        n=1,
    )

    assert orchestrator.resolve_calls == []
    assert approval_gate.is_waiting


@pytest.mark.asyncio
async def test_orchestrator_declining_nothing_pending_logs_but_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # resolve_pending_approval's own fail-closed contract: False means
    # "nothing was actually pending to decline" -- the watchdog must not
    # treat that as an exception-worthy condition, just log it.
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator(resolve_result=False)
    approval_gate = ApprovalPromptGate(detector=None, timeout_s=5.0)  # type: ignore[arg-type]
    approval_gate.start_waiting(time.monotonic() - 10.0)

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            approval_gate=approval_gate,
        ),
        n=1,
    )

    assert orchestrator.resolve_calls == [False]


# --- queued-interjection delivery: the "patient" preset's held-back
# utterance is flushed once the backend goes fully idle, forwarded to both
# the web UI and the orchestrator ---


@pytest.mark.asyncio
async def test_queued_interjection_delivered_once_backend_goes_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=False)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator()
    queue = QueuedInterjection()
    queue.offer("what about the second file")
    forwarder = FakeWebForwarder()

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, queue,
            web_forwarder=forwarder,
        ),
        n=1,
    )

    assert orchestrator.handled == ["what about the second file"]
    assert forwarder.transcripts == ["what about the second file"]


@pytest.mark.asyncio
async def test_queued_interjection_not_delivered_while_still_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator()
    queue = QueuedInterjection()
    queue.offer("still queued")

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, queue,
        ),
        n=1,
    )

    assert orchestrator.handled == []


@pytest.mark.asyncio
async def test_a_failed_delivery_does_not_kill_the_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same defensive policy the function's own comment describes: one
    # delivery failure must not take down the heartbeat + all future
    # queue flushes with it. If it did, the exception would escape the
    # (n+1)th sleep boundary as something other than CancelledError, and
    # _run_ticks's own pytest.raises(CancelledError) would fail.
    adapter = FakeAdapter(busy=False)
    player = FakePlayer(playing=False)

    class FailingOrchestrator(FakeOrchestrator):
        async def handle_transcript(self, text: str) -> None:
            raise RuntimeError("backend unreachable")

    orchestrator = FailingOrchestrator()
    queue = QueuedInterjection()
    queue.offer("this delivery will raise")

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, queue,
        ),
        n=2,  # survives tick 1's failure and reaches tick 2 cleanly
    )


# --- status-computation ladder: priority order across the TUI/web status
# label (paused > approval-waiting > continue-waiting > speaking > working
# > capturing > listening) ---


@pytest.mark.asyncio
async def test_paused_status_wins_over_a_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator()
    approval_gate = ApprovalPromptGate(detector=None, timeout_s=60.0)  # type: ignore[arg-type]
    approval_gate.start_waiting(time.monotonic())
    listening_gate = FakeListeningGate(is_paused=True)
    tui_state = ConversationTuiState()

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            listening_gate=listening_gate, approval_gate=approval_gate, tui_state=tui_state,
        ),
        n=1,
    )

    assert tui_state.status == "paused"


@pytest.mark.asyncio
async def test_approval_waiting_carries_its_own_hint_and_beats_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)
    orchestrator = FakeOrchestrator()
    approval_gate = ApprovalPromptGate(detector=None, timeout_s=60.0)  # type: ignore[arg-type]
    approval_gate.start_waiting(time.monotonic())
    tui_state = ConversationTuiState()

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            approval_gate=approval_gate, tui_state=tui_state,
        ),
        n=1,
    )

    assert tui_state.status == "waiting"
    assert tui_state.waiting_hint is not None and "approval" in tui_state.waiting_hint


@pytest.mark.asyncio
async def test_status_speaking_when_audio_is_playing(monkeypatch: pytest.MonkeyPatch) -> None:
    tui_state = ConversationTuiState()
    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            FakeAdapter(busy=False), FakePlayer(playing=True), WorkingIndicator(),
            FakeOrchestrator(), QueuedInterjection(), tui_state=tui_state,
        ),
        n=1,
    )
    assert tui_state.status == "speaking"


@pytest.mark.asyncio
async def test_status_working_when_busy_and_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    tui_state = ConversationTuiState()
    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            FakeAdapter(busy=True), FakePlayer(playing=False), WorkingIndicator(),
            FakeOrchestrator(), QueuedInterjection(), tui_state=tui_state,
        ),
        n=1,
    )
    assert tui_state.status == "working"


@pytest.mark.asyncio
async def test_status_capturing_when_idle_but_segmenter_in_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tui_state = ConversationTuiState()
    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            FakeAdapter(busy=False), FakePlayer(playing=False), WorkingIndicator(),
            FakeOrchestrator(), QueuedInterjection(),
            segmenter=FakeSegmenter(in_speech=True), tui_state=tui_state,
        ),
        n=1,
    )
    assert tui_state.status == "capturing"


@pytest.mark.asyncio
async def test_status_listening_when_fully_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    tui_state = ConversationTuiState()
    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            FakeAdapter(busy=False), FakePlayer(playing=False), WorkingIndicator(),
            FakeOrchestrator(), QueuedInterjection(), tui_state=tui_state,
        ),
        n=1,
    )
    assert tui_state.status == "listening"


@pytest.mark.asyncio
async def test_status_broadcast_to_web_only_on_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarder = FakeWebForwarder()
    adapter = FakeAdapter(busy=True)
    player = FakePlayer(playing=False)

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), FakeOrchestrator(), QueuedInterjection(),
            web_forwarder=forwarder,
        ),
        n=4,
    )

    # Status never changed ("working" throughout) -- exactly one broadcast,
    # not one per tick.
    assert forwarder.statuses == [("working", None)]


# --- continue-prompt wait: starts only when playback just finished (not
# on every idle tick) and only if there's actually more to reveal ---


@pytest.mark.asyncio
async def test_continue_wait_starts_only_when_playback_just_finished_with_more_to_reveal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=False)
    player = FakePlayer(playing=True)  # starts playing
    orchestrator = FakeOrchestrator(has_more=True)
    continue_gate = ContinuePromptGate(detector=None, timeout_s=5.0)  # type: ignore[arg-type]

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            continue_gate=continue_gate,
        ),
        n=2,
        # Tick 1 sees playing=True (was_playing becomes True). Right before
        # tick 2's body runs, flip to "just finished" -- that's the only
        # transition that should arm the wait.
        pre_tick={2: lambda: setattr(player, "playing", False)},
    )

    assert continue_gate.is_waiting


@pytest.mark.asyncio
async def test_continue_wait_does_not_start_with_nothing_more_to_reveal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(busy=False)
    player = FakePlayer(playing=True)
    orchestrator = FakeOrchestrator(has_more=False)
    continue_gate = ContinuePromptGate(detector=None, timeout_s=5.0)  # type: ignore[arg-type]

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            continue_gate=continue_gate,
        ),
        n=2,
        pre_tick={2: lambda: setattr(player, "playing", False)},
    )

    assert not continue_gate.is_waiting


@pytest.mark.asyncio
async def test_continue_wait_does_not_restart_on_every_idle_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Guards the function's own stated reasoning: restarting the wait on
    # every "not playing" tick (rather than only the falling edge) would
    # never let it expire, since start_waiting() keeps resetting the clock.
    adapter = FakeAdapter(busy=False)
    player = FakePlayer(playing=False)  # never plays at all this session
    orchestrator = FakeOrchestrator(has_more=True)
    continue_gate = ContinuePromptGate(detector=None, timeout_s=5.0)  # type: ignore[arg-type]

    await _run_ticks(
        monkeypatch,
        lambda: _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, QueuedInterjection(),
            continue_gate=continue_gate,
        ),
        n=3,
    )

    assert not continue_gate.is_waiting
