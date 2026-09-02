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
import functools
import io
import json
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
import textwrap
import threading
import time
from collections.abc import Callable, Coroutine
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

from _console import read_key, use_utf8_console  # type: ignore[import-not-found]

from convobox.adapters import create_backend_adapter
from convobox.config import (
    STT_COMPUTE_TYPES,
    AppConfig,
    BackendProfileConfig,
    TTSConfig,
    TTSProfileConfig,
    detect_claude_code_approval_gap,
    detect_permission_conflict,
    load_config,
    load_config_lenient,
    read_aec_estimate,
    resolve_config_path,
)
from convobox.listening_pause import PauseListeningDetector
from convobox.resumeword import ROUNDTRIP_REJECTED_RESUME_WORDS, ResumeWordDetector
from convobox.stt.base import TranscriptResult
from convobox.stt.factory import create_stt_engine
from convobox.tts.factory import (
    DEFAULT_VOICES_DIR,
    create_tts_engine,
    list_kokoro_voices,
    refresh_kokoro_voices,
    resolve_voice_paths,
)

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
_CHOICE_TTS_ENGINES = ("kokoro", "piper")
_CHOICE_STT_ENGINES = ("faster-whisper",)
# Pulled from the real dependency (faster_whisper.utils.available_models()),
# not a hand-maintained duplicate -- stays correct automatically as
# faster-whisper adds/removes models across versions, same "construct the
# real thing rather than guess" preference this codebase already applies
# elsewhere (e.g. ResumeWordDetector/ApprovalDetector as the validators).
_CHOICE_STT_MODELS = tuple(available_models())
_CHOICE_STT_DEVICES = ("auto", "cpu", "cuda")
# config.py's STT_COMPUTE_TYPES is the single source of truth (hardcoded
# there, not dynamically queried from ctranslate2 -- see that constant's
# own comment for why: a 2+ second first-import cost, paid on every
# STTConfig construction if it weren't hardcoded). Reused here rather
# than duplicated so the picker and config.py's own validator can never
# drift apart.
_CHOICE_STT_COMPUTE_TYPES = STT_COMPUTE_TYPES
# Keep in sync with convobox.interrupt_presets.PRESETS's keys (config.py
# validates the actual value against that dict at load time; this tuple is
# just what the TUI offers to pick from).
_CHOICE_INTERRUPT_PRESETS = ("conversational", "patient", "do-not-disturb", "halt", "take-over")
# "file" (a user-supplied sound) isn't implemented yet -- not offered here
# until it is; config.py's own validator rejects anything but these two.
_CHOICE_PAUSE_RESUME_ACK = ("none", "tone")
_BACKEND_PROFILE_DEFAULTS: dict[str, BackendProfileConfig] = {
    "opencode": BackendProfileConfig(url="http://localhost:4096"),
    "claude-code": BackendProfileConfig(url="http://localhost:4096", command=["claude"]),
    "codex": BackendProfileConfig(url="http://localhost:4096", command=["codex"]),
}
# Same per-engine-memory pattern as _BACKEND_PROFILE_DEFAULTS above --
# schema defaults per tts.engine, used both to seed a never-configured
# profile and to fill any field a saved profile leaves unset (None).
_TTS_PROFILE_DEFAULTS: dict[str, TTSProfileConfig] = {
    "kokoro": TTSProfileConfig(
        voice="af_sarah",
        model_path=".models/kokoro/kokoro-v1.0.onnx",
        voices_path=".models/kokoro/voices-v1.0.bin",
        language="en-us",
        rate=1.0,
    ),
    "piper": TTSProfileConfig(rate=1.0, volume=1.0),
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
        "kokoro_voice",
        "piper_voice",
        "piper_speaker",
    ]
    choices: tuple[str, ...] = ()
    help_text: str = ""


