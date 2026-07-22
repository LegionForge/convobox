from __future__ import annotations

import pytest

from convobox.tui.render import (
    _GREEN,
    _RED,
    _YELLOW,
    _fit,
    _heartbeat_color,
    _visible_len,
    _wrap,
    render_conversation_frame,
)
from convobox.tui.state import ConversationTuiState


def _plain(lines: list[str]) -> list[str]:
    """Strip ANSI codes for content assertions that shouldn't care about color."""
    out = []
    for line in lines:
        text = ""
        i = 0
        while i < len(line):
            if line[i] == "\x1b" and i + 1 < len(line) and line[i + 1] == "[":
                j = line.find("m", i)
                i = (j + 1) if j != -1 else len(line)
                continue
            text += line[i]
            i += 1
        out.append(text)
    return out


def test_frame_is_exactly_the_requested_size() -> None:
    state = ConversationTuiState(started=0.0)
    lines = render_conversation_frame(state, width=80, height=24, now=5.0)
    assert len(lines) == 24
    assert all(_visible_len(line) <= 80 for line in lines)


def test_frame_clamps_below_minimum_size() -> None:
    state = ConversationTuiState(started=0.0)
    lines = render_conversation_frame(state, width=1, height=1, now=0.0)
    # Clamped to the module's floor, not a crash or a degenerate 1x1 frame.
    assert len(lines) >= 16


def test_empty_transcript_shows_placeholder() -> None:
    state = ConversationTuiState(started=0.0)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert any("nothing heard yet" in line for line in lines)


def test_empty_full_detail_shows_placeholder() -> None:
    state = ConversationTuiState(started=0.0)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert any("nothing yet" in line for line in lines)


def test_turns_appear_in_order_with_speaker_labels() -> None:
    state = ConversationTuiState(started=0.0)
    state.add_turn("user", "run the tests", "12:00:01")
    state.add_turn("assistant", "running them now", "12:00:02")
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=5.0))
    you_idx = next(i for i, line in enumerate(lines) if "run the tests" in line)
    assistant_idx = next(i for i, line in enumerate(lines) if "running them now" in line)
    assert you_idx < assistant_idx
    assert "you:" in lines[you_idx]
    assert "assistant:" in lines[assistant_idx]
    assert "12:00:01" in lines[you_idx]


def test_long_turn_wraps_not_truncates() -> None:
    # The exact bug caught manually while building this: a colored label
    # prefix made the naive len()-based fit() overcount width and
    # truncate a line that actually fit. Assert the full text survives
    # across wrapped lines, nothing silently dropped or "..."-ed.
    state = ConversationTuiState(started=0.0)
    long_text = "word " * 40
    state.add_turn("assistant", long_text.strip(), "12:00:00")
    lines = _plain(render_conversation_frame(state, width=78, height=30, now=0.0))
    joined = " ".join(lines)
    assert "..." not in " ".join(
        line for line in lines if "word" in line or "assistant" in line
    )
    assert joined.count("word") == 40


def test_full_detail_preserves_paragraph_breaks() -> None:
    state = ConversationTuiState(started=0.0, full_detail="first paragraph\n\nsecond paragraph")
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    first_idx = next(i for i, line in enumerate(lines) if "first paragraph" in line)
    second_idx = next(i for i, line in enumerate(lines) if "second paragraph" in line)
    # A genuine blank line between them, not silently joined into one run.
    assert lines[first_idx + 1].strip() == ""
    assert second_idx > first_idx + 1


def test_warning_absent_by_default_reserves_no_space() -> None:
    with_warning = ConversationTuiState(started=0.0, warning="approve this?")
    without_warning = ConversationTuiState(started=0.0)
    lines_with = render_conversation_frame(with_warning, width=80, height=24, now=0.0)
    lines_without = render_conversation_frame(without_warning, width=80, height=24, now=0.0)
    assert len(lines_with) == len(lines_without) == 24
    assert any("approve this?" in line for line in _plain(lines_with))
    assert not any("approve this?" in line for line in _plain(lines_without))


def test_warning_is_bordered_and_unmissable() -> None:
    state = ConversationTuiState(started=0.0, warning="run rm -rf /tmp/x -- approve?")
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    warning_idx = next(i for i, line in enumerate(lines) if "approve?" in line)
    # Bordered top and bottom by a solid row of '!' so it can't be
    # mistaken for an ordinary transcript line.
    assert set(lines[warning_idx - 1].strip()) == {"!"}
    assert set(lines[warning_idx + 1].strip()) == {"!"}


