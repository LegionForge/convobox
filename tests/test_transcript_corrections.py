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


def test_config_rejects_empty_correction_target() -> None:
    with pytest.raises(ValueError, match="replacement"):
        STTConfig(corrections={"the green": "..."})


def test_config_rejects_duplicate_normalized_correction_sources() -> None:
    with pytest.raises(ValueError, match="normalize to the same"):
        STTConfig(corrections={"yellow-garden": "one", "yellow garden": "two"})