@dataclass(frozen=True)
class SectionSpec:
    key: str
    label: str
    fields: tuple[FieldSpec, ...]
    # True for every section except "display" -- confirmed by reading
    # every call site (2026-08-07, JP asked live after hitting the
    # restart-to-see-a-color-change friction firsthand): run_convobox.py's
    # main() reads load_config() exactly once at startup and constructs
    # the whole mic-loop pipeline (audio streams, STT/TTS engines, the
    # backend subprocess, VAD, safeword/interaction state) from that one
    # snapshot -- none of it re-reads the file live, so every section
    # genuinely needs a restart today. display.* is the one exception:
    # grepped for every read of config.display and it's ONLY ever passed
    # into web/app.py's create_app() to serve GET /api/config -- nothing
    # in the mic loop touches it. Per-SECTION, not per-field, because
    # that's the real granularity the current architecture has -- would
    # need updating if a future section gets split hot/cold internally.
    restart_required: bool = True


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
    # Populated at startup only, when load_config_lenient() had to fall a
    # section back to defaults because the on-disk value failed validation
    # (docs/field-notes/2026-08-06-settings-tui-cannot-open-invalid-config.md).
    # Each entry is "section: message" -- see load_config_lenient's own
    # docstring. Cleared the moment the operator saves or restores a backup,
    # not on every ordinary edit -- it describes what happened at LOAD, not
    # the current validity of state.working (validate_config/_save already
    # covers that separately).
    load_problems: list[str] = field(default_factory=list)

    @property
    def sections(self) -> tuple[SectionSpec, ...]:
        return SECTION_SPECS

    @property
    def flagged_sections(self) -> set[str]:
        return {p.split(":", 1)[0].strip() for p in self.load_problems}

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
            FieldSpec("audio", "input_device", "Input device", "device", help_text="Space/Left/Right cycles real discovered microphones (same list scripts/audio_devices.py --setup offers); leave unset for the system default. Press [t] to test -- this records ~3s from the mic (watch the live level bar in the status line while you speak) and plays the recording back through the configured output device, so you can actually hear whether it picked you up."),
            FieldSpec("audio", "output_device", "Output device", "device", help_text="Space/Left/Right cycles real discovered speakers (same list scripts/audio_devices.py --setup offers); leave unset for the system default. Press [t] to test."),
            FieldSpec("audio", "sample_rate", "Sample rate", "int", help_text="Mic capture rate in Hz. 16000 is the default because STT and VAD both expect it."),
            FieldSpec("audio", "echo_cancellation", "Echo cancellation", "bool", help_text="Enable acoustic echo cancellation when using open speakers in the same room. Space/Left/Right toggles true/false."),
            FieldSpec("audio", "aec_delay_ms", "AEC delay ms", "optional_int", help_text="Render-to-capture delay in milliseconds. Leave unset (recommended) to auto-tune from real stream latencies on every startup -- see 'Last auto-detected' below. Set a fixed number only to override auto-tuning with a value you've specifically measured for this hardware; a wrong fixed value is the #1 cause of weak echo suppression. To clear an already-set value back to unset: delete the digits, then type - (a bare minus sign) and press Enter -- an empty field alone is treated as 'no change', not 'clear', so backspacing to blank and pressing Enter leaves the old value in place."),
            FieldSpec("audio", "aec_ns", "Noise suppression (advanced)", "bool", help_text="Advanced. WebRTC APM's noise suppression stage, layered on top of echo_cancellation (no effect if that's off). Off by default. Live-tested 2026-08-31 on one open-speaker setup (docs/field-notes/2026-08-31-issue-323-ns-agc-open-speaker-trial-agc-hurts-ns-mildly-helps.md, GitHub issue #323): enabling this at aec_ns_level=2 measured a real, consistent improvement -- 15% fewer false barge-ins, +0.46dB more suppression, lower residual mic noise, across N=8 live trials. Cross-platform (Windows/Linux) confirmation is still pending -- see docs/KNOWN-ISSUES.md's NS/AGC entry before relying on this away from the tested Mac mini setup. Space/Left/Right toggles true/false."),
            FieldSpec("audio", "aec_ns_level", "NS level (advanced)", "int", help_text="Advanced. Only matters if Noise suppression above is on. 0=low, 1=moderate, 2=high, 3=very high (WebRTC APM's own enum). 2 (high) is this field's own default, from the 2026-08-31 trial referenced above. A 2026-09-01 same-machine follow-up (see that field note's later section) found 3 (very high) beats 2 in two independent passes (24%/33% fewer false barge-ins) -- still same-machine data pending cross-platform confirmation (docs/UAT-checklist.md's [E10]), so the default hasn't moved, but 3 is worth trying if 2 doesn't feel like enough on your setup."),
            FieldSpec("audio", "aec_agc", "Auto gain control (advanced)", "bool", help_text="Advanced. WebRTC APM's legacy AGC1, layered on top of echo_cancellation (no effect if that's off). Off by default -- and tested WORSE, not just untested: the 2026-08-31 trial (docs/field-notes/2026-08-31-issue-323-ns-agc-open-speaker-trial-agc-hurts-ns-mildly-helps.md, GitHub issue #323) measured this at agc_mode=1 giving 29% MORE false barge-ins and suppression cut nearly in half (9.91dB -> 6.00dB) vs baseline. It amplifies whatever's left in the mic signal AFTER echo cancellation already ran, including residual echo -- not the raw pre-AEC signal the original 'tame a hot mic' idea assumed. Exposed here for advanced users on setups meaningfully different from the tested one (different mic/speaker geometry, headset, etc.) who want to verify it independently, not as a recommendation. Space/Left/Right toggles true/false."),
            FieldSpec("audio", "aec_agc_mode", "AGC mode (advanced)", "int", help_text="Advanced. Only matters if Auto gain control above is on. 0=adaptive analog, 1=adaptive digital, 2=fixed digital (WebRTC APM's own enum). 1 (adaptive digital) is the binding's own default and the one tested above -- which measured worse than AGC off, see that field's help text."),
        ),
    ),
    SectionSpec(
        key="stt",
        label="STT",
        fields=(
            FieldSpec("stt", "engine", "Engine", "choice", _CHOICE_STT_ENGINES, help_text="Speech-to-text backend. Only faster-whisper is implemented right now."),
            FieldSpec("stt", "model", "Model", "choice", _CHOICE_STT_MODELS, help_text="Whisper model size/variant. base (default) is a good speed/accuracy balance. small/medium/large-v3 trade speed for accuracy (large-v3 is the most accurate, slowest, and biggest download). The distil-* variants are distilled models: noticeably faster than their full-size counterpart at a small accuracy cost -- distil-large-v3 is a common sweet spot if base isn't accurate enough but large-v3 feels too slow. .en variants (tiny.en, base.en, ...) are English-only and slightly more accurate for English than the multilingual equivalent. Downloads automatically on first use (one-time, cached in the Hugging Face cache) -- switching models here doesn't fetch anything until you actually run a session with it."),
            FieldSpec("stt", "device", "Device", "choice", _CHOICE_STT_DEVICES, help_text="Inference device. auto (default) autodetects a real GPU (e.g. NVIDIA CUDA) and falls back to cpu if none is visible. Pick cpu or cuda explicitly only to override the autodetection -- e.g. to keep a GPU free for another process, or because cuda is detected but not actually usable (missing CUDA runtime libraries like cuBLAS: LocalTranscriber falls back to cpu permanently for the session either way once that happens, but picking cpu here silences the one-time warning)."),
            FieldSpec("stt", "compute_type", "Compute type", "choice", _CHOICE_STT_COMPUTE_TYPES, help_text="Precision/quantization tradeoff: lower precision = faster + less memory, higher = more accurate. default (recommended): NOT a fixed 'int8 on cpu' -- uses the model's own saved precision, falling back per-device only if unsupported (live-confirmed on a real CPU: a model saved as float16 fell back to float32, not int8, when the CPU lacked efficient float16 support). Set an explicit value below if you want a specific precision guaranteed. float32 is the ceiling -- the model itself was trained in float32, so nothing more precise exists to recover accuracy from (no float64). int8: smallest/fastest, most quantization loss. int8_float32 (cpu) / int8_float16, int8_bfloat16 (cuda): quantized weights with higher-precision math -- a real middle ground, more accurate than plain int8 while still lighter than full precision. int16: cpu-only quantized alternative to int8. float16/bfloat16 (cuda only): near-float32 accuracy, much faster than float32 -- ctranslate2's own recommended GPU default is float16; bfloat16 trades a little precision for better numerical stability on newer GPUs. Not every value works on every device (e.g. bfloat16 needs cuda) -- an incompatible pairing fails clearly at [t] Test, not silently."),
            FieldSpec("stt", "language", "Language", "optional_str", help_text="Pin a language code like en, or leave unset for auto-detect."),
            FieldSpec("stt", "min_language_probability", "Min language probability", "float", help_text="Drop auto-detected transcripts below this confidence threshold."),
            FieldSpec("stt", "hotwords", "Hotwords", "optional_str", help_text="Space-separated words/phrases faster-whisper should be biased toward recognizing. Live UAT finding: a short resume/wake word got repeatedly hallucinated as unrelated fluent sentences instead of misheard as something similar -- Whisper's known failure mode on short/low-signal clips. Put your resume word, safeword phrases, and approval phrase here to bias toward exactly the short critical phrases most likely to hit this. Accuracy nudge only, not a safety mechanism -- the safeword/resume-word checks still run on the raw transcript regardless."),
            FieldSpec("stt", "condition_on_previous_text", "Condition on previous text", "bool", help_text="faster-whisper default: on. Disabling stops a low-signal/short utterance's decode from being biased by whatever fluent text the PREVIOUS segment produced -- a documented contributor to the same short-clip hallucination pattern hotwords addresses. A real tradeoff, not yet live-validated -- worth testing if hotwords alone doesn't fully fix a short resume/wake word, not a default recommendation."),
            FieldSpec("stt", "temperature", "Temperature", "optional_float", help_text="Leave unset (recommended) for faster-whisper's own fallback ladder (0.0, 0.2, 0.4, ... up to 1.0, each retried on a low-confidence decode). Pin to a single value -- 0.0 for fully deterministic, no-fallback decoding -- to test whether the ladder's own higher-temperature retries are contributing to hallucination on an already-short/low-signal clip. Not yet live-validated -- worth testing, not a default recommendation."),
            FieldSpec("stt", "repetition_penalty", "Repetition penalty", "optional_float", help_text="Leave unset for faster-whisper's own default (1.0, no penalty). Real live incident, 2026-08-07: a 1.056s clip of a single 'That's right.' decoded as that phrase repeated 8 times -- a genuine decoder repetition-loop pathology, not an app bug (one transcribe() call, one already-corrupted output). Values above 1.0 penalize the decoder for repeating tokens it's already produced -- try 1.1-1.3 first. Not yet live-validated -- worth testing if you're hitting runaway repetition, not a default recommendation (an untested value could make normal decodes worse, not just fix the rare case)."),
            FieldSpec("stt", "no_repeat_ngram_size", "No-repeat n-gram size", "optional_int", help_text="Leave unset for faster-whisper's own default (0, disabled). The blunter sibling to repetition_penalty above, for the same runaway-repetition failure mode: blocks the decoder from repeating any run of this many tokens twice in one decode. 3 is a reasonable first value to try. Not yet live-validated -- worth testing alongside or instead of repetition_penalty, not a default recommendation."),
        ),
    ),
    SectionSpec(
        key="tts",
        label="TTS",
        fields=(
            FieldSpec("tts", "engine", "Engine", "choice", _CHOICE_TTS_ENGINES, help_text="Text-to-speech backend. kokoro (default) is permissively licensed (MIT + Apache-2.0); piper is GPL-3.0 and requires the separate `piper` extra (`uv sync --extra piper`). Each engine's own voice/settings are remembered when you switch away and back."),
            FieldSpec("tts", "voice", "Voice", "optional_str", help_text="piper only: an installed Piper voice key, such as en_US-lessac-medium."),
            FieldSpec("tts", "model_path", "Model path", "str", help_text="kokoro only: path to the kokoro-v1.0.onnx model file."),
            FieldSpec("tts", "voices_path", "Voices path", "str", help_text="kokoro only: path to the voices-v1.0.bin voice bundle."),
            FieldSpec("tts", "language", "Language", "str", help_text="kokoro only: phonemizer language code, e.g. en-us."),
            FieldSpec("tts", "speaker", "Speaker", "optional_str", help_text="piper only: only for multi-speaker voices (e.g. en_GB-semaine-medium, en_GB-aru-medium, en_GB-vctk-medium, en_US-libritts-high) -- a speaker name from that voice's own list, or a raw numeric index. Leave unset for single-speaker voices or the voice's own default speaker. [t] will report an error naming the available speakers if this doesn't match."),
            FieldSpec("tts", "rate", "Rate", "float", help_text="Speech speed multiplier. 1.0 is normal."),
            FieldSpec("tts", "volume", "Volume", "float", help_text="Speech loudness multiplier. 1.0 is normal. piper only -- kokoro has no volume control."),
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
            FieldSpec("backend", "permission_mode", "Permission mode", "choice", _CHOICE_PERMISSION_MODES, help_text="How much the coding agent may DO. plan: read-only, cannot write or run commands (safe default). approve: may act, but every write/command needs voice approval via your approval_phrase -- real on both Codex (native per-call approval channel) and Claude Code (a PreToolUse hook this adapter builds itself, since headless mode has no native one -- see claude_code.py's module docstring). While a request is pending, say 'explain'/'explanation'/'clarify'/'help' to have the full detail read back before deciding, or 'no' to deny -- the prompt stays open across a clarifying exchange. permissive: BYPASSES ALL PERMISSIONS -- acts without asking on every tool call (Bash, WebFetch/WebSearch, MCP, file edits, everything), not just writes (dangerous). No effect on opencode (set at `opencode serve` launch). Do NOT also set a permission flag in Command -- that's a conflict."),
            FieldSpec("backend", "working_dir", "Working dir", "optional_str", help_text="The directory the spawned coding agent (Codex/Claude Code) runs and EDITS files in. SECURITY: leave unset and the agent inherits ConvoBox's own directory -- a voice session could then modify ConvoBox's source. Point it at an isolated workspace (a scratch/UAT dir separate from any repo you care about) so the agent's edits land there. No effect on opencode (its dir is set by where `opencode serve` was launched). Override per-run with run_convobox.py --working-dir."),
        ),
    ),
    SectionSpec(
        key="interaction",
        label="Interaction",
        fields=(
            FieldSpec("interaction", "interrupt_preset", "Interrupt preset", "choice", _CHOICE_INTERRUPT_PRESETS, help_text="What happens when you talk while ConvoBox is speaking. do-not-disturb (default, safe without headphones/AEC): it ignores you and finishes its full response before listening -- e.g. it's mid-explanation, you say 'wait, stop', it keeps talking to the end, then hears your next command normally (safeword still works). conversational (needs echo_cancellation or headphones): talking over it mutes it immediately and steers the SAME response with your words instead of starting over -- e.g. it's explaining something, you say 'skip to the summary', it goes quiet at once and works that into what it does next. patient (needs echo_cancellation or headphones): doesn't interrupt anything, but remembers your words and delivers them the moment it finishes -- e.g. you say 'also check the logs' mid-answer, it keeps talking normally, then asks about the logs automatically once done. halt (needs echo_cancellation or headphones): talking over it immediately cancels the whole turn, like the safeword, and returns to plain listening -- e.g. it's going down the wrong path, you say anything, it stops mid-sentence and waits for a fresh instruction. take-over (needs echo_cancellation or headphones): cancels the current turn AND acts on your new words right away -- the classic smart-speaker reflex, like interrupting Alexa/Siri with a new question."),
            FieldSpec("interaction", "barge_in_min_speech_ms", "Barge-in min speech ms", "int", help_text="How long speech must continue before it counts as a real interruption."),
            FieldSpec("interaction", "resume_word", "Resume word", "str", help_text="Say this to RESUME after a pause phrase (also the push-word barge-in trigger). Pick something DISTINCT and unlikely in normal conversation (so you don't resume by accident) and clearly transcribable by Whisper (so it matches reliably without needing a corrections-glossary entry). The old default 'ConvoBox' failed both -- confidently mis-heard as 'Control Box' every time. 'Athena' is the round-trip-verified default. Verify a custom word with scripts/roundtrip_smoketest.py first; a warning fires at save time for words already known to mis-transcribe."),
            FieldSpec("interaction", "pause_listening_phrases", "Pause phrases", "list_str", help_text="Comma-separated. Saying one hard-stops in-flight work and pauses listening until the resume word resumes. Same picking rule as the resume word: DISTINCT, unlikely in normal conversation, and cleanly Whisper-transcribable -- a phrase you say naturally mid-conversation would pause the session unexpectedly. Defaults: 'stop listening, pause listening'."),
            # Lives under config.safeword (its own top-level YAML section,
            # unchanged -- SafewordDetector/incident-capture/etc. all still
            # read config.safeword.hard_stop_phrases directly), but grouped
            # here in the TUI/web UI: safeword is one more member of the
            # same "what makes ConvoBox stop talking" family as the interrupt
            # preset and pause phrases above, not a separable concern that
            # deserves its own tab. FieldSpec's "safeword" section string is
            # what _get_value/_set_value actually use to resolve the real
            # config path -- this is a display-grouping change only.
            FieldSpec("safeword", "hard_stop_phrases", "Hard stop phrases", "list_str", help_text="Comma-separated phrases that immediately hard-stop the current turn, in every interrupt preset, paused or not -- the one always-on safety floor (docs/DESIGN-barge-in.md)."),
            FieldSpec("interaction", "pause_resume_ack", "Pause/resume sound", "choice", _CHOICE_PAUSE_RESUME_ACK, help_text="Audio cue when pausing/resuming listening. none (default): silent, matches every release before this one. tone: a short synthesized 3-note chime -- ascending on resume, descending on pause -- no extra files needed."),
            FieldSpec("interaction", "approval_phrase", "Approval phrase", "optional_str", help_text="Opt-in command/file approvals for Codex or Claude Code (needs backend.permission_mode: approve above). Leave unset to keep the safe default: every approval request is denied automatically, no prompts. When set, say this exact phrase to approve a pending request; say 'no' to deny; silence for approval_timeout_s denies. Use a distinctive multi-word phrase -- plain 'yes' is deliberately rejected. Same STT-reliability caution as the resume word: pick something clearly Whisper-transcribable. A NATO-alphabet-style phrase (e.g. 'juliette papa charlie') tends to round-trip more reliably than ordinary words -- verify with scripts/roundtrip_smoketest.py before relying on it."),
            FieldSpec("interaction", "approval_timeout_s", "Approval timeout s", "float", help_text="How long a pending approval waits for a voice decision before silence is treated as an explicit denial (never as consent)."),
            FieldSpec("interaction", "approval_explanation_mode", "Explanation mode", "choice", ("plain", "verbose"), help_text="When you ask for details during a pending approval ('explain', 'clarify', 'help'): plain = human-friendly intent (tool name + key parameters), verbose = technical details (raw JSON data). Doesn't affect the automatic approval announcement -- only the explanation you request when you ask for more detail."),
        ),
    ),
    SectionSpec(
        key="vad",
        label="VAD",
        fields=(
            FieldSpec("vad", "threshold", "Threshold", "float", help_text="Silero VAD speech-probability threshold."),
            FieldSpec("vad", "min_silence_ms", "Min silence ms", "int", help_text="Trailing silence needed to end an utterance."),
            FieldSpec("vad", "min_speech_ms", "Min speech ms", "int", help_text="Minimum speech burst to keep as a real utterance."),
            FieldSpec("vad", "max_utterance_s", "Max utterance s", "optional_float", help_text="Force an utterance to end after this many seconds, even without silence."),
            FieldSpec("vad", "trace_silero_calls", "Trace Silero calls", "bool", help_text="Diagnostic only, off by default -- logs every single Silero window call's duration (~31/s during active audio) at DEBUG level. Deliberately separate from --verbose: too noisy for normal use even at DEBUG. Turn on only when actively chasing the live-hit VAD-freeze issue (see docs/KNOWN-ISSUES.md)."),
        ),
    ),
    SectionSpec(
        key="display",
        label="Display",
        # The one section a plain browser refresh picks up -- see
        # SectionSpec.restart_required's own docstring for how this was
        # confirmed, not assumed.
        restart_required=False,
        fields=(
            FieldSpec("display", "user_color", "User bubble color", "optional_str", help_text="Hex color (#RGB or #RRGGBB, e.g. #2e7dfb) for your own speech bubbles in the web UI. Applies in both light and dark mode alike. Leave unset for the built-in theme default. Type - to clear back to the default."),
            FieldSpec("display", "assistant_color", "Assistant bubble color", "optional_str", help_text="Hex color (#RGB or #RRGGBB, e.g. #f0f0f2) for the AI's response bubbles in the web UI. Applies in both light and dark mode alike. Leave unset for the built-in theme default. Type - to clear back to the default."),
            FieldSpec("display", "user_name", "User display name", "optional_str", help_text="Label shown on your own speech bubbles in the web UI, e.g. your own name. Leave unset to show the raw event type ('transcript'). Only affects transcript/response bubbles -- tool calls/results/errors/approvals always show their literal event type."),
            FieldSpec("display", "assistant_name", "Assistant display name", "optional_str", help_text="Label shown on the AI's response bubbles in the web UI, e.g. 'Athena'. Leave unset to show the raw event type ('response')."),
        ),
    ),
)


