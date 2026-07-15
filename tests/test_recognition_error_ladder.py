from __future__ import annotations

import pytest

from scripts.run_convobox import RecognitionErrorLadder


def test_tier_is_zero_by_default() -> None:
    ladder = RecognitionErrorLadder()
    assert ladder.tier == 0


def test_observe_failure_increments_tier_and_returns_it() -> None:
    ladder = RecognitionErrorLadder()
    assert ladder.observe_failure() == 1
    assert ladder.tier == 1
    assert ladder.observe_failure() == 2
    assert ladder.tier == 2


def test_tier_caps_at_max_tier_instead_of_growing_unbounded() -> None:
    ladder = RecognitionErrorLadder(max_tier=3)
    for _ in range(10):
        ladder.observe_failure()
    assert ladder.tier == 3


def test_reset_clears_the_streak() -> None:
    ladder = RecognitionErrorLadder()
    ladder.observe_failure()
    ladder.observe_failure()
    assert ladder.tier == 2
    ladder.reset()
    assert ladder.tier == 0


def test_reset_while_already_at_zero_is_a_no_op() -> None:
    ladder = RecognitionErrorLadder()
    ladder.reset()
    assert ladder.tier == 0


def test_a_new_streak_after_reset_starts_from_tier_one() -> None:
    ladder = RecognitionErrorLadder()
    ladder.observe_failure()
    ladder.reset()
    assert ladder.observe_failure() == 1


def test_max_tier_must_be_at_least_one() -> None:
    with pytest.raises(ValueError):
        RecognitionErrorLadder(max_tier=0)


def test_custom_max_tier_is_respected() -> None:
    ladder = RecognitionErrorLadder(max_tier=1)
    assert ladder.observe_failure() == 1
    assert ladder.observe_failure() == 1
