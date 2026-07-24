from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from convobox.config import AppConfig
from scripts import settings_tui
from scripts.settings_tui import (
    FieldSpec,
    TuiState,
    _highlight_keys,
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

    int_spec = FieldSpec("audio", "aec_delay_ms", "AEC delay ms", "optional_int")
    assert settings_tui._parse_value(int_spec, "-", 150) is None
    assert settings_tui._parse_value(int_spec, "", 150) == 150  # empty keeps current
    assert settings_tui._parse_value(int_spec, "222", None) == 222


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
        "interrupt_preset",
        "Interrupt preset",
        "choice",
        ("do-not-disturb", "conversational", "take-over"),
    )
    keys = iter([" ", "RIGHT", "ENTER"])
    drawn: list[str] = []

    def _capture_draw(*args: object, **kwargs: object) -> None:
        drawn.append(str(args[3]))

    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    monkeypatch.setattr(settings_tui, "_draw_modal", _capture_draw)

    accepted, value = settings_tui._edit_value_interactive(spec, "do-not-disturb")
    assert accepted is True
    assert value == "take-over"
    assert drawn == ["do-not-disturb", "conversational", "take-over"]


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


def test_switching_backends_remembers_opencodes_model() -> None:
    config = _make_config(**{"backend.name": "opencode"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.working.backend.model = "openai/gpt-5.6-sol"

    settings_tui._switch_backend(state.working, "codex")
    assert state.working.backend.model is None
    assert state.working.backend_profiles["opencode"].model == "openai/gpt-5.6-sol"

    settings_tui._switch_backend(state.working, "opencode")
    assert state.working.backend.model == "openai/gpt-5.6-sol"


def test_backend_section_hides_irrelevant_field_per_backend() -> None:
    config = _make_config(**{"backend.name": "opencode"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, section in enumerate(state.sections) if section.key == "backend")
    assert [field.key for field in state.current_fields()] == ["name", "url", "model"]

    settings_tui._switch_backend(state.working, "codex")
    assert [field.key for field in state.current_fields()] == [
        "name", "command", "permission_mode", "working_dir",
    ]


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
            "tts.engine": "piper",
            "tts.voice": voice,
        }
    )
    report = validate_config(config)
    assert report.errors == []


def test_validate_config_reports_missing_voice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.engine": "piper", "tts.voice": None})
    report = validate_config(config)
    assert any("tts.voice is required" in msg for msg in report.errors)


def test_validate_config_reports_missing_kokoro_voice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": None})
    report = validate_config(config)
    assert any("tts.voice is required" in msg for msg in report.errors)


def test_validate_config_warns_when_kokoro_model_files_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(
        **{
            "tts.engine": "kokoro",
            "tts.voice": "af_sarah",
            "tts.model_path": str(tmp_path / "missing-model.onnx"),
            "tts.voices_path": str(tmp_path / "missing-voices.bin"),
        }
    )
    report = validate_config(config)
    assert report.errors == []
    assert any("tts.model_path" in msg for msg in report.warnings)
    assert any("tts.voices_path" in msg for msg in report.warnings)


# --- STT device: pick-from-list rather than free text (JP's ask: "we
# should have a chooser for cpu/gpu"). Only str kind before this. ---


def test_stt_section_exposes_device_as_a_choice_field() -> None:
    stt = next(s for s in settings_tui.SECTION_SPECS if s.key == "stt")
    spec = next((f for f in stt.fields if f.key == "device"), None)
    assert spec is not None
    assert spec.kind == "choice"
    assert set(spec.choices) == {"auto", "cpu", "cuda"}


def test_validate_config_accepts_default_stt_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_US-lessac-medium.onnx.json").write_text("{}", encoding="utf-8")
    config = _make_config(**{"tts.voice": "en_US-lessac-medium"})
    report = validate_config(config)
    assert not any("stt.device" in w for w in report.warnings)


def test_validate_config_warns_on_unrecognized_stt_device() -> None:
    # A warning, not an error -- stt.device passes straight through to
    # ctranslate2/faster-whisper, which may accept values beyond the three
    # the TUI offers (e.g. a specific GPU index); this only flags a
    # stale/typo'd value from an existing convobox.yaml.
    config = _make_config(**{"stt.device": "cuda:1"})
    report = validate_config(config)
    assert any("stt.device" in w and "cuda:1" in w for w in report.warnings)


# --- Whisper model size: pick-from-list rather than free text (JP's ask:
# "we need a chooser for the whisper model size"). Choices are pulled
# from the installed faster-whisper's own available_models(), not a
# hand-maintained duplicate. ---


def test_stt_section_exposes_model_as_a_choice_field() -> None:
    stt = next(s for s in settings_tui.SECTION_SPECS if s.key == "stt")
    spec = next((f for f in stt.fields if f.key == "model"), None)
    assert spec is not None
    assert spec.kind == "choice"
    # Exact real values from the installed faster-whisper, not a guess.
    from faster_whisper.utils import available_models
    assert set(spec.choices) == set(available_models())
    assert "base" in spec.choices  # the shipped default
    assert "large-v3" in spec.choices  # JP's specific ask


def test_validate_config_accepts_default_stt_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_US-lessac-medium.onnx.json").write_text("{}", encoding="utf-8")
    config = _make_config(**{"tts.voice": "en_US-lessac-medium"})
    report = validate_config(config)
    assert not any("stt.model" in w for w in report.warnings)


def test_validate_config_warns_on_unrecognized_stt_model() -> None:
    config = _make_config(**{"stt.model": "whisper-nonexistent-variant"})
    report = validate_config(config)
    assert any(
        "stt.model" in w and "whisper-nonexistent-variant" in w for w in report.warnings
    )


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


def test_validate_config_rejects_backend_command_with_stray_trailing_commas() -> None:
    # Live UAT incident, 2026-07-22: typing "codex.cmd, --model, gpt-5.6-terra"
    # into the Command field (following this same TUI's OTHER convention --
    # list_str fields like safeword phrases ARE comma-separated) parses via
    # shlex.split into ["codex.cmd,", "--model,", "gpt-5.6-terra"] --
    # syntactically valid-looking, silently wrong, and it saved without any
    # error. The session crashed hard mid-UAT with a bare
    # `FileNotFoundError: [WinError 2]` with nothing connecting it to the typo.
    config = _make_config(
        **{"backend.name": "codex", "backend.command": ["codex.cmd,", "--model,", "gpt-5.6-terra"]}
    )
    report = validate_config(config)
    assert any(
        "end with a comma" in e and "codex.cmd" in e and "gpt-5.6-terra" in e for e in report.errors
    )


def test_validate_config_comma_typo_error_suggests_the_fixed_command() -> None:
    config = _make_config(
        **{"backend.name": "codex", "backend.command": ["codex.cmd,", "--model,", "gpt-5.6-terra"]}
    )
    report = validate_config(config)
    error = next(e for e in report.errors if "end with a comma" in e)
    assert "['codex.cmd', '--model', 'gpt-5.6-terra']" in error


def test_validate_config_comma_typo_takes_priority_over_path_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A comma-mangled token also won't resolve via shutil.which -- the
    # specific "you likely mistyped this" error should fire instead of the
    # generic "not found on PATH" warning, not alongside it.
    monkeypatch.setattr(settings_tui.shutil, "which", lambda cmd: None)
    config = _make_config(**{"backend.name": "codex", "backend.command": ["codex.cmd,"]})
    report = validate_config(config)
    assert any("end with a comma" in e for e in report.errors)
    assert not any("not found on PATH" in w for w in report.warnings)


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


def test_save_only_writes_fields_that_actually_differ_from_defaults(tmp_path: Path) -> None:
    # The 2026-07-15 incident this guards against: a plain model_dump()
    # writes EVERY field, including ones the user never touched -- so a
    # single save silently baked a stale aec_delay_ms=100 into
    # convobox.yaml and permanently disabled AEC delay auto-tuning. Only
    # the one field actually changed here (tts.voice) should appear.
    path = tmp_path / "convobox.yaml"
    config = _make_config(**{"tts.voice": "en_US-lessac-medium"})

    save_with_backup(path, config)
    saved = path.read_text(encoding="utf-8")

    assert "voice: en_US-lessac-medium" in saved
    assert "aec_delay_ms" not in saved  # untouched -- must stay unset (None = auto-tune)
    assert "sample_rate" not in saved  # untouched -- equals the schema default


def test_save_then_reload_round_trips_to_an_identical_config(tmp_path: Path) -> None:
    from convobox.config import load_config

    path = tmp_path / "convobox.yaml"
    config = _make_config(**{"tts.voice": "en_US-lessac-medium", "audio.aec_delay_ms": 222})

    save_with_backup(path, config)
    reloaded = load_config(path)

    assert reloaded == config


def test_aec_delay_help_panel_shows_last_auto_detected_estimate(tmp_path: Path) -> None:
    from convobox.config import write_aec_estimate

    path = tmp_path / "convobox.yaml"
    write_aec_estimate(path, 222, 180.0, 32.0)
    state = TuiState(path=path, original=AppConfig(), working=AppConfig())
    spec = FieldSpec("audio", "aec_delay_ms", "AEC delay ms", "optional_int")

    lines = settings_tui._help_panel_lines(
        _state_with_field(state, spec), width=80, height=40
    )

    assert any("Last auto-detected: 222ms" in line for line in lines)


def test_aec_delay_help_panel_placeholder_when_never_measured(tmp_path: Path) -> None:
    path = tmp_path / "convobox.yaml"
    state = TuiState(path=path, original=AppConfig(), working=AppConfig())
    spec = FieldSpec("audio", "aec_delay_ms", "AEC delay ms", "optional_int")

    lines = settings_tui._help_panel_lines(
        _state_with_field(state, spec), width=80, height=40
    )

    assert any("Last auto-detected: none yet" in line for line in lines)


def _state_with_field(state: TuiState, spec: FieldSpec) -> TuiState:
    # _help_panel_lines reads state.current_field(), which is derived from
    # the section/field cursor position, not settable directly -- easier
    # to monkeypatch the lookup than to navigate the real section list.
    state.current_field = lambda: spec  # type: ignore[method-assign]
    return state


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
        "Edit Interrupt preset",
        "Editing interaction.interrupt_preset",
        ["Current: do-not-disturb", "Use Left/Right or Space to cycle choices."],
        "conversational",
        100,
        30,
        choice_options=["do-not-disturb", "conversational", "take-over"],
        choice_value="conversational",
    )
    joined = "\n".join(lines)
    assert "Options:" in joined
    assert "| > conversational" in joined


