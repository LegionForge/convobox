from __future__ import annotations

import pytest

from convobox.confirmword import ConfirmwordDetector


@pytest.fixture
def detector() -> ConfirmwordDetector:
    # A distinctive code word the user would never say by accident.
    return ConfirmwordDetector(approval_phrase="nightingale")


def test_exact_match(detector: ConfirmwordDetector) -> None:
    assert detector.check("nightingale") is True


def test_case_insensitive(detector: ConfirmwordDetector) -> None:
    assert detector.check("NIGHTingale") is True


def test_embedded_in_sentence(detector: ConfirmwordDetector) -> None:
    assert detector.check("yes, I confirm: nightingale, go ahead") is True


def test_tolerates_punctuation_and_extra_whitespace(detector: ConfirmwordDetector) -> None:
    assert detector.check("...nightingale!") is True


def test_no_false_positive_on_substring(detector: ConfirmwordDetector) -> None:
    # word-boundary aware: not a match inside a larger word
    assert detector.check("nightingales sing") is False


def test_no_false_positive_on_ordinary_affirmation(detector: ConfirmwordDetector) -> None:
    # the whole point: a casual "yes" must NOT approve
    assert detector.check("yes go ahead") is False
    assert detector.check("sure, sounds good") is False


def test_empty_transcript(detector: ConfirmwordDetector) -> None:
    assert detector.check("") is False


def test_whitespace_only_transcript(detector: ConfirmwordDetector) -> None:
    assert detector.check("   \t\n  ") is False


def test_multi_word_approval_phrase_with_distinctive_token() -> None:
    detector = ConfirmwordDetector(approval_phrase="confirm nightingale")
    assert detector.check("okay, confirm nightingale please") is True
    assert detector.check("confirm this") is False  # missing the distinctive token


def test_approval_phrase_property_returns_original() -> None:
    detector = ConfirmwordDetector(approval_phrase="Code Nightingale")
    assert detector.approval_phrase == "Code Nightingale"


# --- the safety-critical construction guards ---


def test_phrase_normalizing_to_empty_raises() -> None:
    with pytest.raises(ValueError, match="normalizes to nothing"):
        ConfirmwordDetector(approval_phrase="!!!")


@pytest.mark.parametrize(
    "casual",
    ["yes", "Yeah", "sure", "okay", "ok", "uh huh", "oui", "da", "ja", "yes please"],
)
def test_common_affirmation_as_whole_phrase_is_rejected(casual: str) -> None:
    # casual speech must never be arm-able as the approval phrase
    with pytest.raises(ValueError, match="common affirmations"):
        ConfirmwordDetector(approval_phrase=casual)


def test_distinctive_token_rescues_a_phrase_that_starts_with_yes() -> None:
    # "yes" alone is banned, but "yes nightingale" carries a distinctive token
    detector = ConfirmwordDetector(approval_phrase="yes nightingale")
    assert detector.check("yes nightingale") is True