# Swapped in for tts.voice when engine is kokoro (see _visible_fields_for_section)
# -- the base "voice" FieldSpec above is Piper's free-text field (any
# HuggingFace-catalog voice name, auto-downloaded); Kokoro's voices are a
# fixed, closed set baked into the downloaded voices file, so a real
# picker (kind="kokoro_voice", see _kokoro_voice_choices) fits better than
# free text a typo could silently break.
_TTS_VOICE_KOKORO_FIELD = FieldSpec(
    "tts", "voice", "Voice", "kokoro_voice",
    help_text=(
        "A Kokoro voice name. Space/Left/Right cycles the real voices in "
        "tts.voices_path (54 as of kokoro-onnx's v1.0 release) -- download "
        "it first via [t] if the list shows only the placeholder below. "
        "Naming convention: <language><gender>_<name> -- af_/am_ = American "
        "English female/male, bf_/bm_ = British English, ef_/em_ = Spanish, "
        "ff_ = French, hf_/hm_ = Hindi, if_/im_ = Italian, jf_/jm_ = "
        "Japanese, pf_/pm_ = Portuguese, zf_/zm_ = Mandarin. tts.language "
        "isn't auto-adjusted when you change voices -- non-English voices "
        "need a matching phonemizer language too, and only en-us/en-gb are "
        "confirmed to work well here so far."
    ),
)

# Swapped in for tts.voice/tts.speaker when engine is piper -- same
# reasoning as _TTS_VOICE_KOKORO_FIELD above: picking from what's actually
# installed beats free text a typo could silently break. Unlike Kokoro's
# fixed 54-voice set, Piper's real catalog is 163 voices/44 languages
# (scripts/voice_picker.py's job to browse/download); this picker only
# offers voices ALREADY downloaded, same "never trigger a surprise network
# call just by cycling" stance as the Kokoro picker.
_TTS_VOICE_PIPER_FIELD = FieldSpec(
    "tts", "voice", "Voice", "piper_voice",
    help_text=(
        "An installed Piper voice key, such as en_US-lessac-medium. "
        "Space/Left/Right cycles voices already downloaded to .models/piper "
        "-- use scripts/voice_picker.py to browse/download from Piper's "
        "full 163-voice/44-language catalog first if the list is empty."
    ),
)
_TTS_SPEAKER_PIPER_FIELD = FieldSpec(
    "tts", "speaker", "Speaker", "piper_speaker",
    help_text=(
        "Only for multi-speaker voices (e.g. en_GB-semaine-medium: 4 named "
        "speakers, en_GB-aru-medium: 12, en_GB-vctk-medium: 109, "
        "en_US-libritts-high: 904). Space/Left/Right cycles the CURRENT "
        "voice's own real speaker names, read from its downloaded config -- "
        "'(voice default)' clears back to the voice's own default speaker. "
        "Shows unavailable if tts.voice isn't set/saved/downloaded yet, or "
        "is a genuinely single-speaker voice."
    ),
)


def _visible_fields_for_section(config: AppConfig, section: SectionSpec) -> tuple[FieldSpec, ...]:
    if section.key == "tts":
        if config.tts.engine == "kokoro":
            fields = tuple(
                field for field in section.fields
                if field.key in {"engine", "voice", "model_path", "voices_path", "language", "rate"}
            )
            return tuple(
                _TTS_VOICE_KOKORO_FIELD if field.key == "voice" else field
                for field in fields
            )
        if config.tts.engine == "piper":
            fields = tuple(
                field for field in section.fields
                if field.key in {"engine", "voice", "speaker", "rate", "volume"}
            )
            swap = {"voice": _TTS_VOICE_PIPER_FIELD, "speaker": _TTS_SPEAKER_PIPER_FIELD}
            return tuple(swap.get(field.key, field) for field in fields)
        return section.fields
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


def _fit_input_buffer(buffer: str, width: int) -> str:
    """Renders the modal's "> {buffer}" input line, keeping the "> "
    prompt fixed and truncating buffer from the START (not fit()'s usual
    END) once it overflows width. This editor's cursor is always at the
    end of buffer -- typing only ever appends, backspace only ever
    removes from the end, there's no left/right cursor movement within
    the text -- so once a long value (e.g. a space-separated
    stt.hotwords list) exceeds the visible width, fit()'s normal
    head-then-"..." truncation freezes the display on the first N
    characters forever: you can keep typing (the real buffer is
    unaffected) but never see what you just typed, and have no way to
    tell the value even changed. Showing the END instead keeps whatever
    is currently being typed in view, matching ordinary single-line text
    input scrolling behavior. Live UAT finding, 2026-08-10 (JP, editing
    stt.hotwords)."""
    prefix = "> "
    available = width - len(prefix)
    if available <= 0:
        return fit(prefix, width)
    if len(buffer) > available:
        shown = "..." + buffer[-(available - 3):] if available > 3 else buffer[-available:]
    else:
        shown = buffer
    return (prefix + shown).ljust(width)


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


def _tts_profile_defaults(name: str) -> TTSProfileConfig:
    profile = _TTS_PROFILE_DEFAULTS.get(name)
    if profile is None:
        return TTSProfileConfig()
    return profile.model_copy(deep=True)


def _tts_profile_value(config: AppConfig, name: str) -> TTSProfileConfig:
    profile = config.tts_profiles.get(name)
    if profile is not None:
        return profile.model_copy(deep=True)
    return _tts_profile_defaults(name)


def _set_tts_profile(config: AppConfig, name: str, profile: TTSProfileConfig) -> None:
    config.tts_profiles[name] = profile.model_copy(deep=True)


def _tts_profile_from_active(config: AppConfig, name: str) -> TTSProfileConfig:
    if name == "kokoro":
        return TTSProfileConfig(
            voice=config.tts.voice,
            model_path=config.tts.model_path,
            voices_path=config.tts.voices_path,
            language=config.tts.language,
            rate=config.tts.rate,
        )
    return TTSProfileConfig(
        voice=config.tts.voice,
        rate=config.tts.rate,
        volume=config.tts.volume,
        speaker=config.tts.speaker,
    )


def _apply_tts_profile(config: AppConfig, name: str) -> None:
    profile = _tts_profile_value(config, name)
    defaults = _tts_profile_defaults(name)
    config.tts.engine = name
    config.tts.voice = profile.voice if profile.voice is not None else defaults.voice
    config.tts.rate = profile.rate if profile.rate is not None else (defaults.rate or 1.0)
    if name == "kokoro":
        config.tts.model_path = profile.model_path or defaults.model_path or config.tts.model_path
        config.tts.voices_path = profile.voices_path or defaults.voices_path or config.tts.voices_path
        config.tts.language = profile.language or defaults.language or config.tts.language
        # speaker is piper-only; clear it so a stale value never leaks
        # into a saved config the kokoro branch would just ignore anyway.
        config.tts.speaker = None
    else:
        config.tts.volume = profile.volume if profile.volume is not None else (defaults.volume or 1.0)
        config.tts.speaker = profile.speaker


def _switch_tts_engine(config: AppConfig, new_engine: str) -> None:
    current_engine = config.tts.engine
    if new_engine == current_engine:
        return
    _set_tts_profile(config, current_engine, _tts_profile_from_active(config, current_engine))
    _apply_tts_profile(config, new_engine)


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

# The kokoro_voice picker's placeholder when tts.voices_path hasn't been
# downloaded yet -- same "never let a picker offer zero choices" reasoning
# as _SYSTEM_DEFAULT: _cycle_choice raises on an empty list, which would
# crash the modal on the very first Left/Right/Space press. Landing on
# this instead of a real voice name on ENTER is treated as "nothing
# picked" (see _edit_value_interactive), not a literal value to save.
_KOKORO_VOICE_UNAVAILABLE = "(voices file not downloaded -- press [t] to fetch it)"


def _kokoro_voice_choices(config: AppConfig) -> list[str]:
    """Real voice names from the downloaded tts.voices_path file, or the
    placeholder above if it isn't downloaded yet (list_kokoro_voices
    already degrades to [] for a missing/corrupt file; this is just the
    picker-safe non-empty wrapper around that, same shape as
    _device_choices for audio devices)."""
    voices = list_kokoro_voices(config.tts.voices_path)
    return voices if voices else [_KOKORO_VOICE_UNAVAILABLE]


# Same "never offer zero choices" reasoning as _KOKORO_VOICE_UNAVAILABLE.
_PIPER_VOICE_UNAVAILABLE = "(no voices downloaded -- use scripts/voice_picker.py to download one)"
# Piper speaker picker's "use this voice's own default speaker" choice --
# always first, same role as _SYSTEM_DEFAULT for device fields (tts.speaker
# is str | None; None means "voice's default", not a literal string to save).
_PIPER_SPEAKER_DEFAULT = "(voice default)"
_PIPER_SPEAKER_UNAVAILABLE = "(pick + save a downloaded voice first, or it has no named speakers)"


def _piper_voice_choices() -> list[str]:
    """Locally installed Piper voice keys (e.g. "en_US-lessac-medium"),
    or the placeholder above if none are downloaded yet. Deliberately
    does NOT browse Piper's full 163-voice HuggingFace catalog here --
    that's scripts/voice_picker.py's job (search/download/audition);
    this picker, like the Kokoro one, only offers what's already on
    disk, so cycling here never triggers a surprise network download.
    """
    import voice_picker

    voices = voice_picker.installed_voices(DEFAULT_VOICES_DIR)
    return voices if voices else [_PIPER_VOICE_UNAVAILABLE]