# --- key-name highlighting: live UAT feedback that a long help_text wall
# of prose (e.g. a 400+ character field help string) buried the actual
# actionable keys with no visual distinction from the surrounding
# sentence -- see _highlight_keys's own docstring. ---


def test_highlight_keys_wraps_recognized_key_names() -> None:
    result = _highlight_keys("Press Enter to accept, Esc to cancel")
    assert "\x1b[1m\x1b[36mEnter\x1b[0m" in result
    assert "\x1b[1m\x1b[36mEsc\x1b[0m" in result
    # Plain prose around the keys is untouched.
    assert "Press " in result
    assert " to accept, " in result


def test_highlight_keys_is_word_boundary_aware() -> None:
    # "Entered"/"Uploads" must not trip a highlight on the "Enter"/"Up"
    # substring -- a false positive here would color a random hostname or
    # everyday word.
    result = _highlight_keys("The value was Entered and Uploads succeeded")
    assert "\x1b[" not in result


def test_highlight_keys_leaves_plain_text_with_no_keys_unchanged() -> None:
    assert _highlight_keys("nothing actionable here") == "nothing actionable here"


def test_render_legend_bar_is_reverse_video(tmp_path: Path) -> None:
    # The bottom "Keys: ..." bar must be visually unmissable (live UAT
    # feedback: a plain-text legend line was too easy to skim past while
    # reading a long help panel) -- reverse-video, same treatment the
    # selected section tab already gets.
    config = AppConfig()
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))

    lines = render(state, 120, 30)
    legend_lines = [line for line in lines if "Keys:" in line]
    assert len(legend_lines) == 1
    assert legend_lines[0].startswith("\x1b[7m")
    assert legend_lines[0].rstrip().endswith("\x1b[0m")


