from __future__ import annotations

from convobox.tui.state import ConversationTuiState
from scripts.run_convobox import _handle_tui_key


def test_tab_switches_focus_pane() -> None:
    state = ConversationTuiState()
    assert state.focus_pane == "detail"
    _handle_tui_key(state, "TAB")
    assert state.focus_pane == "transcript"
    _handle_tui_key(state, "TAB")
    assert state.focus_pane == "detail"


def test_up_down_scroll_the_focused_pane_by_one_line() -> None:
    state = ConversationTuiState(focus_pane="detail")
    _handle_tui_key(state, "UP")
    assert state.detail_scroll == 1
    _handle_tui_key(state, "UP")
    assert state.detail_scroll == 2
    _handle_tui_key(state, "DOWN")
    assert state.detail_scroll == 1


def test_scroll_never_goes_negative() -> None:
    state = ConversationTuiState(focus_pane="detail", detail_scroll=0)
    _handle_tui_key(state, "DOWN")
    assert state.detail_scroll == 0


def test_pgup_pgdn_scroll_by_a_full_page() -> None:
    state = ConversationTuiState(focus_pane="detail")
    _handle_tui_key(state, "PGUP")
    assert state.detail_scroll == 10
    _handle_tui_key(state, "PGDN")
    assert state.detail_scroll == 0


def test_home_jumps_to_a_large_offset_end_returns_to_live() -> None:
    state = ConversationTuiState(focus_pane="detail")
    _handle_tui_key(state, "HOME")
    assert state.detail_scroll > 1000
    _handle_tui_key(state, "END")
    assert state.detail_scroll == 0


def test_scroll_keys_apply_to_transcript_when_transcript_focused() -> None:
    state = ConversationTuiState(focus_pane="transcript")
    _handle_tui_key(state, "PGUP")
    assert state.transcript_scroll == 10
    assert state.detail_scroll == 0


def test_unknown_key_is_ignored() -> None:
    state = ConversationTuiState()
    _handle_tui_key(state, "Q")
    assert state.detail_scroll == 0
    assert state.transcript_scroll == 0
    assert state.focus_pane == "detail"