def _piper_speaker_choices(config: AppConfig) -> list[str]:
    """Named speakers for the CURRENTLY CONFIGURED Piper voice (tts.voice),
    read directly from that voice's own downloaded .onnx.json sidecar --
    speaker_id_map lives in that config JSON already (confirmed live,
    2026-07-24: reading a real downloaded en_GB-aru-medium.onnx.json
    yields the identical speaker_id_map PiperVoice.load() would produce),
    so this needs no ONNX model load just to list names. Returns the
    unavailable placeholder if no voice is set, it isn't downloaded, or
    it's a genuinely single-speaker voice (empty map) -- nothing real to
    pick in any of those cases.
    """
    voice = config.tts.voice
    if not voice:
        return [_PIPER_SPEAKER_UNAVAILABLE]
    json_path = DEFAULT_VOICES_DIR / f"{voice}.onnx.json"
    if not json_path.exists():
        return [_PIPER_SPEAKER_UNAVAILABLE]
    try:
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [_PIPER_SPEAKER_UNAVAILABLE]
    speaker_map = data.get("speaker_id_map") or {}
    if not speaker_map:
        return [_PIPER_SPEAKER_UNAVAILABLE]
    return [_PIPER_SPEAKER_DEFAULT, *sorted(speaker_map)]


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
        import audio_devices as ad
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return [_SYSTEM_DEFAULT]
    try:
        devices = ad.dedupe_devices(ad.collect_devices(sd, kind))
    except Exception:  # noqa: BLE001
        return [_SYSTEM_DEFAULT]
    return [_SYSTEM_DEFAULT] + [f"{d['name']}, {d['hostapi']}" for d in devices]


def _choices_for(spec: FieldSpec, config: AppConfig) -> tuple[str, ...]:
    """The live choice list for a field -- static for `choice` fields,
    freshly enumerated (real connected devices, or real downloaded Kokoro
    voices) for `device`/`kokoro_voice` fields, a fixed true/false pair for
    `bool` fields (live UAT feedback, 2026-07-22: a typed bool field let a
    mistype like "flase" through to a raw ValueError instead of just being
    unselectable).
    """
    if spec.kind == "device":
        kind: Literal["input", "output"] = "input" if spec.key == "input_device" else "output"
        return tuple(_device_choices(kind))
    if spec.kind == "kokoro_voice":
        return tuple(_kokoro_voice_choices(config))
    if spec.kind == "piper_voice":
        return tuple(_piper_voice_choices())
    if spec.kind == "piper_speaker":
        return tuple(_piper_speaker_choices(config))
    if spec.kind == "bool":
        return ("false", "true")
    return spec.choices


def _choice_index(spec: FieldSpec, current: Any, config: AppConfig) -> int:
    choices = _choices_for(spec, config)
    if not choices:
        return -1
    # Device/piper_speaker fields are str | None; None maps to that field's
    # own "(unset)" sentinel (always index 0, see _device_choices /
    # _piper_speaker_choices) so cycling from unset advances to the first
    # real choice instead of appearing to do nothing.
    if current is not None:
        lookup = current
    elif spec.kind == "piper_speaker":
        lookup = _PIPER_SPEAKER_DEFAULT
    else:
        lookup = _SYSTEM_DEFAULT
    try:
        return choices.index(lookup)
    except ValueError:
        text = str(lookup).lower()
        for index, choice in enumerate(choices):
            if choice.lower() == text:
                return index
    return -1


def _cycle_choice(spec: FieldSpec, current: Any, delta: int, config: AppConfig) -> str:
    choices = _choices_for(spec, config)
    if not choices:
        raise ValueError("no choices configured")
    idx = _choice_index(spec, current, config)
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


# Backups live in a subdirectory next to the config, not scattered directly
# in the same directory as convobox.yaml (GitHub issue #235, finding D4) --
# a live repo accumulated ~90 convobox.yaml.backup-* files in its root over
# time, which AGENTS.md's own "no scratch/test artifacts in the repo root"
# rule flags, and which made a plain `ls`/directory listing noisy. Backups
# are otherwise unchanged: same filename stamp format, same one-per-save
# cadence, just one level deeper.
_BACKUP_DIRNAME = ".convobox-backups"


def _backup_dir(path: Path) -> Path:
    return path.parent / _BACKUP_DIRNAME


def backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = _backup_dir(path)
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f"{path.name}.backup-{stamp}"
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def list_config_backups(path: Path) -> list[Path]:
    """Every backup_config()-written convobox.yaml.backup-* in path's
    .convobox-backups/ subdirectory, newest first. Filenames are that
    function's own <name>.backup-<YYYYMMDD-HHMMSS> stamp, which sorts
    lexicographically = chronologically -- a hand-renamed one (e.g. a
    trailing custom suffix) still sorts correctly by its date/time prefix,
    since glob only matches names that already start with that stamp.
    Returns [] (not an error) when the subdirectory doesn't exist yet --
    e.g. a config that's never been saved through save_with_backup."""
    backup_dir = _backup_dir(path)
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{path.name}.backup-*"), reverse=True)


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
    elif config.tts.engine == "kokoro":
        if not config.tts.voice:
            report.errors.append("tts.voice is required when tts.engine is kokoro")
        # A cheap existence check only, deliberately NOT resolve_kokoro_model_paths
        # (which downloads): render() calls validate_config on every frame via
        # _section_summary, so an actual ~326MB download attempt here would
        # freeze the TUI on the very next keystroke after these go missing,
        # not just on an explicit [S]/[T] action. The real auto-download-on-
        # first-use happens in create_tts_engine instead, exercised by [t]'s
        # probe_tts and by run_convobox.py's real startup -- both places the
        # operator has already asked for the engine to actually be built.
        if not Path(config.tts.model_path).exists():
            report.warnings.append(
                f"tts.model_path {config.tts.model_path!r} does not exist yet -- "
                "it will be downloaded automatically the first time TTS is used "
                "([t] to test, or downloaded from "
                "https://github.com/thewh1teagle/kokoro-onnx/releases)"
            )
        if not Path(config.tts.voices_path).exists():
            report.warnings.append(
                f"tts.voices_path {config.tts.voices_path!r} does not exist yet -- "
                "it will be downloaded automatically the first time TTS is used "
                "([t] to test, or downloaded from "
                "https://github.com/thewh1teagle/kokoro-onnx/releases)"
            )

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
    # claude-code specifically: this combination doesn't fail safe the way
    # the general warning below describes for other backends (codex denies
    # cleanly with no pending state) -- the hook still gets wired at
    # construction time and nothing can ever answer it, so it's a hard
    # error here, not a warning. See detect_claude_code_approval_gap's own
    # docstring (GitHub issue #235, finding A1) for the full mechanism.
    approval_gap = detect_claude_code_approval_gap(config.backend, config.interaction)
    if approval_gap is not None:
        report.errors.append(approval_gap)
    elif config.backend.permission_mode == "approve" and not config.interaction.approval_phrase:
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


def _compare_test_phrase(engine_name: str) -> str:
    """Names the engine in the spoken phrase itself, so [c]'s back-to-back
    Kokoro/Piper playback is identifiable by ear alone, not just by
    reading the status line afterward."""
    return f"This is a test using the {engine_name!r} text to speech engine."


def _tts_config_for_comparison(config: AppConfig, engine_name: str) -> TTSConfig | None:
    """Build the TTSConfig `engine_name` would use, WITHOUT switching
    config.tts.engine or mutating anything -- lets [c] compare hear both
    engines using each one's own remembered tts_profiles (or the live
    values, if that engine is the one currently active), same "don't
    cross-contaminate the other engine's settings" reasoning as
    _switch_tts_engine. Returns None if that engine has no voice
    configured anywhere yet (nothing meaningful to synthesize -- e.g.
    Piper before any voice has ever been picked, since unlike Kokoro it
    has no single sensible default)."""
    if config.tts.engine == engine_name:
        return config.tts.model_copy(deep=True)
    profile = _tts_profile_value(config, engine_name)
    defaults = _tts_profile_defaults(engine_name)
    voice = profile.voice if profile.voice is not None else defaults.voice
    if voice is None:
        return None
    return TTSConfig(
        engine=engine_name,
        voice=voice,
        rate=profile.rate if profile.rate is not None else (defaults.rate or 1.0),
        volume=profile.volume if profile.volume is not None else (defaults.volume or 1.0),
        model_path=profile.model_path or defaults.model_path or config.tts.model_path,
        voices_path=profile.voices_path or defaults.voices_path or config.tts.voices_path,
        language=profile.language or defaults.language or config.tts.language,
        speaker=profile.speaker if engine_name == "piper" else None,
    )


async def _compare_tts_engines(state: TuiState) -> None:
    """[c]: build BOTH Kokoro and Piper and speak the same test phrase
    through each in turn, so the operator can actually HEAR the
    difference before choosing -- probe_tts ([t]) only confirms synthesis
    succeeds, it never plays anything back.

    Each engine is built from ITS OWN remembered tts_profiles (see
    _tts_config_for_comparison), not by mutating tts.engine back and
    forth -- so comparing never disturbs the config actually staged for
    save, and hearing Piper doesn't require first switching away from
    Kokoro (or vice versa).
    """
    if state.current_section().key != "tts":
        state.status = "[c] compare is only available in the TTS section"
        return

    import io

    import audio_devices as ad
    import sounddevice as sd

    config = state.working
    with contextlib.redirect_stdout(io.StringIO()):
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, _ = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx = ad._default_index(sd, "output")

    results: list[str] = []
    for engine_name in ("kokoro", "piper"):
        tts_config = _tts_config_for_comparison(config, engine_name)
        if tts_config is None:
            results.append(f"{engine_name}: no voice configured yet")
            continue
        try:
            engine = create_tts_engine(tts_config, DEFAULT_VOICES_DIR)
            audio = await engine.synthesize(_compare_test_phrase(engine_name))
        except Exception as exc:  # noqa: BLE001 -- report each engine's own failure, keep comparing the other
            results.append(f"{engine_name}: {type(exc).__name__}: {exc}")
            continue
        if audio.size == 0:
            results.append(f"{engine_name}: produced no audio")
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ad._play_recording(sd, audio, engine.sample_rate, out_idx)
            results.append(f"{engine_name}: played ({audio.shape[0]} samples @ {engine.sample_rate}Hz)")
        except Exception as exc:  # noqa: BLE001
            results.append(f"{engine_name}: playback failed -- {type(exc).__name__}: {exc}")

    state.status = " | ".join(results)


def _refresh_kokoro_voices(state: TuiState) -> None:
    """[d]: force a fresh download of tts.voices_path, replacing whatever
    is already cached -- for when kokoro-onnx's upstream release adds or
    changes voices and the local file predates that. The normal
    auto-download-on-first-use path (resolve_kokoro_model_paths, used by
    create_tts_engine) only fetches when the file is missing, so it would
    never notice a newer release existing once something is already
    cached locally. Only meaningful for tts.engine=kokoro; a no-op
    (with an explanatory status) everywhere else.
    """
    if state.current_section().key != "tts" or state.working.tts.engine != "kokoro":
        state.status = "[d] refresh voices is only available for tts.engine=kokoro"
        return
    state.status = "downloading the latest Kokoro voices file..."
    try:
        refresh_kokoro_voices(state.working.tts.voices_path)
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the TUI over a failed download
        state.status = f"voices refresh failed: {exc}"
        return
    voices = list_kokoro_voices(state.working.tts.voices_path)
    state.status = f"voices refreshed -- {len(voices)} voices now available"