def test_render_help_panel_highlights_key_names_in_field_help_text(tmp_path: Path) -> None:
    config = AppConfig()
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))
    state.selected_section = [s.key for s in state.sections].index("audio")
    fields = state.current_fields()
    state.selected_field = [f.key for f in fields].index("input_device")

    joined = "\n".join(render(state, 140, 40))
    # input_device's help_text says "Space/Left/Right cycles..." -- each
    # of those key names should be individually highlighted.
    assert "\x1b[1m\x1b[36mSpace\x1b[0m" in joined
    assert "\x1b[1m\x1b[36mLeft\x1b[0m" in joined
    assert "\x1b[1m\x1b[36mRight\x1b[0m" in joined


def test_render_modal_header_bar_is_reverse_video() -> None:
    lines = render_modal("Confirm Save", "Save changes?", [], "", 100, 30)
    header_bar = next(line for line in lines if "Esc cancel | Enter confirm" in line)
    assert header_bar.startswith("\x1b[7m")
    assert header_bar.rstrip().endswith("\x1b[0m")


def test_render_modal_footer_highlights_esc_and_enter() -> None:
    lines = render_modal("Confirm Save", "Save changes?", [], "", 100, 30)
    joined = "\n".join(lines)
    assert "\x1b[1m\x1b[36mEsc\x1b[0m cancel | \x1b[1m\x1b[36mEnter\x1b[0m accept" in joined


# --- contextual save/quit key hints: live UAT feedback that even with the
# general key-name legend (above), a user staring at a save/quit prompt had
# to infer which key applied from surrounding prose -- the relevant key
# should be called out explicitly, in brackets, right in the hint. ---


def test_highlight_keys_wraps_bracketed_single_letter_shortcuts() -> None:
    result = _highlight_keys("[S] to save, [Q] to quit and discard")
    assert "\x1b[1m\x1b[36m[S]\x1b[0m" in result
    assert "\x1b[1m\x1b[36m[Q]\x1b[0m" in result
    assert " to save, " in result


def test_highlight_keys_leaves_unbracketed_letters_alone() -> None:
    # A bare "S" or "Q" is far too common in ordinary prose to highlight --
    # only the explicit [X] bracket notation should trigger.
    result = _highlight_keys("S and Q are just letters here")
    assert "\x1b[" not in result


def test_render_header_calls_out_save_and_quit_keys_when_dirty(tmp_path: Path) -> None:
    config = AppConfig()
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))
    state.dirty = True

    header = render(state, 120, 30)[0]
    assert "\x1b[1m\x1b[36m[S]\x1b[0m to save" in header
    assert "\x1b[1m\x1b[36m[Q]\x1b[0m to quit and discard" in header


def test_render_header_omits_save_quit_hint_when_clean(tmp_path: Path) -> None:
    config = AppConfig()
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))
    state.dirty = False

    header = render(state, 120, 30)[0]
    assert "clean" in header
    assert "[S]" not in header
    assert "[Q]" not in header


# --- edit/save status messages: live UAT feedback, 2026-07-22 -- right
# after changing a value, the only feedback was "{label} updated", with no
# mention that the change is only staged until [S] is pressed. The dirty
# header says this too, but it's a separate line the operator isn't
# necessarily looking at at the exact moment they just made a change. ---


def test_toggle_or_cycle_status_names_the_save_key_when_now_dirty() -> None:
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")
    state.selected_field = next(
        i for i, f in enumerate(state.current_fields()) if f.key == "echo_cancellation"
    )

    settings_tui._toggle_or_cycle(state)

    assert state.dirty is True
    assert state.status == "Echo cancellation updated -- [S] to save"


def test_toggle_or_cycle_status_omits_save_hint_when_edit_returns_to_original() -> None:
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")
    state.selected_field = next(
        i for i, f in enumerate(state.current_fields()) if f.key == "echo_cancellation"
    )

    settings_tui._toggle_or_cycle(state)  # now dirty
    settings_tui._toggle_or_cycle(state)  # toggled back -- matches original again

    assert state.dirty is False
    assert state.status == "Echo cancellation updated"
    assert "[S]" not in state.status


def test_render_status_line_highlights_the_save_hint_same_as_the_tip_line() -> None:
    # Regression: the top "status:" line showed this exact text too but
    # was never passed through _highlight_keys, so a bracketed key
    # appeared bold+cyan at the bottom "Tip:" line and plain here.
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.status = "Echo cancellation updated -- [S] to save"

    status_line = render(state, 120, 30)[2]
    assert "\x1b[1m\x1b[36m[S]\x1b[0m" in status_line


