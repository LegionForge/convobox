from __future__ import annotations

import pytest

from convobox.safeword import SafewordDetector


@pytest.fixture
def detector() -> SafewordDetector:
    return SafewordDetector(hard_stop_phrases=["stop stop stop"])


def test_exact_match(detector: SafewordDetector) -> None:
    assert detector.check("stop stop stop") == "stop stop stop"


def test_case_insensitive(detector: SafewordDetector) -> None:
    assert detector.check("STOP Stop stop") == "stop stop stop"


def test_embedded_in_sentence(detector: SafewordDetector) -> None:
    assert detector.check("okay, stop stop stop now please") == "stop stop stop"


def test_tolerates_punctuation_and_extra_whitespace(detector: SafewordDetector) -> None:
    assert detector.check("Stop, stop... stop!") == "stop stop stop"


def test_no_false_positive_on_similar_text(detector: SafewordDetector) -> None:
    assert detector.check("please stop the build") is None
    assert detector.check("stop stop") is None


def test_empty_transcript(detector: SafewordDetector) -> None:
    assert detector.check("") is None


def test_whitespace_only_transcript(detector: SafewordDetector) -> None:
    assert detector.check("   \t\n  ") is None


def test_multiple_phrases_returns_matched_original() -> None:
    detector = SafewordDetector(hard_stop_phrases=["stop stop stop", "abort now"])
    assert detector.check("please abort now") == "abort now"


def test_phrase_normalizing_to_empty_raises_instead_of_silently_dropping() -> None:
    with pytest.raises(ValueError, match="!!!"):
        SafewordDetector(hard_stop_phrases=["stop stop stop", "!!!"])
