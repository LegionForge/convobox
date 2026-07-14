from __future__ import annotations

import pytest

from convobox.interrupt_presets import (
    PRESETS,
    InterruptAxes,
    preset_name_for,
    resolve_preset,
    validate_axes,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("conversational", InterruptAxes("mute", "now")),
        ("patient", InterruptAxes("let-finish", "queue")),
        ("do-not-disturb", InterruptAxes("let-finish", "drop")),
        ("halt", InterruptAxes("abort", "drop")),
        ("take-over", InterruptAxes("abort", "now")),
    ],
)
def test_resolve_preset(name: str, expected: InterruptAxes) -> None:
    assert resolve_preset(name) == expected


def test_resolve_preset_unknown_name_raises_with_choices_listed() -> None:
    with pytest.raises(ValueError, match="unknown interrupt preset") as exc_info:
        resolve_preset("aggressive")
    # every real preset name should be listed in the error, so a user (or a
    # TUI showing the raw message) sees the actual valid choices
    for name in PRESETS:
        assert name in str(exc_info.value)


@pytest.mark.parametrize("name", list(PRESETS))
def test_preset_name_for_round_trips_every_preset(name: str) -> None:
    axes = PRESETS[name]
    assert preset_name_for(axes.on_current_turn, axes.on_new_words) == name


def test_preset_name_for_unnamed_combination_returns_none() -> None:
    # mute+drop is a real, valid grid cell -- just not one of the five named
    # presets (docs/DESIGN-barge-in.md calls this cell "(odd)").
    assert preset_name_for("mute", "drop") is None


def test_all_five_presets_are_distinct() -> None:
    # no two preset names should resolve to the same axes -- if they did,
    # preset_name_for's reverse lookup would be ambiguous.
    assert len(set(PRESETS.values())) == len(PRESETS)


@pytest.mark.parametrize(
    "current,words",
    [("let-finish", "drop"), ("mute", "now"), ("abort", "queue")],
)
def test_validate_axes_accepts_real_values(current: str, words: str) -> None:
    validate_axes(current, words)  # must not raise


def test_validate_axes_rejects_bad_on_current_turn() -> None:
    with pytest.raises(ValueError, match="on_current_turn"):
        validate_axes("pause", "now")


def test_validate_axes_rejects_bad_on_new_words() -> None:
    with pytest.raises(ValueError, match="on_new_words"):
        validate_axes("mute", "discard")


def test_every_preset_axes_pass_validation() -> None:
    # internal consistency guard: PRESETS must never contain a combination
    # validate_axes would itself reject.
    for axes in PRESETS.values():
        validate_axes(axes.on_current_turn, axes.on_new_words)