def test_save_status_names_the_quit_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    voice = "en_US-lessac-medium"
    (tmp_path / f"{voice}.onnx").write_bytes(b"x")
    (tmp_path / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.voice": voice})
    path = tmp_path / "convobox.yaml"
    state = TuiState(path=path, original=AppConfig(), working=config)
    state.dirty = True

    monkeypatch.setattr(settings_tui, "read_key", lambda: "ENTER")  # confirm the save modal
    settings_tui._save(state)

    assert state.dirty is False
    assert state.status == f"saved to {path} -- [Q] to quit"


def test_render_modal_widens_to_fit_long_detail_lines_without_truncating() -> None:
    # Regression: box_width used to be sized off the input buffer alone, so
    # a longer detail line (like the quit-confirmation escape-hatch hint
    # below) was silently cut off mid-word by fit()'s no-wrap truncation.
    hint = "Changed your mind? Press Esc now, then [S] to save first."
    lines = render_modal(
        "Confirm Quit",
        "Discard unsaved changes and quit?",
        ["Unsaved edits will be lost if you confirm.", "", hint],
        "",
        100,
        30,
        severity="destructive",
    )
    joined = "\n".join(lines)
    assert "save first." in joined
    assert "\x1b[1m\x1b[36mEsc\x1b[0m now, then \x1b[1m\x1b[36m[S]\x1b[0m to save first." in joined


def test_handle_browse_quit_confirmation_shows_save_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig()
    state = TuiState(
        path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True)
    )
    state.dirty = True
    drawn: list[str] = []
    monkeypatch.setattr(
        settings_tui,
        "_draw_modal",
        lambda title, prompt, detail_lines, buffer="", severity="normal": drawn.extend(detail_lines),
    )
    monkeypatch.setattr(settings_tui, "read_key", lambda: "ESC")

    still_running = settings_tui._handle_browse(state, "q")

    assert still_running is True
    assert state.status == "quit cancelled"
    assert any("[S] to save first" in line for line in drawn)


# --- Audio device picker (JP asked for "same logic as
# scripts/audio_devices.py --setup" -- these tests exercise that exact
# reuse: monkeypatch audio_devices' own collect_devices/dedupe_devices/etc.
# rather than reimplementing device enumeration, then confirm settings_tui's
# lazy `import audio_devices as ad` picks up the patched functions. This
# only works because `from scripts import settings_tui` (top of this file)
# already ran settings_tui's own sys.path.insert side effect, so the bare
# `import audio_devices` below resolves to the SAME sys.modules entry
# settings_tui's runtime import will later find -- verified directly before
# writing these tests, not assumed. ---

import audio_devices  # noqa: E402 -- see the note above for why this must come after the scripts import


def _fake_device(index: int, name: str, hostapi: str = "MME") -> dict[str, object]:
    return {
        "index": index, "name": name, "hostapi": hostapi,
        "channels": 1, "samplerate": 16000, "default": index == 0,
    }


def _install_fake_sounddevice(
    monkeypatch: pytest.MonkeyPatch, **attrs: object
) -> SimpleNamespace:
    """Stand in for the real `sounddevice` module in `sys.modules`.

    `_device_choices()`/`probe_audio()` do their OWN `import sounddevice as
    sd` internally (not dependency-injected the way `audio_devices.py`'s
    functions are, which is why THOSE can just take a fake `sd` object
    directly -- see `test_audio_devices.py`'s `_fake_sd()`). A real
    `sounddevice` import raises `OSError: PortAudio library not found` on a
    machine with no PortAudio installed at the OS level -- true of this
    project's CI runner, false on the Windows dev box this feature was
    first built and tested on, which is exactly how these tests passed
    locally while genuinely failing in CI (caught live: PR #74's Tests &
    Coverage job failed with this exact OSError). Patching `sys.modules`
    (not `monkeypatch.setattr` on an already-imported module object, which
    only works if the import succeeded in the first place) makes BOTH this
    test's own `import sounddevice` and the function-under-test's internal
    one resolve to this fake, regardless of what's actually installed.
    """
    fake = SimpleNamespace(**attrs)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def test_device_choices_reuses_audio_devices_enumeration(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sounddevice(monkeypatch)
    devices = [_fake_device(0, "Mic A"), _fake_device(1, "Mic B", "WASAPI")]
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: devices)
    monkeypatch.setattr(audio_devices, "dedupe_devices", lambda devs, show_all=False: devs)

    choices = settings_tui._device_choices("input")

    assert choices == [
        settings_tui._SYSTEM_DEFAULT,
        "Mic A, MME",
        "Mic B, WASAPI",
    ]


def test_device_choices_degrades_to_default_on_enumeration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sounddevice import succeeds here (deliberately -- this test is about
    # audio_devices.collect_devices raising, e.g. a real PortAudio query
    # failure at runtime, NOT about sounddevice being uninstalled/failing
    # to import at all; that's a different failure mode, exercised by
    # simply never installing the fake and relying on the real import,
    # which every OTHER device test now avoids on purpose).
    _install_fake_sounddevice(monkeypatch)

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PortAudio not available")

    monkeypatch.setattr(audio_devices, "collect_devices", _raise)

    assert settings_tui._device_choices("output") == [settings_tui._SYSTEM_DEFAULT]


def test_choices_for_dispatches_by_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "X")])
    monkeypatch.setattr(audio_devices, "dedupe_devices", lambda devs, show_all=False: devs)

    device_spec = FieldSpec("audio", "input_device", "Input device", "device")
    assert settings_tui._choices_for(device_spec) == (settings_tui._SYSTEM_DEFAULT, "X, MME")

    choice_spec = FieldSpec("interaction", "interrupt_preset", "Preset", "choice", ("a", "b"))
    assert settings_tui._choices_for(choice_spec) == ("a", "b")

    bool_spec = FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool")
    assert settings_tui._choices_for(bool_spec) == ("false", "true")


