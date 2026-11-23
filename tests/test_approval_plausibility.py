"""The approval plausibility guard: refuse an approve that cannot be a person
speaking (words faster than speech, or a recitation of the STT hotwords).

Origin: live UAT 2026-11-21 -- Whisper emitted the hotwords string as a
transcript ('stop brake eject mayday listening', 'brake' x14), reproduced
offline on synthetic noise. The guard is approve-only; the stop path must be
untouched (last test).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import convobox.safeword.detector as safeword_detector
from convobox.approval import ApprovalDetector
from convobox.confirmword.plausibility import implausible_approval_reason
from scripts.run_convobox import ApprovalPromptGate, approval_utterance_heard_cleanly

HOTWORDS = "stop brake eject mayday listening resume alpha bravo delta halt abort"
SHUFFLED = "bravo stop eject listening alpha brake resume mayday halt delta abort"


# --- speaking-rate rule ------------------------------------------------------


def test_a_normal_approval_is_plausible() -> None:
    assert implausible_approval_reason("alpha bravo delta", 1.4, HOTWORDS) is None
    assert implausible_approval_reason("alpha bravo delta", 1.4, None) is None


def test_a_loop_of_words_in_a_short_clip_is_refused() -> None:
    text = " ".join(["brake"] * 60)
    assert implausible_approval_reason(text, 1.5, HOTWORDS) is not None
    assert "faster than speech" in implausible_approval_reason(text, 1.5, HOTWORDS)


def test_three_words_in_a_blink_is_refused() -> None:
    # 3 words / 0.3 s = 10 words/s: not a person.
    assert implausible_approval_reason("alpha bravo delta", 0.3, None) is not None


def test_rate_boundary_is_not_off_by_one() -> None:
    # 7 words/s is allowed (the limit is strictly greater than).
    assert implausible_approval_reason("a b c d e f g", 1.0, None) is None
    assert implausible_approval_reason("a b c d e f g h", 1.0, None) is not None


@pytest.mark.parametrize("duration", [0.0, -1.0, None])
def test_missing_duration_disables_only_the_rate_rule(duration) -> None:
    assert implausible_approval_reason("alpha bravo delta", duration, HOTWORDS) is None
    # ... but the recitation rule still applies.
    assert implausible_approval_reason(HOTWORDS, duration, HOTWORDS) is not None


# --- recitation rule ---------------------------------------------------------


def test_a_recitation_of_the_hotwords_is_refused() -> None:
    reason = implausible_approval_reason(
        "stop brake eject mayday listening resume alpha bravo delta", 8.0, HOTWORDS
    )
    assert reason is not None and "hotwords" in reason


def test_a_four_word_run_in_hotwords_order_is_refused() -> None:
    assert (
        implausible_approval_reason("resume alpha bravo delta", 3.0, HOTWORDS)
        is not None
    )


def test_a_three_word_run_is_not_a_recitation() -> None:
    # The genuine approval phrase is itself 3 consecutive hotwords in the
    # unshuffled list; it must never trip the recitation rule.
    assert implausible_approval_reason("alpha bravo delta", 1.5, HOTWORDS) is None


def test_recitation_ignores_case_and_punctuation() -> None:
    assert (
        implausible_approval_reason("Stop. Brake, eject; MAYDAY!", 4.0, HOTWORDS)
        is not None
    )


def test_a_shuffled_hotwords_list_keeps_a_real_phrase_and_recitation_distinguishable() -> (
    None
):
    # In the shuffled list the approval words are not adjacent, so the real
    # phrase is nowhere near the run limit...
    assert implausible_approval_reason("alpha bravo delta", 1.5, SHUFFLED) is None
    # ... and an actual recitation of the shuffled list is still refused.
    assert implausible_approval_reason(SHUFFLED, 9.0, SHUFFLED) is not None


def test_the_run_must_be_consecutive_not_merely_present() -> None:
    # Every word is a hotword but not in hotwords order or adjacency.
    assert implausible_approval_reason("abort stop halt brake", 3.0, HOTWORDS) is None


def test_no_hotwords_means_no_recitation_check() -> None:
    assert implausible_approval_reason(HOTWORDS, 8.0, None) is None
    assert implausible_approval_reason(HOTWORDS, 8.0, "") is None


# --- wired into the approval gate -------------------------------------------


class _QuietPlayer:
    playback_ended_at = 0.0  # never played, as the real player reports it

    def is_playing(self) -> bool:
        return False


def _heard(text: str, duration: float, hotwords: str | None) -> bool:
    return approval_utterance_heard_cleanly(
        text,
        result=SimpleNamespace(duration_s=duration, latency_ms=300),
        player=_QuietPlayer(),
        echo_filter=SimpleNamespace(matches_recent=lambda *a, **k: False),
        min_silence_ms=500,
        grace_s=0.3,
        hotwords=hotwords,
        now=1000.0,
    )


def test_a_clean_quiet_approval_is_still_trusted() -> None:
    assert _heard("alpha bravo delta", 1.4, HOTWORDS) is True


def test_a_hallucinated_recitation_is_not_trusted_even_in_silence() -> None:
    assert _heard(HOTWORDS, 6.0, HOTWORDS) is False
    assert _heard(" ".join(["brake"] * 40), 1.2, HOTWORDS) is False


def test_the_gate_leaves_the_request_pending_on_a_recitation() -> None:
    gate = ApprovalPromptGate(ApprovalDetector("alpha bravo delta"), timeout_s=30.0)
    gate.start_waiting(100.0)
    outcome = gate.observe_transcript(
        HOTWORDS, 110.0, heard_cleanly=_heard(HOTWORDS, 6.0, HOTWORDS)
    )
    assert outcome == "ignored"
    assert gate.is_waiting
    # The clock was not extended: it still expires 30 s after the ORIGINAL start.
    assert gate.observe_timeout(130.0) == "deny"


def test_the_gate_still_accepts_a_real_approval_after_a_recitation() -> None:
    gate = ApprovalPromptGate(ApprovalDetector("alpha bravo delta"), timeout_s=30.0)
    gate.start_waiting(100.0)
    assert (
        gate.observe_transcript(
            HOTWORDS, 105.0, heard_cleanly=_heard(HOTWORDS, 6.0, HOTWORDS)
        )
        == "ignored"
    )
    assert (
        gate.observe_transcript(
            "alpha bravo delta",
            108.0,
            heard_cleanly=_heard("alpha bravo delta", 1.4, HOTWORDS),
        )
        == "approve"
    )


def test_deny_is_never_blocked_by_the_guard() -> None:
    gate = ApprovalPromptGate(ApprovalDetector("alpha bravo delta"), timeout_s=30.0)
    gate.start_waiting(100.0)
    # A deny that happens to arrive as a long fast transcript is still a deny
    # (refusing is the safe direction; the guard only refuses an approve).
    assert gate.observe_transcript("no", 101.0, heard_cleanly=False) == "deny"


# --- the stop path is untouched (a stop must never be blocked) ---------------


def test_stop_phrases_are_never_subject_to_the_plausibility_guard() -> None:
    safeword = safeword_detector.SafewordDetector(
        ["stop stop stop", "brake brake brake", "eject eject eject"]
    )
    # Fast, looped, hotword-shaped transcripts still trigger the stop path.
    assert safeword.check(" ".join(["brake"] * 60)) is not None
    assert safeword.check("stop stop stop stop stop stop stop stop") is not None
    assert safeword.check("eject eject eject") is not None
    # And the stop module never references the guard: a stop must never be
    # blockable by it. (Checks the module source, not just its attribute names,
    # so an aliased or inline call would also be caught.)
    source = inspect.getsource(safeword_detector)
    assert "implausible_approval_reason" not in source
    assert "plausibility" not in source