def test_status_label_reflects_state() -> None:
    for status, label in [
        ("listening", "LISTENING"),
        ("capturing", "CAPTURING"),
        ("transcribing", "TRANSCRIBING"),
        ("working", "WORKING"),
        ("speaking", "SPEAKING"),
        ("paused", "PAUSED"),
    ]:
        state = ConversationTuiState(started=0.0, status=status)  # type: ignore[arg-type]
        lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
        assert label in lines[0]


def test_barge_in_flag_shown_only_when_active() -> None:
    active = ConversationTuiState(started=0.0, barge_in_active=True)
    inactive = ConversationTuiState(started=0.0, barge_in_active=False)
    active_line = _plain(render_conversation_frame(active, width=80, height=24, now=0.0))[0]
    inactive_line = _plain(render_conversation_frame(inactive, width=80, height=24, now=0.0))[0]
    assert "BARGE-IN" in active_line
    assert "BARGE-IN" not in inactive_line


# --- diagnostics line: backend name, AEC status, heartbeat ---


def test_diagnostics_line_shows_backend_name() -> None:
    state = ConversationTuiState(started=0.0, backend_name="claude-code")
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "backend: claude-code" in lines[1]


def test_diagnostics_line_shows_aec_off_by_default() -> None:
    state = ConversationTuiState(started=0.0)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "AEC: off" in lines[1]


def test_diagnostics_line_shows_aec_on_with_no_verdict_yet() -> None:
    # AEC enabled but no response has finished yet -- aec_verdict is still
    # "", so no tag should appear, but "AEC: on" must.
    state = ConversationTuiState(started=0.0, aec_enabled=True)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "AEC: on" in lines[1]


def test_diagnostics_line_shows_aec_verdict_tag() -> None:
    state = ConversationTuiState(
        started=0.0,
        aec_enabled=True,
        aec_verdict="  [FLOOR-LIMITED: echo cancelled down to room noise -- success]",
    )
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "AEC: on FLOOR-LIMITED" in lines[1]
    # The full explanation text must NOT leak into the fixed-width status
    # line -- that's what the log line is for, this is a compact tag.
    assert "success" not in lines[1]


def test_diagnostics_line_omits_heartbeat_when_not_silently_busy() -> None:
    state = ConversationTuiState(started=0.0, heartbeat_elapsed_s=None)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "still working" not in lines[1]


def test_diagnostics_line_shows_heartbeat_elapsed_seconds() -> None:
    state = ConversationTuiState(started=0.0, heartbeat_elapsed_s=42.0)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "still working: 42s" in lines[1]


def test_diagnostics_line_omits_mic_level_before_first_chunk() -> None:
    state = ConversationTuiState(started=0.0, mic_level_db=None)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "dBFS" not in lines[1]


def test_diagnostics_line_shows_mic_level() -> None:
    state = ConversationTuiState(started=0.0, mic_level_db=-42.3)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "mic: -42dBFS" in lines[1]


def test_diagnostics_line_omits_rec_tag_by_default() -> None:
    state = ConversationTuiState(started=0.0)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "REC" not in lines[1]


def test_diagnostics_line_shows_rec_tag_with_elapsed_seconds_when_dumping() -> None:
    state = ConversationTuiState(started=0.0, aec_dump_active=True, aec_dump_frames=300)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert "REC 3s" in lines[1]  # 300 frames * 10ms/frame = 3.0s


def test_heartbeat_color_thresholds_match_run_convobox_pys_own() -> None:
    # Mirrors scripts/run_convobox.py's _heartbeat_color boundary tests
    # (test_barge_in.py) -- must stay in sync with that copy.
    assert _heartbeat_color(9.9) == _GREEN
    assert _heartbeat_color(10.0) == _YELLOW
    assert _heartbeat_color(59.9) == _YELLOW
    assert _heartbeat_color(60.0) == _RED
    assert _heartbeat_color(600.0) == _RED


def test_elapsed_time_formats_minutes_and_seconds() -> None:
    state = ConversationTuiState(started=0.0)
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=125.0))
    assert "elapsed 02:05" in lines[0]


def test_transcript_scrolls_to_most_recent_when_overflowing() -> None:
    state = ConversationTuiState(started=0.0)
    for i in range(50):
        state.add_turn("user", f"message {i}", f"12:00:{i:02d}")
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    joined = "\n".join(lines)
    assert "message 49" in joined  # most recent must be visible
    assert "message 0 " not in joined  # earliest scrolled off


# --- scrollable panes: transcript_scroll / detail_scroll / focus_pane ---