# --- bool fields are pickable, not typed: live UAT feedback that Enter on
# a bool field opened a raw text buffer where a mistype (e.g. "flase")
# produced a bare ValueError instead of just being unselectable, 2026-07-22 ---


def test_edit_bool_field_cycles_with_space_like_a_choice_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool")
    keys = iter([" ", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))

    accepted, value = settings_tui._edit_value_interactive(spec, False)

    assert accepted is True
    assert value is True


def test_edit_bool_field_ignores_typed_keystrokes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stray printable keys must never reach the buffer for a bool field --
    # only LEFT/RIGHT/Space (cycling) and Enter/Esc are meaningful.
    spec = FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool")
    keys = iter(["f", "l", "a", "s", "e", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))

    accepted, value = settings_tui._edit_value_interactive(spec, True)

    assert accepted is True
    # Untouched by the stray keystrokes -- still the original current value.
    assert value is True


def test_toggle_or_cycle_device_field_from_unset_goes_to_first_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "Mic A")])
    monkeypatch.setattr(audio_devices, "dedupe_devices", lambda devs, show_all=False: devs)

    config = _make_config()
    assert config.audio.input_device is None
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = 0  # Audio
    state.selected_field = 0  # input_device is the first Audio field

    settings_tui._toggle_or_cycle(state)
    assert state.working.audio.input_device == "Mic A, MME"

    # Cycling again with only one real device wraps back to unset (None,
    # not the "" the picker never actually stores in the config).
    settings_tui._toggle_or_cycle(state)
    assert state.working.audio.input_device is None


def test_edit_device_field_arrow_cycle_and_enter_accepts_sentinel_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "Speaker A")])
    monkeypatch.setattr(audio_devices, "dedupe_devices", lambda devs, show_all=False: devs)
    spec = FieldSpec("audio", "output_device", "Output device", "device")

    # RIGHT once from unset (None) lands on the one real device; RIGHT
    # again wraps back to the system-default sentinel; ENTER must then
    # accept that as None, not the literal sentinel text.
    keys = iter(["RIGHT", "RIGHT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, None)

    assert accepted is True
    assert value is None


def test_edit_device_field_enter_immediately_keeps_current_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No cycling at all -- current is already a real device name, pressing
    # ENTER straight away must not accidentally clear or mangle it.
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "Speaker A")])
    monkeypatch.setattr(audio_devices, "dedupe_devices", lambda devs, show_all=False: devs)
    spec = FieldSpec("audio", "output_device", "Output device", "device")

    keys = iter(["ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, "Speaker A, MME")

    assert accepted is True
    assert value == "Speaker A, MME"


@pytest.mark.asyncio
async def test_probe_audio_reuses_audio_devices_functions_and_reports_both_directions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_collect(sd: object, kind: str) -> list[dict[str, object]]:
        return [_fake_device(0, "Speaker A" if kind == "output" else "Mic A")]

    def fake_resolve(spec: str, devices: list[dict[str, object]]) -> tuple[int | None, str | None]:
        return 0, None

    def fake_play_test_tone(sd: object, index: int, seconds: float = 1.0) -> None:
        calls.append(("play_test_tone", None))
        print("this must not leak to real stdout")  # noqa: T201 -- probe_audio must suppress it

    def fake_test_input_device(
        sd: object, index: int, seconds: float = 3.0, playback_device: int | None = None
    ) -> tuple[float, float]:
        # test_input_device (not the lower-level record_test/level_meter)
        # is what probe_audio must call now -- it's the one function that
        # actually plays the recording back, matching what
        # `audio_devices.py --setup` does (live UAT feedback, 2026-07-22:
        # the old behavior only metered the mic, never let you hear it).
        calls.append(("test_input_device", playback_device))
        print("this must not leak either")  # noqa: T201
        return -30.0, -12.0

    monkeypatch.setattr(audio_devices, "collect_devices", fake_collect)
    monkeypatch.setattr(audio_devices, "resolve_spec", fake_resolve)
    monkeypatch.setattr(audio_devices, "play_test_tone", fake_play_test_tone)
    monkeypatch.setattr(audio_devices, "test_input_device", fake_test_input_device)
    monkeypatch.setattr(audio_devices, "format_level", lambda rms, peak: f"rms={rms} peak={peak}")
    _install_fake_sounddevice(monkeypatch, query_devices=lambda index: {"name": "Speaker A"})

    config = _make_config(**{"audio.output_device": "Speaker A, MME", "audio.input_device": "Mic A, MME"})
    result = await settings_tui.probe_audio(config)

    assert [name for name, _ in calls] == ["play_test_tone", "test_input_device"]
    # The output device actually resolved must be handed to the input
    # test as its playback target, not left to whatever the system
    # default happens to be.
    assert calls[1][1] == 0
    assert "speaker OK" in result
    assert "Speaker A" in result
    assert "mic:" in result
    assert "rms=-30.0 peak=-12.0" in result
    assert "played back" in result
    # The fakes' print() calls must have been swallowed, not reached the
    # real terminal -- probe_audio redirects stdout specifically so a
    # quick [t] test doesn't flicker raw text across the render loop.
    captured = capsys.readouterr()
    assert "this must not leak" not in captured.out


# --- field_key: [t] should test only the currently selected device, not
# always both -- live UAT feedback, 2026-07-22: pressing [t] on Input
# device also played an unrelated output tone first, which read as "it's
# just playing a tone, not testing the mic" since the mic-test playback
# that followed immediately after wasn't distinctly noticed. ---


@pytest.mark.asyncio
async def test_probe_audio_input_device_field_tests_mic_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_play_test_tone(sd: object, index: int, seconds: float = 1.0) -> None:
        calls.append("play_test_tone")

    def fake_test_input_device(
        sd: object, index: int, seconds: float = 3.0, playback_device: int | None = None
    ) -> tuple[float, float]:
        calls.append("test_input_device")
        return -30.0, -12.0

    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "X")])
    monkeypatch.setattr(audio_devices, "resolve_spec", lambda spec, devices: (0, None))
    monkeypatch.setattr(audio_devices, "play_test_tone", fake_play_test_tone)
    monkeypatch.setattr(audio_devices, "test_input_device", fake_test_input_device)
    monkeypatch.setattr(audio_devices, "format_level", lambda rms, peak: f"rms={rms} peak={peak}")
    _install_fake_sounddevice(monkeypatch, query_devices=lambda index: {"name": "X"})

    config = _make_config(**{"audio.output_device": "X, MME", "audio.input_device": "X, MME"})
    result = await settings_tui.probe_audio(config, "input_device")

    assert calls == ["test_input_device"]
    assert "mic:" in result
    assert "speaker" not in result


