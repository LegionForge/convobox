from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from convobox.tui.state import ConversationTuiState
from scripts.run_convobox import _handle_tui_key, _read_pending_key


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


# --- _read_pending_key: the raw Windows (msvcrt) key read Ctrl+C fix,
# confirmed live (JP, 2026-07-28) -- Ctrl+C did nothing during a --tui
# session. A fake msvcrt module is injected into sys.modules so this runs
# identically on the real Windows dev machine (temporarily shadowing the
# real module) and on a non-Windows CI runner (where msvcrt doesn't exist
# at all to import). ---


def _install_fake_msvcrt(monkeypatch: pytest.MonkeyPatch, key: str, kbhit: bool = True) -> MagicMock:
    fake = types.ModuleType("msvcrt")
    fake.kbhit = MagicMock(return_value=kbhit)  # type: ignore[attr-defined]
    fake.getwch = MagicMock(return_value=key)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return fake


def test_read_pending_key_ctrl_c_sends_ctrl_c_event_to_the_console_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_msvcrt(monkeypatch, "\x03")
    kill = MagicMock()
    monkeypatch.setattr("os.kill", kill)
    monkeypatch.setattr("signal.CTRL_C_EVENT", 0, raising=False)

    result = _read_pending_key()

    assert result is None
    # pid=0 (the whole console process group), not os.getpid() -- see the
    # function's own comment for why os.getpid() would silently fail to
    # deliver the event in the common case (a python.exe launched as a
    # shell's child is not its own process group leader).
    kill.assert_called_once_with(0, 0)


def test_read_pending_key_ordinary_key_does_not_send_ctrl_c_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_msvcrt(monkeypatch, "\t")
    kill = MagicMock()
    monkeypatch.setattr("os.kill", kill)

    result = _read_pending_key()

    assert result == "TAB"
    kill.assert_not_called()


def test_read_pending_key_returns_none_without_reading_when_no_key_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_msvcrt(monkeypatch, "\x03", kbhit=False)

    result = _read_pending_key()

    assert result is None
    fake.getwch.assert_not_called()