def test_transcript_scroll_offset_reveals_older_lines() -> None:
    state = ConversationTuiState(started=0.0)
    for i in range(50):
        state.add_turn("user", f"message {i}", f"12:00:{i:02d}")
    live = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert not any("message 0 " in line for line in live)

    state.transcript_scroll = 1000  # far more than the real history -- must clamp, not crash
    scrolled_to_top = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert any("message 0 " in line for line in scrolled_to_top)
    # Scrolled all the way up -- the very latest message is off the top of the pane.
    assert not any("message 49" in line for line in scrolled_to_top)


def test_detail_scroll_offset_reveals_earlier_paragraphs() -> None:
    paragraphs = "\n\n".join(f"paragraph {i}" for i in range(30))
    state = ConversationTuiState(started=0.0, full_detail=paragraphs)
    live = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert not any("paragraph 0" == line.strip() for line in live)

    state.detail_scroll = 1000
    scrolled = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert any("paragraph 0" == line.strip() for line in scrolled)


def test_scroll_offset_beyond_available_history_clamps_without_blank_window() -> None:
    # A stale offset (left over from a wider frame, or content that shrank)
    # must never produce an out-of-range slice or an all-blank pane.
    state = ConversationTuiState(started=0.0, full_detail="only one short line")
    state.detail_scroll = 999_999
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    assert any("only one short line" in line for line in lines)


def test_focus_pane_marker_shown_on_focused_pane_only() -> None:
    transcript_focused = ConversationTuiState(started=0.0, focus_pane="transcript")
    detail_focused = ConversationTuiState(started=0.0, focus_pane="detail")
    t_lines = render_conversation_frame(transcript_focused, width=80, height=24, now=0.0)
    d_lines = render_conversation_frame(detail_focused, width=80, height=24, now=0.0)
    transcript_header_t = next(line for line in t_lines if "Transcript" in line)
    transcript_header_d = next(line for line in d_lines if "Transcript" in line)
    assert "▸" in transcript_header_t
    assert "▸" not in transcript_header_d


def test_scrolled_pane_shows_scrolled_hint() -> None:
    state = ConversationTuiState(started=0.0, full_detail="\n\n".join(f"p{i}" for i in range(30)))
    state.detail_scroll = 5
    lines = _plain(render_conversation_frame(state, width=80, height=24, now=0.0))
    detail_header = next(line for line in lines if "Full response" in line)
    assert "scrolled" in detail_header


def test_wrap_preserves_all_words() -> None:
    text = " ".join(f"w{i}" for i in range(20))
    wrapped = _wrap(text, width=20)
    assert " ".join(wrapped).split() == text.split()
    assert all(len(line) <= 20 for line in wrapped)


def test_wrap_empty_paragraph_becomes_blank_line() -> None:
    assert _wrap("a\n\nb", width=10) == ["a", "", "b"]


def test_fit_pads_short_ansi_text_to_visible_width() -> None:
    colored = "\x1b[31mhi\x1b[0m"
    fitted = _fit(colored, 10)
    assert _visible_len(fitted) == 10


def test_fit_does_not_truncate_colored_text_that_visually_fits() -> None:
    # Regression for the exact bug found manually: a naive len()-based fit
    # truncated a colored "assistant:" label even though its VISIBLE
    # length was well within the requested width.
    colored = "\x1b[36myou:\x1b[0m short line"
    fitted = _fit(colored, 40)
    assert "..." not in fitted
    assert "short line" in fitted


def test_fit_truncates_when_visible_text_actually_overflows() -> None:
    fitted = _fit("this is definitely too long for ten", 10)
    assert _visible_len(fitted) == 10
    assert fitted.endswith("...")


def test_update_mic_level_takes_first_reading_as_is() -> None:
    state = ConversationTuiState()
    state.update_mic_level(-30.0)
    assert state.mic_level_db == -30.0


def test_update_mic_level_jumps_immediately_to_a_louder_reading() -> None:
    state = ConversationTuiState(mic_level_db=-50.0)
    state.update_mic_level(-20.0)
    assert state.mic_level_db == -20.0


def test_update_mic_level_eases_toward_a_quieter_reading() -> None:
    state = ConversationTuiState(mic_level_db=-20.0)
    state.update_mic_level(-50.0)
    # Partway there, not all the way (decay, not an instant drop).
    assert -50.0 < state.mic_level_db < -20.0


def test_update_mic_level_converges_to_a_sustained_quieter_reading() -> None:
    state = ConversationTuiState(mic_level_db=-20.0)
    for _ in range(50):
        state.update_mic_level(-50.0)
    assert state.mic_level_db == pytest.approx(-50.0, abs=0.1)
