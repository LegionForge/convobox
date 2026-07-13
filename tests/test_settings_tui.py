from __future__ import annotations

from pathlib import Path

import pytest

from convobox.config import AppConfig
from scripts import settings_tui
from scripts.settings_tui import (
    FieldSpec,
    TuiState,
    backup_config,
    render,
    render_modal,
    save_with_backup,
    validate_config,
)


def _make_config(**updates: object) -> AppConfig:
    config = AppConfig()
    for dotted, value in updates.items():
        section, key = dotted.split(".", 1)
        setattr(getattr(config, section), key, value)
    return config


def test_parse_optional_and_list_values() -> None:
    spec = FieldSpec("audio", "input_device", "Input device", "optional_str")
    assert settings_tui._parse_value(spec, "-", "x") is None
    assert settings_tui._parse_value(spec, "", "x") == "x"

    list_spec = FieldSpec("safeword", "hard_stop_phrases", "Hard stop phrases", "list_str")
    assert settings_tui._parse_value(list_spec, "stop stop stop, mayday", []) == [
        "stop stop stop",
        "mayday",
    ]
    assert settings_tui._parse_value(list_spec, "-", ["x"]) == []

    cmd_spec = FieldSpec("backend", "command", "Command", "command")
    assert settings_tui._parse_value(cmd_spec, "claude --model x", None) == [
        "claude",
        "--model",
        "x",
    ]
    assert settings_tui._parse_value(cmd_spec, "-", ["claude"]) is None

    float_spec = FieldSpec("vad", "max_utterance_s", "Max utterance s", "optional_float")
    assert settings_tui._parse_value(float_spec, "-", 12.0) is None
    assert settings_tui._parse_value(float_spec, "17.5", None) == 17.5


