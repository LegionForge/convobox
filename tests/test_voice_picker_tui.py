from __future__ import annotations

import re
from pathlib import Path

from scripts.voice_picker_tui import TuiState, fit, render, viewport_start, voice_row

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

CATALOG = {
    f"aa_AA-voice{i:02d}-low": {
        "language": {"name_english": "Testish", "country_english": "Testland", "code": "aa_AA"},
        "quality": "low",
        "num_speakers": 1,
    }
    for i in range(50)
}


def _state(tmp_path: Path, **overrides: object) -> TuiState:
    state = TuiState(catalog=CATALOG, voices_dir=tmp_path)
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def _visible(line: str) -> str:
    return _ANSI_RE.sub("", line)


# --- fit ---


def test_fit_pads_short_text_to_width() -> None:
    assert fit("ab", 5) == "ab   "


def test_fit_truncates_long_text_with_ellipsis() -> None:
    assert fit("abcdefghij", 8) == "abcde..."
    assert len(fit("abcdefghij", 8)) == 8


def test_fit_zero_width_is_empty() -> None:
    assert fit("abc", 0) == ""


# --- viewport ---


def test_viewport_stays_put_when_selection_visible() -> None:
    assert viewport_start(selected=5, total=50, height=10, current_start=3) == 3


def test_viewport_scrolls_down_to_reveal_selection() -> None:
    assert viewport_start(selected=15, total=50, height=10, current_start=0) == 6


def test_viewport_scrolls_up_to_reveal_selection() -> None:
    assert viewport_start(selected=2, total=50, height=10, current_start=5) == 2


def test_viewport_zero_when_everything_fits() -> None:
    assert viewport_start(selected=3, total=5, height=10, current_start=0) == 0


def test_viewport_never_overshoots_the_end() -> None:
    assert viewport_start(selected=49, total=50, height=10, current_start=0) == 40


# --- render geometry ---


def test_render_produces_exactly_height_lines(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for width, height in ((80, 24), (120, 40), (40, 12), (200, 60)):
        lines = render(state, width, height)
        assert len(lines) == height


def test_render_lines_never_exceed_width(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for width, height in ((80, 24), (43, 13), (161, 51)):
        for line in render(state, width, height):
            assert len(_visible(line)) <= width


def test_render_is_pure_ascii_plus_ansi(tmp_path: Path) -> None:
    # The whole point of the design: renders on any display, no special
    # fonts. Everything outside ANSI escape codes must be printable ASCII.
    state = _state(tmp_path)
    for line in render(state, 100, 30):
        for ch in _visible(line):
            assert 0x20 <= ord(ch) <= 0x7E, f"non-ASCII character {ch!r} in {line!r}"


def test_render_marks_selected_row_with_reverse_video(tmp_path: Path) -> None:
    state = _state(tmp_path, selected=4)
    lines = render(state, 100, 30)
    selected_lines = [ln for ln in lines if "\x1b[7m" in ln]
    assert len(selected_lines) == 1
    assert "voice04" in _visible(selected_lines[0])
    assert _visible(selected_lines[0]).startswith(">")


def test_render_marks_installed_voices(tmp_path: Path) -> None:
    (tmp_path / "aa_AA-voice00-low.onnx").write_bytes(b"x")
    state = TuiState(catalog=CATALOG, voices_dir=tmp_path)
    top_row = _visible(render(state, 100, 30)[3])  # first list row
    assert "*" in top_row and "voice00" in top_row


def test_render_scrolls_to_keep_selection_on_screen(tmp_path: Path) -> None:
    state = _state(tmp_path, selected=45)
    lines = render(state, 100, 20)
    joined = _visible("\n".join(lines))
    assert "voice45" in joined
    assert "voice00" not in joined  # scrolled past the top


def test_render_small_terminal_does_not_crash(tmp_path: Path) -> None:
    state = _state(tmp_path)
    lines = render(state, 10, 5)  # below minimums: clamped, not crashed
    assert lines


def test_filter_narrows_and_status_line_reflects_it(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.filter_text = "voice07"
    state.apply_filter()
    assert state.filtered == ["aa_AA-voice07-low"]
    joined = _visible("\n".join(render(state, 100, 24)))
    assert "1/50 voices" in joined


# --- voice_row ---


def test_voice_row_contains_key_language_and_quality(tmp_path: Path) -> None:
    row = voice_row(CATALOG, "aa_AA-voice01-low", installed=False, width=100)
    assert "aa_AA-voice01-low" in row
    assert "Testish (Testland)" in row
    assert "low" in row


def test_voice_row_multi_speaker_note() -> None:
    catalog = {
        "bb_BB-multi-high": {
            "language": {"name_english": "Testish", "country_english": "Testland", "code": "bb"},
            "quality": "high",
            "num_speakers": 12,
        }
    }
    assert "12spk" in voice_row(catalog, "bb_BB-multi-high", installed=False, width=100)
