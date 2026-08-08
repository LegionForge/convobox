from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.safeword.detector import SafewordDetector
from convobox.stt.corrections import TranscriptCorrector
from convobox.tui.state import ConversationTuiState
from convobox.web.bridge import (
    WebApprovalBridge,
    WebEventForwarder,
    WebListeningBridge,
    WebSafewordBridge,
    WebTextInputBridge,
)
from convobox.web.history import HistoryDB, new_session_id
from convobox.web.stream import EventBroadcaster


@pytest.fixture
def db(tmp_path: Path) -> HistoryDB:
    history = HistoryDB(tmp_path / "events.db")
    yield history
    history.close()


def test_both_none_is_a_harmless_noop(db: HistoryDB) -> None:
    # Matches web.enabled=False (the default) making zero difference.
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder(BackendEvent(type=BackendEventType.TEXT, content="hi"))  # must not raise


@pytest.mark.asyncio
async def test_forwards_to_history_when_given(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="it works"))
    # B2: history writes are queued and drained by a background task, not
    # written synchronously in-line -- await the forwarder's own tracked
    # writer task so the write is actually durable before asserting on it,
    # rather than guessing at a timing.
    assert forwarder._writer_task is not None
    await forwarder._writer_task

    events = db.get_session_events(session_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "response"
    assert events[0]["backend_response"] == "it works"


@pytest.mark.asyncio
async def test_tool_call_event_type_is_not_folded_into_response(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TOOL_CALL, tool="Bash", tool_input="ls"))
    assert forwarder._writer_task is not None
    await forwarder._writer_task

    stored = db.get_session_events(session_id)[0]
    assert stored["event_type"] == "tool_call"
    assert stored["tool_name"] == "Bash"


@pytest.mark.asyncio
async def test_approval_request_gets_its_own_event_type(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.APPROVAL_REQUEST, tool="Bash"))
    assert forwarder._writer_task is not None
    await forwarder._writer_task

    stored = db.get_session_events(session_id)[0]
    assert stored["event_type"] == "approval_request"


# --- B2 (2026-08-08 review): history writes are queued and drained by a
# background task rather than written synchronously in-line on the event
# loop (see WebEventForwarder.__init__'s own comment for why a per-call
# asyncio.to_thread() wasn't used instead). ---


@pytest.mark.asyncio
async def test_write_is_not_synchronous_the_row_is_not_there_until_awaited(
    db: HistoryDB,
) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="deferred"))
    # No await at all yet -- the write is queued, not yet executed. This is
    # the behavior change B2 exists for: append_event() used to run
    # in-line, so this assertion would have failed on the OLD code (the
    # row would already be there).
    assert db.get_session_events(session_id) == []

    assert forwarder._writer_task is not None
    await forwarder._writer_task
    assert len(db.get_session_events(session_id)) == 1


@pytest.mark.asyncio
async def test_writes_land_in_the_order_they_were_queued(db: HistoryDB) -> None:
    # Real risk B2's design guards against: append_event() stamps its own
    # `timestamp` from time.time() at EXECUTION time, not queue time -- if
    # writes ran on separate, independently-scheduled threads (a naive
    # per-call asyncio.to_thread()), a later-queued write could execute
    # and land BEFORE an earlier one, corrupting get_session_events()'s own
    # `ORDER BY timestamp ASC` reading order. A single queue + one worker
    # (this test fires several events back to back, synchronously, with no
    # await between them -- the same shape a fast tool_call/tool_result
    # pair from a real backend arrives in) must still preserve order.
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    for i in range(10):
        forwarder(BackendEvent(type=BackendEventType.TEXT, content=f"event {i}"))
    assert forwarder._writer_task is not None
    await forwarder._writer_task

    stored = db.get_session_events(session_id)
    assert [row["backend_response"] for row in stored] == [f"event {i}" for i in range(10)]


