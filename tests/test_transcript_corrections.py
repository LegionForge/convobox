from __future__ import annotations

import pytest

from convobox.config import STTConfig
from convobox.stt.corrections import TranscriptCorrector


def test_regression_i_agree_can_be_corrected_from_the_green() -> None:
    corrector = TranscriptCorrector({"the green": "I agree"})

    assert corrector.correct("the green") == "I agree"


def test_correction_handles_case_and_punctuation_between_words() -> None:
    corrector = TranscriptCorrector({"yellow garden": "Yellow Garden"})

    assert corrector.correct("open YELLOW-garden, please") == "open Yellow Garden, please"


def test_correction_does_not_match_inside_a_larger_word() -> None:
    corrector = TranscriptCorrector({"garden": "orchard"})

    assert corrector.correct("gardener") == "gardener"


def test_more_specific_correction_wins_over_shorter_one() -> None:
    corrector = TranscriptCorrector(
        {"yellow garden": "Yellow Garden", "garden": "orchard"}
    )

    assert corrector.correct("yellow garden") == "Yellow Garden"


def test_no_configured_corrections_leaves_the_transcript_untouched() -> None:
    # No compiled pattern to match against -- the common case (feature
    # unused) must be a cheap, exact passthrough, not an empty-alternation
    # regex that happens to match nothing.
    assert TranscriptCorrector({}).correct("say this exactly") == "say this exactly"
    assert TranscriptCorrector(None).correct("say this exactly") == "say this exactly"


def test_config_rejects_a_correction_source_that_normalizes_to_nothing() -> None:
    # Distinct from test_config_rejects_empty_correction_target below:
    # this is the SOURCE side (the phrase to match) normalizing to
    # nothing, e.g. punctuation-only -- checked first, before the
    # replacement-side check.
    with pytest.raises(ValueError, match="source"):
        STTConfig(corrections={"...": "something"})


def test_config_rejects_empty_correction_target() -> None:
    with pytest.raises(ValueError, match="replacement"):
        STTConfig(corrections={"the green": "..."})


def test_config_rejects_duplicate_normalized_correction_sources() -> None:
    with pytest.raises(ValueError, match="normalize to the same"):
        STTConfig(corrections={"yellow-garden": "one", "yellow garden": "two"})
