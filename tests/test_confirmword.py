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


# --- fuzzy match: tolerates STT noise on the SAME phrase, not a different
# one. Real mishearings observed live, 2026-07-21, UAT session with
# approval_phrase="juliette papa charlie" -- none of these matched the old
# exact-only check, even though a human would recognize them instantly. ---


@pytest.fixture
def nato_detector() -> ConfirmwordDetector:
    return ConfirmwordDetector(approval_phrase="juliette papa charlie")


@pytest.mark.parametrize(
    "mishearing",
    [
        "Juliet Papa Charlie",  # missing one letter -- the most common miss
        "Juliet Papa Charley",
        "Julianne Papa-Charlie",
    ],
)
def test_fuzzy_match_accepts_real_close_mishearings(
    nato_detector: ConfirmwordDetector, mishearing: str
) -> None:
    assert nato_detector.check(mishearing) is True


@pytest.mark.parametrize(
    "not_close_enough",
    [
        "yes",
        "okay",
        "sure",
        "stop",
        "cancel that",
        "no",
        "go ahead and do it",
        "run the tests",
        "yeah but stop the deploy",
        "charlie",  # the one distinctive word alone, still not the phrase
    ],
)
def test_fuzzy_match_rejects_unrelated_speech(
    nato_detector: ConfirmwordDetector, not_close_enough: str
) -> None:
    # These must stay firmly rejected -- fuzzy tolerance is for STT noise on
    # the SAME phrase, not a license to loosely match anything nearby.
    assert nato_detector.check(not_close_enough) is False


def test_fuzzy_match_embedded_in_a_longer_discuss_style_sentence() -> None:
    # The real failure mode was a short utterance that's ALMOST just the
    # phrase (STT mangled a letter or two), not a long unrelated sentence
    # that happens to contain a fuzzy-close 3-word window -- confirm a
    # genuinely unrelated longer sentence still doesn't accidentally match.
    detector = ConfirmwordDetector(approval_phrase="juliette papa charlie")
    assert detector.check("We really have to put that cherry away now") is False