@pytest.mark.asyncio
async def test_a_failed_write_is_logged_and_does_not_block_the_next_one(
    db: HistoryDB, caplog: pytest.LogCaptureFixture
) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)
    real_append_event = db.append_event
    calls = {"n": 0}

    def flaky_append_event(*args: object, **kwargs: object) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("simulated write failure")
        return real_append_event(*args, **kwargs)  # type: ignore[arg-type]

    db.append_event = flaky_append_event  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        forwarder(BackendEvent(type=BackendEventType.TEXT, content="dropped"))
        forwarder(BackendEvent(type=BackendEventType.TEXT, content="survives"))
        assert forwarder._writer_task is not None
        await forwarder._writer_task

    stored = db.get_session_events(session_id)
    assert len(stored) == 1
    assert stored[0]["backend_response"] == "survives"
    assert "history write failed" in caplog.text


def test_history_none_skips_persistence_but_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder(BackendEvent(type=BackendEventType.DONE))  # must not raise


@pytest.mark.asyncio
async def test_broadcasts_to_a_subscriber_when_given() -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="live"))
    # The forwarder schedules the broadcast (asyncio.ensure_future) rather
    # than awaiting it, matching its sync on_event-hook contract -- give
    # the scheduled task a turn to actually run before checking the queue.
    await asyncio.sleep(0)

    payload = queue.get_nowait()
    assert payload["content"] == "live"


def test_broadcaster_none_skips_broadcast_but_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder(BackendEvent(type=BackendEventType.TEXT, content="hi"))  # must not raise


@pytest.mark.asyncio
async def test_forwards_to_both_history_and_broadcaster_together(db: HistoryDB) -> None:
    session_id = new_session_id()
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=broadcaster)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="both"))
    await asyncio.sleep(0)
    assert forwarder._writer_task is not None
    await forwarder._writer_task

    assert db.get_session_events(session_id)[0]["backend_response"] == "both"
    assert queue.get_nowait()["content"] == "both"


# --- Integration: a real Orchestrator wired with WebEventForwarder as its
# on_event hook (the actual run_convobox.py wiring shape), not just the
# forwarder in isolation -- proves the two really compose, since
# Orchestrator's on_event contract is what WebEventForwarder.__call__ has
# to match. ---


class _MinimalBackendAdapter(BackendAdapter):
    """Just enough of BackendAdapter to drive Orchestrator._consume_events()
    with a scripted event list -- see FakeBackendAdapter in
    test_orchestrator.py for the fuller version used elsewhere."""

    def __init__(self, events_to_yield: list[BackendEvent]) -> None:
        self._events_to_yield = events_to_yield

    async def send_text(self, text: str) -> None: ...
    async def send_interject(self, text: str) -> None: ...
    async def send_hard_stop(self) -> None: ...
    def is_busy(self) -> bool:
        return False

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        for event in self._events_to_yield:
            await asyncio.sleep(0)
            yield event


@pytest.mark.asyncio
async def test_orchestrator_wired_with_web_forwarder_persists_and_broadcasts(
    db: HistoryDB,
) -> None:
    session_id = new_session_id()
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=broadcaster)
    adapter = _MinimalBackendAdapter(
        [BackendEvent(type=BackendEventType.TEXT, content="wired end to end")]
    )
    orch = Orchestrator(
        adapter=adapter, safeword=SafewordDetector(["stop stop stop"]), on_event=forwarder
    )

    await orch._consume_events()
    await asyncio.sleep(0)  # let the forwarder's scheduled broadcast task run
    assert forwarder._writer_task is not None
    await forwarder._writer_task  # let the queued history write actually land

    stored = db.get_session_events(session_id)
    assert len(stored) == 1
    assert stored[0]["backend_response"] == "wired end to end"
    assert queue.get_nowait()["content"] == "wired end to end"


# --- forward_transcript: the OTHER half of the web wiring gap this session
# found while building a demo -- Orchestrator.on_event only ever sees
# BackendEvents (backend responses/tool calls), never the user's own
# recognized speech that prompted one, so run_convobox.py's call sites for
# Orchestrator.handle_transcript() also call this directly. ---


