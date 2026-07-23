"""Interactive settings TUI for editing and validating convobox.yaml.

The first cut is deliberately conservative:

- one config profile only
- staged edits in memory until explicit save
- backup + atomic replace on save
- validation before save
- section-level test hooks for TTS/STT/backend

It is stdlib-only plus the repo's own runtime modules, so it can run in the
same environments as the rest of ConvoBox.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from faster_whisper.utils import available_models
from pydantic import ValidationError

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/settings_tui.py`) and imported as
# scripts.settings_tui (e.g. from a pytest test).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console  # type: ignore[import-not-found]

from convobox.adapters import create_backend_adapter
from convobox.config import (
    AppConfig,
    BackendProfileConfig,
    detect_permission_conflict,
    load_config,
    read_aec_estimate,
    resolve_config_path,
)
from convobox.stt.factory import create_stt_engine
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine, resolve_voice_paths
from convobox.listening_pause import PauseListeningDetector
from convobox.resumeword import ROUNDTRIP_REJECTED_RESUME_WORDS, ResumeWordDetector

_RESET = "\x1b[0m"
_REVERSE = "\x1b[7m"
_BOLD = "\x1b[1m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"

# Keyboard shortcut names worth calling out wherever they appear in prose
# (the help panel's field-specific text, status/tip lines, modal footers) --
# live UAT feedback: a long help_text wall of text (e.g. permission_mode's,
# 400+ characters) buries the actual actionable keys inside it with no
# visual distinction from the surrounding sentence. Word-boundary matched
# so e.g. "Upload"/"Downtime" never trip a false highlight. Deliberately
# excludes single-letter shortcuts (T/S/R/Q) -- those appear as ordinary
# standalone words in normal English prose ("a value", "I recommend") far
# too often to highlight safely outside the one place they're unambiguous
# (the legend bar's own fixed "T test" / "S save" text, built by this
# module, not free-form prose).
_KEY_NAME_RE = re.compile(
    r"\b(Esc|Escape|Enter|Space|Tab|Left|Right|Up|Down|Home|End|PgUp|PgDn)\b"
)
# Single-letter shortcuts (T/S/R/Q/...), safe to highlight ONLY when
# bracketed -- that's the existing convention this module's own prose
# already uses for them ("Press [t] to test.", "press [t] to live-test
# first") specifically because a bare letter isn't distinguishable from
# ordinary prose but "[t]" unambiguously is. Live UAT feedback, 2026-07-22:
# on the screens where the user must actually decide whether to (q)uit,
# (s)ave, or press (Esc) to back out, the relevant key needs to be called
# out explicitly, not just present somewhere in a paragraph -- this is
# the mechanism that makes that possible without also risking a highlight
# on some unrelated bare "s" or "q" in normal sentence.
_BRACKET_KEY_RE = re.compile(r"\[([A-Za-z])\]")


def _highlight_keys(text: str) -> str:
    """Bold+color every recognized key name -- and every bracketed
    single-letter shortcut like ``[s]`` -- in `text`.

    Must only be called on text that has ALREADY been through `fit()` (or
    is never going through it again) -- inserted ANSI codes are zero-width
    on a real terminal but not to Python's `len()`, so fitting/padding
    AFTER highlighting would miscount the visible width and break column
    alignment. Same "style wraps the already-sized string" ordering this
    module already uses for `_REVERSE`-highlighted cells.
    """
    text = _KEY_NAME_RE.sub(lambda m: f"{_BOLD}{_CYAN}{m.group(0)}{_RESET}", text)
    return _BRACKET_KEY_RE.sub(lambda m: f"{_BOLD}{_CYAN}{m.group(0)}{_RESET}", text)

_CHOICE_BACKENDS = ("opencode", "claude-code", "codex")
_CHOICE_PERMISSION_MODES = ("plan", "approve", "permissive")
_CHOICE_TTS_ENGINES = ("piper",)
_CHOICE_STT_ENGINES = ("faster-whisper",)
# Pulled from the real dependency (faster_whisper.utils.available_models()),
# not a hand-maintained duplicate -- stays correct automatically as
# faster-whisper adds/removes models across versions, same "construct the
# real thing rather than guess" preference this codebase already applies
# elsewhere (e.g. ResumeWordDetector/ApprovalDetector as the validators).
_CHOICE_STT_MODELS = tuple(available_models())
_CHOICE_STT_DEVICES = ("auto", "cpu", "cuda")
# Keep in sync with convobox.interrupt_presets.PRESETS's keys (config.py
# validates the actual value against that dict at load time; this tuple is
# just what the TUI offers to pick from).
_CHOICE_INTERRUPT_PRESETS = ("conversational", "patient", "do-not-disturb", "halt", "take-over")
_BACKEND_PROFILE_DEFAULTS: dict[str, BackendProfileConfig] = {
    "opencode": BackendProfileConfig(url="http://localhost:4096"),
    "claude-code": BackendProfileConfig(url="http://localhost:4096", command=["claude"]),
    "codex": BackendProfileConfig(url="http://localhost:4096", command=["codex"]),
}


@dataclass(frozen=True)
class FieldSpec:
    section: str
    key: str
    label: str
    kind: Literal[
        "str",
        "optional_str",
        "int",
        "optional_int",
        "optional_float",
        "float",
        "bool",
        "choice",
        "device",
        "list_str",
        "command",
    ]
    choices: tuple[str, ...] = ()
    help_text: str = ""


@dataclass(frozen=True)
class SectionSpec:
    key: str
    label: str
    fields: tuple[FieldSpec, ...]


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errors


@dataclass
class TuiState:
    path: Path
    original: AppConfig
    working: AppConfig
    selected_section: int = 0
    selected_field: int = 0
    dirty: bool = False
    status: str = "BIOS style: Left/Right tabs, Up/Down fields, Enter edit"
    last_report: ValidationReport | None = None

    @property
    def sections(self) -> tuple[SectionSpec, ...]:
        return SECTION_SPECS

    def current_section(self) -> SectionSpec:
        return self.sections[self.selected_section]

    def current_fields(self) -> tuple[FieldSpec, ...]:
        return _visible_fields_for_section(self.working, self.current_section())

    def current_field(self) -> FieldSpec | None:
        fields = self.current_fields()
        if not fields:
            return None
        self.selected_field = max(0, min(self.selected_field, len(fields) - 1))
        return fields[self.selected_field]

    def move_section(self, delta: int) -> None:
        self.selected_section = max(0, min(self.selected_section + delta, len(self.sections) - 1))
        self.selected_field = min(self.selected_field, max(0, len(self.current_fields()) - 1))

    def move_field(self, delta: int) -> None:
        fields = self.current_fields()
        if not fields:
            return
        self.selected_field = max(0, min(self.selected_field + delta, len(fields) - 1))


SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="audio",
        label="Audio",
        fields=(
            FieldSpec("audio", "input_device", "Input device", "device", help_text="Space/Left/Right cycles real discovered microphones (same list scripts/audio_devices.py --setup offers); leave unset for the system default. Press [t] to test -- this records ~1s from the mic and plays the recording back through the configured output device, so you can actually hear whether it picked you up."),
            FieldSpec("audio", "output_device", "Output device", "device", help_text="Space/Left/Right cycles real discovered speakers (same list scripts/audio_devices.py --setup offers); leave unset for the system default. Press [t] to test."),
            FieldSpec("audio", "sample_rate", "Sample rate", "int", help_text="Mic capture rate in Hz. 16000 is the default because STT and VAD both expect it."),
            FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool", help_text="Enable acoustic echo cancellation when using open speakers in the same room. Space/Left/Right toggles true/false."),
            FieldSpec("audio", "aec_delay_ms", "AEC delay ms", "optional_int", help_text="Render-to-capture delay in milliseconds. Leave unset (recommended) to auto-tune from real stream latencies on every startup -- see 'Last auto-detected' below. Set a fixed number only to override auto-tuning with a value you've specifically measured for this hardware; a wrong fixed value is the #1 cause of weak echo suppression. To clear an already-set value back to unset: delete the digits, then type - (a bare minus sign) and press Enter -- an empty field alone is treated as 'no change', not 'clear', so backspacing to blank and pressing Enter leaves the old value in place."),
        ),
    ),
    SectionSpec(
        key="stt",
        label="STT",
        fields=(
            FieldSpec("stt", "engine", "Engine", "choice", _CHOICE_STT_ENGINES, help_text="Speech-to-text backend. Only faster-whisper is implemented right now."),
            FieldSpec("stt", "model", "Model", "choice", _CHOICE_STT_MODELS, help_text="Whisper model size/variant. base (default) is a good speed/accuracy balance. small/medium/large-v3 trade speed for accuracy (large-v3 is the most accurate, slowest, and biggest download). The distil-* variants are distilled models: noticeably faster than their full-size counterpart at a small accuracy cost -- distil-large-v3 is a common sweet spot if base isn't accurate enough but large-v3 feels too slow. .en variants (tiny.en, base.en, ...) are English-only and slightly more accurate for English than the multilingual equivalent. Downloads automatically on first use (one-time, cached in the Hugging Face cache) -- switching models here doesn't fetch anything until you actually run a session with it."),
            FieldSpec("stt", "device", "Device", "choice", _CHOICE_STT_DEVICES, help_text="Inference device. auto (default) autodetects a real GPU (e.g. NVIDIA CUDA) and falls back to cpu if none is visible. Pick cpu or cuda explicitly only to override the autodetection -- e.g. to keep a GPU free for another process, or because cuda is detected but not actually usable (missing CUDA runtime libraries like cuBLAS: LocalTranscriber falls back to cpu permanently for the session either way once that happens, but picking cpu here silences the one-time warning)."),
            FieldSpec("stt", "compute_type", "Compute type", "str", help_text="Whisper compute precision. 'default' (recommended) picks the right precision for whichever device was selected above (int8 on cpu, float16 on GPU). Set an explicit value (int8, float16, ...) only to override."),
            FieldSpec("stt", "language", "Language", "optional_str", help_text="Pin a language code like en, or leave unset for auto-detect."),
            FieldSpec("stt", "min_language_probability", "Min language probability", "float", help_text="Drop auto-detected transcripts below this confidence threshold."),
        ),
    ),
    SectionSpec(
        key="tts",
        label="TTS",
        fields=(
            FieldSpec("tts", "engine", "Engine", "choice", _CHOICE_TTS_ENGINES, help_text="Text-to-speech backend. Piper is the first supported local engine."),
            FieldSpec("tts", "voice", "Voice", "optional_str", help_text="Installed Piper voice key, such as en_US-lessac-medium."),
            FieldSpec("tts", "speaker", "Speaker", "optional_str", help_text="Only for multi-speaker voices (e.g. en_GB-semaine-medium, en_GB-aru-medium, en_GB-vctk-medium, en_US-libritts-high) -- a speaker name from that voice's own list, or a raw numeric index. Leave unset for single-speaker voices or the voice's own default speaker. [t] will report an error naming the available speakers if this doesn't match."),
            FieldSpec("tts", "rate", "Rate", "float", help_text="Speech speed multiplier. 1.0 is normal."),
            FieldSpec("tts", "volume", "Volume", "float", help_text="Speech loudness multiplier. 1.0 is normal."),
        ),
    ),
    SectionSpec(
        key="backend",
        label="Backend",
        fields=(
            FieldSpec("backend", "name", "Name", "choice", _CHOICE_BACKENDS, help_text="Which coding agent ConvoBox should drive."),
            FieldSpec("backend", "url", "URL", "str", help_text="HTTP/SSE endpoint for OpenCode."),
            FieldSpec("backend", "model", "Model", "optional_str", help_text="opencode only: provider/model-id to pin (e.g. openai/gpt-5.6-sol -- see `opencode models` for the full list). Leave unset for opencode's own default -- which may be a hosted free-tier model, not necessarily your own configured provider. NOT a CLI flag: `opencode serve` has no -m option; this is sent via the session-creation API instead."),
            FieldSpec("backend", "command", "Command", "command", help_text="Base CLI command for subprocess backends such as Claude Code or Codex. Space-separated, e.g. `codex.cmd --model gpt-5.6-terra` -- NOT comma-separated like the list fields elsewhere in this TUI (e.g. safeword phrases); a stray comma becomes part of the argument text and the command will fail to launch."),
            FieldSpec("backend", "permission_mode", "Permission mode", "choice", _CHOICE_PERMISSION_MODES, help_text="How much the coding agent may DO. plan: read-only, cannot write or run commands (safe default). approve: may act, but every write/command needs voice approval via your approval_phrase -- real on both Codex (native per-call approval channel) and Claude Code (a PreToolUse hook this adapter builds itself, since headless mode has no native one -- see claude_code.py's module docstring). permissive: acts without asking (dangerous). No effect on opencode (set at `opencode serve` launch). Do NOT also set a permission flag in Command -- that's a conflict."),
            FieldSpec("backend", "working_dir", "Working dir", "optional_str", help_text="The directory the spawned coding agent (Codex/Claude Code) runs and EDITS files in. SECURITY: leave unset and the agent inherits ConvoBox's own directory -- a voice session could then modify ConvoBox's source. Point it at an isolated workspace (a scratch/UAT dir separate from any repo you care about) so the agent's edits land there. No effect on opencode (its dir is set by where `opencode serve` was launched). Override per-run with run_convobox.py --working-dir."),
        ),
    ),
    SectionSpec(
        key="interaction",
        label="Interaction",
        fields=(
            FieldSpec("interaction", "interrupt_preset", "Interrupt preset", "choice", _CHOICE_INTERRUPT_PRESETS, help_text="do-not-disturb (default, safe without headphones/AEC): finish, drop overlap. conversational: mute+steer now. patient: finish, then deliver. halt: abort, drop. take-over: abort, steer now."),
            FieldSpec("interaction", "barge_in_min_speech_ms", "Barge-in min speech ms", "int", help_text="How long speech must continue before it counts as a real interruption."),
            FieldSpec("interaction", "resume_word", "Resume word", "str", help_text="Say this to RESUME after a pause phrase (also the push-word barge-in trigger). Pick something DISTINCT and unlikely in normal conversation (so you don't resume by accident) and clearly transcribable by Whisper (so it matches reliably without needing a corrections-glossary entry). The old default 'ConvoBox' failed both -- confidently mis-heard as 'Control Box' every time. 'Athena' is the round-trip-verified default. Verify a custom word with scripts/roundtrip_smoketest.py first; a warning fires at save time for words already known to mis-transcribe."),
            FieldSpec("interaction", "pause_listening_phrases", "Pause phrases", "list_str", help_text="Comma-separated. Saying one hard-stops in-flight work and pauses listening until the resume word resumes. Same picking rule as the resume word: DISTINCT, unlikely in normal conversation, and cleanly Whisper-transcribable -- a phrase you say naturally mid-conversation would pause the session unexpectedly. Defaults: 'stop listening, pause listening'."),
            FieldSpec("interaction", "approval_phrase", "Approval phrase", "optional_str", help_text="Opt-in command/file approvals for Codex or Claude Code (needs backend.permission_mode: approve above). Leave unset to keep the safe default: every approval request is denied automatically, no prompts. When set, say this exact phrase to approve a pending request; say 'no' to deny; silence for approval_timeout_s denies. Use a distinctive multi-word phrase -- plain 'yes' is deliberately rejected. Same STT-reliability caution as the resume word: pick something clearly Whisper-transcribable. A NATO-alphabet-style phrase (e.g. 'juliette papa charlie') tends to round-trip more reliably than ordinary words -- verify with scripts/roundtrip_smoketest.py before relying on it."),
            FieldSpec("interaction", "approval_timeout_s", "Approval timeout s", "float", help_text="How long a pending approval waits for a voice decision before silence is treated as an explicit denial (never as consent)."),
        ),
    ),
    SectionSpec(
        key="safeword",
        label="Safeword",
        fields=(FieldSpec("safeword", "hard_stop_phrases", "Hard stop phrases", "list_str", help_text="Comma-separated phrases that immediately hard-stop the current turn."),),
    ),
    SectionSpec(
        key="vad",
        label="VAD",
        fields=(
            FieldSpec("vad", "threshold", "Threshold", "float", help_text="Silero VAD speech-probability threshold."),
            FieldSpec("vad", "min_silence_ms", "Min silence ms", "int", help_text="Trailing silence needed to end an utterance."),
            FieldSpec("vad", "min_speech_ms", "Min speech ms", "int", help_text="Minimum speech burst to keep as a real utterance."),
            FieldSpec("vad", "max_utterance_s", "Max utterance s", "optional_float", help_text="Force an utterance to end after this many seconds, even without silence."),
        ),
    ),
)


def _visible_fields_for_section(config: AppConfig, section: SectionSpec) -> tuple[FieldSpec, ...]:
    if section.key != "backend":
        return section.fields
    backend_name = config.backend.name
    if backend_name == "opencode":
        return tuple(field for field in section.fields if field.key in {"name", "url", "model"})
    if backend_name in {"claude-code", "codex"}:
        return tuple(
            field for field in section.fields
            if field.key in {"name", "command", "working_dir", "permission_mode"}
        )
    return section.fields


def fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) > width:
        return text[: width - 3] + "..." if width > 3 else text[:width]
    return text.ljust(width)


def viewport_start(selected: int, total: int, height: int, current_start: int) -> int:
    if total <= height:
        return 0
    start = current_start
    if selected < start:
        start = selected
    elif selected >= start + height:
        start = selected - height + 1
    return max(0, min(start, total - height))


def default_config_path() -> Path:
    return resolve_config_path()


def _section_model(config: AppConfig, section: str) -> Any:
    return getattr(config, section)


def _get_value(config: AppConfig, spec: FieldSpec) -> Any:
    return getattr(_section_model(config, spec.section), spec.key)


def _set_value(config: AppConfig, spec: FieldSpec, value: Any) -> None:
    setattr(_section_model(config, spec.section), spec.key, value)


def _backend_profile_defaults(name: str) -> BackendProfileConfig:
    profile = _BACKEND_PROFILE_DEFAULTS.get(name)
    if profile is None:
        return BackendProfileConfig()
    return profile.model_copy(deep=True)


def _backend_profile_value(config: AppConfig, name: str) -> BackendProfileConfig:
    profile = config.backend_profiles.get(name)
    if profile is not None:
        return profile.model_copy(deep=True)
    return _backend_profile_defaults(name)


def _set_backend_profile(config: AppConfig, name: str, profile: BackendProfileConfig) -> None:
    config.backend_profiles[name] = profile.model_copy(deep=True)


def _backend_profile_from_active(config: AppConfig, name: str) -> BackendProfileConfig:
    if name == "opencode":
        return BackendProfileConfig(url=config.backend.url, model=config.backend.model)
    if name in {"claude-code", "codex"}:
        return BackendProfileConfig(
            url=config.backend.url,
            command=list(config.backend.command) if config.backend.command is not None else None,
        )
    return BackendProfileConfig(
        url=config.backend.url,
        command=list(config.backend.command) if config.backend.command is not None else None,
    )


def _apply_backend_profile(config: AppConfig, name: str) -> None:
    profile = _backend_profile_value(config, name)
    defaults = _backend_profile_defaults(name)
    config.backend.name = name
    resolved_url = profile.url if profile.url is not None else defaults.url
    if resolved_url is not None:
        config.backend.url = resolved_url
    if name == "opencode":
        config.backend.command = None
        config.backend.model = profile.model if profile.model is not None else defaults.model
    else:
        config.backend.model = None
        if profile.command is not None:
            config.backend.command = list(profile.command)
        else:
            config.backend.command = list(defaults.command) if defaults.command is not None else None


def _switch_backend(config: AppConfig, new_name: str) -> None:
    current_name = config.backend.name
    if new_name == current_name:
        return
    _set_backend_profile(config, current_name, _backend_profile_from_active(config, current_name))
    _apply_backend_profile(config, new_name)


def _format_value(value: Any) -> str:
    if value is None:
        return "(unset)"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "(empty)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_value(spec: FieldSpec, raw: str, current: Any) -> Any:
    text = raw.strip()
    if spec.kind == "bool":
        if not text:
            return current
        lowered = text.lower()
        if lowered in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "f", "no", "n", "off"}:
            return False
        raise ValueError("enter yes/no, true/false, or 1/0")
    if spec.kind == "choice":
        if not text:
            return current
        for choice in spec.choices:
            if text.lower() == choice.lower():
                return choice
        raise ValueError(f"choose one of: {', '.join(spec.choices)}")
    if spec.kind == "int":
        if not text:
            return current
        return int(text)
    if spec.kind == "float":
        if not text:
            return current
        return float(text)
    if spec.kind == "optional_float":
        if text == "-":
            return None
        if not text:
            return current
        return float(text)
    if spec.kind == "optional_int":
        if text == "-":
            return None
        if not text:
            return current
        return int(text)
    if spec.kind == "command":
        if text == "-":
            return None
        if not text:
            return current
        return shlex.split(text)
    if spec.kind == "list_str":
        if text == "-":
            return []
        if not text:
            return current
        return [item.strip() for item in text.split(",") if item.strip()]
    if spec.kind in ("optional_str", "device"):
        # device's cycled-to-sentinel case (_SYSTEM_DEFAULT -> None) is
        # handled by the caller before this is ever reached (see
        # _edit_value_interactive) -- this branch only sees typed text, so
        # it can mirror optional_str's convention exactly: '-' clears,
        # empty keeps current, unchanged.
        if text == "-":
            return None
        if not text:
            return current
        return text
    if not text:
        return current
    return text


# The device picker's "leave unset" choice. Deliberately NOT "" -- an empty
# buffer already means something else in the edit modal (user backspaced
# everything / never typed anything -> _parse_value's "keep current"
# convention, same as every other optional_str-shaped field). Using a
# visually distinct, unambiguous sentinel means cycling here vs. typing an
# empty buffer can never be confused with each other.
_SYSTEM_DEFAULT = "(system default)"


def _device_choices(kind: Literal["input", "output"]) -> list[str]:
    """Real, deduped device names for the picker.

    Same discovery/dedup logic as `python scripts/audio_devices.py --setup`
    (collect_devices + dedupe_devices, imported and called directly, not
    reimplemented) -- so the choices offered here exactly match what that
    tool would suggest. `_SYSTEM_DEFAULT` is always first so cycling can
    return to "leave unset". Device enumeration must never crash the TUI --
    if sounddevice/PortAudio can't be queried for any reason, degrade to
    just the default sentinel rather than raising into the render loop.
    """
    try:
        import sounddevice as sd

        import audio_devices as ad
    except Exception:  # noqa: BLE001
        return [_SYSTEM_DEFAULT]
    try:
        devices = ad.dedupe_devices(ad.collect_devices(sd, kind))
    except Exception:  # noqa: BLE001
        return [_SYSTEM_DEFAULT]
    return [_SYSTEM_DEFAULT] + [f"{d['name']}, {d['hostapi']}" for d in devices]


def _choices_for(spec: FieldSpec) -> tuple[str, ...]:
    """The live choice list for a field -- static for `choice` fields,
    freshly enumerated (real connected devices) for `device` fields, a
    fixed true/false pair for `bool` fields (live UAT feedback,
    2026-07-22: a typed bool field let a mistype like "flase" through to
    a raw ValueError instead of just being unselectable).
    """
    if spec.kind == "device":
        kind: Literal["input", "output"] = "input" if spec.key == "input_device" else "output"
        return tuple(_device_choices(kind))
    if spec.kind == "bool":
        return ("false", "true")
    return spec.choices


def _choice_index(spec: FieldSpec, current: Any) -> int:
    choices = _choices_for(spec)
    if not choices:
        return -1
    # Device fields are str | None; None maps to _SYSTEM_DEFAULT (always
    # index 0, see _device_choices) so cycling from unset advances to the
    # first real device instead of appearing to do nothing.
    lookup = current if current is not None else _SYSTEM_DEFAULT
    try:
        return choices.index(lookup)
    except ValueError:
        text = str(lookup).lower()
        for index, choice in enumerate(choices):
            if choice.lower() == text:
                return index
    return -1


def _cycle_choice(spec: FieldSpec, current: Any, delta: int) -> str:
    choices = _choices_for(spec)
    if not choices:
        raise ValueError("no choices configured")
    idx = _choice_index(spec, current)
    return choices[(idx + delta) % len(choices)]


def _read_leading_header(path: Path) -> list[str]:
    if not path.exists():
        return []
    leading: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            leading.append(line)
        else:
            break
    return leading


def _dump_config(config: AppConfig) -> str:
    # exclude_defaults=True: only fields whose value actually differs from
    # AppConfig's own schema default get written. Confirmed live (2026-07-15
    # incident): a plain model_dump() writes EVERY field, including ones a
    # user never touched, so a single save silently baked a stale
    # aec_delay_ms=100 into convobox.yaml and permanently disabled AEC
    # delay auto-tuning -- the user had no way to tell "set on purpose"
    # from "just what a full dump happened to produce". A field omitted
    # from the YAML loads back to the exact same default value via
    # load_config()/AppConfig's own defaults, so this changes what gets
    # WRITTEN, not what gets LOADED -- verified via a real save/reload
    # round-trip in tests/test_settings_tui.py.
    return yaml.safe_dump(config.model_dump(mode="python", exclude_defaults=True), sort_keys=False)


def backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def write_config(path: Path, config: AppConfig) -> None:
    header = _read_leading_header(path)
    body = _dump_config(config)
    content = ("\n".join(header) + "\n") if header else ""
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=str(path.parent), prefix=f".{path.name}."
    ) as tmp:
        tmp.write(content + body)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def save_with_backup(path: Path, config: AppConfig) -> Path | None:
    backup = backup_config(path)
    try:
        write_config(path, config)
    except Exception:
        if backup is not None and backup.exists():
            with contextlib.suppress(Exception):
                os.replace(backup, path)
        raise
    return backup


def validate_config(config: AppConfig) -> ValidationReport:
    report = ValidationReport()
    try:
        AppConfig.model_validate(config.model_dump(mode="python"))
    except ValidationError as exc:
        report.errors.append(str(exc))
        return report

    if config.backend.name not in _CHOICE_BACKENDS:
        report.errors.append(
            f"backend.name {config.backend.name!r} is not supported here "
            f"(implemented: {', '.join(_CHOICE_BACKENDS)})"
        )
    if config.stt.engine not in _CHOICE_STT_ENGINES:
        report.errors.append(
            f"stt.engine {config.stt.engine!r} is not supported here "
            f"(implemented: {', '.join(_CHOICE_STT_ENGINES)})"
        )
    if config.stt.model not in _CHOICE_STT_MODELS:
        # A warning, not an error: this list comes from the installed
        # faster-whisper version's own available_models() -- an older
        # saved convobox.yaml naming a model that version has since
        # dropped (or a genuinely custom/local model path) shouldn't be
        # hard-blocked, just flagged for a second look.
        report.warnings.append(
            f"stt.model {config.stt.model!r} is not one of the models this "
            f"installed faster-whisper version lists "
            f"({', '.join(_CHOICE_STT_MODELS)}) -- double-check it's intentional"
        )
    if config.stt.device not in _CHOICE_STT_DEVICES:
        # A warning, not an error: unlike stt.engine (checked against
        # convobox's OWN supported-engines list), stt.device is passed
        # straight through to ctranslate2/faster-whisper, which may accept
        # values beyond these three (e.g. a specific GPU index) -- this
        # only exists to nudge a stale/typo'd value from an existing
        # convobox.yaml, not to hard-block something ctranslate2 itself
        # might honor.
        report.warnings.append(
            f"stt.device {config.stt.device!r} is not one of the values the "
            f"Settings TUI offers ({', '.join(_CHOICE_STT_DEVICES)}) -- it will "
            "still be passed through to faster-whisper as-is, but double-check "
            "it's intentional"
        )
    if config.tts.engine not in _CHOICE_TTS_ENGINES:
        report.errors.append(
            f"tts.engine {config.tts.engine!r} is not supported here "
            f"(implemented: {', '.join(_CHOICE_TTS_ENGINES)})"
        )
    if config.tts.engine == "piper":
        if not config.tts.voice:
            report.errors.append("tts.voice is required when tts.engine is piper")
        else:
            try:
                resolve_voice_paths(config.tts.voice, DEFAULT_VOICES_DIR)
            except FileNotFoundError as exc:
                report.errors.append(str(exc))

    if config.backend.name in {"claude-code", "codex"}:
        if not config.backend.command:
            report.errors.append(
                f"backend.command is required when backend.name is {config.backend.name!r}"
            )
        else:
            # A token ending in a stray comma is a near-certain typo, not a
            # real command argument -- this field is space-separated
            # (shlex-style: "claude --model x"), unlike list_str fields
            # elsewhere in this same TUI (e.g. safeword.hard_stop_phrases),
            # which ARE comma-separated. Live-found 2026-07-22: typing
            # "codex.cmd, --model, gpt-5.6-terra" here (following that
            # OTHER convention by habit) parses via shlex.split into
            # ["codex.cmd,", "--model,", "gpt-5.6-terra"] -- syntactically
            # valid-looking, silently wrong, and only surfaced as a bare
            # `FileNotFoundError: [WinError 2]` deep in a live session
            # crash, with nothing connecting it back to the typo. A hard
            # error (not a warning) since a trailing comma is never a
            # legitimate argument, unlike a not-yet-on-PATH executable.
            comma_tokens = [t for t in config.backend.command if t.endswith(",")]
            if comma_tokens:
                fixed = [t.rstrip(",") for t in config.backend.command]
                report.errors.append(
                    f"backend.command token(s) {comma_tokens!r} end with a comma -- "
                    f"this field is space-separated, not comma-separated (did you mean {fixed!r}?)"
                )
            elif shutil.which(config.backend.command[0]) is None:
                # Dependency-level check: the backend is a local CLI
                # ConvoBox spawns, so if its executable isn't on PATH the
                # loop will fail with a bare FileNotFoundError at first
                # utterance. A warning (not an error) surfaces it at save
                # time without blocking -- PATH at edit time may
                # legitimately differ from run time.
                report.warnings.append(
                    f"backend command {config.backend.command[0]!r} was not found on PATH -- "
                    f"is {config.backend.name} installed? it will fail to start until it is"
                )
    if config.backend.name == "opencode" and not config.backend.url.startswith(("http://", "https://")):
        report.warnings.append(
            "backend.url does not start with http:// or https://; the connection may fail"
        )
    if config.backend.name in {"claude-code", "codex"}:
        working_dir = config.backend.working_dir
        if not working_dir:
            report.warnings.append(
                f"backend.working_dir is unset -- the {config.backend.name} agent "
                "will run in ConvoBox's own directory and can modify its source. "
                "Point it at an isolated workspace."
            )
        elif not Path(working_dir).expanduser().is_dir():
            report.warnings.append(
                f"backend.working_dir {working_dir!r} is not an existing directory "
                "(it will fail at startup until created)"
            )
    conflict = detect_permission_conflict(config.backend)
    if conflict is not None:
        report.errors.append(conflict)
    if config.backend.permission_mode == "approve" and not config.interaction.approval_phrase:
        report.warnings.append(
            "backend.permission_mode is 'approve' but interaction.approval_phrase is "
            "unset -- every approval request will be denied automatically with no "
            "voice prompt (the safe fail-closed default, but likely not what you "
            "intended when choosing 'approve')"
        )
    if (
        config.backend.name == "opencode"
        and config.backend.model is not None
        and "/" not in config.backend.model
    ):
        # BackendConfig's own field_validator catches this at model
        # CONSTRUCTION time, but the TUI mutates an already-constructed
        # AppConfig's fields via plain setattr() (no validate_assignment),
        # so a bad value typed into this field would otherwise sit
        # unflagged until the next full config reload -- surface it here
        # too, at save time, matching every other backend field's own
        # save-time check on this same code path.
        report.errors.append(
            f'backend.model {config.backend.model!r} must be "provider/model-id" '
            f'(e.g. "openai/gpt-5.6-sol") -- see `opencode models` for the full list'
        )
    try:
        # The real runtime constructor is the validator: run_convobox.py
        # builds this exact detector at startup, so a value it rejects
        # (normalizes to nothing) would crash the session before the first
        # utterance. Same save-time-check rationale as backend.model above.
        detector = ResumeWordDetector(config.interaction.resume_word)
    except ValueError as exc:
        report.errors.append(f"interaction.resume_word: {exc}")
    else:
        if detector.normalized_resume_word in ROUNDTRIP_REJECTED_RESUME_WORDS:
            report.warnings.append(
                f"interaction.resume_word {config.interaction.resume_word!r} is confirmed to "
                "mis-transcribe through the real TTS->STT round-trip (see "
                "convobox.resumeword.detector) -- the resume word will likely never match, "
                "leaving 'stop listening' with no voice resume. 'Athena' is the "
                "verified default; test alternatives with scripts/roundtrip_smoketest.py."
            )
    # Empty pause phrases would leave no way to pause a live session; a
    # phrase normalizing to nothing could never match. Same
    # construct-the-real-detector rationale as the resume word above:
    # PauseListeningDetector is what run_convobox.py builds at startup.
    if not config.interaction.pause_listening_phrases:
        report.warnings.append(
            "interaction.pause_listening_phrases is empty -- there will be no "
            "way to pause a live listening session by voice."
        )
    else:
        try:
            PauseListeningDetector(config.interaction.pause_listening_phrases)
        except ValueError as exc:
            report.errors.append(f"interaction.pause_listening_phrases: {exc}")
    if config.audio.sample_rate <= 0:
        report.errors.append("audio.sample_rate must be positive")
    if config.audio.aec_delay_ms is not None and config.audio.aec_delay_ms < 0:
        report.errors.append("audio.aec_delay_ms must be non-negative")
    if config.vad.threshold < 0 or config.vad.threshold > 1:
        report.errors.append("vad.threshold must be between 0 and 1")
    return report


async def probe_tts(config: AppConfig) -> str:
    engine = create_tts_engine(config.tts, DEFAULT_VOICES_DIR)
    chunks = []
    async for chunk in engine.synthesize_stream("ConvoBox settings test."):
        chunks.append(chunk)
        if sum(len(item) for item in chunks) > 48000:
            break
    engine.stop()
    total = sum(len(chunk) for chunk in chunks)
    return f"TTS probe succeeded ({total} samples @ {engine.sample_rate} Hz)"


async def probe_stt(config: AppConfig) -> str:
    transcriber = create_stt_engine(config.stt)
    silence = np.zeros(int(config.audio.sample_rate), dtype=np.float32)
    result = transcriber.transcribe(silence)
    return (
        "STT probe succeeded "
        f"(text={result.text!r}, lang={result.language}, "
        f"confidence={result.language_probability:.2f})"
    )


async def probe_backend(config: AppConfig) -> str:
    adapter = create_backend_adapter(config.backend)
    consumer: asyncio.Task[None] | None = None

    async def _consume() -> None:
        async for _ in adapter.events():
            return

    try:
        consumer = asyncio.create_task(_consume())
        await asyncio.wait_for(adapter.wait_listening(timeout=0.5), timeout=1.0)
        await asyncio.sleep(0.15)
        probe_error = consumer.exception() if consumer.done() else None
        if probe_error is not None:
            if isinstance(probe_error, FileNotFoundError):
                cmd = (config.backend.command or [config.backend.name])[0]
                raise RuntimeError(
                    f"backend executable {cmd!r} not found -- is {config.backend.name} "
                    "installed and on PATH?"
                ) from probe_error
            raise probe_error
    finally:
        if consumer is not None:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer
        await adapter.aclose()
    return f"Backend probe started for {config.backend.name!r}"


async def probe_audio(config: AppConfig) -> str:
    """Play a short tone on the configured speaker; record the configured
    mic and play the recording back through the speaker so you can
    actually hear whether the right mic is picking you up -- a level
    meter alone (the old behavior here) confirms *something* is being
    captured but not that it's the mic you think it is, or that it
    sounds right. Live UAT feedback, 2026-07-22.

    Reuses scripts/audio_devices.py's own device-resolution, tone, and
    record+playback functions directly (collect_devices/resolve_spec/
    play_test_tone/test_input_device) -- the same logic
    `python scripts/audio_devices.py --setup` uses, not a reimplementation
    -- shortened for a quick in-TUI check and silenced (that script is a
    CLI tool that prints; every other probe here reports through
    state.status, not stdout, so stray prints would flicker across the
    render loop until the next redraw wipes them).
    """
    import io

    import sounddevice as sd

    import audio_devices as ad

    results: list[str] = []
    with contextlib.redirect_stdout(io.StringIO()):
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, out_err = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx, out_err = ad._default_index(sd, "output"), None
        if out_idx is not None:
            name = sd.query_devices(out_idx)["name"]
            ad.play_test_tone(sd, out_idx, seconds=0.6)
            results.append(f"speaker OK: played 0.6s tone on {name!r}")
        else:
            results.append(f"speaker: {out_err or 'no device found'}")

        in_devices = ad.collect_devices(sd, "input")
        if config.audio.input_device:
            in_idx, in_err = ad.resolve_spec(config.audio.input_device, in_devices)
        else:
            in_idx, in_err = ad._default_index(sd, "input"), None
        if in_idx is not None:
            rms_db, peak_db = ad.test_input_device(
                sd, in_idx, seconds=1.2, playback_device=out_idx
            )
            results.append(f"mic: {ad.format_level(rms_db, peak_db)} (played back)")
        else:
            results.append(f"mic: {in_err or 'no device found'}")

    return " | ".join(results)


def _section_summary(config: AppConfig) -> list[str]:
    report = validate_config(config)
    lines = [
        f"backend: {config.backend.name}  tts: {config.tts.engine}/{config.tts.voice or '(unset)'}  "
        f"stt: {config.stt.engine}/{config.stt.model}  audio: {config.audio.input_device or 'default'} -> "
        f"{config.audio.output_device or 'default'}",
    ]
    if report.warnings:
        lines.append("warnings: " + " | ".join(report.warnings[:2]))
    if report.errors:
        lines.append("errors: " + " | ".join(report.errors[:2]))
    return lines


def _section_tabs(state: TuiState, width: int) -> str:
    tabs: list[str] = []
    for idx, section in enumerate(state.sections):
        label = f" {section.label} "
        if idx == state.selected_section:
            tabs.append(f"{_REVERSE}[{label}]{_RESET}")
        else:
            tabs.append(f"[{label}]")
    tabs_line = " ".join(tabs)
    return fit(tabs_line, width)


def _field_hint(spec: FieldSpec) -> str:
    if spec.kind == "choice":
        return f"choices: {', '.join(spec.choices)}"
    if spec.kind == "device":
        return "Space/Left/Right cycles discovered devices, or type a name/index ('-' to clear)"
    if spec.kind == "command":
        return "enter command line text, or '-' to clear"
    if spec.kind == "list_str":
        return "comma-separated list, or '-' to clear"
    if spec.kind in {"optional_str", "optional_float", "optional_int"}:
        return "enter text, or '-' to clear"
    if spec.kind == "bool":
        return "enter yes/no, true/false, or 1/0"
    return "enter a new value"


def _wrap_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [""]
    wrapped = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
    return wrapped or [""]


def _aec_estimate_summary(config_path: Path) -> str:
    """Read-only diagnostic for the aec_delay_ms field's help panel --
    what run_convobox.py last auto-detected on this machine, from the
    sidecar file it writes (never convobox.yaml itself; see
    config.write_aec_estimate's docstring for why). Best-effort: never
    raises, degrades to an explanatory placeholder if nothing's been
    measured yet."""
    estimate = read_aec_estimate(config_path)
    if estimate is None:
        return "Last auto-detected: none yet -- run a live session with AEC on at least once."
    return (
        f"Last auto-detected: {estimate.get('delay_ms')}ms "
        f"(out {estimate.get('output_latency_ms')}ms + in {estimate.get('input_latency_ms')}ms "
        f"+ 10ms, measured {estimate.get('measured_at')})"
    )


def _help_panel_lines(state: TuiState, width: int, height: int) -> list[str]:
    spec = state.current_field()
    if spec is None:
        return ["", ""]
    title = f"{spec.section.upper()} / {spec.label}"
    value = f"Value: {_format_value(_get_value(state.working, spec))}"
    hint = spec.help_text or _field_hint(spec)
    lines = [title, value, ""]
    lines.extend(_wrap_text(hint, width))
    if spec.section == "backend":
        lines.append("")
        lines.extend(
            _wrap_text(
                "Backend profiles are remembered per backend: opencode uses url, claude-code/codex use command.",
                width,
            )
        )
    if spec.kind == "choice" and spec.choices:
        lines.append("")
        lines.extend(_wrap_text("Choices: " + ", ".join(spec.choices), width))
    if spec.section == "audio" and spec.key == "aec_delay_ms":
        lines.append("")
        lines.extend(_wrap_text(_aec_estimate_summary(state.path), width))
    extra = [
        f"Key: {spec.section}.{spec.key}",
        f"Type: {spec.kind}",
    ]
    lines.extend([""] + extra)
    return lines[:height]


def render_modal(
    title: str,
    prompt: str,
    detail_lines: list[str],
    buffer: str,
    width: int,
    height: int,
    severity: Literal["normal", "destructive"] = "normal",
    choice_options: list[str] | None = None,
    choice_value: str | None = None,
) -> list[str]:
    width = max(width, 80)
    height = max(height, 24)
    border = "=" if severity == "destructive" else "-"
    accent = f"{_RED}{_BOLD}" if severity == "destructive" else f"{_CYAN}{_BOLD}"
    tone = " DANGER " if severity == "destructive" else " INFO "
    lines: list[str] = []
    lines.append(fit(f" ConvoBox Settings TUI | {title} ", width))
    lines.append(fit(f" status: {prompt}", width))
    # Reverse-video, same treatment as the main screen's own legend bar --
    # a modal is exactly where "what do Esc/Enter actually do right now"
    # needs to be unmissable, not read off as part of the status line.
    lines.append(
        _REVERSE
        + fit(
            f" {tone}{'Esc cancel | Enter confirm' if severity == 'normal' else 'Esc back out carefully | Enter confirm'} ",
            width,
        )
        + _RESET
    )
    lines.append(accent + "+" + border * (width - 2) + "+" + _RESET)
    body_height = height - 6
    content_lines = [f"{tone.strip()} {title}", "", prompt, ""]
    content_lines.extend(detail_lines)
    if choice_options:
        selected = choice_value if choice_value in choice_options else (choice_options[0] if choice_options else None)
        content_lines.extend(["", "Options:"])
        for option in choice_options:
            marker = ">" if option == selected else " "
            content_lines.append(f" {marker} {option}")
    content_lines.append("")
    content_lines.append(
        "Esc cancel | Enter accept"
        if severity == "normal"
        else "Esc back out carefully | Enter accept"
    )
    content_lines.append(f"> {buffer}")
    # Sized off the actual content, not just the input buffer -- a detail
    # line (e.g. a save/quit hint) longer than the old fixed floor was
    # silently truncated mid-word since fit() has no wrapping.
    longest_line = max((len(line) for line in content_lines), default=0)
    box_width = min(width - 4, max(52, longest_line + 4, len(buffer) + 8))
    left_pad = max(0, (width - box_width) // 2 - 1)
    right_pad = max(0, width - left_pad - box_width - 2)
    box_top = " " * left_pad + border + border * (box_width - 2) + border + " " * right_pad
    lines.append(box_top[:width])
    inner_width = box_width - 2
    for idx in range(body_height):
        if idx < len(content_lines):
            # _highlight_keys AFTER fit(), same ordering rule as the main
            # screen's help panel -- see that function's own docstring.
            inner = _highlight_keys(fit(content_lines[idx], inner_width))
        else:
            inner = fit("", inner_width)
        lines.append(
            " " * left_pad
            + "|"
            + inner
            + "|"
            + " " * max(0, width - left_pad - box_width - 2)
        )
    lines.append(box_top[:width])
    tip = (
        " Tip: Escape cancels the modal and returns to the editor"
        if severity == "normal"
        else " Tip: Escape returns to the editor without changing anything"
    )
    lines.append(_highlight_keys(fit(tip, width)))
    return lines[:height]


def _draw_modal(
    title: str,
    prompt: str,
    detail_lines: list[str],
    buffer: str,
    severity: Literal["normal", "destructive"] = "normal",
    choice_options: list[str] | None = None,
    choice_value: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> None:
    if width is None or height is None:
        try:
            size = os.get_terminal_size()
            width = size.columns if width is None else width
            height = size.lines if height is None else height
        except OSError:
            width = 100 if width is None else width
            height = 30 if height is None else height
    sys.stdout.write(
        "\x1b[H"
        + "\n".join(
            render_modal(
                title,
                prompt,
                detail_lines,
                buffer,
                width,
                height,
                severity=severity,
                choice_options=choice_options,
                choice_value=choice_value,
            )
        )
    )
    sys.stdout.flush()


def _edit_value_interactive(spec: FieldSpec, current: Any) -> tuple[bool, Any]:
    is_pickable = spec.kind in ("choice", "device", "bool")
    # Device fields are str | None; _format_value(None) is the display
    # string "(unset)", which isn't in _choices_for's list and would break
    # cycling/index-lookup. Seed the buffer with the picker's own sentinel
    # instead so an unset device field starts aligned with choice index 0,
    # same as a "choice" field (which is never None) already is.
    if spec.kind == "device" and current is None:
        buffer = _SYSTEM_DEFAULT
    else:
        buffer = _format_value(current)
    hint = spec.help_text or _field_hint(spec)
    prompt = f"Editing {spec.section}.{spec.key}"
    if is_pickable:
        detail_lines = [
            f"Current: {_format_value(current)}",
            hint,
            "Use Left/Right or Space to cycle choices, Enter to accept, Esc to cancel.",
        ]
    else:
        detail_lines = [f"Current: {_format_value(current)}", hint]
    choice_options = list(_choices_for(spec)) if is_pickable else None
    _draw_modal(
        f"Edit {spec.label}",
        prompt,
        detail_lines,
        buffer,
        choice_options=choice_options,
        choice_value=buffer if is_pickable else None,
    )
    while True:
        key = read_key()
        if key == "ESC":
            return False, current
        if key == "ENTER":
            accepted = buffer
            if spec.kind == "device" and buffer == _SYSTEM_DEFAULT:
                # Unambiguous: this only happens via cycling (typing never
                # produces this exact sentinel text), so it always means
                # "the user explicitly picked system default," never
                # _parse_value's "buffer is empty, keep current" case.
                return True, None
            return True, _parse_value(spec, accepted, current)
        if is_pickable:
            if key in {"LEFT", "UP"}:
                buffer = _cycle_choice(spec, buffer, -1)
                _draw_modal(
                    f"Edit {spec.label}",
                    prompt,
                    detail_lines,
                    buffer,
                    choice_options=choice_options,
                    choice_value=buffer,
                )
                continue
            if key in {"RIGHT", "DOWN", " "}:
                buffer = _cycle_choice(spec, buffer, 1)
                _draw_modal(
                    f"Edit {spec.label}",
                    prompt,
                    detail_lines,
                    buffer,
                    choice_options=choice_options,
                    choice_value=buffer,
                )
                continue
        if spec.kind != "bool":
            # A bool field only ever has two valid values, both reachable
            # by cycling above -- no typed value can ever be more correct
            # than that, so typing here can only ever produce a mistype
            # (see _choices_for's docstring for the incident).
            if key == "BACKSPACE":
                buffer = buffer[:-1]
            elif len(key) == 1 and key.isprintable():
                buffer += key
        _draw_modal(
            f"Edit {spec.label}",
            prompt,
            [f"Current: {_format_value(current)}", hint],
            buffer,
            choice_options=choice_options,
            choice_value=buffer if is_pickable else None,
        )


def _confirm_modal(
    title: str,
    prompt: str,
    detail_lines: list[str],
    severity: Literal["normal", "destructive"] = "normal",
) -> bool:
    while True:
        _draw_modal(title, prompt, detail_lines, "", severity=severity)
        key = read_key()
        if key == "ESC":
            return False
        if key == "ENTER":
            return True


def render(state: TuiState, width: int, height: int) -> list[str]:
    width = max(width, 80)
    height = max(height, 24)
    left_width = max(36, min(54, width // 2 + 4))
    right_width = max(24, width - left_width - 3)
    lines: list[str] = []
    # Explicit and highlighted when dirty (live UAT feedback, 2026-07-22):
    # a plain "dirty" label is easy to miss entirely; the moment there ARE
    # unsaved changes is exactly when the save/quit keys matter most, so
    # name them right here instead of leaving the operator to find them in
    # the legend bar on their own.
    dirty_indicator = "dirty -- [S] to save, [Q] to quit and discard" if state.dirty else "clean"
    header = f" ConvoBox Settings TUI | {dirty_indicator} | {state.path}"
    lines.append(_highlight_keys(fit(header, width)))
    summary = _section_summary(state.working)
    lines.append(fit(summary[0], width))
    status = f" status: {state.status}"
    if len(summary) > 1:
        status += f" | {summary[1]}"
    lines.append(fit(status, width))
    lines.append(_section_tabs(state, width))
    lines.append("+" + "-" * (width - 2) + "+")

    body_height = height - 10
    field_count = len(state.current_fields())
    field_start = viewport_start(state.selected_field, field_count, body_height, 0)
    visible_fields = state.current_fields()[field_start : field_start + body_height]
    help_lines = _help_panel_lines(state, right_width, body_height)

    for row in range(body_height):
        left_cell = ""
        if row < len(visible_fields):
            spec = visible_fields[row]
            value = _get_value(state.working, spec)
            pointer = ">" if (field_start + row) == state.selected_field else " "
            left_cell = f"{pointer} {spec.label:<28.28} {_format_value(value)}"
            if (field_start + row) == state.selected_field:
                left_cell = _REVERSE + fit(left_cell, left_width) + _RESET
            else:
                left_cell = fit(left_cell, left_width)
        else:
            left_cell = fit("", left_width)

        right_cell = help_lines[row] if row < len(help_lines) else ""
        # _highlight_keys AFTER fit(): its ANSI codes are zero-width on a
        # real terminal but not to len(), so highlighting first would throw
        # off fit()'s own padding/truncation math -- see that function's
        # own docstring.
        lines.append(f"{left_cell} | {_highlight_keys(fit(right_cell, right_width))}")

    lines.append("+" + "-" * (width - 2) + "+")
    # Reverse-video legend bar, same treatment the selected section tab
    # already gets -- a dedicated, visually unmissable "what can I press
    # right now" area, not another line of plain text easy to skim past
    # while reading a long help panel. Kept on ONE line (not wrapped into a
    # multi-line legend): the six-shortcut set here never changes across
    # sections/fields, so a single scannable bar covers it.
    lines.append(
        _REVERSE
        + fit(
            " Keys: Left/Right tabs  Up/Down fields  Enter edit  Space toggle/cycle  "
            "T test  S save  R revert  Q quit",
            width,
        )
        + _RESET
    )
    lines.append(_highlight_keys(fit(f" Tip: {state.status}", width)))
    return lines


def _enable_ansi() -> None:
    if os.name == "nt":
        os.system("")  # nosec B605 B607


def read_key() -> str:
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT", "G": "HOME", "O": "END"}.get(code, "")
        if ch == "\r":
            return "ENTER"
        if ch == "\x08":
            return "BACKSPACE"
        if ch == "\x1b":
            return "ESC"
        return ch

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            if ch in ("\r", "\n"):
                return "ENTER"
            if ch == "\x7f":
                return "BACKSPACE"
            return ch
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return "ESC"
        seq = sys.stdin.read(1)
        if seq != "[":
            return "ESC"
        code = sys.stdin.read(1)
        return {"A": "UP", "B": "DOWN", "D": "LEFT", "C": "RIGHT", "H": "HOME", "F": "END"}.get(code, "")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def draw(state: TuiState) -> None:
    size = os.get_terminal_size()
    sys.stdout.write("\x1b[H" + "\n".join(render(state, size.columns, size.lines)))
    sys.stdout.flush()


def _toggle_or_cycle(state: TuiState) -> None:
    spec = state.current_field()
    if spec is None:
        state.status = "nothing to change in this section"
        return
    current = _get_value(state.working, spec)
    new_value: bool | str | None
    if spec.kind == "bool":
        new_value = not bool(current)
    elif spec.kind in ("choice", "device"):
        try:
            new_value = _cycle_choice(spec, current, 1)
        except ValueError:
            state.status = "no choices configured"
            return
        if spec.kind == "device" and new_value == _SYSTEM_DEFAULT:
            # The underlying field is str | None; unset means None, not
            # the display sentinel.
            new_value = None
    else:
        state.status = "space toggles booleans and cycles choices only"
        return
    if spec.section == "backend" and spec.key == "name":
        # backend.name is a choice field, so new_value is a str here; str()
        # makes that explicit for the type checker.
        _switch_backend(state.working, str(new_value))
    else:
        _set_value(state.working, spec, new_value)
    state.dirty = state.working.model_dump(mode="python") != state.original.model_dump(mode="python")
    state.status = f"{spec.label} updated"


def _prompt_edit(state: TuiState) -> None:
    spec = state.current_field()
    if spec is None:
        state.status = "nothing to edit in this section"
        return
    current = _get_value(state.working, spec)
    try:
        accepted, new_value = _edit_value_interactive(spec, current)
    except Exception as exc:  # noqa: BLE001
        state.status = f"invalid value: {exc}"
        return
    if not accepted:
        state.status = "edit cancelled"
        return
    if spec.section == "backend" and spec.key == "name":
        _switch_backend(state.working, new_value)
    else:
        _set_value(state.working, spec, new_value)
    state.dirty = state.working.model_dump(mode="python") != state.original.model_dump(mode="python")
    state.status = f"{spec.label} updated"


def _restore_original(state: TuiState) -> None:
    state.working = state.original.model_copy(deep=True)
    state.dirty = False
    state.last_report = None
    state.status = "staged changes reverted"


def _save(state: TuiState) -> None:
    report = validate_config(state.working)
    state.last_report = report
    if report.errors:
        state.status = "save blocked: " + report.errors[0]
        return
    if report.warnings:
        state.status = "warning: " + report.warnings[0]
    detail = ["This writes a backup first and then atomically replaces the config."]
    if report.warnings:
        detail.append("")
        detail.append("Warnings (save still allowed):")
        detail.extend(f"  - {warning}" for warning in report.warnings)
        detail.append("")
        detail.append("Tip: press [t] to live-test the selected backend/engine first.")
    if not _confirm_modal(
        "Confirm Save",
        f"Save changes to {state.path}?",
        detail,
    ):
        state.status = "save cancelled"
        return
    try:
        save_with_backup(state.path, state.working)
    except Exception as exc:  # noqa: BLE001
        state.status = f"save failed: {exc}"
        return
    state.original = state.working.model_copy(deep=True)
    state.dirty = False
    state.status = f"saved to {state.path}"


async def _test_state(state: TuiState) -> None:
    section = state.current_section().key
    report = validate_config(state.working)
    state.last_report = report
    if report.errors:
        state.status = "test blocked: " + report.errors[0]
        return
    try:
        if section == "tts":
            state.status = await probe_tts(state.working)
        elif section == "stt":
            state.status = await probe_stt(state.working)
        elif section == "backend":
            state.status = await probe_backend(state.working)
        elif section == "audio":
            state.status = await probe_audio(state.working)
        else:
            state.status = f"{section} configuration validated"
    except Exception as exc:  # noqa: BLE001
        state.status = f"{section} test failed: {type(exc).__name__}: {exc}"


def _handle_browse(state: TuiState, key: str) -> bool:
    lowered = key.lower() if len(key) == 1 else key
    if lowered in ("q", "esc"):
        if state.dirty and not _confirm_modal(
            "Confirm Quit",
            "Discard unsaved changes and quit?",
            [
                "Unsaved edits will be lost if you confirm.",
                "",
                "Changed your mind? Press Esc now, then [S] to save first.",
            ],
            severity="destructive",
        ):
            state.status = "quit cancelled"
            return True
        return False
    if key == "UP":
        state.move_field(-1)
    elif key == "DOWN":
        state.move_field(1)
    elif key == "LEFT":
        state.move_section(-1)
        state.selected_field = 0
    elif key == "RIGHT":
        state.move_section(1)
        state.selected_field = 0
    elif key == "HOME":
        state.selected_field = 0
        state.selected_section = 0
    elif key == "END":
        state.selected_section = len(state.sections) - 1
        state.selected_field = max(0, len(state.current_fields()) - 1)
    elif key == "ENTER":
        _prompt_edit(state)
    elif lowered == " ":
        _toggle_or_cycle(state)
    elif lowered == "r":
        if _confirm_modal(
            "Confirm Revert",
            "Revert staged changes back to the last saved config?",
            ["This only resets the working copy; the file on disk is unchanged."],
            severity="destructive",
        ):
            _restore_original(state)
        else:
            state.status = "revert cancelled"
    elif lowered == "s":
        _save(state)
    elif lowered == "t":
        asyncio.run(_test_state(state))
    return True


def run_tui(config_path: Path | None = None) -> None:
    path = config_path or default_config_path()
    config = load_config(path)
    state = TuiState(path=path, original=config, working=config.model_copy(deep=True))
    _enable_ansi()
    sys.stdout.write("\x1b[?25l\x1b[2J")
    sys.stdout.flush()
    try:
        running = True
        while running:
            draw(state)
            key = read_key()
            if not key:
                continue
            running = _handle_browse(state, key)
    finally:
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()
    print(state.status)


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default=None, help="path to a convobox.yaml file")
    args = parser.parse_args()
    run_tui(Path(args.config) if args.config else None)


if __name__ == "__main__":
    main()