@pytest.mark.asyncio
async def test_probe_audio_output_device_field_tests_speaker_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_play_test_tone(sd: object, index: int, seconds: float = 1.0) -> None:
        calls.append("play_test_tone")

    def fake_test_input_device(
        sd: object, index: int, seconds: float = 3.0, playback_device: int | None = None
    ) -> tuple[float, float]:
        calls.append("test_input_device")
        return -30.0, -12.0

    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "X")])
    monkeypatch.setattr(audio_devices, "resolve_spec", lambda spec, devices: (0, None))
    monkeypatch.setattr(audio_devices, "play_test_tone", fake_play_test_tone)
    monkeypatch.setattr(audio_devices, "test_input_device", fake_test_input_device)
    _install_fake_sounddevice(monkeypatch, query_devices=lambda index: {"name": "X"})

    config = _make_config(**{"audio.output_device": "X, MME", "audio.input_device": "X, MME"})
    result = await settings_tui.probe_audio(config, "output_device")

    assert calls == ["play_test_tone"]
    assert "speaker OK" in result
    assert "mic" not in result


@pytest.mark.asyncio
async def test_probe_audio_tests_both_when_no_device_field_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Other audio fields (sample_rate, echo_cancellation, aec_delay_ms) or
    # no selection at all aren't specifically about one device -- default
    # to testing both, same as before this field-aware behavior existed.
    calls: list[str] = []
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "X")])
    monkeypatch.setattr(audio_devices, "resolve_spec", lambda spec, devices: (0, None))
    monkeypatch.setattr(
        audio_devices, "play_test_tone", lambda sd, index, seconds=1.0: calls.append("play_test_tone")
    )
    monkeypatch.setattr(
        audio_devices,
        "test_input_device",
        lambda sd, index, seconds=3.0, playback_device=None: (calls.append("test_input_device"), (-30.0, -12.0))[1],
    )
    monkeypatch.setattr(audio_devices, "format_level", lambda rms, peak: f"rms={rms} peak={peak}")
    _install_fake_sounddevice(monkeypatch, query_devices=lambda index: {"name": "X"})

    config = _make_config(**{"audio.output_device": "X, MME", "audio.input_device": "X, MME"})
    result = await settings_tui.probe_audio(config, "sample_rate")

    assert calls == ["play_test_tone", "test_input_device"]
    assert "speaker OK" in result
    assert "mic:" in result


# --- _probe_input_device_live: live UAT feedback, 2026-07-23 -- the
# plain [t] test only showed a single level reading after the whole
# recording finished. A live-updating bar while actually speaking makes
# gain problems (clipping, too quiet, wrong device) easier to judge than
# one static number. Input device now records ~3s with the level shown
# live in the TUI's own status line (via draw()), not a raw terminal
# overlay; every other audio field keeps the quicker non-live probe. ---


class _FakeInputStream:
    """Stands in for sounddevice.InputStream -- fires the callback once
    with a fixed sample on __enter__, matching how the real stream would
    invoke it from its own audio thread, without needing real hardware
    or real elapsed time."""

    def __init__(self, samplerate: int, channels: int, device: int, callback: object) -> None:
        self._callback = callback

    def __enter__(self) -> "_FakeInputStream":
        indata = np.array([[0.25]], dtype=np.float32)
        self._callback(indata, 1, None, None)  # type: ignore[misc]
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_probe_input_device_live_shows_a_live_status_while_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [_fake_device(0, "Mic A")])
    monkeypatch.setattr(audio_devices, "resolve_spec", lambda spec, devices: (0, None))
    monkeypatch.setattr(audio_devices, "_default_index", lambda sd, kind: None)
    monkeypatch.setattr(audio_devices, "level_meter", lambda audio: (-18.0, -6.0))
    monkeypatch.setattr(audio_devices, "format_level", lambda rms, peak: f"rms={rms} peak={peak}")
    played_back: list[tuple[object, int, int | None]] = []
    monkeypatch.setattr(
        audio_devices,
        "_play_recording",
        lambda sd, audio, rate, playback_device: played_back.append((audio, rate, playback_device)),
    )
    _install_fake_sounddevice(
        monkeypatch,
        InputStream=_FakeInputStream,
        query_devices=lambda index: {"name": "Mic A", "default_samplerate": 16000},
    )

    drawn_statuses: list[str] = []
    monkeypatch.setattr(settings_tui, "draw", lambda state: drawn_statuses.append(state.status))

    config = _make_config(**{"audio.input_device": "Mic A, MME", "audio.output_device": None})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))

    result = await settings_tui._probe_input_device_live(state, seconds=0.05)

    assert any("recording, speak normally" in s and "rms=" in s for s in drawn_statuses)
    assert result == "mic: rms=-18.0 peak=-6.0 (played back)"
    assert len(played_back) == 1
    assert played_back[0][2] is None  # out_idx: no output_device configured, no default resolvable