def test_modal_edit_can_cancel_with_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec("safeword", "hard_stop_phrases", "Hard stop phrases", "list_str")
    keys = iter(["a", "b", "ESC"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, ["stop stop stop"])
    assert accepted is False
    assert value == ["stop stop stop"]


def test_modal_edit_accepts_value_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec("audio", "input_device", "Input device", "optional_str")
    keys = iter(["h", "i", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, "")
    assert accepted is True
    assert value == "hi"


def test_modal_choice_edit_cycles_with_space_and_arrow(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec(
        "interaction",
        "interrupt_mode",
        "Interrupt mode",
        "choice",
        ("none", "stop_audio", "abort_turn"),
    )
    keys = iter([" ", "RIGHT", "ENTER"])
    drawn: list[str] = []

    def _capture_draw(*args: object, **kwargs: object) -> None:
        drawn.append(str(args[3]))

    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    monkeypatch.setattr(settings_tui, "_draw_modal", _capture_draw)

    accepted, value = settings_tui._edit_value_interactive(spec, "none")
    assert accepted is True
    assert value == "abort_turn"
    assert drawn == ["none", "stop_audio", "abort_turn"]


def test_switching_backends_remembers_backend_specific_values() -> None:
    config = _make_config(
        **{
            "backend.name": "opencode",
            "backend.url": "http://localhost:7777",
        }
    )
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))

    settings_tui._switch_backend(state.working, "codex")
    assert state.working.backend.name == "codex"
    assert state.working.backend.command == ["codex"]
    assert state.working.backend_profiles["opencode"].url == "http://localhost:7777"

    state.working.backend.command = ["codex", "--model", "gpt-5"]
    settings_tui._switch_backend(state.working, "claude-code")
    assert state.working.backend.name == "claude-code"
    assert state.working.backend.command == ["claude"]
    assert state.working.backend_profiles["codex"].command == ["codex", "--model", "gpt-5"]

    settings_tui._switch_backend(state.working, "codex")
    assert state.working.backend.name == "codex"
    assert state.working.backend.command == ["codex", "--model", "gpt-5"]
    assert state.working.backend.url == "http://localhost:4096"


def test_backend_section_hides_irrelevant_field_per_backend() -> None:
    config = _make_config(**{"backend.name": "opencode"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, section in enumerate(state.sections) if section.key == "backend")
    assert [field.key for field in state.current_fields()] == ["name", "url"]

    settings_tui._switch_backend(state.working, "codex")
    assert [field.key for field in state.current_fields()] == ["name", "command"]


def test_backend_help_mentions_per_backend_memory() -> None:
    config = _make_config(**{"backend.name": "codex"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, section in enumerate(state.sections) if section.key == "backend")
    state.selected_field = 0
    help_lines = settings_tui._help_panel_lines(state, 80, 20)
    joined = "\n".join(help_lines)
    assert "Backend profiles are remembered per backend" in joined


def test_confirm_modal_cancels_on_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = iter(["ESC"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    assert settings_tui._confirm_modal("Confirm", "Proceed?", ["detail"]) is False


def test_validate_config_passes_when_voice_files_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    voice = "en_US-lessac-medium"
    (tmp_path / f"{voice}.onnx").write_bytes(b"x")
    (tmp_path / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)

    config = _make_config(
        **{
            "tts.voice": voice,
        }
    )
    report = validate_config(config)
    assert report.errors == []


def test_validate_config_reports_missing_voice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    report = validate_config(AppConfig())
    assert any("tts.voice is required" in msg for msg in report.errors)


def test_validate_config_warns_when_backend_command_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The exact surprise from UAT: a schema-valid codex config that can't
    # actually launch. The dependency check must flag it at save time.
    monkeypatch.setattr(settings_tui.shutil, "which", lambda cmd: None)
    config = _make_config(**{"backend.name": "codex", "backend.command": ["codex"]})
    report = validate_config(config)
    assert any("not found on PATH" in w and "codex" in w for w in report.warnings)


def test_validate_config_no_backend_warning_when_command_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui.shutil, "which", lambda cmd: f"C:/bin/{cmd}.cmd")
    config = _make_config(**{"backend.name": "claude-code", "backend.command": ["claude"]})
    report = validate_config(config)
    assert not any("not found on PATH" in w for w in report.warnings)


def test_validate_config_skips_path_check_for_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    # opencode is HTTP, not a spawned CLI -- the PATH check must not apply.
    consulted: list[str] = []
    monkeypatch.setattr(settings_tui.shutil, "which", lambda cmd: consulted.append(cmd) or None)
    validate_config(_make_config(**{"backend.name": "opencode"}))
    assert consulted == []


def test_backup_and_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "convobox.yaml"
    path.write_text("backend:\n  name: opencode\n", encoding="utf-8")
    config = _make_config(**{"tts.voice": "en_US-lessac-medium"})

    backup = save_with_backup(path, config)

    assert backup is not None
    assert backup.exists()
    saved = path.read_text(encoding="utf-8")
    assert "tts:" in saved
    assert "voice: en_US-lessac-medium" in saved


def test_save_with_backup_restores_original_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "convobox.yaml"
    original = "backend:\n  name: opencode\n"
    path.write_text(original, encoding="utf-8")
    config = _make_config(**{"tts.voice": "en_US-lessac-medium"})

    def _bad_write(target: Path, cfg: AppConfig) -> None:
        target.write_text("corrupted\n", encoding="utf-8")
        raise RuntimeError("boom")

    monkeypatch.setattr(settings_tui, "write_config", _bad_write)

    with pytest.raises(RuntimeError, match="boom"):
        save_with_backup(path, config)

    assert path.read_text(encoding="utf-8") == original


def test_backup_config_returns_none_for_new_file(tmp_path: Path) -> None:
    assert backup_config(tmp_path / "missing.yaml") is None


def test_render_includes_sections_and_dirty_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    voice = "en_US-lessac-medium"
    (tmp_path / f"{voice}.onnx").write_bytes(b"x")
    (tmp_path / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    config = _make_config(**{"tts.voice": voice})
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))
    state.dirty = True

    lines = render(state, 100, 30)
    joined = "\n".join(lines)
    assert "ConvoBox Settings TUI" in joined
    assert "dirty" in joined
    assert "TTS" in joined


def test_render_modal_uses_same_chrome() -> None:
    lines = render_modal(
        "Confirm Save",
        "Save changes?",
        ["This writes a backup first."],
        "",
        100,
        30,
    )
    joined = "\n".join(lines)
    assert "ConvoBox Settings TUI" in joined
    assert "Confirm Save" in joined
    assert "Esc cancel | Enter confirm" in joined


def test_render_modal_marks_destructive_actions_more_strongly() -> None:
    lines = render_modal(
        "Confirm Revert",
        "Revert staged changes?",
        ["This cannot be undone."],
        "",
        100,
        30,
        severity="destructive",
    )
    joined = "\n".join(lines)
    assert "DANGER" in joined
    assert "Esc back out carefully | Enter confirm" in joined
    assert "=" in joined


def test_render_modal_shows_choice_selector() -> None:
    lines = render_modal(
        "Edit Interrupt mode",
        "Editing interaction.interrupt_mode",
        ["Current: none", "Use Left/Right or Space to cycle choices."],
        "stop_audio",
        100,
        30,
        choice_options=["none", "stop_audio", "abort_turn"],
        choice_value="stop_audio",
    )
    joined = "\n".join(lines)
    assert "Options:" in joined
    assert "| > stop_audio" in joined
    assert "|   none" in joined
