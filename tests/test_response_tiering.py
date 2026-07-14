from __future__ import annotations

import pytest

from convobox.response_tiering import (
    DEFAULT_CONTINUE_PHRASES,
    DEFAULT_DECLINE_PHRASES,
    ContinueDetector,
)


@pytest.fixture
def detector() -> ContinueDetector:
    return ContinueDetector()


@pytest.mark.parametrize("phrase", DEFAULT_CONTINUE_PHRASES)
def test_default_continue_phrases_match(detector: ContinueDetector, phrase: str) -> None:
    assert detector.check(phrase) == "continue"


@pytest.mark.parametrize("phrase", DEFAULT_DECLINE_PHRASES)
def test_default_decline_phrases_match(detector: ContinueDetector, phrase: str) -> None:
    assert detector.check(phrase) == "decline"


def test_case_insensitive(detector: ContinueDetector) -> None:
    assert detector.check("CONTINUE") == "continue"
    assert detector.check("No Thanks") == "decline"


def test_embedded_in_sentence(detector: ContinueDetector) -> None:
    assert detector.check("okay, please continue with the rest") == "continue"
    assert detector.check("that's enough for now, thanks") == "decline"


def test_tolerates_punctuation_and_extra_whitespace(detector: ContinueDetector) -> None:
    assert detector.check("Go on!") == "continue"
    assert detector.check("No thanks.") == "decline"


def test_no_false_positive_on_substring(detector: ContinueDetector) -> None:
    # word-boundary aware: not a match inside a larger word
    assert detector.check("discontinued the old approach") is None


def test_no_match_on_ordinary_speech(detector: ContinueDetector) -> None:
    assert detector.check("run the tests please") is None


def test_empty_transcript(detector: ContinueDetector) -> None:
    assert detector.check("") is None


def test_whitespace_only_transcript(detector: ContinueDetector) -> None:
    assert detector.check("   \t\n  ") is None


def test_custom_phrase_lists() -> None:
    detector = ContinueDetector(
        continue_phrases=["tell me more"],
        decline_phrases=["I'm done"],
    )
    assert detector.check("tell me more") == "continue"
    assert detector.check("I'm done") == "decline"
    assert detector.check("continue") is None  # default no longer active


# --- construction guards ---


def test_phrase_normalizing_to_empty_raises() -> None:
    with pytest.raises(ValueError, match="normalize to nothing"):
        ContinueDetector(continue_phrases=["!!!"])


def test_overlapping_continue_and_decline_phrases_raises() -> None:
    with pytest.raises(ValueError, match="both continue_phrases and decline_phrases"):
        ContinueDetector(continue_phrases=["go on"], decline_phrases=["go on"])


# --- deliberately UNLIKE ConfirmwordDetector: no common-affirmation ban ---


@pytest.mark.parametrize("casual", ["yes", "okay", "sure", "no", "stop"])
def test_common_words_are_allowed_in_custom_vocabularies(casual: str) -> None:
    # Continue/decline is the low-stakes vocabulary (docs/DESIGN-0.3.0-
    # interaction-and-safety.md's safety invariant): misreading it just
    # means hearing more or less detail, never a destructive action, so
    # common words must NOT be banned the way ConfirmwordDetector bans them.
    detector = ContinueDetector(continue_phrases=[casual], decline_phrases=["nope"])
    assert detector.check(casual) == "continue"


# --- "I'm good" was tested and rejected -- must not be a default ---


def test_declined_but_stt_unsafe_phrase_is_not_a_default() -> None:
    assert "I'm good" not in DEFAULT_DECLINE_PHRASES
    assert "i'm good" not in [p.lower() for p in DEFAULT_DECLINE_PHRASES]
