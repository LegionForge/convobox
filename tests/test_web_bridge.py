from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.safeword.detector import SafewordDetector
from convobox.web.bridge import WebApprovalBridge, WebEventForwarder, WebListeningBridge
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


def test_forwards_to_history_when_given(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="it works"))

    events = db.get_session_events(session_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "response"
    assert events[0]["backend_response"] == "it works"


def test_tool_call_event_type_is_not_folded_into_response(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TOOL_CALL, tool="Bash", tool_input="ls"))

    stored = db.get_session_events(session_id)[0]
    assert stored["event_type"] == "tool_call"
    assert stored["tool_name"] == "Bash"


def test_approval_request_gets_its_own_event_type(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.APPROVAL_REQUEST, tool="Bash"))

    stored = db.get_session_events(session_id)[0]
    assert stored["event_type"] == "approval_request"


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

    stored = db.get_session_events(session_id)
    assert len(stored) == 1
    assert stored[0]["backend_response"] == "wired end to end"
    assert queue.get_nowait()["content"] == "wired end to end"


# --- forward_transcript: the OTHER half of the web wiring gap this session
# found while building a demo -- Orchestrator.on_event only ever sees
# BackendEvents (backend responses/tool calls), never the user's own
# recognized speech that prompted one, so run_convobox.py's call sites for
# Orchestrator.handle_transcript() also call this directly. ---


def test_forward_transcript_persists_to_history_when_given(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder.forward_transcript("what should I work on next")

    stored = db.get_session_events(session_id)
    assert len(stored) == 1
    assert stored[0]["event_type"] == "transcript"
    assert stored[0]["user_transcript"] == "what should I work on next"


def test_forward_transcript_with_no_history_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder.forward_transcript("hello")  # must not raise


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

    def stop(self) -> None:
        self.stop_calls += 1


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
    gate = _FakeListeningGate(is_paused=False)
    player = _FakePlayer()
    tts = _FakeTTS()
    adapter = _FakeAdapterForListening()
    bridge = WebListeningBridge()
    bridge.set_targets(gate, player, tts, adapter)  # type: ignore[arg-type]

    changed = await bridge.pause()

    assert changed is True
    assert gate.is_paused is True
    assert bridge.is_paused is True
    assert player.stop_calls == 1
    assert tts.stop_calls == 1
    assert adapter.hard_stop_calls == 1


@pytest.mark.asyncio
async def test_listening_bridge_pause_while_already_paused_is_a_noop() -> None:
    # Matches ListeningGate.observe()'s own behavior: once is_paused is
    # true, it never re-enters the "pause" branch, so a second pause
    # phrase (or a second button click) must not re-hard-stop anything.
    gate = _FakeListeningGate(is_paused=True)
    player = _FakePlayer()
    tts = _FakeTTS()
    adapter = _FakeAdapterForListening()
    bridge = WebListeningBridge()
    bridge.set_targets(gate, player, tts, adapter)  # type: ignore[arg-type]

    changed = await bridge.pause()

    assert changed is False
    assert player.stop_calls == 0
    assert tts.stop_calls == 0
    assert adapter.hard_stop_calls == 0


def test_listening_bridge_resume_clears_the_flag_with_no_side_effects() -> None:
    gate = _FakeListeningGate(is_paused=True)
    player = _FakePlayer()
    tts = _FakeTTS()
    adapter = _FakeAdapterForListening()
    bridge = WebListeningBridge()
    bridge.set_targets(gate, player, tts, adapter)  # type: ignore[arg-type]

    changed = bridge.resume()

    assert changed is True
    assert gate.is_paused is False
    assert bridge.is_paused is False
    assert player.stop_calls == 0
    assert tts.stop_calls == 0
    assert adapter.hard_stop_calls == 0


def test_listening_bridge_resume_while_not_paused_is_a_noop() -> None:
    gate = _FakeListeningGate(is_paused=False)
    bridge = WebListeningBridge()
    bridge.set_targets(gate, _FakePlayer(), _FakeTTS(), _FakeAdapterForListening())  # type: ignore[arg-type]

    assert bridge.resume() is False
