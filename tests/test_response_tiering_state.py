from __future__ import annotations

from convobox.response_tiering import ResponseTierState, split_tiers


def test_split_tiers_single_paragraph_is_one_tier() -> None:
    assert split_tiers("just one short sentence.") == ["just one short sentence."]


def test_split_tiers_splits_on_blank_lines() -> None:
    text = "first paragraph.\n\nsecond paragraph.\n\nthird paragraph."
    assert split_tiers(text) == ["first paragraph.", "second paragraph.", "third paragraph."]


def test_split_tiers_strips_whitespace_per_paragraph() -> None:
    text = "  first.  \n\n  second.  "
    assert split_tiers(text) == ["first.", "second."]


def test_split_tiers_drops_empty_paragraphs_from_extra_blank_lines() -> None:
    text = "first.\n\n\n\nsecond."
    assert split_tiers(text) == ["first.", "second."]


def test_split_tiers_empty_text_is_empty_list() -> None:
    assert split_tiers("") == []
    assert split_tiers("   ") == []


def test_state_start_returns_first_tier() -> None:
    state = ResponseTierState()
    first = state.start("first paragraph.\n\nsecond paragraph.")
    assert first == "first paragraph."


def test_state_has_more_true_for_multi_paragraph_response() -> None:
    state = ResponseTierState()
    state.start("first.\n\nsecond.")
    assert state.has_more() is True


def test_state_has_more_false_for_single_paragraph_response() -> None:
    # The common case: a short, single-paragraph reply has nothing held
    # back -- tier 0 already IS the whole response.
    state = ResponseTierState()
    state.start("just one short reply.")
    assert state.has_more() is False


def test_state_reveal_more_returns_the_next_tier_in_order() -> None:
    state = ResponseTierState()
    state.start("first.\n\nsecond.\n\nthird.")
    assert state.reveal_more() == "second."
    assert state.reveal_more() == "third."


def test_state_reveal_more_returns_none_once_exhausted() -> None:
    state = ResponseTierState()
    state.start("first.\n\nsecond.")
    assert state.reveal_more() == "second."
    assert state.reveal_more() is None
    assert state.has_more() is False


def test_state_reveal_more_on_single_tier_response_is_none_immediately() -> None:
    state = ResponseTierState()
    state.start("only one paragraph here.")
    assert state.reveal_more() is None


def test_state_start_again_resets_and_discards_old_remaining_tiers() -> None:
    # A new response replaces whatever was being tiered before -- the old
    # response's remaining tiers are moot once a new one exists, same as
    # the TUI's full-detail pane resetting per-turn.
    state = ResponseTierState()
    state.start("old first.\n\nold second.\n\nold third.")
    assert state.has_more() is True

    second_response_first = state.start("new first.\n\nnew second.")
    assert second_response_first == "new first."
    assert state.reveal_more() == "new second."
    assert state.reveal_more() is None


def test_state_empty_response_has_nothing_to_reveal() -> None:
    state = ResponseTierState()
    first = state.start("")
    assert first == ""
    assert state.has_more() is False
    assert state.reveal_more() is None
