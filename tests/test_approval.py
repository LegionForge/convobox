from __future__ import annotations

import pytest

from convobox.approval import DEFAULT_DENY_PHRASES, ApprovalDetector


@pytest.fixture
def detector() -> ApprovalDetector:
    # A distinctive code word the user would never say by accident.
    return ApprovalDetector(approval_phrase="nightingale")


def test_approve_exact_match(detector: ApprovalDetector) -> None:
    assert detector.check("nightingale") == "approve"


def test_approve_case_insensitive(detector: ApprovalDetector) -> None:
    assert detector.check("NIGHTingale") == "approve"


def test_approve_embedded_in_sentence(detector: ApprovalDetector) -> None:
    assert detector.check("yes, I confirm: nightingale, go ahead") == "approve"


@pytest.mark.parametrize("phrase", DEFAULT_DENY_PHRASES)
def test_default_deny_phrases_match(detector: ApprovalDetector, phrase: str) -> None:
    assert detector.check(phrase) == "deny"


def test_deny_case_insensitive(detector: ApprovalDetector) -> None:
    assert detector.check("NO") == "deny"


def test_deny_embedded_in_sentence(detector: ApprovalDetector) -> None:
    assert detector.check("no, cancel that please") == "deny"


def test_unmatched_speech_is_discuss(detector: ApprovalDetector) -> None:
    # the whole point of this detector, unlike ContinueDetector's "pass":
    # an unrelated utterance during a pending approval must NOT fall through
    # to normal command routing -- it's "discuss," and the approval prompt
    # stays open.
    assert detector.check("what does that command actually do?") == "discuss"
    assert detector.check("hmm, tell me more first") == "discuss"


def test_empty_transcript_is_none(detector: ApprovalDetector) -> None:
    assert detector.check("") is None


def test_whitespace_only_transcript_is_none(detector: ApprovalDetector) -> None:
    assert detector.check("   \t\n  ") is None


def test_no_false_positive_approve_on_ordinary_affirmation(detector: ApprovalDetector) -> None:
    # a casual "yes"/"sure" must not approve -- reuses ConfirmwordDetector's
    # own strict guard, this just confirms the composition preserves it.
    assert detector.check("yes go ahead") == "discuss"
    assert detector.check("sure, sounds good") == "discuss"


def test_no_false_positive_on_substring(detector: ApprovalDetector) -> None:
    # word-boundary aware, like the other detectors: "nightingales" is not
    # "nightingale", and "canceled" is not "cancel".
    assert detector.check("nightingales sing") == "discuss"
    assert detector.check("the operation canceled itself") == "discuss"


def test_approval_phrase_property_returns_original() -> None:
    detector = ApprovalDetector(approval_phrase="Code Nightingale")
    assert detector.approval_phrase == "Code Nightingale"


def test_custom_deny_phrases() -> None:
    detector = ApprovalDetector(approval_phrase="nightingale", deny_phrases=["nope", "abort"])
    assert detector.check("nope") == "deny"
    assert detector.check("abort") == "deny"
    assert detector.check("no") == "discuss"  # not in the custom list


# --- construction guards ---


def test_invalid_approval_phrase_raises_same_as_confirmword_detector() -> None:
    # ApprovalDetector delegates construction validation to ConfirmwordDetector
    # -- it must not silently swallow or duplicate that safety check.
    with pytest.raises(ValueError, match="normalizes to nothing"):
        ApprovalDetector(approval_phrase="!!!")
    with pytest.raises(ValueError, match="common affirmations"):
        ApprovalDetector(approval_phrase="yes")


def test_empty_deny_phrase_raises() -> None:
    with pytest.raises(ValueError, match="normalize to nothing"):
        ApprovalDetector(approval_phrase="nightingale", deny_phrases=["!!!"])


def test_approval_phrase_also_in_deny_phrases_raises() -> None:
    with pytest.raises(ValueError, match="ambiguous vocabulary"):
        ApprovalDetector(approval_phrase="nightingale", deny_phrases=["nightingale"])