@pytest.mark.asyncio
async def test_forward_transcript_persists_to_history_when_given(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder.forward_transcript("what should I work on next")
    assert forwarder._writer_task is not None
    await forwarder._writer_task

    stored = db.get_session_events(session_id)
    assert len(stored) == 1
    assert stored[0]["event_type"] == "transcript"
    assert stored[0]["user_transcript"] == "what should I work on next"


def test_forward_transcript_with_no_history_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder.forward_transcript("hello")  # must not raise


# --- forward_status: the mic loop's own listening/capturing/speaking/
# working/waiting/paused activity state, broadcast live but never
# persisted (it's ephemeral, not a conversation event). ---


@pytest.mark.asyncio
async def test_forward_status_broadcasts_to_a_subscriber() -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)

    forwarder.forward_status("paused")
    await asyncio.sleep(0)

    assert queue.get_nowait() == {"type": "status", "status": "paused", "detail": None}


@pytest.mark.asyncio
async def test_forward_status_includes_the_current_activity_detail() -> None:
    # WorkingIndicator.current_activity (a tool name, or None for
    # "thinking") -- the TUI's own heartbeat tag has shown this since
    # PR #190; this is that same detail reaching the web UI's status line.
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)

    forwarder.forward_status("working", "Bash")
    await asyncio.sleep(0)

    assert queue.get_nowait() == {"type": "status", "status": "working", "detail": "Bash"}


def test_forward_status_never_touches_history(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder.forward_status("listening")  # must not raise or persist

    assert db.get_session_events(session_id) == []


def test_forward_status_with_no_broadcaster_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder.forward_status("working")  # must not raise


@pytest.mark.asyncio
async def test_forward_transcript_broadcasts_to_a_subscriber() -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)

    forwarder.forward_transcript("hello")
    await asyncio.sleep(0)

    payload = queue.get_nowait()
    assert payload == {"type": "transcript", "content": "hello"}


# --- WebApprovalBridge: lets the web UI's approve/deny/explain buttons
# answer the same pending approval a spoken phrase would
# (ApprovalPromptGate/Orchestrator.resolve_pending_approval). A fake gate
# (matching ApprovalGateLike's shape) and a fake orchestrator stand in for
# the real run_convobox.py objects -- this tests the bridge's own
# decision-forwarding/state-sync logic, not the real approval machinery
# (covered by tests/test_approval_prompt_gate.py and the orchestrator's
# own tests). ---


class _FakeGate:
    def __init__(self, waiting: bool = True, explanation: str | None = None) -> None:
        self._waiting = waiting
        self._explanation = explanation
        self.start_waiting_calls: list[tuple[float, str | None]] = []
        self.cancelled = False

    @property
    def is_waiting(self) -> bool:
        return self._waiting

    @property
    def pending_explanation(self) -> str | None:
        return self._explanation

    def start_waiting(self, now: float, explanation: str | None = None) -> None:
        self._waiting = True
        self._explanation = explanation
        self.start_waiting_calls.append((now, explanation))

    def cancel_wait(self) -> None:
        self.cancelled = True
        self._waiting = False


class _FakeOrchestrator:
    def __init__(self, resolves: bool = True) -> None:
        self.resolves = resolves
        self.calls: list[bool] = []

    async def resolve_pending_approval(self, approved: bool) -> bool:
        self.calls.append(approved)
        return self.resolves


def test_bridge_with_no_targets_reports_nothing_pending() -> None:
    bridge = WebApprovalBridge()
    assert bridge.is_pending is False
    assert bridge.pending_explanation is None
    assert bridge.extend() is None


@pytest.mark.asyncio
async def test_bridge_with_no_targets_decide_is_a_harmless_false() -> None:
    bridge = WebApprovalBridge()
    assert await bridge.decide(True) is False