async def probe_stt(config: AppConfig) -> str:
    # WhisperModel construction downloads the model on first use (tens of MB
    # for tiny/base, ~3GB for large-v3) -- a real, possibly multi-minute
    # blocking call. Off the event loop thread so it can't freeze the TUI's
    # render loop or, on the web UI, the whole uvicorn server (SSE stream
    # included) for every connected tab while one Test click downloads.
    def _run() -> TranscriptResult:
        transcriber = create_stt_engine(config.stt)
        silence = np.zeros(int(config.audio.sample_rate), dtype=np.float32)
        return transcriber.transcribe(silence)

    result = await asyncio.to_thread(_run)
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


async def probe_audio(config: AppConfig, field_key: str | None = None) -> str:
    """Test the device(s) relevant to the currently selected field.

    field_key == "input_device": mic only -- record from it and play the
    recording back through the configured speaker, so you can actually
    hear whether the right mic is picking you up (a level meter alone
    confirms *something* is captured, not that it's the right device or
    sounds right).
    field_key == "output_device": speaker only -- play a short tone.
    Anything else (no field selected, or a non-device audio field like
    sample_rate/echo_cancellation/aec_delay_ms): test both, same as
    before -- there's no single device those fields are specifically
    about.

    Live UAT feedback, 2026-07-22: this used to test BOTH directions
    unconditionally regardless of which field was selected, so pressing
    [t] on Input device also played an unrelated output tone first --
    which read as "it's just playing a tone, not testing the mic",
    since the actual mic-test playback that followed immediately after
    wasn't distinctly noticed as a separate thing.

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

    import audio_devices as ad
    import sounddevice as sd

    test_output = field_key != "input_device"
    test_input = field_key != "output_device"

    results: list[str] = []
    with contextlib.redirect_stdout(io.StringIO()):
        # Resolved regardless of test_output: test_input still needs it as
        # test_input_device's playback target.
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, out_err = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx, out_err = ad._default_index(sd, "output"), None
        if test_output:
            if out_idx is not None:
                name = sd.query_devices(out_idx)["name"]
                ad.play_test_tone(sd, out_idx, seconds=0.6)
                results.append(f"speaker OK: played 0.6s tone on {name!r}")
            else:
                results.append(f"speaker: {out_err or 'no device found'}")

        if test_input:
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


async def _probe_input_device_live(state: TuiState, seconds: float = 3.0) -> str:
    """Record ~3s from the configured mic, showing a live level bar in
    the TUI's own status line WHILE recording (not a raw terminal
    overlay -- this redraws through the normal `draw()` path so it stays
    part of the same screen), then play the recording back through the
    configured speaker.

    Live UAT feedback, 2026-07-23: the plain [t] test (probe_audio, still
    used for every other audio field) only shows a single level reading
    after the whole recording finishes. Watching the level move in real
    time while actually speaking makes gain problems (clipping, too
    quiet, wrong device picking up something else entirely) far easier to
    judge than one static number -- this is specifically what `[t]` does
    now for Input device; every other audio field keeps the quicker,
    non-live probe_audio() path.

    format_level() already renders a full bar+numbers+verdict string
    (see audio_devices.py) -- reused as-is for each live tick, not
    reimplemented here.
    """
    import io

    import audio_devices as ad
    import sounddevice as sd

    config = state.working
    with contextlib.redirect_stdout(io.StringIO()):
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, _ = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx = ad._default_index(sd, "output")

        in_devices = ad.collect_devices(sd, "input")
        if config.audio.input_device:
            in_idx, in_err = ad.resolve_spec(config.audio.input_device, in_devices)
        else:
            in_idx, in_err = ad._default_index(sd, "input"), None
    if in_idx is None:
        return f"mic: {in_err or 'no device found'}"

    chunks: list[np.ndarray] = []
    latest = {"rms": -120.0, "peak": -120.0}

    def _callback(indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        chunks.append(indata[:, 0].copy())
        latest["rms"], latest["peak"] = ad.level_meter(indata[:, 0])

    rate = ad._CAPTURE_RATE
    try:
        stream = sd.InputStream(samplerate=rate, channels=1, device=in_idx, callback=_callback)
    except Exception:  # noqa: BLE001 -- fall back to the device's own rate
        rate = int(sd.query_devices(in_idx)["default_samplerate"])
        stream = sd.InputStream(samplerate=rate, channels=1, device=in_idx, callback=_callback)

    steps = max(1, int(seconds / 0.1))
    with stream:
        for _ in range(steps):
            state.status = f"recording, speak normally -- {ad.format_level(latest['rms'], latest['peak'])}"
            draw(state)
            await asyncio.sleep(0.1)

    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    rms_db, peak_db = ad.level_meter(audio)
    with contextlib.redirect_stdout(io.StringIO()):
        ad._play_recording(sd, audio, rate, out_idx)
    return f"mic: {ad.format_level(rms_db, peak_db)} (played back)"


def _section_summary(config: AppConfig) -> list[str]:
    report = validate_config(config)
    lines = [
        (
            f"backend: {config.backend.name}  tts: {config.tts.engine}/{config.tts.voice or '(unset)'}  "
            f"stt: {config.stt.engine}/{config.stt.model}  audio: {config.audio.input_device or 'default'} -> "
            f"{config.audio.output_device or 'default'}"
        ),
    ]
    if report.warnings:
        lines.append("warnings: " + " | ".join(report.warnings[:2]))
    if report.errors:
        lines.append("errors: " + " | ".join(report.errors[:2]))
    return lines


def _section_tabs(state: TuiState, width: int) -> str:
    flagged = state.flagged_sections
    tabs: list[str] = []
    for idx, section in enumerate(state.sections):
        marker = "! " if section.key in flagged else ""
        label = f" {marker}{section.label} "
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
    if spec.section == "tts":
        lines.append("")
        lines.extend(
            _wrap_text(
                "Press [c] to hear Kokoro and Piper speak the same test phrase back to "
                "back -- each engine's own remembered voice/settings, without switching "
                "tts.engine or losing anything staged.",
                width,
            )
        )
        if state.working.tts.engine == "kokoro":
            lines.append("")
            lines.extend(
                _wrap_text(
                    "Press [d] to redownload the Kokoro voices file, replacing whatever's "
                    "cached -- use this if a newer kokoro-onnx release adds voices this "
                    "list doesn't show yet.",
                    width,
                )
            )
    if spec.kind == "choice" and spec.choices:
        lines.append("")
        lines.extend(_wrap_text("Choices: " + ", ".join(spec.choices), width))
    if spec.kind == "kokoro_voice":
        lines.append("")
        lines.extend(_wrap_text("Choices: " + ", ".join(_kokoro_voice_choices(state.working)), width))
    if spec.kind == "piper_voice":
        lines.append("")
        lines.extend(_wrap_text("Choices: " + ", ".join(_piper_voice_choices()), width))
    if spec.kind == "piper_speaker":
        lines.append("")
        lines.extend(_wrap_text("Choices: " + ", ".join(_piper_speaker_choices(state.working)), width))
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
    hint_override: str | None = None,
) -> list[str]:
    # Previously forced an artificial 80x24 floor regardless of the real
    # terminal size -- the same wrap-and-garble mechanism as render()'s
    # analogous bug (docs/KNOWN-ISSUES.md, "Settings TUI ignores real
    # terminal size..."): every line here was padded/truncated to a WIDTH
    # the real terminal might not have, so the terminal itself wrapped it,
    # breaking _draw_modal's cursor-home-based repaint the same way render()
    # broke draw()'s. Unlike render()'s two-column layout, a modal degrades
    # safely at any real width via fit()'s own truncation and box_width's
    # already-width-bounded calc below (box_width <= int(width * 0.8), so it
    # can never exceed the real width) -- no separate "too small" fallback
    # message is needed here, just don't inflate width/height past what the
    # terminal actually reports. The remaining floor is only a sanity
    # minimum against degenerate (near-zero) sizes.
    width = max(width, 20)
    height = max(height, 8)
    border = "=" if severity == "destructive" else "-"
    accent = f"{_RED}{_BOLD}" if severity == "destructive" else f"{_CYAN}{_BOLD}"
    tone = " DANGER " if severity == "destructive" else " INFO "
    # hint_override lets a specific dialog (e.g. Confirm Save) spell out
    # exactly what Esc/Enter do in its own words -- live UAT feedback,
    # 2026-07-25: the generic "Esc cancel | Enter confirm" didn't say
    # WHAT gets saved or that cancelling discards nothing, which matters
    # more here than in most confirm dialogs (Revert has the opposite
    # Esc/Enter risk profile, so it deliberately keeps the generic text).
    default_hint = "Esc cancel | Enter confirm" if severity == "normal" else "Esc back out carefully | Enter confirm"
    hint_text = hint_override or default_hint
    footer_hint = hint_override or ("Esc cancel | Enter accept" if severity == "normal" else "Esc back out carefully | Enter accept")
    lines: list[str] = []
    lines.append(fit(f" ConvoBox Settings TUI | {title} ", width))
    lines.append(fit(f" status: {prompt}", width))
    # Reverse-video, same treatment as the main screen's own legend bar --
    # a modal is exactly where "what do Esc/Enter actually do right now"
    # needs to be unmissable, not read off as part of the status line.
    lines.append(
        _REVERSE
        + fit(f" {tone}{hint_text} ", width)
        + _RESET
    )
    lines.append(accent + "+" + border * (width - 2) + "+" + _RESET)
    body_height = height - 6
    content_lines = [f"{tone.strip()} {title}", "", prompt, ""]
    content_lines.extend(detail_lines)
    if choice_options:
        selected = choice_value if choice_value in choice_options else (choice_options[0] if choice_options else None)
        try:
            selected_idx = choice_options.index(selected) if selected is not None else 0
        except ValueError:
            selected_idx = 0
        content_lines.extend(["", "Options:"])
        # Pre-calculate max option width to avoid oversizing the modal
        # Reserve 4 chars for " > " prefix, then cap at 70 chars display width
        max_option_display = 70
        # Scroll the option list so the selection is always visible --
        # UAT feedback, 2026-07-24: a 54-voice Kokoro list only ever
        # rendered the first ~15 entries (whatever fit body_height); once
        # the selected index moved past that static window, the `>`
        # marker scrolled off screen with zero visual feedback -- arrow
        # keys visibly changed the "Current:"/buffer line but the list
        # itself never appeared to move. Reserve 2 extra rows against
        # body_height for the "more above/below" indicators regardless of
        # whether both end up shown -- simpler than exact accounting and
        # the margin is cheap against a normal terminal height.
        fixed_remaining = 2  # "Esc.../Enter..." + "> buffer" rows still to come
        available = max(3, body_height - len(content_lines) - fixed_remaining - 2)
        total = len(choice_options)
        if total <= available:
            window_start, window_end = 0, total
        else:
            half = available // 2
            window_start = max(0, selected_idx - half)
            window_end = min(total, window_start + available)
            window_start = max(0, window_end - available)
        if window_start > 0:
            content_lines.append(f"   ... {window_start} more above ...")
        for i in range(window_start, window_end):
            option = choice_options[i]
            marker = ">" if i == selected_idx else " "
            # Truncate long options with ellipsis if needed
            display_option = option if len(option) <= max_option_display else option[:max_option_display-3] + "..."
            content_lines.append(f" {marker} {display_option}")
        if window_end < total:
            content_lines.append(f"   ... {total - window_end} more below ...")
    content_lines.append("")
    content_lines.append(footer_hint)
    content_lines.append(f"> {buffer}")
    # Sized off the actual content, not just the input buffer -- a detail
    # line (e.g. a save/quit hint) longer than the old fixed floor was
    # silently truncated mid-word since fit() has no wrapping.
    longest_line = max((len(line) for line in content_lines), default=0)
    # Cap box width at 80% of terminal width to prevent overflow; minimum 52 chars
    max_box_width = int(width * 0.8)
    box_width = min(max_box_width, max(52, longest_line + 4, len(buffer) + 8))
    left_pad = max(0, (width - box_width) // 2 - 1)
    # Ensure box fits within terminal width without truncation
    actual_box_width = min(box_width, width - left_pad - 2)
    right_pad = width - left_pad - actual_box_width - 2
    box_top = " " * left_pad + border + border * (actual_box_width - 2) + border + " " * right_pad
    lines.append(box_top)
    inner_width = actual_box_width - 2
    input_line_idx = len(content_lines) - 1  # always "> {buffer}", see append above
    for idx in range(body_height):
        if idx < len(content_lines):
            # _highlight_keys AFTER fit(), same ordering rule as the main
            # screen's help panel -- see that function's own docstring.
            # The input line uses a tail-truncating fit, not the regular
            # head-truncating one -- see _fit_input_buffer's own docstring.
            fitted = (
                _fit_input_buffer(buffer, inner_width)
                if idx == input_line_idx
                else fit(content_lines[idx], inner_width)
            )
            inner = _highlight_keys(fitted)
        else:
            inner = fit("", inner_width)
        lines.append(
            " " * left_pad
            + "|"
            + inner
            + "|"
            + " " * max(0, width - left_pad - actual_box_width - 2)
        )
    lines.append(box_top)
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
    hint_override: str | None = None,
) -> None:
    if width is None or height is None:
        try:
            size = os.get_terminal_size()
            width = size.columns if width is None else width
            height = size.lines if height is None else height
        except OSError:
            width = 100 if width is None else width
            height = 30 if height is None else height
    # Clear-to-end-of-line on every row (\x1b[K), not just cursor-home --
    # relying on exact-width padding to overwrite the previous frame is
    # fragile against any width miscalculation (e.g. wide Unicode chars
    # mis-measured by the terminal), which otherwise leaves stale
    # fragments from a wider previous frame visible past the new content.
    sys.stdout.write(
        "\x1b[H"
        # Explicit "\r\n" -- see draw()'s own comment on the same join for
        # why this can't rely on the terminal driver's OPOST/ONLCR
        # translation of a bare "\n".
        + "\x1b[K\r\n".join(
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
                hint_override=hint_override,
            )
        )
        + "\x1b[K"
    )
    sys.stdout.flush()


# Tracks whether a modal's own read_key()/_draw_modal() loop is currently
# on screen (see _tracks_modal_depth below) -- run_tui()'s SIGWINCH handler
# checks this before repainting so a resize while a modal is open doesn't
# blow it away with the main browse screen underneath instead of the modal
# that's actually showing. A module-level counter rather than a plain bool
# so nested modals (e.g. a Confirm Quit dialog raised from inside an open
# field editor) don't have the outer one's exit clear it prematurely.
_modal_depth = 0


def _tracks_modal_depth(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        global _modal_depth
        _modal_depth += 1
        try:
            return fn(*args, **kwargs)
        finally:
            _modal_depth -= 1

    return wrapper


@_tracks_modal_depth
def _edit_value_interactive(spec: FieldSpec, current: Any, config: AppConfig) -> tuple[bool, Any]:
    is_pickable = spec.kind in ("choice", "device", "bool", "kokoro_voice")
    # Device fields are str | None; _format_value(None) is the display
    # string "(unset)", which isn't in _choices_for's list and would break
    # cycling/index-lookup. Seed the buffer with the picker's own sentinel
    # instead so an unset device field starts aligned with choice index 0,
    # same as a "choice" field (which is never None) already is.
    if spec.kind == "device" and current is None:
        buffer = _SYSTEM_DEFAULT
    elif spec.kind == "command":
        # NOT _format_value() -- its generic list formatting is
        # comma-joined ("codex.cmd, --model, x"), correct for the OTHER
        # list-kind fields (which really are comma-separated, e.g.
        # safeword.hard_stop_phrases) but wrong here: this field's own
        # _parse_value branch re-parses via shlex.split() (space-based,
        # shell-style, no comma handling at all). Seeding the buffer with
        # the comma-joined form meant simply opening this field and
        # pressing Enter unchanged -- no typing at all -- silently
        # corrupted the command, appending a literal trailing comma to
        # every argument (["codex.cmd", "--model", "x"] roundtripped to
        # ["codex.cmd,", "--model,", "x"]). shlex.join() is shlex.split()'s
        # own inverse, so seeding with it round-trips correctly whether
        # or not the user actually changes anything. Live UAT finding,
        # 2026-08-10.
        buffer = shlex.join(current) if current else ""
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
    choice_options = list(_choices_for(spec, config)) if is_pickable else None
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
            if spec.kind == "kokoro_voice" and buffer == _KOKORO_VOICE_UNAVAILABLE:
                # The placeholder shown when tts.voices_path isn't
                # downloaded yet -- there's nothing real to accept, so
                # this is a cancel, not a value (never write the
                # placeholder text itself into tts.voice).
                return False, current
            return True, _parse_value(spec, accepted, current)
        if is_pickable:
            if key in {"LEFT", "UP"}:
                buffer = _cycle_choice(spec, buffer, -1, config)
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
                buffer = _cycle_choice(spec, buffer, 1, config)
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


@_tracks_modal_depth
def _confirm_modal(
    title: str,
    prompt: str,
    detail_lines: list[str],
    severity: Literal["normal", "destructive"] = "normal",
    hint_override: str | None = None,
) -> bool:
    while True:
        _draw_modal(title, prompt, detail_lines, "", severity=severity, hint_override=hint_override)
        key = read_key()
        if key == "ESC":
            return False
        if key == "ENTER":
            return True


_MIN_LEFT_WIDTH = 36
_MIN_RIGHT_WIDTH = 24
_MIN_USABLE_WIDTH = _MIN_LEFT_WIDTH + 3 + _MIN_RIGHT_WIDTH  # " | " separator
_MIN_USABLE_HEIGHT = 12  # header/tabs/legend chrome (10 lines) + >=2 visible fields


def render(state: TuiState, width: int, height: int) -> list[str]:
    # Previously forced a minimum 80x24 regardless of the REAL terminal
    # size (`width = max(width, 80)`) -- live-reported 2026-08-30: on any
    # real terminal narrower/shorter than that (a common split-pane size,
    # not an edge case), every line this emits was wider than the actual
    # terminal, so the terminal itself wrapped each logical row into two+
    # visual rows. That broke draw()'s "\x1b[H" + one "\x1b[K"-cleared
    # write per logical line repaint scheme -- cursor-home no longer
    # landed on the previous frame's real row boundaries once wrapping
    # was happening, which is why navigation looked broken ("arrows
    # don't seem to go where I want"): the selected-field pointer WAS
    # moving internally, but the garbled repaint made the new position
    # unreadable, not merely absent. Now uses the real size and falls
    # back to an explicit message below this layout's own hard minimum
    # (_MIN_LEFT_WIDTH + _MIN_RIGHT_WIDTH themselves have hardcoded
    # floors, so simply removing the outer max() alone still overflows
    # anything narrower than _MIN_USABLE_WIDTH) rather than attempting a
    # two-column layout that can't fit and silently overflowing again.
    if width < _MIN_USABLE_WIDTH or height < _MIN_USABLE_HEIGHT:
        msg = (
            f"Terminal too small ({width}x{height}) -- resize to at "
            f"least {_MIN_USABLE_WIDTH}x{_MIN_USABLE_HEIGHT} to use the "
            "Settings TUI."
        )
        return [msg[:width]] if width > 0 else [""]
    left_width = max(_MIN_LEFT_WIDTH, min(54, width // 2 + 4))
    right_width = max(_MIN_RIGHT_WIDTH, width - left_width - 3)
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
    # Same highlighting as the "Tip:" line at the bottom of this render
    # (which shows this exact same state.status text) -- previously only
    # that copy was highlighted, so a bracketed key like "[S]" appeared
    # bold+cyan at the bottom of the screen but plain here, right next to
    # where the operator is actually looking after an edit.
    lines.append(_highlight_keys(fit(status, width)))
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


def draw(state: TuiState) -> None:
    size = os.get_terminal_size()
    # Clear-to-end-of-line on every row -- see _draw_modal's comment for why
    # relying on exact-width padding alone isn't reliable enough.
    #
    # Explicit "\r\n", not a bare "\n": this can now run from the SIGWINCH
    # handler installed in run_tui(), which can fire at any point --
    # including while read_key() is mid-blocking-read with the terminal
    # already put into raw mode via tty.setraw() (which disables the tty
    # driver's own OPOST/ONLCR output translation until read_key()'s
    # finally restores it). A bare "\n" written during that exact window
    # would move the cursor down a row WITHOUT returning it to column 0,
    # misaligning every row after the first -- confirmed via a real
    # pty.fork() run: a resize-triggered frame landed as one 2000+ byte
    # "line" with zero \r bytes in it. Writing "\r\n" ourselves makes this
    # correct regardless of the terminal's current OPOST/ONLCR state.
    sys.stdout.write(
        "\x1b[H" + "\x1b[K\r\n".join(render(state, size.columns, size.lines)) + "\x1b[K"
    )
    sys.stdout.flush()


def _field_updated_status(spec: FieldSpec, dirty: bool) -> str:
    """Status line shown right after a field changes -- live UAT feedback,
    2026-07-22: a plain "{label} updated" gave no indication that the
    change was only staged, not saved. The dirty-state header line says
    this too, but it's a separate line the operator isn't necessarily
    looking at at the exact moment they just made a change; naming the
    save key right in the same message that confirms the edit closes
    that gap without relying on them to notice it elsewhere.
    """
    if dirty:
        return f"{spec.label} updated -- [S] to save"
    return f"{spec.label} updated"


def _toggle_or_cycle(state: TuiState) -> None:
    spec = state.current_field()
    if spec is None:
        state.status = "nothing to change in this section"
        return
    current = _get_value(state.working, spec)
    new_value: bool | str | None
    if spec.kind == "bool":
        new_value = not bool(current)
    elif spec.kind in ("choice", "device", "kokoro_voice", "piper_voice", "piper_speaker"):
        try:
            new_value = _cycle_choice(spec, current, 1, state.working)
        except ValueError:
            state.status = "no choices configured"
            return
        if spec.kind == "device" and new_value == _SYSTEM_DEFAULT:
            # The underlying field is str | None; unset means None, not
            # the display sentinel.
            new_value = None
        if spec.kind == "kokoro_voice" and new_value == _KOKORO_VOICE_UNAVAILABLE:
            # Nothing real to cycle to yet -- leave tts.voice untouched
            # rather than writing the placeholder text as if it were a
            # real voice name.
            state.status = "tts.voices_path not downloaded yet -- press [t] to fetch it"
            return
        if spec.kind == "piper_voice" and new_value == _PIPER_VOICE_UNAVAILABLE:
            state.status = "no piper voices downloaded -- use scripts/voice_picker.py first"
            return
        if spec.kind == "piper_speaker":
            if new_value == _PIPER_SPEAKER_UNAVAILABLE:
                state.status = "no named speakers for the current tts.voice"
                return
            if new_value == _PIPER_SPEAKER_DEFAULT:
                new_value = None
    else:
        state.status = "space toggles booleans and cycles choices only"
        return
    if spec.section == "backend" and spec.key == "name":
        # backend.name is a choice field, so new_value is a str here; str()
        # makes that explicit for the type checker.
        _switch_backend(state.working, str(new_value))
    elif spec.section == "tts" and spec.key == "engine":
        _switch_tts_engine(state.working, str(new_value))
    else:
        _set_value(state.working, spec, new_value)
    state.dirty = state.working.model_dump(mode="python") != state.original.model_dump(mode="python")
    state.status = _field_updated_status(spec, state.dirty)


async def _test_kokoro_voice(voice: str, config: AppConfig) -> str:
    """Synthesize and play a sample phrase with the given Kokoro voice,
    returning a one-line result the caller can show the operator.

    Reuses _compare_tts_engines' own established pattern (build a real
    TTSConfig, engine.synthesize(), play through the CONFIGURED output
    device via audio_devices' resolve_spec/_play_recording) rather than
    a bespoke path -- a prior version called create_tts_engine with
    kwargs it doesn't accept, and resolve_voice_paths (a Piper-only
    function) on the literal string "kokoro", then swallowed the
    resulting TypeError in a blanket except, so [t] silently did nothing
    at all. sounddevice.play() alone (the prior version's fallback)
    would also have ignored config.audio.output_device and played
    through the SYSTEM default device instead of the configured one.
    """
    import io

    import audio_devices as ad
    import sounddevice as sd

    tts_config = _tts_config_for_comparison(config, "kokoro")
    if tts_config is None:
        tts_config = TTSConfig(engine="kokoro", voice=voice)
    else:
        tts_config = tts_config.model_copy(update={"voice": voice})

    with contextlib.redirect_stdout(io.StringIO()):
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, _ = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx = ad._default_index(sd, "output")

    engine = create_tts_engine(tts_config, DEFAULT_VOICES_DIR)
    audio = await engine.synthesize(f"Testing {voice}. LegionForge ConvoBox voice testing.")
    if audio.size == 0:
        return f"{voice}: produced no audio"
    with contextlib.redirect_stdout(io.StringIO()):
        ad._play_recording(sd, audio, engine.sample_rate, out_idx)
    return f"{voice}: played ({audio.shape[0]} samples @ {engine.sample_rate}Hz)"


async def _test_piper_voice(voice: str, config: AppConfig) -> str:
    """Synthesize and play a sample phrase with the given Piper voice --
    same established pattern as _test_kokoro_voice (real TTSConfig,
    engine.synthesize(), playback through the CONFIGURED output device).
    """
    import io

    import audio_devices as ad
    import sounddevice as sd

    tts_config = _tts_config_for_comparison(config, "piper")
    if tts_config is None:
        tts_config = TTSConfig(engine="piper", voice=voice)
    else:
        tts_config = tts_config.model_copy(update={"voice": voice})

    with contextlib.redirect_stdout(io.StringIO()):
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, _ = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx = ad._default_index(sd, "output")

    engine = create_tts_engine(tts_config, DEFAULT_VOICES_DIR)
    audio = await engine.synthesize(f"Testing {voice}. LegionForge ConvoBox voice testing.")
    if audio.size == 0:
        return f"{voice}: produced no audio"
    with contextlib.redirect_stdout(io.StringIO()):
        ad._play_recording(sd, audio, engine.sample_rate, out_idx)
    return f"{voice}: played ({audio.shape[0]} samples @ {engine.sample_rate}Hz)"


async def _test_piper_speaker(speaker: str, config: AppConfig) -> str:
    """Synthesize and play a sample phrase with the CURRENT Piper voice
    (config.tts.voice) using the given speaker. Unlike voice testing,
    this needs a voice already set -- speakers only exist relative to a
    specific voice's own speaker_id_map.
    """
    tts_config = _tts_config_for_comparison(config, "piper")
    # _tts_config_for_comparison returns the live config unconditionally
    # when its engine already matches "piper" -- even with voice=None --
    # so voice must be checked explicitly here too, not just "is None".
    # Checked BEFORE importing sounddevice/audio_devices below: CI runners
    # (Linux, no PortAudio installed) raise OSError just from importing
    # sounddevice, confirmed live in GitHub Actions run 30150052201 --
    # this early-return path must never touch that import at all.
    if tts_config is None or tts_config.voice is None:
        return "no piper voice configured yet -- pick + save one first"

    import io

    import audio_devices as ad
    import sounddevice as sd

    speaker_value = None if speaker == _PIPER_SPEAKER_DEFAULT else speaker
    tts_config = tts_config.model_copy(update={"speaker": speaker_value})
    label = speaker if speaker_value is not None else "voice's default speaker"

    with contextlib.redirect_stdout(io.StringIO()):
        out_devices = ad.collect_devices(sd, "output")
        if config.audio.output_device:
            out_idx, _ = ad.resolve_spec(config.audio.output_device, out_devices)
        else:
            out_idx = ad._default_index(sd, "output")

    engine = create_tts_engine(tts_config, DEFAULT_VOICES_DIR)
    audio = await engine.synthesize(f"Testing speaker {label}. LegionForge ConvoBox voice testing.")
    if audio.size == 0:
        return f"{label}: produced no audio"
    with contextlib.redirect_stdout(io.StringIO()):
        ad._play_recording(sd, audio, engine.sample_rate, out_idx)
    return f"{label}: played ({audio.shape[0]} samples @ {engine.sample_rate}Hz)"


@_tracks_modal_depth
def _scrollable_test_picker_modal(
    modal_title: str,
    editing_prompt: str,
    choices: list[str],
    unavailable_sentinel: str,
    current: str | None,
    test_fn: Callable[[str], Coroutine[Any, Any, str]] | None,
) -> tuple[bool, str | None]:
    """Generic scrolling choice picker with an optional [t] test key --
    shared core behind the Kokoro-voice, Piper-voice, and Piper-speaker
    pickers. Arrows/Space cycle with the selection always visible
    (render_modal's own choice_options windowing keeps the `>` marker in
    view regardless of list length -- UAT feedback, 2026-07-24: a 54-entry
    list previously only ever showed the first ~15, so cycling past that
    changed the "Current:" line with zero visible list movement). Enter
    confirms, Esc cancels. [t], if given, synthesizes+plays the current
    choice and shows a real result or error line -- never silently
    swallowed (a prior Kokoro-only version did exactly that, see
    _test_kokoro_voice's docstring for the specific bug).
    """
    if not choices:
        choices = [unavailable_sentinel]
    try:
        index = choices.index(_format_value(current))
    except (ValueError, IndexError):
        index = 0

    last_test_result: str | None = None
    while True:
        unavailable = choices[index] == unavailable_sentinel
        detail_lines = [
            "Use arrows or Space to cycle:",
            "",
            "Left/Right or Up/Down to move",
            "Space to cycle forward",
            "[t] to test the current selection" if (test_fn is not None and not unavailable) else "",
            "Enter to confirm, Esc to cancel",
        ]
        if last_test_result is not None:
            detail_lines.extend(["", last_test_result])
        _draw_modal(
            modal_title,
            editing_prompt,
            detail_lines,
            choices[index],
            choice_options=choices,
            choice_value=choices[index],
        )

        key = read_key()
        if key == "ESC":
            return False, current
        if key == "ENTER":
            if unavailable:
                # Nothing real to accept -- same convention as the
                # generic editor's own handling of this sentinel.
                return False, current
            return True, choices[index]
        if key.lower() == "t" and test_fn is not None and not unavailable:
            # Show a "testing..." state immediately -- synthesis + real
            # playback takes a couple of seconds, and the picker would
            # otherwise look frozen with no feedback that [t] did anything.
            _draw_modal(
                modal_title,
                editing_prompt,
                [*detail_lines[:-1], f"Testing {choices[index]}..."],
                choices[index],
                choice_options=choices,
                choice_value=choices[index],
            )
            try:
                last_test_result = asyncio.run(test_fn(choices[index]))
            except Exception as exc:  # noqa: BLE001 -- surfaced to the operator below, not swallowed
                last_test_result = f"test failed: {type(exc).__name__}: {exc}"
            continue

        # All arrow keys and space cycle through choices in this context
        if key in {"LEFT", "UP"}:
            index = (index - 1) % len(choices)
        elif key in {"RIGHT", "DOWN", " "}:
            index = (index + 1) % len(choices)
        last_test_result = None


def _kokoro_voice_picker_modal(current: str, config: AppConfig) -> tuple[bool, str | None]:
    """Dedicated submenu for selecting a Kokoro voice.

    Uses the module-level _TTS_VOICE_KOKORO_FIELD (the real FieldSpec for
    tts.voice) rather than constructing a new one -- a prior version built
    an ad hoc FieldSpec with an invalid `is_required` kwarg, which raised
    on every open and was silently swallowed by a blanket except, always
    falling back to a single hardcoded voice. That bug shipped invisibly
    because the failure path looked identical to "working, only one
    voice downloaded."
    """
    voices = list(_choices_for(_TTS_VOICE_KOKORO_FIELD, config))
    return _scrollable_test_picker_modal(
        "Select Kokoro Voice",
        "Editing tts.voice",
        voices,
        _KOKORO_VOICE_UNAVAILABLE,
        current,
        lambda v: _test_kokoro_voice(v, config),
    )


def _piper_voice_picker_modal(current: str, config: AppConfig) -> tuple[bool, str | None]:
    """Dedicated submenu for selecting an installed Piper voice."""
    voices = _piper_voice_choices()
    return _scrollable_test_picker_modal(
        "Select Piper Voice",
        "Editing tts.voice",
        voices,
        _PIPER_VOICE_UNAVAILABLE,
        current,
        lambda v: _test_piper_voice(v, config),
    )


def _piper_speaker_picker_modal(current: str | None, config: AppConfig) -> tuple[bool, str | None]:
    """Dedicated submenu for selecting a named speaker of the CURRENT
    Piper voice (config.tts.voice). current=None (unset) is seeded as
    _PIPER_SPEAKER_DEFAULT so the picker starts on "(voice default)",
    same convention as the device picker's _SYSTEM_DEFAULT seeding.
    """
    speakers = _piper_speaker_choices(config)
    seed = _PIPER_SPEAKER_DEFAULT if current is None else current
    accepted, chosen = _scrollable_test_picker_modal(
        "Select Piper Speaker",
        "Editing tts.speaker",
        speakers,
        _PIPER_SPEAKER_UNAVAILABLE,
        seed,
        lambda s: _test_piper_speaker(s, config),
    )
    if accepted and chosen == _PIPER_SPEAKER_DEFAULT:
        return True, None
    return accepted, chosen


def _prompt_edit(state: TuiState) -> None:
    spec = state.current_field()
    if spec is None:
        state.status = "nothing to edit in this section"
        return
    current = _get_value(state.working, spec)

    # Use a dedicated picker for kokoro_voice/piper_voice/piper_speaker
    # fields instead of the generic free-text/cycle editor.
    if spec.kind == "kokoro_voice":
        try:
            accepted, new_value = _kokoro_voice_picker_modal(current, state.working)
        except Exception as exc:  # noqa: BLE001
            state.status = f"invalid value: {exc}"
            return
    elif spec.kind == "piper_voice":
        try:
            accepted, new_value = _piper_voice_picker_modal(current, state.working)
        except Exception as exc:  # noqa: BLE001
            state.status = f"invalid value: {exc}"
            return
    elif spec.kind == "piper_speaker":
        try:
            accepted, new_value = _piper_speaker_picker_modal(current, state.working)
        except Exception as exc:  # noqa: BLE001
            state.status = f"invalid value: {exc}"
            return
    else:
        try:
            accepted, new_value = _edit_value_interactive(spec, current, state.working)
        except Exception as exc:  # noqa: BLE001
            state.status = f"invalid value: {exc}"
            return

    if not accepted:
        state.status = "edit cancelled"
        return
    if new_value == current:
        # Live UAT, 2026-08-02: pressing Enter to confirm a picker without
        # cycling to a different choice (a natural way to back out, short
        # of knowing Esc is the actual cancel key) is "accepted" with the
        # SAME value -- previously fell through to _field_updated_status
        # below, which said "{label} updated" (state.dirty came back False
        # because nothing in the whole config changed) even though this
        # field was never actually touched. Distinct message, and skips
        # the pointless _set_value/_switch_* calls below.
        state.status = f"{spec.label} unchanged"
        return
    if spec.section == "backend" and spec.key == "name":
        # backend.name is always a "choice" field (never one of the
        # picker kinds above that can return None), so this is a real
        # invariant, not a defensive workaround.
        assert isinstance(new_value, str)
        _switch_backend(state.working, new_value)
    elif spec.section == "tts" and spec.key == "engine":
        assert isinstance(new_value, str)
        _switch_tts_engine(state.working, new_value)
    else:
        _set_value(state.working, spec, new_value)
    state.dirty = state.working.model_dump(mode="python") != state.original.model_dump(mode="python")
    state.status = _field_updated_status(spec, state.dirty)


def _restore_original(state: TuiState) -> None:
    state.working = state.original.model_copy(deep=True)
    state.dirty = False
    state.last_report = None
    state.status = "staged changes reverted"


def _restore_from_backup(state: TuiState) -> None:
    backups = list_config_backups(state.path)
    if not backups:
        state.status = f"no backups found in {_backup_dir(state.path)}"
        return
    latest = backups[0]
    if not _confirm_modal(
        "Restore From Backup",
        f"Load {latest.name} into the working copy?",
        [
            f"This replaces every staged value with {latest.name}'s contents.",
            "Nothing is written to disk until you press [S] to save.",
            (
                f"({len(backups)} backup(s) available in {_backup_dir(state.path).name}/; "
                "restoring the most recent.)"
            ),
        ],
    ):
        state.status = "restore cancelled"
        return
    try:
        restored = load_config(latest)
    except ValidationError as exc:
        # A backup should always have been a previously-valid save -- if
        # one somehow isn't, say so loudly rather than silently falling
        # back to defaults the way load_config_lenient does for the
        # startup case; this path is meant to recover known-good state,
        # not manufacture a new fallback.
        state.status = f"backup {latest.name} itself failed to load: {exc}"
        return
    state.working = restored
    state.dirty = state.working.model_dump(mode="python") != state.original.model_dump(
        mode="python"
    )
    state.load_problems = []
    state.status = f"restored from {latest.name} -- [S] to save, or keep editing"


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
        hint_override=f"Esc cancel without saving | Enter accept and save changes to {state.path}?",
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
    state.load_problems = []
    # Explicit that quitting is now safe -- the operator just watched the
    # dirty-state header/save hint tell them to press [S]; closing the
    # loop here means they don't have to separately notice the header
    # flipping back to "clean" before trusting that [Q] won't discard
    # anything (live UAT feedback, 2026-07-22).
    state.status = f"saved to {state.path} -- [Q] to quit"


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
            field = state.current_field()
            field_key = field.key if field else None
            if field_key == "input_device":
                state.status = await _probe_input_device_live(state)
            else:
                state.status = await probe_audio(state.working, field_key)
        else:
            state.status = f"{section} configuration validated"
    except Exception as exc:  # noqa: BLE001
        state.status = f"{section} test failed: {type(exc).__name__}: {exc}"


_SPINNER_FRAMES = "|/-\\"


def _key_waiting() -> bool:
    """Non-blocking "is a key pending?" check, both platforms.

    read_key() itself always blocks until a key arrives -- fine for the
    main draw()->read_key() loop, wrong here: this only needs to peek
    during the spinner's own poll tick, not stall it waiting for input
    that may never come.

    Defensive: select.select() needs sys.stdin to be a real, fileno()-
    having stream. It isn't always -- pytest's captured stdin under CI is
    a pseudofile and raises UnsupportedOperation here (live-confirmed:
    GitHub Actions run for PR #196, io.UnsupportedOperation: "redirected
    stdin is pseudofile, has no fileno()") -- and the same class of
    failure could hit any real invocation with stdin redirected/piped,
    not just tests. Treat "can't check" as "no key waiting" rather than
    crashing the whole spinner/test over a feature (ESC-cancel) that was
    never going to fire without a real interactive terminal anyway.
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            return msvcrt.kbhit()
        import select

        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (OSError, ValueError, io.UnsupportedOperation):
        return False


def _run_with_spinner(state: TuiState, run: Callable[[], None]) -> None:
    """Runs `run` on a background thread, redrawing an elapsed-time spinner
    in state.status until it finishes, ESC cancels waiting.

    run_tui()'s own loop is a plain synchronous draw() -> read_key() cycle
    with no independent redraw tick -- calling something slow (e.g.
    asyncio.run(_test_state(...)) directly, as this used to) blocks that
    loop completely, so a large not-yet-cached Whisper model download
    (tens of seconds to minutes for large-v3 -- live-confirmed 2026-08-01,
    still running past 330s on one real connection) looked exactly like a
    hang, with no way to tell the two apart. `run` sets state.status
    itself on completion (success or failure, see _test_state's own
    try/except); this only owns the status line while `run` is still in
    flight.

    ESC hands control back immediately but does NOT stop the underlying
    work -- there's no safe way to kill a Python thread mid-download, and
    huggingface_hub's own transfer loop exposes no cancellation hook this
    code could call into. `run` keeps executing as an orphaned daemon
    thread and will still overwrite state.status with its own real result
    whenever it eventually finishes, possibly minutes later while the
    operator is doing something else -- rare and purely cosmetic (the
    next real keypress redraws normally regardless), not a correctness
    issue, but worth being honest about rather than implying a clean stop.
    """
    thread = threading.Thread(target=run, daemon=True)
    start = time.monotonic()
    thread.start()
    frame = 0
    while thread.is_alive():
        if _key_waiting() and read_key() == "ESC":
            state.status = (
                "cancelled -- can't safely interrupt an in-flight "
                "download/model call, so it keeps running in the "
                "background; this status may still change once it finishes"
            )
            return
        elapsed = time.monotonic() - start
        spinner = _SPINNER_FRAMES[frame % len(_SPINNER_FRAMES)]
        state.status = (
            f"{spinner} testing... ({elapsed:.0f}s, ESC to cancel) -- a "
            "not-yet-cached model can take a while to download"
        )
        draw(state)
        frame += 1
        time.sleep(0.12)
    thread.join()


def _handle_browse(state: TuiState, key: str) -> bool:
    # key.lower() unconditionally, not just for single-char keys: read_key()
    # returns "ESC" (uppercase, 3 chars) for a real Escape press, and the
    # old `if len(key) == 1 else key` guard left that uncased, so
    # `lowered in ("q", "esc")` could never match it -- Esc was a silent
    # no-op in the browse view with no quit-confirm at all. Safe to drop
    # the guard: every multi-char token's lowered form (e.g. "up", "enter")
    # is still distinct from the single-char bindings checked below.
    lowered = key.lower()
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
    elif lowered == "b":
        _restore_from_backup(state)
    elif lowered == "t":
        _run_with_spinner(state, lambda: asyncio.run(_test_state(state)))
    elif lowered == "c":
        asyncio.run(_compare_tts_engines(state))
    elif lowered == "d":
        _refresh_kokoro_voices(state)
    return True


def _apply_load_recovery(state: TuiState, problems: list[str]) -> None:
    """Reflects load_config_lenient()'s problems list (if any) onto a
    freshly constructed TuiState: flags it dirty, jumps to the first
    affected section, and sets a status banner naming what happened and
    how to respond. No-op when problems is empty (the ordinary case).

    Split out from run_tui() so this logic -- which is the whole point of
    load_config_lenient existing -- is unit-testable without driving the
    real interactive read_key()/draw() loop.
    """
    if not problems:
        return
    # Section(s) on disk failed validation and were loaded as defaults
    # instead of crashing (load_config_lenient's docstring has the full
    # incident). Marked dirty even though working == original here --
    # "clean" would wrongly claim this matches the file that's still
    # sitting on disk with the rejected value in it.
    state.load_problems = problems
    state.dirty = True
    for idx, section in enumerate(state.sections):
        if section.key in state.flagged_sections:
            state.selected_section = idx
            break
    state.status = (
        f"{len(problems)} setting(s) on disk were invalid and reset to "
        f"defaults ({', '.join(sorted(state.flagged_sections))}) -- "
        "review and [S] save, or [B] restore last backup"
    )


def run_tui(config_path: Path | None = None) -> None:
    path = config_path or default_config_path()
    config, _raw, problems = load_config_lenient(path)
    state = TuiState(path=path, original=config, working=config.model_copy(deep=True))
    _apply_load_recovery(state, problems)
    _enable_ansi()
    sys.stdout.write("\x1b[?25l\x1b[2J")
    sys.stdout.flush()
    # Opt-in, zero effect unless set (live-debugging aid, 2026-08-30): two
    # reported arrow-key fixes (SS3 recognition, a wider select() timeout)
    # each seemed plausible but neither had a confirmed-working live report
    # -- "sorta" better, still needing multiple presses per field. Rather
    # than guess a third root cause, this logs the exact raw key read and
    # the resulting navigation state on every keypress, so the next live
    # session gives ground truth instead of another hypothesis.
    debug_path = os.environ.get("CONVOBOX_TUI_DEBUG_KEYS")
    debug_file = open(debug_path, "a", encoding="utf-8") if debug_path else None  # noqa: SIM115
    # Live-reported 2026-08-30 (docs/KNOWN-ISSUES.md, "...never repaints on
    # resize alone"): the main loop below only calls draw() once per
    # keypress, then blocks inside read_key()'s raw-mode sys.stdin.read(1)
    # -- resizing the terminal while idle left the stale layout on screen
    # until the next key. SIGWINCH is delivered on every resize; Python
    # (PEP 475) automatically retries the interrupted read(1) once the
    # handler returns, so this just needs to repaint, not touch the read
    # loop itself. Not available on Windows (no SIGWINCH there) -- draw()
    # is still correct on the next keypress in that case, same as before.
    _resize_handler_installed = hasattr(signal, "SIGWINCH")
    if _resize_handler_installed:
        def _on_resize(signum: int, frame: Any) -> None:
            # Skip while a modal's own loop owns the screen (_modal_depth,
            # set by _tracks_modal_depth) -- repainting the main browse
            # screen here would blow away whatever modal is actually
            # showing instead of it. That modal's own next keystroke-driven
            # redraw already picks up the new size; only the idle main-loop
            # case (no modal, blocked in read_key()) needs this handler.
            if _modal_depth == 0:
                with contextlib.suppress(Exception):
                    draw(state)

        _previous_winch_handler = signal.signal(signal.SIGWINCH, _on_resize)
    try:
        running = True
        while running:
            draw(state)
            before = (state.selected_section, state.selected_field)
            t0 = time.monotonic()
            key = read_key()
            read_ms = (time.monotonic() - t0) * 1000
            if debug_file is not None:
                debug_file.write(
                    f"{time.strftime('%H:%M:%S')} read_key()={key!r} "
                    f"(took {read_ms:.1f}ms) before=section{before[0]}/field{before[1]}\n"
                )
                debug_file.flush()
            if not key:
                if debug_file is not None:
                    debug_file.write("  -> empty key, looping without handling\n")
                    debug_file.flush()
                continue
            running = _handle_browse(state, key)
            if debug_file is not None:
                after = (state.selected_section, state.selected_field)
                debug_file.write(
                    f"  -> handled, after=section{after[0]}/field{after[1]} "
                    f"(moved={after != before})\n"
                )
                debug_file.flush()
    finally:
        if _resize_handler_installed:
            signal.signal(signal.SIGWINCH, _previous_winch_handler)
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()
        if debug_file is not None:
            debug_file.close()
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
