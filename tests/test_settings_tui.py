from __future__ import annotations

import asyncio
import io
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import numpy as np
import pytest

from convobox.config import AppConfig
from convobox.stt.base import TranscriptResult
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
    accepted, value = settings_tui._edit_value_interactive(spec, ["stop stop stop"], AppConfig())
    assert accepted is False
    assert value == ["stop stop stop"]


def test_modal_edit_accepts_value_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec("audio", "input_device", "Input device", "optional_str")
    keys = iter(["h", "i", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, "", AppConfig())
    assert accepted is True
    assert value == "hi"


def test_modal_command_edit_unchanged_does_not_corrupt_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live UAT finding, 2026-08-10: the Command field's buffer used to be
    # seeded via the generic _format_value() (comma-joined, correct for
    # OTHER list-kind fields like hard_stop_phrases, wrong here) while its
    # OWN parse-on-accept uses shlex.split() (space-based, no comma
    # handling). Simply opening this field and pressing Enter WITHOUT
    # TYPING ANYTHING used to corrupt every token with a trailing comma.
    spec = FieldSpec("backend", "command", "Command", "command")
    keys = iter(["ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(
        spec, ["codex.cmd", "--model", "gpt-5.6-terra"], AppConfig()
    )
    assert accepted is True
    assert value == ["codex.cmd", "--model", "gpt-5.6-terra"]


def test_modal_command_edit_none_seeds_an_empty_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec("backend", "command", "Command", "command")
    keys = iter(["ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, None, AppConfig())
    assert accepted is True
    assert value is None  # empty buffer -> _parse_value's "keep current" case


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

    accepted, value = settings_tui._edit_value_interactive(spec, "do-not-disturb", AppConfig())
    assert accepted is True
    assert value == "take-over"
    assert drawn == ["do-not-disturb", "conversational", "take-over"]


def test_compute_type_is_registered_as_a_choice_field() -> None:
    stt = next(s for s in settings_tui.SECTION_SPECS if s.key == "stt")
    spec = next(f for f in stt.fields if f.key == "compute_type")
    assert spec.kind == "choice"
    # Same source of truth as config.py's own validator (STT_COMPUTE_TYPES)
    # -- picker and validator can never drift apart.
    assert spec.choices == settings_tui.STT_COMPUTE_TYPES
    assert "default" in spec.choices
    assert "float32" in spec.choices
    assert "float64" not in spec.choices  # no such thing -- float32 is the ceiling


def test_compute_type_cycles_with_space_and_arrow(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec("stt", "compute_type", "Compute type", "choice", ("default", "int8", "float32"))
    keys = iter([" ", " ", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    monkeypatch.setattr(settings_tui, "_draw_modal", lambda *a, **k: None)

    accepted, value = settings_tui._edit_value_interactive(spec, "default", AppConfig())

    assert accepted is True
    assert value == "float32"


def test_pause_resume_ack_is_registered_as_a_choice_field() -> None:
    # P8 (docs/DESIGN-barge-in.md): must be pickable, not free-text, and
    # must NOT offer "file" -- that value isn't implemented yet.
    interaction = next(s for s in settings_tui.SECTION_SPECS if s.key == "interaction")
    spec = next(f for f in interaction.fields if f.key == "pause_resume_ack")
    assert spec.kind == "choice"
    assert spec.choices == ("none", "tone")


def test_pause_resume_ack_cycles_with_space_and_arrow(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = FieldSpec("interaction", "pause_resume_ack", "Pause/resume sound", "choice", ("none", "tone"))
    keys = iter([" ", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    monkeypatch.setattr(settings_tui, "_draw_modal", lambda *a, **k: None)

    accepted, value = settings_tui._edit_value_interactive(spec, "none", AppConfig())
    assert accepted is True
    assert value == "tone"


def test_safeword_has_no_section_of_its_own() -> None:
    assert not any(s.key == "safeword" for s in settings_tui.SECTION_SPECS)


def test_safeword_field_is_grouped_under_interaction() -> None:
    interaction = next(s for s in settings_tui.SECTION_SPECS if s.key == "interaction")
    spec = next(f for f in interaction.fields if f.key == "hard_stop_phrases")
    assert spec.kind == "list_str"
    # Display grouping only -- the field's own "section" is still
    # "safeword", which is what _get_value/_set_value use to resolve the
    # real config.safeword.hard_stop_phrases path (SafewordDetector,
    # incident capture, etc. are all untouched by this reorg).
    assert spec.section == "safeword"

    config = AppConfig()
    assert settings_tui._get_value(config, spec) == config.safeword.hard_stop_phrases
    settings_tui._set_value(config, spec, ["stop stop stop", "abort now"])
    assert config.safeword.hard_stop_phrases == ["stop stop stop", "abort now"]


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
        "name", "command", "permission_mode", "working_dir", "warn_if_working_dir_not_git",
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


def test_modal_depth_tracks_a_modal_loop_being_on_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_tui()'s SIGWINCH handler checks _modal_depth before repainting
    # the main screen (docs/KNOWN-ISSUES.md's resize entry) so an idle
    # resize while a modal is open doesn't blow it away -- this confirms
    # the counter set by @_tracks_modal_depth is actually 0 outside a
    # modal, >0 WHILE one is on screen, and back to 0 once it returns.
    assert settings_tui._modal_depth == 0
    seen_depth_during_call = None

    def fake_read_key() -> str:
        nonlocal seen_depth_during_call
        seen_depth_during_call = settings_tui._modal_depth
        return "ESC"

    monkeypatch.setattr(settings_tui, "read_key", fake_read_key)
    settings_tui._confirm_modal("Confirm", "Proceed?", ["detail"])

    assert seen_depth_during_call == 1
    assert settings_tui._modal_depth == 0


def test_modal_depth_nests_correctly_across_a_confirm_inside_an_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The counter (not a plain bool) exists specifically for this case: a
    # Confirm Quit dialog can be raised from inside an already-open field
    # editor, and the inner dialog returning must not clear the flag while
    # the outer editor is still on screen.
    spec = FieldSpec("audio", "input_device", "Input device", "optional_str")
    depths_seen: list[int] = []

    def fake_read_key() -> str:
        depths_seen.append(settings_tui._modal_depth)
        if len(depths_seen) == 1:
            # First read inside the outer edit modal: nest a confirm modal
            # (using the real function, not a stub) before finishing.
            inner_keys = iter(["ESC"])
            monkeypatch.setattr(settings_tui, "read_key", lambda: next(inner_keys))
            settings_tui._confirm_modal("Nested", "Really?", [])
            assert settings_tui._modal_depth == 1  # back to just the outer editor
            monkeypatch.setattr(settings_tui, "read_key", fake_read_key)
        return "ESC"

    monkeypatch.setattr(settings_tui, "read_key", fake_read_key)
    settings_tui._edit_value_interactive(spec, "", AppConfig())

    assert settings_tui._modal_depth == 0


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


def _write_fake_kokoro_voices(path: Path, *names: str) -> None:
    """A real, tiny npz archive shaped like the actual kokoro-onnx voices
    file -- same format list_kokoro_voices reads, verified in
    tests/test_tts_factory.py against a real downloaded voices-v1.0.bin."""
    np.savez(path, **{name: np.zeros((1, 1), dtype=np.float32) for name in names})
    path.with_suffix(path.suffix + ".npz").rename(path)


def _write_fake_piper_voice(
    voices_dir: Path, key: str, speaker_id_map: dict[str, int] | None = None
) -> None:
    """A minimal installed-Piper-voice pair: an empty .onnx (only its
    existence/stem matters to installed_voices' glob) and a real .onnx.json
    sidecar carrying speaker_id_map -- the same field _piper_speaker_choices
    reads directly, confirmed live 2026-07-24 against a real downloaded
    en_GB-aru-medium.onnx.json to match what PiperVoice.load() would expose.
    """
    voices_dir.mkdir(parents=True, exist_ok=True)
    (voices_dir / f"{key}.onnx").write_bytes(b"")
    config: dict[str, object] = {"speaker_id_map": speaker_id_map or {}}
    (voices_dir / f"{key}.onnx.json").write_text(json.dumps(config), encoding="utf-8")


# --- Kokoro voice picker: tts.voice becomes a real "cycle the actually
# downloaded voices" field for kokoro (unlike Piper's free-text voice
# key), since Kokoro's voices are a fixed, closed set baked into
# tts.voices_path rather than a per-voice download catalog. ---


def test_tts_section_shows_engine_specific_voice_picker_kind_per_engine() -> None:
    kokoro_config = _make_config(**{"tts.engine": "kokoro"})
    kokoro_state = TuiState(
        path=Path("convobox.yaml"), original=kokoro_config, working=kokoro_config.model_copy(deep=True)
    )
    kokoro_state.selected_section = next(i for i, s in enumerate(kokoro_state.sections) if s.key == "tts")
    kokoro_fields = kokoro_state.current_fields()
    voice_field = next(f for f in kokoro_fields if f.key == "voice")
    assert voice_field.kind == "kokoro_voice"
    assert "model_path" in {f.key for f in kokoro_fields}
    assert "speaker" not in {f.key for f in kokoro_fields}

    piper_config = _make_config(**{"tts.engine": "piper"})
    piper_state = TuiState(
        path=Path("convobox.yaml"), original=piper_config, working=piper_config.model_copy(deep=True)
    )
    piper_state.selected_section = next(i for i, s in enumerate(piper_state.sections) if s.key == "tts")
    piper_fields = piper_state.current_fields()
    piper_voice_field = next(f for f in piper_fields if f.key == "voice")
    # Piper voice/speaker also get dedicated pickers now (not free text) --
    # same "pick from what's real, not free text a typo could break"
    # reasoning as Kokoro's picker, added after live UAT feedback asked for
    # it (2026-07-25).
    assert piper_voice_field.kind == "piper_voice"
    piper_speaker_field = next(f for f in piper_fields if f.key == "speaker")
    assert piper_speaker_field.kind == "piper_speaker"
    assert "model_path" not in {f.key for f in piper_fields}


def test_kokoro_voice_choices_returns_real_voices_when_downloaded(tmp_path: Path) -> None:
    voices_path = tmp_path / "voices.bin"
    _write_fake_kokoro_voices(voices_path, "af_sarah", "am_adam")
    config = _make_config(**{"tts.engine": "kokoro", "tts.voices_path": str(voices_path)})

    assert settings_tui._kokoro_voice_choices(config) == ["af_sarah", "am_adam"]


def test_kokoro_voice_choices_falls_back_to_placeholder_when_not_downloaded(tmp_path: Path) -> None:
    config = _make_config(
        **{"tts.engine": "kokoro", "tts.voices_path": str(tmp_path / "missing.bin")}
    )

    assert settings_tui._kokoro_voice_choices(config) == [settings_tui._KOKORO_VOICE_UNAVAILABLE]


def test_choices_for_dispatches_kokoro_voice(tmp_path: Path) -> None:
    voices_path = tmp_path / "voices.bin"
    _write_fake_kokoro_voices(voices_path, "af_sarah", "am_adam")
    config = _make_config(**{"tts.engine": "kokoro", "tts.voices_path": str(voices_path)})
    spec = FieldSpec("tts", "voice", "Voice", "kokoro_voice")

    assert settings_tui._choices_for(spec, config) == ("af_sarah", "am_adam")


def test_edit_kokoro_voice_field_cycles_through_real_voices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voices_path = tmp_path / "voices.bin"
    _write_fake_kokoro_voices(voices_path, "af_sarah", "am_adam", "bf_emma")
    config = _make_config(
        **{"tts.engine": "kokoro", "tts.voices_path": str(voices_path), "tts.voice": "af_sarah"}
    )
    spec = FieldSpec("tts", "voice", "Voice", "kokoro_voice")

    keys = iter(["RIGHT", "RIGHT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, "af_sarah", config)

    assert accepted is True
    assert value == "bf_emma"


def test_edit_kokoro_voice_field_enter_on_placeholder_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Voices not downloaded yet -- the only "choice" is the placeholder.
    # Cycling to it (RIGHT, since the current value "af_sarah" isn't in
    # the single-item choices list) then accepting must not write the
    # placeholder text into tts.voice.
    config = _make_config(
        **{"tts.engine": "kokoro", "tts.voices_path": str(tmp_path / "missing.bin"), "tts.voice": "af_sarah"}
    )
    spec = FieldSpec("tts", "voice", "Voice", "kokoro_voice")

    keys = iter(["RIGHT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._edit_value_interactive(spec, "af_sarah", config)

    assert accepted is False
    assert value == "af_sarah"


def test_toggle_or_cycle_kokoro_voice_field_advances_to_next_real_voice(tmp_path: Path) -> None:
    voices_path = tmp_path / "voices.bin"
    _write_fake_kokoro_voices(voices_path, "af_sarah", "am_adam")
    config = _make_config(
        **{"tts.engine": "kokoro", "tts.voices_path": str(voices_path), "tts.voice": "af_sarah"}
    )
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "voice")

    settings_tui._toggle_or_cycle(state)

    assert state.working.tts.voice == "am_adam"


def test_toggle_or_cycle_kokoro_voice_field_leaves_voice_untouched_when_not_downloaded(
    tmp_path: Path,
) -> None:
    config = _make_config(
        **{
            "tts.engine": "kokoro",
            "tts.voices_path": str(tmp_path / "missing.bin"),
            "tts.voice": "af_sarah",
        }
    )
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "voice")

    settings_tui._toggle_or_cycle(state)

    assert state.working.tts.voice == "af_sarah"
    assert "not downloaded yet" in state.status


def test_help_panel_shows_real_kokoro_voice_choices(tmp_path: Path) -> None:
    voices_path = tmp_path / "voices.bin"
    _write_fake_kokoro_voices(voices_path, "af_sarah", "am_adam")
    config = _make_config(**{"tts.engine": "kokoro", "tts.voices_path": str(voices_path)})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "voice")

    lines = settings_tui._help_panel_lines(state, 80, 20)

    assert any("af_sarah" in line and "am_adam" in line for line in lines)


# --- Piper voice/speaker pickers: same "pick from what's real, not free
# text a typo could break" reasoning as Kokoro's picker above, added after
# live UAT feedback asked for parity (2026-07-25). Unlike Kokoro's fixed
# 54-voice set, Piper voices are individually downloaded (installed_voices
# globs .models/piper's real *.onnx files) and some are genuinely
# multi-speaker (speaker_id_map read directly from each voice's own
# .onnx.json sidecar, no model load needed -- confirmed live against a
# real downloaded en_GB-aru-medium.onnx.json). ---


def test_piper_voice_choices_returns_real_installed_voices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_US-lessac-medium")
    _write_fake_piper_voice(tmp_path, "en_GB-alan-medium")

    assert settings_tui._piper_voice_choices() == ["en_GB-alan-medium", "en_US-lessac-medium"]


def test_piper_voice_choices_falls_back_to_placeholder_when_none_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path / "empty")

    assert settings_tui._piper_voice_choices() == [settings_tui._PIPER_VOICE_UNAVAILABLE]


def test_piper_speaker_choices_returns_default_plus_real_speakers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-aru-medium", {"03": 0, "01": 1, "06": 2})
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-aru-medium"})

    choices = settings_tui._piper_speaker_choices(config)

    # "(voice default)" always first (maps to None -- the picker-safe
    # sentinel for "use the voice's own default speaker"), then the
    # real names sorted, matching how they'd be typed/matched elsewhere.
    assert choices == [settings_tui._PIPER_SPEAKER_DEFAULT, "01", "03", "06"]


def test_piper_speaker_choices_unavailable_when_voice_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.engine": "piper", "tts.voice": None})

    assert settings_tui._piper_speaker_choices(config) == [settings_tui._PIPER_SPEAKER_UNAVAILABLE]


def test_piper_speaker_choices_unavailable_when_voice_not_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_US-lessac-medium"})

    assert settings_tui._piper_speaker_choices(config) == [settings_tui._PIPER_SPEAKER_UNAVAILABLE]


def test_piper_speaker_choices_unavailable_for_single_speaker_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_US-lessac-medium", {})  # empty speaker_id_map
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_US-lessac-medium"})

    assert settings_tui._piper_speaker_choices(config) == [settings_tui._PIPER_SPEAKER_UNAVAILABLE]


def test_choices_for_dispatches_piper_voice_and_speaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-aru-medium", {"01": 0})
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-aru-medium"})

    voice_spec = FieldSpec("tts", "voice", "Voice", "piper_voice")
    speaker_spec = FieldSpec("tts", "speaker", "Speaker", "piper_speaker")

    assert settings_tui._choices_for(voice_spec, config) == ("en_GB-aru-medium",)
    assert settings_tui._choices_for(speaker_spec, config) == (settings_tui._PIPER_SPEAKER_DEFAULT, "01")


def test_toggle_or_cycle_piper_voice_field_advances_to_next_real_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-alan-medium")
    _write_fake_piper_voice(tmp_path, "en_US-lessac-medium")
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-alan-medium"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "voice")

    settings_tui._toggle_or_cycle(state)

    assert state.working.tts.voice == "en_US-lessac-medium"


def test_toggle_or_cycle_piper_speaker_field_maps_default_sentinel_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-aru-medium", {"01": 0})
    config = _make_config(
        **{"tts.engine": "piper", "tts.voice": "en_GB-aru-medium", "tts.speaker": "01"}
    )
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "speaker")

    # Cycling from "01" (index 1) advances to index 0, "(voice default)" --
    # which must be written as None, not the literal sentinel text.
    settings_tui._toggle_or_cycle(state)

    assert state.working.tts.speaker is None


def test_toggle_or_cycle_piper_speaker_field_leaves_speaker_untouched_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.engine": "piper", "tts.voice": None, "tts.speaker": None})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "speaker")

    settings_tui._toggle_or_cycle(state)

    assert state.working.tts.speaker is None
    assert "no named speakers" in state.status


def test_piper_voice_picker_modal_cycle_and_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-alan-medium")
    _write_fake_piper_voice(tmp_path, "en_US-lessac-medium")
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-alan-medium"})

    keys = iter(["RIGHT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._piper_voice_picker_modal("en_GB-alan-medium", config)

    assert accepted is True
    assert value == "en_US-lessac-medium"


def test_piper_voice_picker_modal_esc_cancels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-alan-medium")
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-alan-medium"})

    monkeypatch.setattr(settings_tui, "read_key", lambda: "ESC")
    accepted, value = settings_tui._piper_voice_picker_modal("en_GB-alan-medium", config)

    assert accepted is False
    assert value == "en_GB-alan-medium"


def test_piper_voice_picker_modal_enter_on_placeholder_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path / "empty")
    config = _make_config(**{"tts.engine": "piper", "tts.voice": None})

    monkeypatch.setattr(settings_tui, "read_key", lambda: "ENTER")
    accepted, _value = settings_tui._piper_voice_picker_modal(None, config)

    assert accepted is False


def test_piper_speaker_picker_modal_starts_on_default_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-aru-medium", {"01": 0, "02": 1})
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-aru-medium", "tts.speaker": None})

    # From "(voice default)" (index 0, since current=None seeds it there),
    # RIGHT advances to "01", ENTER confirms.
    keys = iter(["RIGHT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._piper_speaker_picker_modal(None, config)

    assert accepted is True
    assert value == "01"


def test_piper_speaker_picker_modal_confirming_default_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    _write_fake_piper_voice(tmp_path, "en_GB-aru-medium", {"01": 0})
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_GB-aru-medium", "tts.speaker": "01"})

    # Starts on "01" (current); LEFT wraps back to "(voice default)", ENTER confirms.
    keys = iter(["LEFT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    accepted, value = settings_tui._piper_speaker_picker_modal("01", config)

    assert accepted is True
    assert value is None


def test_test_piper_speaker_reports_when_no_voice_configured() -> None:
    config = _make_config(**{"tts.engine": "piper", "tts.voice": None})

    result = asyncio.run(settings_tui._test_piper_speaker("01", config))

    assert "pick + save" in result


# --- render_modal choice-window scrolling: a long choice list (e.g.
# Piper's/Kokoro's real voice counts) must keep the selected `>` marker
# visible, not just render the first N entries and silently truncate the
# rest -- live UAT feedback, 2026-07-24: cycling past a static ~15-entry
# window changed the "Current:"/buffer line with zero visible list
# movement, since the marker had scrolled off-screen with no indicator. ---


def test_render_modal_scrolls_to_keep_far_selection_visible() -> None:
    choices = [f"voice_{i:02d}" for i in range(60)]
    lines = render_modal(
        "Select Voice", "Editing tts.voice", [], "voice_55", 100, 30,
        choice_options=choices, choice_value="voice_55",
    )
    joined = "\n".join(lines)
    assert "| > voice_55" in joined
    assert "more above" in joined
    # The far end of the list is genuinely off-window here -- confirms
    # this is windowing, not just dumping everything.
    assert "voice_00" not in joined


def test_render_modal_shows_no_scroll_indicators_when_everything_fits() -> None:
    choices = ["a", "b", "c"]
    lines = render_modal(
        "Select Voice", "Editing tts.voice", [], "b", 100, 30,
        choice_options=choices, choice_value="b",
    )
    joined = "\n".join(lines)
    assert "more above" not in joined
    assert "more below" not in joined
    assert "| > b" in joined


# --- TTS per-engine profile memory: switching tts.engine used to lose
# whatever voice/settings the OTHER engine had, since both share the same
# underlying TTSConfig fields -- mirrors backend_profiles' existing
# per-backend memory (test_switching_backends_remembers_backend_specific_values
# above), and is what makes [c] compare (below) able to hear both engines
# using each one's own real settings rather than whatever's currently active. ---


def test_switching_tts_engine_remembers_engine_specific_values() -> None:
    config = _make_config(
        **{
            "tts.engine": "kokoro",
            "tts.voice": "af_sarah",
            "tts.rate": 1.2,
        }
    )
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))

    settings_tui._switch_tts_engine(state.working, "piper")
    assert state.working.tts.engine == "piper"
    assert state.working.tts.voice is None  # piper has no sensible default voice
    assert state.working.tts_profiles["kokoro"].voice == "af_sarah"
    assert state.working.tts_profiles["kokoro"].rate == 1.2

    state.working.tts.voice = "en_US-lessac-medium"
    state.working.tts.speaker = "prudence"
    settings_tui._switch_tts_engine(state.working, "kokoro")
    assert state.working.tts.engine == "kokoro"
    assert state.working.tts.voice == "af_sarah"
    assert state.working.tts.rate == 1.2
    assert state.working.tts.speaker is None  # piper-only field, cleared on switch to kokoro
    assert state.working.tts_profiles["piper"].voice == "en_US-lessac-medium"
    assert state.working.tts_profiles["piper"].speaker == "prudence"

    settings_tui._switch_tts_engine(state.working, "piper")
    assert state.working.tts.voice == "en_US-lessac-medium"
    assert state.working.tts.speaker == "prudence"


def test_switching_tts_engine_to_itself_is_a_noop() -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": "af_bella"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))

    settings_tui._switch_tts_engine(state.working, "kokoro")

    assert state.working.tts.voice == "af_bella"
    assert state.working.tts_profiles == {}


def test_prompt_edit_switches_tts_engine_via_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": "af_sarah"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "engine")

    keys = iter(["RIGHT", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    settings_tui._prompt_edit(state)

    assert state.working.tts.engine == "piper"
    assert state.working.tts_profiles["kokoro"].voice == "af_sarah"


def test_prompt_edit_confirming_without_cycling_reports_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live UAT, 2026-08-02: pressing Enter to accept a picker without ever
    # cycling to a different choice previously said "Compute type updated"
    # -- state.dirty came back False (nothing in the whole config changed)
    # so _field_updated_status's dirty=False branch fired, but that message
    # was written for "changed then reverted," not "never touched." Confirm
    # the field is genuinely untouched and the status says so distinctly.
    config = _make_config(**{"stt.compute_type": "default"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "stt")
    state.selected_field = next(i for i, f in enumerate(state.current_fields()) if f.key == "compute_type")

    keys = iter(["ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))
    settings_tui._prompt_edit(state)

    assert state.working.stt.compute_type == "default"
    assert state.status == "Compute type unchanged"
    assert state.dirty is False


# --- [c] compare: hear Kokoro and Piper speak the same phrase back to
# back, each built from its own remembered profile, without touching
# tts.engine or anything staged for save. ---


def test_compare_tts_engines_only_available_in_tts_section() -> None:
    config = _make_config()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")

    asyncio.run(settings_tui._compare_tts_engines(state))

    assert "only available in the TTS section" in state.status


def test_tts_config_for_comparison_returns_none_when_piper_has_no_voice() -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": "af_sarah"})

    assert settings_tui._tts_config_for_comparison(config, "piper") is None
    kokoro_config = settings_tui._tts_config_for_comparison(config, "kokoro")
    assert kokoro_config is not None
    assert kokoro_config.voice == "af_sarah"


def test_tts_config_for_comparison_uses_remembered_profile_not_active_fields() -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": "af_sarah"})
    config.tts_profiles["piper"] = settings_tui.TTSProfileConfig(voice="en_US-lessac-medium")

    piper_config = settings_tui._tts_config_for_comparison(config, "piper")

    assert piper_config is not None
    assert piper_config.engine == "piper"
    assert piper_config.voice == "en_US-lessac-medium"


def test_compare_test_phrase_names_the_engine() -> None:
    # [c] compare plays Kokoro then Piper back to back -- naming the
    # engine in the spoken phrase itself makes each one identifiable by
    # ear alone, not just by reading the status line afterward.
    assert settings_tui._compare_test_phrase("piper") == (
        "This is a test using the 'piper' text to speech engine."
    )
    assert settings_tui._compare_test_phrase("kokoro") == (
        "This is a test using the 'kokoro' text to speech engine."
    )


def test_compare_tts_engines_plays_both_and_never_mutates_working_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": "af_sarah"})
    config.tts_profiles["piper"] = settings_tui.TTSProfileConfig(voice="en_US-lessac-medium")
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")
    before = state.working.model_dump(mode="python")

    class _FakeEngine:
        def __init__(self, tag: str) -> None:
            self.sample_rate = 24000
            self._tag = tag

        async def synthesize(self, text: str) -> np.ndarray:
            return np.ones(10, dtype=np.float32)

    built: list[str] = []

    def _fake_create_tts_engine(tts_config, voices_dir):
        built.append(tts_config.engine)
        return _FakeEngine(tts_config.engine)

    monkeypatch.setattr(settings_tui, "create_tts_engine", _fake_create_tts_engine)
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [])
    played: list[str] = []
    monkeypatch.setattr(audio_devices, "_default_index", lambda sd, kind: None)
    monkeypatch.setattr(audio_devices, "_play_recording", lambda sd, audio, rate, device: played.append("played"))

    asyncio.run(settings_tui._compare_tts_engines(state))

    assert built == ["kokoro", "piper"]
    assert played == ["played", "played"]
    assert "kokoro:" in state.status and "piper:" in state.status
    assert state.working.model_dump(mode="python") == before


def test_compare_tts_engines_reports_engine_failure_without_stopping_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voice": "af_sarah"})
    config.tts_profiles["piper"] = settings_tui.TTSProfileConfig(voice="en_US-lessac-medium")
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    def _fake_create_tts_engine(tts_config, voices_dir):
        if tts_config.engine == "kokoro":
            raise RuntimeError("boom")

        class _FakeEngine:
            sample_rate = 24000

            async def synthesize(self, text: str) -> np.ndarray:
                return np.ones(10, dtype=np.float32)

        return _FakeEngine()

    monkeypatch.setattr(settings_tui, "create_tts_engine", _fake_create_tts_engine)
    _install_fake_sounddevice(monkeypatch)
    monkeypatch.setattr(audio_devices, "collect_devices", lambda sd, kind: [])
    monkeypatch.setattr(audio_devices, "_default_index", lambda sd, kind: None)
    monkeypatch.setattr(audio_devices, "_play_recording", lambda sd, audio, rate, device: None)

    asyncio.run(settings_tui._compare_tts_engines(state))

    assert "kokoro: RuntimeError: boom" in state.status
    assert "piper: played" in state.status


# --- [d] refresh Kokoro voices: force a fresh download, replacing
# whatever's cached, for when kokoro-onnx's upstream release changes the
# voice set. ---


def test_refresh_kokoro_voices_downloads_and_reports_the_new_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voices_path = tmp_path / "voices.bin"
    config = _make_config(**{"tts.engine": "kokoro", "tts.voices_path": str(voices_path)})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    def _fake_refresh(path: str) -> None:
        _write_fake_kokoro_voices(Path(path), "af_sarah", "am_adam", "new_voice")

    monkeypatch.setattr(settings_tui, "refresh_kokoro_voices", _fake_refresh)

    settings_tui._refresh_kokoro_voices(state)

    assert "3 voices now available" in state.status


def test_refresh_kokoro_voices_reports_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(**{"tts.engine": "kokoro", "tts.voices_path": str(tmp_path / "voices.bin")})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    def _fake_refresh(path: str) -> None:
        raise FileNotFoundError("404 not found")

    monkeypatch.setattr(settings_tui, "refresh_kokoro_voices", _fake_refresh)

    settings_tui._refresh_kokoro_voices(state)

    assert "voices refresh failed" in state.status


def test_refresh_kokoro_voices_is_a_noop_for_piper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(**{"tts.engine": "piper"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    def _fail_if_called(path: str) -> None:
        raise AssertionError("should not download for piper")

    monkeypatch.setattr(settings_tui, "refresh_kokoro_voices", _fail_if_called)

    settings_tui._refresh_kokoro_voices(state)

    assert "only available for tts.engine=kokoro" in state.status


def test_refresh_kokoro_voices_is_a_noop_outside_tts_section(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(**{"tts.engine": "kokoro"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")

    def _fail_if_called(path: str) -> None:
        raise AssertionError("should not download outside the tts section")

    monkeypatch.setattr(settings_tui, "refresh_kokoro_voices", _fail_if_called)

    settings_tui._refresh_kokoro_voices(state)

    assert "only available for tts.engine=kokoro" in state.status


# --- [v] browse Piper voice catalog: hands the terminal to
# voice_picker_tui.py's own picker in-process, applies whatever it
# returns through THIS session's own state (not a second independent
# writer to convobox.yaml). 2026-09-03, JP: "no way of downloading new
# voices for kokoro or piper" -- kokoro already had [d]; this is piper's
# equivalent. ---


def test_browse_piper_voice_catalog_applies_the_chosen_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(**{"tts.engine": "piper", "tts.voice": None})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    captured: dict[str, object] = {}

    def _fake_run_tui(voices_dir: object, refresh: bool, offer_save: bool) -> str:
        captured["voices_dir"] = voices_dir
        captured["refresh"] = refresh
        captured["offer_save"] = offer_save
        return "en_US-lessac-medium"

    fake_module = SimpleNamespace(run_tui=_fake_run_tui)
    monkeypatch.setitem(sys.modules, "voice_picker_tui", fake_module)

    settings_tui._browse_piper_voice_catalog(state)

    assert state.working.tts.voice == "en_US-lessac-medium"
    assert state.dirty is True
    assert "en_US-lessac-medium" in state.status
    # offer_save=False -- this session must never let voice_picker_tui.py
    # write to convobox.yaml on its own; settings_tui.py owns that via
    # its own [S] Save.
    assert captured["offer_save"] is False


def test_browse_piper_voice_catalog_handles_no_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(**{"tts.engine": "piper", "tts.voice": "en_US-lessac-medium"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    fake_module = SimpleNamespace(run_tui=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "voice_picker_tui", fake_module)

    settings_tui._browse_piper_voice_catalog(state)

    # Unchanged -- quitting the catalog without choosing must not clear
    # or otherwise touch the existing tts.voice value.
    assert state.working.tts.voice == "en_US-lessac-medium"
    assert state.dirty is False
    assert "no voice was chosen" in state.status


def test_browse_piper_voice_catalog_is_a_noop_for_kokoro(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(**{"tts.engine": "kokoro"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "tts")

    def _fail_if_called(*a: object, **k: object) -> None:
        raise AssertionError("should not launch the catalog browser for kokoro")

    monkeypatch.setitem(sys.modules, "voice_picker_tui", SimpleNamespace(run_tui=_fail_if_called))

    settings_tui._browse_piper_voice_catalog(state)

    assert "only available for tts.engine=piper" in state.status


def test_browse_piper_voice_catalog_is_a_noop_outside_tts_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(**{"tts.engine": "piper"})
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.selected_section = next(i for i, s in enumerate(state.sections) if s.key == "audio")

    def _fail_if_called(*a: object, **k: object) -> None:
        raise AssertionError("should not launch the catalog browser outside tts")

    monkeypatch.setitem(sys.modules, "voice_picker_tui", SimpleNamespace(run_tui=_fail_if_called))

    settings_tui._browse_piper_voice_catalog(state)

    assert "only available for tts.engine=piper" in state.status


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


# --- codex + approve mode (2026-09-02): live-verified via a raw JSON-RPC
# probe that no codex-cli approval_policy value both starts successfully
# AND actually escalates a write to approval -- run_convobox.py's own
# startup guard SystemExits on this, so it's a hard error here, not a
# warning. See codex.py's _CODEX_APPROVE_MODE_ERROR / docs/KNOWN-ISSUES.md.


def test_validate_config_errors_on_codex_approve_mode() -> None:
    config = _make_config(**{"backend.name": "codex", "backend.permission_mode": "approve"})
    report = validate_config(config)
    assert any("codex" in e and "not currently usable" in e for e in report.errors)


def test_validate_config_no_error_for_codex_plan_mode() -> None:
    config = _make_config(**{"backend.name": "codex", "backend.permission_mode": "plan"})
    report = validate_config(config)
    assert not any("not currently usable" in e for e in report.errors)


def test_validate_config_no_error_for_claude_code_approve_mode() -> None:
    # The restriction is codex-specific -- claude-code's own "approve"
    # support is real and unaffected (guarded separately by the approval-
    # gap check above, which needs an approval_phrase, not a hard block).
    config = _make_config(
        **{
            "backend.name": "claude-code",
            "backend.permission_mode": "approve",
            "interaction.approval_phrase": "alpha bravo charlie",
        }
    )
    report = validate_config(config)
    assert not any("not currently usable" in e for e in report.errors)


# --- backend.warn_if_working_dir_not_git (2026-09-04, JP): wired through
# validate_config() so both the Settings TUI and the web UI (which reuses
# this exact function) surface the same warning. ---


def test_validate_config_warns_when_working_dir_is_not_a_git_repo(tmp_path: Path) -> None:
    config = _make_config(**{"backend.working_dir": str(tmp_path)})
    report = validate_config(config)
    assert any("git init" in w for w in report.warnings)


def test_validate_config_no_warning_when_the_toggle_is_off(tmp_path: Path) -> None:
    config = _make_config(
        **{"backend.working_dir": str(tmp_path), "backend.warn_if_working_dir_not_git": False}
    )
    report = validate_config(config)
    assert not any("git init" in w for w in report.warnings)


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


# --- audio.input_device / output_device connectivity (2026-09-02, JP live
# on macOS): a Bluetooth headset configured then disconnected made
# run_convobox.py crash at runtime (fixed separately, run_convobox.py's
# _validate_audio_device). This is the OTHER half -- surface it here too,
# in the Settings TUI's own summary, before the user tries to run at all. ---


def test_validate_config_warns_when_configured_input_device_is_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_tui,
        "_device_currently_unavailable",
        lambda device, kind: f"no device matching {device!r}" if kind == "input" else None,
    )
    config = _make_config(**{"audio.input_device": "AirPods Pro, Core Audio"})
    report = validate_config(config)
    assert any(
        "audio.input_device" in w and "AirPods Pro" in w and "not currently connected" in w
        for w in report.warnings
    )


def test_validate_config_warns_when_configured_output_device_is_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_tui,
        "_device_currently_unavailable",
        lambda device, kind: f"no device matching {device!r}" if kind == "output" else None,
    )
    config = _make_config(**{"audio.output_device": "AirPods Pro, Core Audio"})
    report = validate_config(config)
    assert any(
        "audio.output_device" in w and "AirPods Pro" in w and "not currently connected" in w
        for w in report.warnings
    )


def test_validate_config_no_device_warning_when_currently_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_tui, "_device_currently_unavailable", lambda device, kind: None)
    config = _make_config(**{"audio.input_device": "MacBook Pro Microphone, Core Audio"})
    report = validate_config(config)
    assert not any("not currently connected" in w for w in report.warnings)


def test_validate_config_no_device_warning_when_device_is_unset() -> None:
    # Unset (system default) has nothing to check -- must not even
    # attempt device enumeration for it.
    config = _make_config()
    report = validate_config(config)
    assert not any("not currently connected" in w for w in report.warnings)


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


# --- Recovering from a bad on-disk config (docs/field-notes/2026-08-06-
# settings-tui-cannot-open-invalid-config.md): a leftover stt.compute_type/
# stt.device mismatch (from PR #210's own live-test) crashed settings_tui.py
# outright -- the one tool meant to fix a bad config couldn't open with one.


def test_list_config_backups_is_newest_first(tmp_path: Path) -> None:
    # Backups live in a .convobox-backups/ subdirectory next to the config
    # (GitHub issue #235, finding D4), not scattered directly alongside it.
    path = tmp_path / "convobox.yaml"
    backup_dir = tmp_path / ".convobox-backups"
    backup_dir.mkdir()
    (backup_dir / "convobox.yaml.backup-20260805-100000").write_text("a", encoding="utf-8")
    (backup_dir / "convobox.yaml.backup-20260806-090000").write_text("b", encoding="utf-8")
    (backup_dir / "convobox.yaml.backup-20260805-223836").write_text("c", encoding="utf-8")
    (backup_dir / "unrelated.yaml.backup-20260807-000000").write_text("d", encoding="utf-8")

    backups = settings_tui.list_config_backups(path)

    assert [b.name for b in backups] == [
        "convobox.yaml.backup-20260806-090000",
        "convobox.yaml.backup-20260805-223836",
        "convobox.yaml.backup-20260805-100000",
    ]


def test_list_config_backups_empty_when_none_exist(tmp_path: Path) -> None:
    assert settings_tui.list_config_backups(tmp_path / "convobox.yaml") == []


def test_backup_config_writes_into_a_convobox_backups_subdirectory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "convobox.yaml"
    path.write_text("tts:\n  voice: en_US-lessac-medium\n", encoding="utf-8")

    backup = backup_config(path)

    assert backup is not None
    assert backup.parent == tmp_path / ".convobox-backups"
    assert backup.parent.name not in {"", "."}
    # The config's own directory (the old location) must NOT also get a
    # stray backup file -- a regression here would mean both places end
    # up with copies, not a clean move.
    assert list((tmp_path).glob("convobox.yaml.backup-*")) == []


def test_apply_load_recovery_is_a_no_op_when_nothing_failed() -> None:
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    settings_tui._apply_load_recovery(state, [])
    assert state.load_problems == []
    assert state.dirty is False
    assert state.status == "BIOS style: Left/Right tabs, Up/Down fields, Enter edit"


def test_apply_load_recovery_flags_dirty_and_jumps_to_the_bad_section() -> None:
    config = AppConfig()  # stt already at its (valid) defaults, as load_config_lenient would return
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    problems = ["stt: Value error, compute_type 'float16' is not supported on device 'cpu'"]

    settings_tui._apply_load_recovery(state, problems)

    assert state.load_problems == problems
    assert state.dirty is True
    assert state.flagged_sections == {"stt"}
    assert state.sections[state.selected_section].key == "stt"
    assert "1 setting(s)" in state.status
    assert "[B] restore last backup" in state.status


def test_section_tabs_marks_a_flagged_section() -> None:
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    state.load_problems = ["stt: bad pairing"]

    tabs = settings_tui._section_tabs(state, 200)

    assert "! STT" in tabs
    assert "! Audio" not in tabs  # unrelated section stays unmarked


def test_restore_from_backup_loads_the_newest_backup_and_clears_load_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "convobox.yaml"
    good = _make_config(**{"stt.device": "cpu", "stt.compute_type": "int8"})
    save_with_backup(path, good)  # 1st save: no prior file, so no backup yet
    save_with_backup(path, AppConfig())  # 2nd save: backs up `good` into .backup-<stamp>

    broken = AppConfig()  # stands in for load_config_lenient's defaults-fallback result
    state = TuiState(path=path, original=broken, working=broken.model_copy(deep=True))
    state.load_problems = ["stt: Value error, compute_type 'float16' is not supported"]

    monkeypatch.setattr(settings_tui, "_draw_modal", lambda *a, **k: None)
    monkeypatch.setattr(settings_tui, "read_key", lambda: "ENTER")

    settings_tui._restore_from_backup(state)

    assert state.working.stt.compute_type == "int8"
    assert state.load_problems == []
    assert state.dirty is True  # restored value differs from `broken` (state.original)
    assert "restored from" in state.status


def test_restore_from_backup_cancelled_leaves_state_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "convobox.yaml"
    save_with_backup(path, _make_config(**{"stt.compute_type": "int8"}))  # no backup yet
    save_with_backup(path, AppConfig())  # backs up the int8 config into .backup-<stamp>

    config = AppConfig()
    state = TuiState(path=path, original=config, working=config.model_copy(deep=True))
    state.load_problems = ["stt: bad pairing"]

    monkeypatch.setattr(settings_tui, "_draw_modal", lambda *a, **k: None)
    monkeypatch.setattr(settings_tui, "read_key", lambda: "ESC")

    settings_tui._restore_from_backup(state)

    assert state.working == config  # unchanged
    assert state.load_problems == ["stt: bad pairing"]  # unchanged
    assert state.status == "restore cancelled"


def test_restore_from_backup_reports_when_none_exist(tmp_path: Path) -> None:
    path = tmp_path / "convobox.yaml"
    config = AppConfig()
    state = TuiState(path=path, original=config, working=config.model_copy(deep=True))

    settings_tui._restore_from_backup(state)

    assert "no backups found" in state.status


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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(line: str) -> int:
    """Length of `line` as it actually occupies terminal columns -- ANSI
    SGR codes are zero-width there but not to plain len(), same
    distinction _highlight_keys's own docstring calls out for fit()."""
    return len(_ANSI_RE.sub("", line))


def test_render_shows_too_small_message_below_minimum_usable_size(tmp_path: Path) -> None:
    # docs/KNOWN-ISSUES.md, "Settings TUI ignores real terminal size below
    # 80x24": render() previously forced width/height up to 80x24
    # regardless of the real terminal, so every line it emitted overflowed
    # a genuinely smaller terminal and got wrapped by the terminal itself,
    # garbling the redraw. Below the layout's own hard minimum
    # (_MIN_USABLE_WIDTH/_MIN_USABLE_HEIGHT), it must fall back to a
    # single explicit message instead of attempting a layout that can't
    # fit -- this is the first coverage of that fallback path at all.
    config = AppConfig()
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))

    lines = render(state, 60, 20)

    assert len(lines) == 1
    assert "too small" in lines[0].lower()
    assert "60x20" in lines[0]
    assert _visible_len(lines[0]) <= 60


def test_render_uses_full_layout_at_minimum_usable_size(tmp_path: Path) -> None:
    config = AppConfig()
    state = TuiState(path=tmp_path / "convobox.yaml", original=config, working=config.model_copy(deep=True))

    lines = render(state, settings_tui._MIN_USABLE_WIDTH, settings_tui._MIN_USABLE_HEIGHT)

    assert "ConvoBox Settings TUI" in "\n".join(lines)
    for line in lines:
        assert _visible_len(line) <= settings_tui._MIN_USABLE_WIDTH


def test_render_modal_does_not_inflate_a_real_narrow_terminal() -> None:
    # Same bug class as render() above, for the modal's own repaint path
    # (_draw_modal): render_modal() used to force width/height up to
    # 80x24 too, so a modal on a genuinely narrower/shorter real terminal
    # overflowed and wrapped exactly like the main screen did. Unlike
    # render(), the modal degrades via fit()'s own truncation rather than
    # a dedicated fallback message -- this just confirms no line exceeds
    # the REAL terminal size that was actually passed in.
    lines = render_modal("Confirm Save", "Save changes?", ["A detail line."], "", 40, 15)

    assert len(lines) <= 15
    for line in lines:
        assert _visible_len(line) <= 40


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


def test_render_modal_hint_override_replaces_generic_esc_enter_text() -> None:
    # Confirm Save uses this to spell out exactly what happens, since the
    # generic "Esc cancel | Enter confirm" doesn't say WHAT gets saved or
    # that cancelling discards nothing -- live UAT feedback, 2026-07-25.
    lines = render_modal(
        "Confirm Save",
        "Save changes to convobox.yaml?",
        ["This writes a backup first."],
        "",
        100,
        30,
        hint_override="Esc cancel without saving | Enter accept and save changes to convobox.yaml?",
    )
    joined = "\n".join(lines)
    assert "Esc cancel without saving | Enter accept and save changes to convobox.yaml?" in joined
    # The generic text must not also appear -- overridden, not appended.
    assert "Esc cancel | Enter confirm" not in joined
    assert "Esc cancel | Enter accept" not in joined


def test_save_confirmation_shows_explicit_hint_with_real_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = "en_US-lessac-medium"
    (tmp_path / f"{voice}.onnx").write_bytes(b"x")
    (tmp_path / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings_tui, "DEFAULT_VOICES_DIR", tmp_path)
    config = _make_config(**{"tts.voice": voice})
    path = tmp_path / "convobox.yaml"
    state = TuiState(path=path, original=AppConfig(), working=config)
    state.dirty = True

    captured: dict[str, object] = {}

    def _spy_confirm_modal(title: str, prompt: str, detail_lines: list[str], **kwargs: object) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(settings_tui, "_confirm_modal", _spy_confirm_modal)
    settings_tui._save(state)

    assert captured["hint_override"] == (
        f"Esc cancel without saving | Enter accept and save changes to {path}?"
    )
    # _save genuinely saved (the spy returned True, same as a real Enter press).
    assert state.dirty is False


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


def test_render_modal_input_line_shows_the_end_of_a_long_buffer_not_the_start() -> None:
    # Live UAT finding, 2026-08-10: editing a long stt.hotwords list, the
    # displayed "> {buffer}" line froze on the first N characters once the
    # buffer exceeded the visible width -- fit()'s ordinary head-then-"..."
    # truncation, applied to every OTHER content line, means whatever you
    # just typed at the end was invisible with zero feedback it even
    # changed. The input line must show the END (where the cursor always
    # is -- this editor only ever appends/backspaces, no cursor movement),
    # not the start.
    buffer = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november"
    lines = render_modal(
        "Edit Hotwords",
        "Editing stt.hotwords",
        ["Current: (unset)", "Space-separated words."],
        buffer,
        60,
        30,
    )
    joined = "\n".join(lines)
    # The tail of the buffer (what was most recently typed) must be
    # visible -- the OLD head-truncating behavior would never show this.
    assert "mike november" in joined
    # The very start of the buffer must NOT be fully present with nothing
    # missing -- some truncation must have actually happened, or this
    # test isn't exercising the overflow path at all.
    assert "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november" not in joined
    # A leading ellipsis marks the truncation, same visual language as
    # fit()'s own trailing "..." elsewhere in this file.
    assert "> ..." in joined


def test_render_modal_input_line_short_buffer_is_unaffected() -> None:
    # The tail-truncating input-line formatter must be a no-op (same as
    # plain fit()) when the buffer already fits -- no leading "...", no
    # missing characters.
    lines = render_modal(
        "Edit Voice", "Editing tts.voice", ["Current: (unset)", "hint"], "af_sarah", 100, 30
    )
    joined = "\n".join(lines)
    assert "> af_sarah" in joined
    assert "> ...af_sarah" not in joined


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
        lambda title, prompt, detail_lines, buffer="", **kwargs: drawn.extend(detail_lines),
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

import audio_devices


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

    config = AppConfig()
    device_spec = FieldSpec("audio", "input_device", "Input device", "device")
    assert settings_tui._choices_for(device_spec, config) == (settings_tui._SYSTEM_DEFAULT, "X, MME")

    choice_spec = FieldSpec("interaction", "interrupt_preset", "Preset", "choice", ("a", "b"))
    assert settings_tui._choices_for(choice_spec, config) == ("a", "b")

    bool_spec = FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool")
    assert settings_tui._choices_for(bool_spec, config) == ("false", "true")


# --- bool fields are pickable, not typed: live UAT feedback that Enter on
# a bool field opened a raw text buffer where a mistype (e.g. "flase")
# produced a bare ValueError instead of just being unselectable, 2026-07-22 ---


def test_edit_bool_field_cycles_with_space_like_a_choice_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool")
    keys = iter([" ", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))

    accepted, value = settings_tui._edit_value_interactive(spec, False, AppConfig())

    assert accepted is True
    assert value is True


def test_edit_bool_field_ignores_typed_keystrokes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stray printable keys must never reach the buffer for a bool field --
    # only LEFT/RIGHT/Space (cycling) and Enter/Esc are meaningful.
    spec = FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool")
    keys = iter(["f", "l", "a", "s", "e", "ENTER"])
    monkeypatch.setattr(settings_tui, "read_key", lambda: next(keys))

    accepted, value = settings_tui._edit_value_interactive(spec, True, AppConfig())

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
    accepted, value = settings_tui._edit_value_interactive(spec, None, AppConfig())

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
    accepted, value = settings_tui._edit_value_interactive(spec, "Speaker A, MME", AppConfig())

    assert accepted is True
    assert value == "Speaker A, MME"


@pytest.mark.asyncio
async def test_probe_stt_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeTranscriber:
        def transcribe(self, samples: np.ndarray) -> TranscriptResult:
            return TranscriptResult(
                text="hello there",
                language="en",
                language_probability=0.91,
                latency_ms=12.0,
                duration_s=1.0,
                avg_logprob=-0.05,
            )

    monkeypatch.setattr(settings_tui, "create_stt_engine", lambda stt_config: _FakeTranscriber())

    status = await settings_tui.probe_stt(AppConfig())

    assert "STT probe succeeded" in status
    assert "hello there" in status
    assert "en" in status


@pytest.mark.asyncio
async def test_probe_stt_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real regression guard: WhisperModel construction (inside
    # create_stt_engine) downloads the model on first use -- a genuinely
    # slow, blocking call. probe_stt must run it via asyncio.to_thread so
    # it can't freeze the TUI's render loop or, on the web UI, the whole
    # uvicorn server for every connected tab while one Test click
    # downloads. Simulated here with a synchronous time.sleep() standing
    # in for that slow call; a concurrently-running ticker task proves
    # the event loop stayed free during it.
    class _SlowTranscriber:
        def transcribe(self, samples: np.ndarray) -> TranscriptResult:
            time.sleep(0.3)
            return TranscriptResult(
                text="", language="en", language_probability=1.0,
                latency_ms=0.0, duration_s=0.0, avg_logprob=0.0,
            )

    monkeypatch.setattr(settings_tui, "create_stt_engine", lambda stt_config: _SlowTranscriber())

    ticks: list[float] = []

    async def _ticker() -> None:
        for _ in range(6):
            await asyncio.sleep(0.05)
            ticks.append(time.monotonic())

    await asyncio.gather(settings_tui.probe_stt(AppConfig()), _ticker())

    # If probe_stt blocked the event loop for its 0.3s "download", the
    # ticker couldn't have made any progress during that window.
    assert len(ticks) >= 4


def test_key_waiting_survives_a_stdin_with_no_fileno(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: pytest's own captured stdin under CI is a pseudofile
    # with no real fd -- select.select() raised io.UnsupportedOperation
    # here, live-confirmed on GitHub Actions for PR #196. Any real
    # invocation with stdin redirected/piped could hit the same class of
    # failure, not just tests.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    assert settings_tui._key_waiting() is False


def test_run_with_spinner_runs_to_completion_and_shows_elapsed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_tui, "draw", lambda state: None)
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))
    seen_statuses: list[str] = []

    def _slow_task() -> None:
        time.sleep(0.2)
        state.status = "done"

    orig_status_setter = TuiState.__setattr__

    def _capturing_setattr(self: TuiState, name: str, value: object) -> None:
        if name == "status":
            seen_statuses.append(value)  # type: ignore[arg-type]
        orig_status_setter(self, name, value)

    monkeypatch.setattr(TuiState, "__setattr__", _capturing_setattr)

    settings_tui._run_with_spinner(state, _slow_task)

    assert state.status == "done"
    # At least one spinner frame was shown before the final status landed.
    assert any("testing..." in s for s in seen_statuses[:-1])


def test_run_with_spinner_esc_returns_immediately_without_joining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ESC can't actually stop an in-flight download (no safe way to kill a
    # Python thread, no cancellation hook into huggingface_hub's transfer
    # loop) -- it must hand control back right away regardless of whether
    # `run` is still genuinely working, not block waiting for it to notice.
    monkeypatch.setattr(settings_tui, "draw", lambda state: None)
    monkeypatch.setattr(settings_tui, "_key_waiting", lambda: True)
    monkeypatch.setattr(settings_tui, "read_key", lambda: "ESC")
    config = AppConfig()
    state = TuiState(path=Path("convobox.yaml"), original=config, working=config.model_copy(deep=True))

    release = threading.Event()

    def _still_running_task() -> None:
        release.wait(timeout=2.0)
        state.status = "finished late, after cancel"

    settings_tui._run_with_spinner(state, _still_running_task)

    assert "cancelled" in state.status
    assert "background" in state.status
    release.set()  # let the orphaned daemon thread finish, don't leak it


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
        print("this must not leak to real stdout")

    def fake_test_input_device(
        sd: object, index: int, seconds: float = 3.0, playback_device: int | None = None
    ) -> tuple[float, float]:
        # test_input_device (not the lower-level record_test/level_meter)
        # is what probe_audio must call now -- it's the one function that
        # actually plays the recording back, matching what
        # `audio_devices.py --setup` does (live UAT feedback, 2026-07-22:
        # the old behavior only metered the mic, never let you hear it).
        calls.append(("test_input_device", playback_device))
        print("this must not leak either")
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

    def __enter__(self) -> Self:
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


def test_validate_errors_not_warns_when_claude_code_approve_has_no_phrase() -> None:
    # claude-code specifically doesn't fail safe the way codex does (see
    # detect_claude_code_approval_gap's docstring, GitHub issue #235
    # finding A1) -- this is a hard error, not the general warning above.
    config = _make_config(
        **{
            "backend.name": "claude-code",
            "backend.command": ["claude"],
            "backend.permission_mode": "approve",
        }
    )
    report = validate_config(config)
    assert any("nothing able to ever answer it" in e for e in report.errors)
    assert not any("approval_phrase is unset" in w for w in report.warnings)


def test_validate_does_not_error_when_claude_code_approve_has_a_phrase() -> None:
    config = _make_config(
        **{
            "backend.name": "claude-code",
            "backend.command": ["claude"],
            "backend.permission_mode": "approve",
            "interaction.approval_phrase": "juliette papa charlie",
        }
    )
    report = validate_config(config)
    assert not any("nothing able to ever answer it" in e for e in report.errors)


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


def test_run_tui_installs_and_restores_sigwinch_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # docs/KNOWN-ISSUES.md, "...never repaints on resize alone": the main
    # loop only redrew once per keypress, blocking inside read_key() the
    # rest of the time, so a resize while idle left the stale layout on
    # screen until the next key. run_tui() now installs a SIGWINCH handler
    # that repaints immediately; this confirms it's actually wired up
    # (fires a real draw on a real signal, not just present in the source)
    # and cleanly restored afterward rather than leaking into the rest of
    # the test process.
    if not hasattr(signal, "SIGWINCH"):
        pytest.skip("SIGWINCH is not available on this platform")

    path = tmp_path / "convobox.yaml"
    draw_calls: list[object] = []
    monkeypatch.setattr(settings_tui, "draw", lambda state: draw_calls.append(state))
    original_handler = signal.getsignal(signal.SIGWINCH)

    keys = iter(["q"])

    def fake_read_key() -> str:
        # Fired from inside the (already unblocked) read_key call rather
        # than from a real blocking read -- CPython delivers a pending
        # signal at the next bytecode boundary regardless, so the handler
        # still runs and repaints before this function returns "q".
        os.kill(os.getpid(), signal.SIGWINCH)
        return next(keys)

    monkeypatch.setattr(settings_tui, "read_key", fake_read_key)

    settings_tui.run_tui(path)

    assert len(draw_calls) >= 2  # the loop's own draw() + at least one resize-triggered draw
    assert signal.getsignal(signal.SIGWINCH) == original_handler