@pytest.mark.asyncio
async def test_bridge_decide_approve_calls_orchestrator_and_cancels_gate() -> None:
    gate = _FakeGate(waiting=True)
    orch = _FakeOrchestrator(resolves=True)
    bridge = WebApprovalBridge()
    bridge.set_targets(orch, gate)  # type: ignore[arg-type]

    assert bridge.is_pending is True
    resolved = await bridge.decide(True)

    assert resolved is True
    assert orch.calls == [True]
    assert gate.cancelled is True
    assert bridge.is_pending is False


@pytest.mark.asyncio
async def test_bridge_decide_deny_passes_false_through() -> None:
    gate = _FakeGate(waiting=True)
    orch = _FakeOrchestrator(resolves=True)
    bridge = WebApprovalBridge()
    bridge.set_targets(orch, gate)  # type: ignore[arg-type]

    resolved = await bridge.decide(False)

    assert resolved is True
    assert orch.calls == [False]
    assert gate.cancelled is True


@pytest.mark.asyncio
async def test_bridge_decide_when_gate_not_waiting_skips_the_orchestrator() -> None:
    gate = _FakeGate(waiting=False)
    orch = _FakeOrchestrator(resolves=True)
    bridge = WebApprovalBridge()
    bridge.set_targets(orch, gate)  # type: ignore[arg-type]

    resolved = await bridge.decide(True)

    assert resolved is False
    assert orch.calls == []  # never asked -- nothing was pending


@pytest.mark.asyncio
async def test_bridge_decide_leaves_gate_waiting_when_orchestrator_had_nothing_pending() -> None:
    # Fail-closed race case: the gate thinks a request is pending but the
    # backend disagrees (already resolved another way). The gate must NOT
    # be cancelled here -- same "re-open, don't silently treat as decided"
    # rule run_convobox.py's own voice path follows.
    gate = _FakeGate(waiting=True)
    orch = _FakeOrchestrator(resolves=False)
    bridge = WebApprovalBridge()
    bridge.set_targets(orch, gate)  # type: ignore[arg-type]

    resolved = await bridge.decide(True)

    assert resolved is False
    assert gate.cancelled is False
    assert gate.is_waiting is True


def test_bridge_extend_resets_the_wait_and_returns_the_explanation() -> None:
    gate = _FakeGate(waiting=True, explanation="rm -rf .incident-captures/*.wav")
    bridge = WebApprovalBridge()
    bridge.set_targets(_FakeOrchestrator(), gate)  # type: ignore[arg-type]

    explanation = bridge.extend()

    assert explanation == "rm -rf .incident-captures/*.wav"
    assert gate.is_waiting is True
    assert len(gate.start_waiting_calls) == 1


def test_bridge_extend_returns_none_when_nothing_pending() -> None:
    gate = _FakeGate(waiting=False)
    bridge = WebApprovalBridge()
    bridge.set_targets(_FakeOrchestrator(), gate)  # type: ignore[arg-type]

    assert bridge.extend() is None


# --- WebListeningBridge: lets the web UI's Stop/Resume listening button do
# exactly what a spoken pause/resume phrase does (ListeningGate.observe()'s
# "pause"/"resume" branches) -- fakes stand in for the real ListeningGate/
# player/tts/adapter, same reasoning as the approval-bridge fakes above:
# this tests the bridge's own side-effect-triggering logic, not the real
# voice-path machinery (covered elsewhere). ---


class _FakeListeningGate:
    def __init__(self, is_paused: bool = False) -> None:
        self.is_paused = is_paused


class _FakePlayer:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.play_calls: list[tuple[object, int]] = []

    def stop(self) -> None:
        self.stop_calls += 1

    def play(self, samples: object, sample_rate: int) -> None:
        self.play_calls.append((samples, sample_rate))


class _FakeTTS:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class _FakeAdapterForListening:
    def __init__(self) -> None:
        self.hard_stop_calls = 0

    async def send_hard_stop(self) -> None:
        self.hard_stop_calls += 1


