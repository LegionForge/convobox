from __future__ import annotations

from convobox.response_tiering import ContinueDetector
from scripts.run_convobox import ContinuePromptGate


def _gate(timeout_s: float = 2.5) -> ContinuePromptGate:
    return ContinuePromptGate(ContinueDetector(), timeout_s)


def test_not_waiting_by_default() -> None:
    gate = _gate()
    assert gate.is_waiting is False


def test_start_waiting_sets_is_waiting() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.is_waiting is True


def test_observe_transcript_continue_ends_the_wait_and_returns_continue() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("continue") == "continue"
    assert gate.is_waiting is False


def test_observe_transcript_decline_ends_the_wait_and_returns_decline() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("that's enough") == "decline"
    assert gate.is_waiting is False


def test_observe_transcript_unrelated_speech_ends_the_wait_and_returns_pass() -> None:
    # Unlike ListeningGate's paused state (which drops everything until
    # the wake word), a non-matching reply here means the user moved on
    # to a new topic, not that they're still mid-answer -- "pass" tells
    # the caller to forward it normally, not drop it.
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("run the tests instead") == "pass"
    assert gate.is_waiting is False


def test_observe_timeout_false_while_not_waiting() -> None:
    gate = _gate(timeout_s=2.5)
    assert gate.observe_timeout(now=100.0) is False


def test_observe_timeout_false_before_the_window_elapses() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_timeout(now=11.0) is False
    assert gate.is_waiting is True


def test_observe_timeout_true_exactly_once_when_the_window_elapses() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_timeout(now=12.5) is True
    assert gate.is_waiting is False
    # Already expired -- a second poll tick must not fire again.
    assert gate.observe_timeout(now=13.0) is False


def test_a_reply_before_the_timeout_prevents_the_timeout_from_firing() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    gate.observe_transcript("continue")
    assert gate.observe_timeout(now=12.5) is False


def test_start_waiting_again_resets_the_window() -> None:
    # A second tiered response starts a fresh wait -- not accumulated
    # with any leftover time from a previous one.
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    gate.start_waiting(now=11.0)
    assert gate.observe_timeout(now=12.5) is False  # only 1.5s since the SECOND start
    assert gate.observe_timeout(now=13.5) is True
