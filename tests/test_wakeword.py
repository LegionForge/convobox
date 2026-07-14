from __future__ import annotations

import pytest

from convobox.wakeword import DEFAULT_WAKE_WORD, WakewordDetector


@pytest.fixture
def detector() -> WakewordDetector:
    return WakewordDetector(wake_word="Athena")


def test_exact_match(detector: WakewordDetector) -> None:
    assert detector.check("Athena") is True


def test_case_insensitive(detector: WakewordDetector) -> None:
    assert detector.check("ATHENA") is True


def test_embedded_in_sentence(detector: WakewordDetector) -> None:
    assert detector.check("hey Athena, hold on a second") is True


def test_tolerates_punctuation_and_extra_whitespace(detector: WakewordDetector) -> None:
    assert detector.check("...Athena!") is True


def test_no_false_positive_on_substring(detector: WakewordDetector) -> None:
    # word-boundary aware: not a match inside a larger word
    assert detector.check("Athenaeum is a nice word") is False


def test_no_match_on_unrelated_speech(detector: WakewordDetector) -> None:
    assert detector.check("can you run the tests again") is False


def test_empty_transcript(detector: WakewordDetector) -> None:
    assert detector.check("") is False


def test_whitespace_only_transcript(detector: WakewordDetector) -> None:
    assert detector.check("   \t\n  ") is False


def test_multi_word_wake_phrase() -> None:
    detector = WakewordDetector(wake_word="hey Athena")
    assert detector.check("okay, hey Athena, stop for a second") is True
    assert detector.check("Athena") is False  # the whole phrase must match


def test_wake_word_property_returns_original() -> None:
    detector = WakewordDetector(wake_word="Hey Athena")
    assert detector.wake_word == "Hey Athena"


def test_default_wake_word() -> None:
    detector = WakewordDetector()
    assert detector.wake_word == DEFAULT_WAKE_WORD
    assert DEFAULT_WAKE_WORD == "Athena"  # pins the value the round-trip STT test verified
    assert detector.check("hey Athena, hold on") is True


# --- the construction guard (shared with Safeword/Confirmword) ---


def test_phrase_normalizing_to_empty_raises() -> None:
    with pytest.raises(ValueError, match="normalizes to nothing"):
        WakewordDetector(wake_word="!!!")


# --- deliberately UNLIKE ConfirmwordDetector: no common-affirmation ban ---


@pytest.mark.parametrize("casual", ["yes", "okay", "sure", "hi", "computer"])
def test_common_words_are_allowed_as_wake_words(casual: str) -> None:
    # A wake word is the push-word barge-in trigger, not an approval gate --
    # a false-fire just means the assistant starts listening a beat early,
    # not that something destructive happens. Unlike ConfirmwordDetector,
    # this must NOT reject common affirmations/fillers -- that ban belongs
    # only to the high-stakes approval vocabulary. See the module docstring
    # and docs/DESIGN-0.3.0-interaction-and-safety.md's safety invariant.
    detector = WakewordDetector(wake_word=casual)
    assert detector.check(casual) is True