class _FakeOrchestratorForListening:
    def __init__(self) -> None:
        self.stop_event_loop_calls = 0
        self.hard_stop_calls = 0

    async def stop_event_loop(self) -> None:
        self.stop_event_loop_calls += 1

    async def hard_stop(self) -> None:
        # WebListeningBridge.pause() delegates to this rather than calling
        # player.stop()/tts.stop()/adapter.send_hard_stop() itself -- the
        # real Orchestrator.hard_stop() does those on ITS OWN stored
        # player/tts/adapter (the same instances, in production; separate
        # fakes here, by design -- see the tests that assert on this
        # counter instead of the player/tts/adapter fakes directly).
        self.hard_stop_calls += 1


def test_listening_bridge_with_no_targets_reports_not_ready_and_not_paused() -> None:
    bridge = WebListeningBridge()
    assert bridge.is_ready is False
    assert bridge.is_paused is False


@pytest.mark.asyncio
async def test_listening_bridge_with_no_targets_pause_is_a_harmless_false() -> None:
    bridge = WebListeningBridge()
    assert await bridge.pause() is False


def test_listening_bridge_with_no_targets_resume_is_a_harmless_false() -> None:
    bridge = WebListeningBridge()
    assert bridge.resume() is False


@pytest.mark.asyncio
async def test_listening_bridge_pause_hard_stops_playback_and_backend() -> None:
    # PR #191 (2026-07-31, safety-critical): hard-stopping the adapter
    # alone still lets an already-in-flight turn's trailing TEXT event
    # reach _on_event() and get spoken after the pause -- stop_event_loop()
    # is what actually prevents that, and this button was a third,
    # previously-unfixed call site missing it (live UAT, 2026-08-02).
    # The actual player.stop()/tts.stop()/adapter.send_hard_stop()/
    # stop_event_loop() sequence is Orchestrator.hard_stop()'s own
    # responsibility now (see test_orchestrator.py's
    # test_hard_stop_method_runs_the_same_sequence_directly and
    # test_hard_stop_method_cancels_the_event_loop) -- this test only
    # needs to confirm the bridge delegates to it.
    gate = _FakeListeningGate(is_paused=False)
    orchestrator = _FakeOrchestratorForListening()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening(), orchestrator,
    )

    changed = await bridge.pause()

    assert changed is True
    assert gate.is_paused is True
    assert bridge.is_paused is True
    assert orchestrator.hard_stop_calls == 1


@pytest.mark.asyncio
async def test_listening_bridge_pause_while_already_paused_is_a_noop() -> None:
    # Matches ListeningGate.observe()'s own behavior: once is_paused is
    # true, it never re-enters the "pause" branch, so a second pause
    # phrase (or a second button click) must not re-hard-stop anything.
    gate = _FakeListeningGate(is_paused=True)
    player = _FakePlayer()
    tts = _FakeTTS()
    adapter = _FakeAdapterForListening()
    orchestrator = _FakeOrchestratorForListening()
    bridge = WebListeningBridge()
    bridge.set_targets(gate, player, tts, adapter, orchestrator)  # type: ignore[arg-type]

    changed = await bridge.pause()

    assert changed is False
    assert player.stop_calls == 0
    assert tts.stop_calls == 0
    assert adapter.hard_stop_calls == 0
    assert orchestrator.stop_event_loop_calls == 0


@pytest.mark.asyncio
async def test_listening_bridge_pause_plays_the_ack_tone_when_configured() -> None:
    gate = _FakeListeningGate(is_paused=False)
    player = _FakePlayer()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, player, _FakeTTS(), _FakeAdapterForListening(),
        _FakeOrchestratorForListening(), "tone",
    )

    await bridge.pause()

    assert len(player.play_calls) == 1


@pytest.mark.asyncio
async def test_listening_bridge_pause_plays_no_tone_by_default() -> None:
    gate = _FakeListeningGate(is_paused=False)
    player = _FakePlayer()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, player, _FakeTTS(), _FakeAdapterForListening(), _FakeOrchestratorForListening(),
    )

    await bridge.pause()

    assert player.play_calls == []