@pytest.mark.asyncio
async def test_probe_input_device_live_reports_missing_device_without_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [])
    monkeypatch.setattr(
        audio_devices, "resolve_spec", lambda spec, devices: (None, f"no device matching {spec!r}")
    )
    monkeypatch.setattr(audio_devices, "_default_index", lambda sd, kind: None)
    _install_fake_sounddevice(monkeypatch)

    config = _make_config(**{"audio.input_device": "Nonexistent Mic"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))

    result = await settings_tui._probe_input_device_live(state, seconds=0.05)

    assert "no device matching" in result


@pytest.mark.asyncio
async def test_test_state_uses_live_probe_for_input_device_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = "en_US-lessac-medium"
    (tmp_path / f"{voice}.onnx").write_bytes(b"x")
    (tmp_path / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)

    async def fake_live_probe(state: TuiState, seconds: float = 3.0) -> str:
        return "live probe ran"

    async def fake_probe_audio(config: object, field_key: str | None = None) -> str:
        return "plain probe ran"

    monkeypatch.setattr(settings_tui, "_probe_input_device_live", fake_live_probe)
    monkeypatch.setattr(settings_tui, "probe_audio", fake_probe_audio)

    config = _make_config(**{"tts.voice": voice})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")
    state.selected_field = next(
        i for i, f in enumerate(state.current_fields()) if f.key == "input_device"
    )

    await settings_tui._test_state(state)
    assert state.status == "live probe ran"

    state.selected_field = next(
        i for i, f in enumerate(state.current_fields()) if f.key == "output_device"
    )
    await settings_tui._test_state(state)
    assert state.status == "plain probe ran"


@pytest.mark.asyncio
async def test_test_state_passes_the_selected_field_key_to_probe_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = "en_US-lessac-medium"
    (tmp_path / f"{voice}.onnx").write_bytes(b"x")
    (tmp_path / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)

    captured: dict[str, object] = {}

    async def fake_probe_audio(config: object, field_key: str | None = None) -> str:
        captured["field_key"] = field_key
        return "ok"

    monkeypatch.setattr(settings_tui, "probe_audio", fake_probe_audio)

    config = _make_config(**{"tts.voice": voice})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")
    # Not input_device: that field now routes to _probe_input_device_live
    # instead of probe_audio (see test_test_state_uses_live_probe_for_input_device_field).
    state.selected_field = next(
        i for i, f in enumerate(state.current_fields()) if f.key == "output_device"
    )

    await settings_tui._test_state(state)

    assert captured["field_key"] == "output_device"


@pytest.mark.asyncio
async def test_probe_audio_reports_resolution_errors_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [])
    monkeypatch.setattr(
        audio_devices, "resolve_spec", lambda spec, devices: (None, f"no device matching {spec!r}")
    )

    config = _make_config(**{"audio.output_device": "Nonexistent Device", "audio.input_device": None})
    monkeypatch.setattr(audio_devices, "_default_index", lambda sd, kind: None)

    result = await settings_tui.probe_audio(config)

    assert "no device matching" in result
    assert "mic: no device found" in result


# --- resume word: TUI-configurable, validated by the real detector ---


def test_interaction_section_exposes_resume_word_field() -> None:
    interaction = next(s for s in settings_tui.SECTION_SPECS if s.key == "interaction")
    spec = next((f for f in interaction.fields if f.key == "resume_word"), None)
    assert spec is not None
    assert spec.kind == "str"


def test_validate_config_rejects_resume_word_that_normalizes_to_nothing() -> None:
    # The real runtime constructor (ResumeWordDetector) is the validator; a
    # value it rejects would otherwise crash run_convobox.py at startup.
    config = _make_config(**{"interaction.resume_word": "!!!"})
    report = validate_config(config)
    assert any("resume_word" in error for error in report.errors)


def test_validate_config_warns_on_roundtrip_rejected_resume_word() -> None:
    # "ConvoBox" is the confirmed-broken original default (mis-transcribed
    # as "Control Box" every time) -- a warning, not an error: a user's own
    # STT stack may differ, and the detector deliberately doesn't hard-ban.
    config = _make_config(**{"interaction.resume_word": "ConvoBox"})
    report = validate_config(config)
    assert not any("resume_word" in error for error in report.errors)
    assert any("mis-transcribe" in warning for warning in report.warnings)


def test_validate_config_accepts_verified_default_resume_word() -> None:
    report = validate_config(_make_config(**{"interaction.resume_word": "Athena"}))
    assert not any("resume_word" in error for error in report.errors)
    assert not any("resume_word" in warning for warning in report.warnings)


# --- pause phrases: TUI-editable, validated like the resume word ---


def test_interaction_section_exposes_pause_phrases_field() -> None:
    interaction = next(s for s in settings_tui.SECTION_SPECS if s.key == "interaction")
    spec = next((f for f in interaction.fields if f.key == "pause_listening_phrases"), None)
    assert spec is not None
    assert spec.kind == "list_str"


