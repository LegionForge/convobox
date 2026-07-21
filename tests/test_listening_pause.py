from __future__ import annotations

import pytest

from convobox.listening_pause import DEFAULT_PAUSE_PHRASES, PauseListeningDetector


@pytest.fixture
def detector() -> PauseListeningDetector:
    return PauseListeningDetector()


def test_default_phrases_match(detector: PauseListeningDetector) -> None:
    assert detector.check("stop listening") == "stop listening"
    assert detector.check("pause listening") == "pause listening"


def test_case_insensitive(detector: PauseListeningDetector) -> None:
    assert detector.check("STOP Listening") == "stop listening"


def test_embedded_in_sentence(detector: PauseListeningDetector) -> None:
    assert detector.check("okay, stop listening for now please") == "stop listening"


def test_tolerates_punctuation_and_extra_whitespace(detector: PauseListeningDetector) -> None:
    assert detector.check("Stop... listening!") == "stop listening"


def test_no_false_positive_on_similar_text(detector: PauseListeningDetector) -> None:
    assert detector.check("please stop the build") is None
    assert detector.check("listening") is None


def test_empty_transcript(detector: PauseListeningDetector) -> None:
    assert detector.check("") is None


def test_whitespace_only_transcript(detector: PauseListeningDetector) -> None:
    assert detector.check("   \t\n  ") is None


def test_custom_phrases_override_default() -> None:
    detector = PauseListeningDetector(pause_phrases=["go to sleep"])
    assert detector.check("go to sleep") == "go to sleep"
    assert detector.check("stop listening") is None  # default no longer active


def test_multiple_custom_phrases_returns_matched_original() -> None:
    detector = PauseListeningDetector(pause_phrases=["stop listening", "that's enough"])
    assert detector.check("okay that's enough for now") == "that's enough"


def test_phrase_normalizing_to_empty_raises_instead_of_silently_dropping() -> None:
    with pytest.raises(ValueError, match="!!!"):
        PauseListeningDetector(pause_phrases=["stop listening", "!!!"])


def test_default_pause_phrases_constant() -> None:
    assert DEFAULT_PAUSE_PHRASES == ("stop listening", "pause listening")


# --- deliberately UNLIKE ConfirmwordDetector: no common-affirmation ban ---


@pytest.mark.parametrize("casual", ["yes", "okay", "sure", "hi"])
def test_common_words_are_allowed_as_pause_phrases(casual: str) -> None:
    # Pausing listening is benign (say the resume word again to resume), not
    # destructive -- same low safety tier as SafewordDetector, not
    # ConfirmwordDetector's approval-gate tier. See the module docstring.
    detector = PauseListeningDetector(pause_phrases=[casual])
    assert detector.check(casual) == casual