def test_listening_bridge_resume_clears_the_flag_with_no_side_effects() -> None:
    gate = _FakeListeningGate(is_paused=True)
    player = _FakePlayer()
    tts = _FakeTTS()
    adapter = _FakeAdapterForListening()
    orchestrator = _FakeOrchestratorForListening()
    bridge = WebListeningBridge()
    bridge.set_targets(gate, player, tts, adapter, orchestrator)  # type: ignore[arg-type]

    changed = bridge.resume()

    assert changed is True
    assert gate.is_paused is False
    assert bridge.is_paused is False
    assert player.stop_calls == 0
    assert tts.stop_calls == 0
    assert adapter.hard_stop_calls == 0


def test_listening_bridge_resume_plays_the_ack_tone_when_configured() -> None:
    gate = _FakeListeningGate(is_paused=True)
    player = _FakePlayer()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, player, _FakeTTS(), _FakeAdapterForListening(),
        _FakeOrchestratorForListening(), "tone",
    )

    bridge.resume()

    assert len(player.play_calls) == 1


def test_listening_bridge_resume_plays_no_tone_by_default() -> None:
    gate = _FakeListeningGate(is_paused=True)
    player = _FakePlayer()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, player, _FakeTTS(), _FakeAdapterForListening(), _FakeOrchestratorForListening(),
    )

    bridge.resume()

    assert player.play_calls == []


def test_listening_bridge_resume_while_not_paused_is_a_noop() -> None:
    gate = _FakeListeningGate(is_paused=False)
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening(), _FakeOrchestratorForListening(),
    )

    assert bridge.resume() is False


# --- Live incident, 2026-08-05 (docs/field-notes/2026-08-05-web-resume-
# desyncs-tui-display.md): a web-triggered pause/resume flipped the real
# ListeningGate but left an attached TUI's transcript pane showing the
# stale "paused" system turn forever, reading as a hung session even
# though the mic loop had genuinely resumed. These tests cover the fix --
# pause()/resume() now append a system turn when a real ConversationTuiState
# is wired in via set_targets(), same as the voice path already did. ---


@pytest.mark.asyncio
async def test_listening_bridge_pause_appends_a_tui_system_turn_when_wired() -> None:
    gate = _FakeListeningGate(is_paused=False)
    tui_state = ConversationTuiState()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening(),
        _FakeOrchestratorForListening(), "none", tui_state, "Athena",
    )

    await bridge.pause()

    assert len(tui_state.turns) == 1
    assert tui_state.turns[0].speaker == "system"
    assert "Athena" in tui_state.turns[0].text


@pytest.mark.asyncio
async def test_listening_bridge_pause_is_silent_without_a_tui_state() -> None:
    # No tui_state passed to set_targets() (the default) -- must not raise,
    # matching every other optional target on this bridge.
    gate = _FakeListeningGate(is_paused=False)
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening(),
        _FakeOrchestratorForListening(),
    )

    assert await bridge.pause() is True


def test_listening_bridge_resume_appends_a_tui_system_turn_when_wired() -> None:
    gate = _FakeListeningGate(is_paused=True)
    tui_state = ConversationTuiState()
    bridge = WebListeningBridge()
    bridge.set_targets(  # type: ignore[arg-type]
        gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening(),
        _FakeOrchestratorForListening(), "none", tui_state,
    )

    bridge.resume()

    assert len(tui_state.turns) == 1
    assert tui_state.turns[0].speaker == "system"
    assert "resumed" in tui_state.turns[0].text.lower()


def test_listening_bridge_resume_is_silent_without_a_tui_state() -> None:
    gate = _FakeListeningGate(is_paused=True)
    bridge = WebListeningBridge()
    bridge.set_targets(gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening(), _FakeOrchestratorForListening())  # type: ignore[arg-type]

    assert bridge.resume() is True


