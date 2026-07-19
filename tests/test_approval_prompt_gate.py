from __future__ import annotations

from convobox.approval import ApprovalDetector
from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.tui import ConversationTuiState
from scripts.run_convobox import ApprovalPromptGate, _on_backend_event


def _gate(timeout_s: float = 2.5) -> ApprovalPromptGate:
    return ApprovalPromptGate(ApprovalDetector(approval_phrase="nightingale"), timeout_s)


def test_not_waiting_by_default() -> None:
    gate = _gate()
    assert gate.is_waiting is False


def test_start_waiting_sets_is_waiting() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.is_waiting is True


def test_observe_transcript_approve_ends_the_wait_and_returns_approve() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("nightingale", now=1.0) == "approve"
    assert gate.is_waiting is False


def test_observe_transcript_deny_ends_the_wait_and_returns_deny() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("no", now=1.0) == "deny"
    assert gate.is_waiting is False


def test_observe_transcript_discuss_does_not_end_the_wait() -> None:
    # The whole point, unlike ContinuePromptGate's "pass": an approval
    # prompt must stay open and answerable across a clarifying exchange.
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("what does that command do?", now=1.0) == "discuss"
    assert gate.is_waiting is True


def test_observe_transcript_discuss_resets_the_waiting_clock() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_transcript("tell me more first", now=12.0) == "discuss"
    # 2.4s since the discuss reply (now=12.0) -- would have expired if
    # measured from the original start_waiting(now=10.0) instead.
    assert gate.observe_timeout(now=14.4) is None
    assert gate.observe_timeout(now=14.5) == "deny"


def test_observe_transcript_unclear_speech_does_not_change_state() -> None:
    # Normalizes to nothing -- no signal at all, not even "discuss".
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_transcript("...!!!", now=11.0) is None
    assert gate.is_waiting is True
    # Clock was NOT reset by the no-signal utterance.
    assert gate.observe_timeout(now=12.5) == "deny"


def test_observe_timeout_none_while_not_waiting() -> None:
    gate = _gate(timeout_s=2.5)
    assert gate.observe_timeout(now=100.0) is None


def test_observe_timeout_none_before_the_window_elapses() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_timeout(now=11.0) is None
    assert gate.is_waiting is True


def test_observe_timeout_deny_exactly_once_when_the_window_elapses() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_timeout(now=12.5) == "deny"
    assert gate.is_waiting is False
    # Already expired -- a second poll tick must not fire again.
    assert gate.observe_timeout(now=13.0) is None


def test_an_approve_reply_before_the_timeout_prevents_the_timeout_from_firing() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    gate.observe_transcript("nightingale", now=10.5)
    assert gate.observe_timeout(now=12.5) is None


def test_start_waiting_again_resets_the_window() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    gate.start_waiting(now=11.0)
    assert gate.observe_timeout(now=12.5) is None  # only 1.5s since the SECOND start
    assert gate.observe_timeout(now=13.5) == "deny"


def test_codex_approval_event_starts_gate_and_sets_tui_warning() -> None:
    gate = _gate()
    state = ConversationTuiState()
    _on_backend_event(
        state,
        BackendEvent(
            BackendEventType.APPROVAL_REQUEST,
            content="APPROVAL REQUIRED — COMMAND EXECUTION\n\nRequested command:\necho harmless",
        ),
        "cobalt night and gale",
        gate,
    )
    assert gate.is_waiting is True
    assert state.warning is not None
    assert "echo harmless" in state.warning
    assert "cobalt night and gale" in state.warning
