from __future__ import annotations

from typing import Literal, NamedTuple

# The two-axis interrupt model from docs/DESIGN-barge-in.md: every sensible
# "what happens when the user talks over a response" pattern is a point on
# a grid, not a hand-picked list of modes. Axis 1 -- what happens to the
# assistant's CURRENT turn; Axis 2 -- what happens to the user's NEW words.
# Named presets are just the useful cells on that grid, so a user (or the
# Settings TUI) can pick a name instead of two raw axis values, while power
# users can still set the axes directly for any of the 9 combinations,
# named or not.
#
# This module is pure grid/preset logic only -- no config wiring, no
# orchestration. Deliberately scoped narrowly (same phasing discipline as
# every other primitive shipped this cycle): config.py and run_convobox.py
# consume this in a follow-up, once this piece is reviewed on its own.

OnCurrentTurn = Literal["let-finish", "mute", "abort"]
OnNewWords = Literal["drop", "queue", "now"]

_ON_CURRENT_TURN_VALUES: frozenset[str] = frozenset({"let-finish", "mute", "abort"})
_ON_NEW_WORDS_VALUES: frozenset[str] = frozenset({"drop", "queue", "now"})


class InterruptAxes(NamedTuple):
    """One point on the grid: what happens to the current turn, and to new words."""

    on_current_turn: OnCurrentTurn
    on_new_words: OnNewWords


# The named presets -- the design doc's grid table, made literal. Only the
# cells worth naming; several grid combinations (e.g. mute+drop, abort+queue)
# are real but deliberately unnamed ("odd"/"rare" per the design doc) --
# still reachable by setting the axes directly, just not given a preset name.
PRESETS: dict[str, InterruptAxes] = {
    # mute + now: stop talking over the user, steer the backend with their
    # words, but don't abort work in flight. The shipped default -- see
    # DESIGN-barge-in.md's "coding-agent nuance" for why this beats the
    # consumer-assistant reflex (take-over) as the default here.
    "conversational": InterruptAxes("mute", "now"),
    # let-finish + queue: it finishes, then does your thing.
    "patient": InterruptAxes("let-finish", "queue"),
    # let-finish + drop: ignores you until done (the safeword still works --
    # this preset only affects the interrupt trigger, never the hard-stop).
    "do-not-disturb": InterruptAxes("let-finish", "drop"),
    # abort + drop: stop everything, await a fresh command.
    "halt": InterruptAxes("abort", "drop"),
    # abort + now: stop everything and do the new thing immediately -- the
    # consumer-assistant reflex. Opt-in, not the default, for the reason
    # "conversational" exists: an agent mid-refactor can be left in a worse
    # state by an abort than a person interrupting Siri ever could be.
    "take-over": InterruptAxes("abort", "now"),
}


def resolve_preset(name: str) -> InterruptAxes:
    """The axes a named preset resolves to.

    Raises ``ValueError`` (not a KeyError) for an unknown name, with the
    valid choices listed -- config validation should surface this directly
    to the user, not a bare traceback.
    """
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(
            f"unknown interrupt preset {name!r}; choose one of "
            f"{sorted(PRESETS)}, or set on_current_turn/on_new_words directly "
            f"for a combination with no preset name"
        ) from None


def preset_name_for(on_current_turn: str, on_new_words: str) -> str | None:
    """Reverse lookup: which preset (if any) matches these exact axis values?

    None for a valid-but-unnamed combination (e.g. mute+drop) -- that's not
    an error, it just means the user (or the Settings TUI) is looking at a
    custom combination rather than one of the five named presets. Useful for
    a TUI to show "custom" instead of silently mislabeling an edited config.
    """
    axes = InterruptAxes(on_current_turn, on_new_words)  # type: ignore[arg-type]
    for name, preset_axes in PRESETS.items():
        if preset_axes == axes:
            return name
    return None


def validate_axes(on_current_turn: str, on_new_words: str) -> None:
    """Raises ``ValueError`` if either axis value isn't a real choice.

    Separate from resolve_preset(): this validates a pair of EXPLICIT axis
    values (the "sculpt any cell yourself" path), not a preset name.
    """
    if on_current_turn not in _ON_CURRENT_TURN_VALUES:
        raise ValueError(
            f"on_current_turn {on_current_turn!r} is not one of "
            f"{sorted(_ON_CURRENT_TURN_VALUES)}"
        )
    if on_new_words not in _ON_NEW_WORDS_VALUES:
        raise ValueError(
            f"on_new_words {on_new_words!r} is not one of {sorted(_ON_NEW_WORDS_VALUES)}"
        )