# --- WebSafewordBridge: lets the web UI's Stop button do exactly what
# saying a safeword phrase does (Orchestrator.hard_stop()) -- distinct from
# WebListeningBridge.pause(), which additionally enters ListeningGate's
# paused-until-resume-word state. Reuses _FakeOrchestratorForListening
# above since both bridges delegate to the same Orchestrator.hard_stop(). ---


def test_safeword_bridge_with_no_target_reports_not_ready() -> None:
    bridge = WebSafewordBridge()
    assert bridge.is_ready is False


@pytest.mark.asyncio
async def test_safeword_bridge_with_no_target_trigger_is_a_harmless_false() -> None:
    bridge = WebSafewordBridge()
    assert await bridge.trigger() is False


@pytest.mark.asyncio
async def test_safeword_bridge_trigger_delegates_to_orchestrator_hard_stop() -> None:
    orchestrator = _FakeOrchestratorForListening()
    bridge = WebSafewordBridge()
    bridge.set_targets(orchestrator)  # type: ignore[arg-type]

    assert bridge.is_ready is True
    triggered = await bridge.trigger()

    assert triggered is True
    assert orchestrator.hard_stop_calls == 1


# --- WebTextInputBridge: lets the web UI's text entry box submit a message
# the same way `run_convobox.py --text "..."` already does -- corrections
# applied, forwarded to history/SSE as a transcript, then
# Orchestrator.handle_transcript(). Uses the real TranscriptCorrector (cheap,
# pure, no I/O) rather than a fake -- this is exactly the behavior worth
# proving, not something to stub past. ---


class _FakeOrchestratorForText:
    def __init__(self) -> None:
        self.handled: list[str] = []

    async def handle_transcript(self, text: str) -> None:
        self.handled.append(text)


class _FakeForwarderForText:
    def __init__(self) -> None:
        self.forwarded: list[str] = []

    def forward_transcript(self, text: str) -> None:
        self.forwarded.append(text)


def test_text_bridge_with_no_targets_reports_not_ready() -> None:
    bridge = WebTextInputBridge()
    assert bridge.is_ready is False


@pytest.mark.asyncio
async def test_text_bridge_with_no_targets_submit_is_a_harmless_false() -> None:
    bridge = WebTextInputBridge()
    assert await bridge.submit("hello") is False


@pytest.mark.asyncio
async def test_text_bridge_rejects_blank_text() -> None:
    orchestrator = _FakeOrchestratorForText()
    bridge = WebTextInputBridge()
    bridge.set_targets(orchestrator, TranscriptCorrector(), None)  # type: ignore[arg-type]

    assert await bridge.submit("   ") is False
    assert orchestrator.handled == []


@pytest.mark.asyncio
async def test_text_bridge_submits_stripped_text_to_the_orchestrator() -> None:
    orchestrator = _FakeOrchestratorForText()
    bridge = WebTextInputBridge()
    bridge.set_targets(orchestrator, TranscriptCorrector(), None)  # type: ignore[arg-type]

    accepted = await bridge.submit("  run the tests  ")

    assert accepted is True
    assert orchestrator.handled == ["run the tests"]


@pytest.mark.asyncio
async def test_text_bridge_applies_corrections_before_sending() -> None:
    orchestrator = _FakeOrchestratorForText()
    corrector = TranscriptCorrector({"bargain": "barge-in"})
    bridge = WebTextInputBridge()
    bridge.set_targets(orchestrator, corrector, None)  # type: ignore[arg-type]

    await bridge.submit("test the bargain detection")

    assert orchestrator.handled == ["test the barge-in detection"]


@pytest.mark.asyncio
async def test_text_bridge_forwards_the_corrected_text_to_history() -> None:
    orchestrator = _FakeOrchestratorForText()
    forwarder = _FakeForwarderForText()
    corrector = TranscriptCorrector({"bargain": "barge-in"})
    bridge = WebTextInputBridge()
    bridge.set_targets(orchestrator, corrector, forwarder)  # type: ignore[arg-type]

    await bridge.submit("test the bargain detection")

    assert forwarder.forwarded == ["test the barge-in detection"]