def test_validate_config_warns_when_pause_phrases_empty() -> None:
    config = _make_config(**{"interaction.pause_listening_phrases": []})
    report = validate_config(config)
    assert any("pause_listening_phrases" in w for w in report.warnings)
    assert not any("pause_listening_phrases" in e for e in report.errors)


def test_validate_config_rejects_pause_phrase_that_normalizes_to_nothing() -> None:
    config = _make_config(**{"interaction.pause_listening_phrases": ["!!!"]})
    report = validate_config(config)
    assert any("pause_listening_phrases" in e for e in report.errors)


def test_validate_config_accepts_default_pause_phrases() -> None:
    report = validate_config(_make_config())
    assert not any("pause_listening_phrases" in e for e in report.errors)
    assert not any("pause_listening_phrases" in w for w in report.warnings)


# --- backend working dir: TUI-editable for subprocess backends, warned ---


def test_working_dir_field_visible_for_codex_not_opencode() -> None:
    backend = next(s for s in settings_tui.SECTION_SPECS if s.key == "backend")
    codex_fields = {
        f.key for f in settings_tui._visible_fields_for_section(
            _make_config(**{"backend.name": "codex", "backend.command": ["codex"]}), backend
        )
    }
    assert "working_dir" in codex_fields
    opencode_fields = {
        f.key for f in settings_tui._visible_fields_for_section(
            _make_config(**{"backend.name": "opencode"}), backend
        )
    }
    assert "working_dir" not in opencode_fields


def test_validate_warns_when_codex_working_dir_unset() -> None:
    config = _make_config(**{"backend.name": "codex", "backend.command": ["codex"]})
    report = validate_config(config)
    assert any("working_dir is unset" in w for w in report.warnings)


# --- permission mode + approval phrase: found live, 2026-07-20 -- JP
# looked for a way to set backend.permission_mode/interaction.
# approval_phrase in the Settings TUI and couldn't find either. Root
# cause for permission_mode specifically: the FieldSpec already existed
# in SECTION_SPECS, but _visible_fields_for_section's claude-code/codex
# whitelist never included "permission_mode" -- defined in code, invisible
# in the actual TUI. approval_phrase had no FieldSpec at all. ---


def test_permission_mode_field_visible_for_codex_not_opencode() -> None:
    backend = next(s for s in settings_tui.SECTION_SPECS if s.key == "backend")
    codex_fields = {
        f.key for f in settings_tui._visible_fields_for_section(
            _make_config(**{"backend.name": "codex", "backend.command": ["codex"]}), backend
        )
    }
    assert "permission_mode" in codex_fields
    opencode_fields = {
        f.key for f in settings_tui._visible_fields_for_section(
            _make_config(**{"backend.name": "opencode"}), backend
        )
    }
    assert "permission_mode" not in opencode_fields


def test_interaction_section_exposes_approval_phrase_and_timeout_fields() -> None:
    interaction = next(s for s in settings_tui.SECTION_SPECS if s.key == "interaction")
    phrase_spec = next((f for f in interaction.fields if f.key == "approval_phrase"), None)
    assert phrase_spec is not None
    assert phrase_spec.kind == "optional_str"
    timeout_spec = next((f for f in interaction.fields if f.key == "approval_timeout_s"), None)
    assert timeout_spec is not None
    assert timeout_spec.kind == "float"


def test_validate_config_rejects_approval_phrase_that_is_just_yes() -> None:
    # The real runtime constructor (ApprovalDetector -> ConfirmwordDetector)
    # rejects plain affirmations at construction time; re-running
    # AppConfig.model_validate() at the top of validate_config() already
    # surfaces this (same mechanism as every other field's save-time check
    # on this code path) -- no separate ApprovalDetector call needed here.
    config = _make_config(**{"interaction.approval_phrase": "yes"})
    report = validate_config(config)
    assert any("approval_phrase" in error for error in report.errors)


def test_validate_config_accepts_a_distinctive_approval_phrase() -> None:
    report = validate_config(_make_config(**{"interaction.approval_phrase": "juliette papa charlie"}))
    assert not any("approval_phrase" in e for e in report.errors)


def test_validate_warns_when_approve_mode_has_no_approval_phrase() -> None:
    config = _make_config(
        **{
            "backend.name": "codex",
            "backend.command": ["codex"],
            "backend.permission_mode": "approve",
        }
    )
    report = validate_config(config)
    assert any(
        "permission_mode is 'approve'" in w and "approval_phrase is unset" in w
        for w in report.warnings
    )


def test_validate_does_not_warn_when_approve_mode_has_an_approval_phrase() -> None:
    config = _make_config(
        **{
            "backend.name": "codex",
            "backend.command": ["codex"],
            "backend.permission_mode": "approve",
            "interaction.approval_phrase": "juliette papa charlie",
        }
    )
    report = validate_config(config)
    assert not any("approval_phrase is unset" in w for w in report.warnings)


def test_validate_rejects_permission_mode_conflicting_with_raw_command_flag() -> None:
    # detect_permission_conflict (convobox.config) is the single source of
    # truth for this check; wiring it into validate_config() means a
    # conflicting raw flag typed into backend.command is caught at TUI
    # save time too, not just at run_convobox.py startup.
    config = _make_config(
        **{
            "backend.name": "codex",
            "backend.command": ["codex", "--dangerously-bypass-approvals-and-sandbox"],
            "backend.permission_mode": "plan",
        }
    )
    report = validate_config(config)
    assert any("permission_mode" in e for e in report.errors)
