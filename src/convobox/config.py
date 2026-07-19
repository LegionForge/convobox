from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from convobox.interrupt_presets import resolve_preset
from convobox.listening_pause import DEFAULT_PAUSE_PHRASES
from convobox.wakeword import DEFAULT_WAKE_WORD


class AudioConfig(BaseModel):
    input_device: str | None = None
    output_device: str | None = None
    sample_rate: int = 16000
    # Acoustic echo cancellation (WebRTC APM via the optional [aec]
    # extra). Off by default: it needs the extra installed, and its
    # value depends on the speaker/mic arrangement -- see
    # docs/DESIGN-echo-and-barge-in.md.
    echo_cancellation: bool = False
    # Hint for the canceller: expected ms between writing audio to the
    # output device and hearing it back in the mic (device buffers +
    # acoustic path). None (the default) means auto-tune: run_convobox.py
    # measures the real output+input stream latencies on first playback
    # and uses that instead -- confirmed live (2026-07-15) that a wrong
    # fixed hint (measured ~222ms vs a stale 100ms) keeps WebRTC AEC3 from
    # converging, so the assistant's own voice leaks into the mic and
    # trips the barge-in overlap gate. Set an explicit int only if you've
    # measured a genuinely better fixed value for this exact hardware and
    # want it to override auto-tuning.
    aec_delay_ms: int | None = None


class VADConfig(BaseModel):
    threshold: float = 0.5
    min_silence_ms: int = 500
    min_speech_ms: int = 250
    # Force-emit an utterance that exceeds this many seconds of audio even if
    # no silence gap has occurred. None = unlimited (the pre-existing
    # behavior). Without a cap, continuous speech means an unbounded buffer
    # and no transcript at all until the speaker pauses; observed live as a
    # 30.5s single utterance whose transcript only arrived after it ended.
    max_utterance_s: float | None = None


class STTConfig(BaseModel):
    # Which STT engine to build (see convobox.stt.factory). Only
    # faster-whisper is implemented today; the field exists so STT is
    # selectable/pluggable symmetrically with tts.engine.
    engine: str = "faster-whisper"
    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = None
    # Drop transcripts whose detected-language probability falls below this
    # (0.0 = disabled). Live testing showed detections under ~0.4 on accented
    # or ambiguous audio are usually hallucinations, sometimes in an entirely
    # different script. Only meaningful when ``language`` is None (a pinned
    # language reports probability 1.0). Consumers must still check the
    # safeword on the raw transcript BEFORE applying this gate: a confidence
    # filter must never be able to swallow a hard stop.
    min_language_probability: float = 0.0
    # Exact, operator-maintained fixes for recurring STT mistakes.  Applied
    # only to ordinary command routing after raw safeword/pause/approval
    # checks; see convobox.stt.corrections.TranscriptCorrector.  Keeping the
    # glossary in config makes every rewrite inspectable and portable, rather
    # than silently training on a user's voice data.
    corrections: dict[str, str] = Field(default_factory=dict)

    @field_validator("corrections")
    @classmethod
    def _validate_corrections(cls, v: dict[str, str]) -> dict[str, str]:
        # Constructing the corrector performs normalization-aware validation
        # (empty sources/targets and duplicate normalized sources).  Import
        # lazily to keep config's existing import surface lightweight.
        from convobox.stt.corrections import TranscriptCorrector

        TranscriptCorrector(v)
        return v


class TTSConfig(BaseModel):
    engine: str = "piper"
    voice: str | None = None
    rate: float = 1.0
    volume: float = 1.0
    # piper only: select a speaker for a multi-speaker voice, by name
    # (matching the voice's own speaker_id_map, e.g. "prudence" for
    # en_GB-semaine-medium) or a raw numeric index. None (default) uses
    # the voice's own default speaker (index 0) -- unchanged behavior
    # for the single-speaker voices this project has used until now.
    # Real, not hypothetical: several already-downloaded Piper voices in
    # this repo (en_GB-semaine-medium: 4 named speakers, en_GB-aru-medium:
    # 12, en_GB-vctk-medium: 109, en_US-libritts-high: 904) are genuinely
    # multi-speaker and this had no way to select anything but the
    # implicit default. No pydantic-level format validation here -- unlike
    # backend.model's cheap "/" check, resolving a speaker name requires
    # the actual voice model loaded (PiperVoice.load), which only happens
    # in PiperTTSEngine's own construction; see that class for the real
    # validation and error message.
    speaker: str | None = None


class InteractionConfig(BaseModel):
    # What happens when the user talks while a response is playing --
    # one of the named presets in convobox.interrupt_presets.PRESETS
    # (docs/DESIGN-barge-in.md's two-axis grid: on_current_turn x
    # on_new_words). Default is "do-not-disturb" (let-finish + drop) --
    # behaviorally identical to the old interrupt_mode="none" default
    # (half-duplex: overlapping speech is dropped) -- deliberately NOT
    # switched to "conversational" by this migration. Whether
    # "conversational" should become the shipped default is a real
    # product decision flagged for live UAT, not something a schema
    # refactor should silently decide (docs/DESIGN-0.3.0-interaction-and-safety.md's
    # open questions). Non-"do-not-disturb"/"halt" presets need
    # audio.echo_cancellation (or headphones) -- see
    # docs/DESIGN-echo-and-barge-in.md -- without it the assistant's own
    # voice trips the VAD and it interrupts itself.
    interrupt_preset: str = "do-not-disturb"
    # Sustained speech required before barge-in fires, so a cough or a
    # chair creak doesn't kill a response.
    barge_in_min_speech_ms: int = 250

    @field_validator("interrupt_preset")
    @classmethod
    def _validate_interrupt_preset(cls, v: str) -> str:
        resolve_preset(v)  # raises ValueError listing valid choices
        return v
    # Shared by two independent features (docs/DESIGN-barge-in.md, "Pause/
    # resume listening"): the push-word barge-in trigger (future work) and
    # resuming from the paused listening state (below) both use this word.
    wake_word: str = DEFAULT_WAKE_WORD
    # Saying one of these hard-stops in-flight backend work (same as the
    # safeword) and enters a paused state where only wake_word is heard,
    # until it's said and normal listening resumes.
    pause_listening_phrases: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PAUSE_PHRASES)
    )
    # Response tiering (docs/DESIGN-0.3.0-interaction-and-safety.md, Phase
    # 2): "voice always gives the tiered/short version." Off by default --
    # existing sessions hear the full response exactly as before. When on,
    # only the first paragraph of a multi-paragraph response is spoken;
    # ContinueDetector's "continue"/"go on"/a bare "yes" within
    # continue_timeout_s of the response finishing speaks the rest.
    # Silence past the timeout implies "no" -- never treated as consent to
    # keep talking, same non-auto-approve spirit as approval prompts, just
    # for a much lower-stakes decision.
    tier_responses: bool = False
    # 1-4s range per the design doc; 2.5s split-the-difference default,
    # not yet live-UAT-tuned against a real "did that feel laggy or
    # naggy" pass.
    continue_timeout_s: float = 2.5


class SafewordConfig(BaseModel):
    hard_stop_phrases: list[str] = Field(default_factory=lambda: ["stop stop stop"])


class BackendConfig(BaseModel):
    name: str = "opencode"
    # Used by HTTP-based backends (opencode).
    url: str = "http://localhost:4096"
    # Used by subprocess-based backends (claude-code): the base command to
    # spawn, e.g. ["claude"] or ["claude", "--model", "claude-haiku-4-5"].
    # The adapter appends the protocol flags it needs itself.
    command: list[str] | None = None
    # opencode only: pin which model a NEW session uses, "provider/model-id"
    # (matches `opencode models`' own output format, e.g.
    # "openai/gpt-5.6-sol"). None (default) leaves it to opencode's own
    # default -- confirmed live, 2026-07-14, that this can silently be a
    # hosted free-tier model (OpenCode Zen's own default) rather than the
    # user's own configured provider, with no error or warning either way.
    # NOT a CLI flag: `opencode serve` (the mode this adapter connects to)
    # has no -m/--model option at all (confirmed via `opencode serve
    # --help`) -- that flag only exists on `opencode run`/the interactive
    # TUI, neither of which this project's HTTP+SSE adapter uses. The real
    # mechanism, confirmed against a live server's own OpenAPI spec
    # (`GET /doc`), is `POST /api/session`'s optional `model: {providerID,
    # id}` field -- see OpenCodeAdapter._ensure_session().
    model: str | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str | None) -> str | None:
        if v is not None and "/" not in v:
            raise ValueError(
                f"backend.model {v!r} must be \"provider/model-id\" "
                f"(e.g. \"openai/gpt-5.6-sol\") -- see `opencode models` "
                f"for the full list"
            )
        return v


class BackendProfileConfig(BaseModel):
    # Per-backend memory for the settings TUI. `url`/`model` matter for
    # opencode; `command` matters for claude-code and codex.
    url: str | None = None
    command: list[str] | None = None
    model: str | None = None


class AppConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    safeword: SafewordConfig = Field(default_factory=SafewordConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    backend_profiles: dict[str, BackendProfileConfig] = Field(default_factory=dict)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)


def resolve_config_path(path: str | Path | None = None) -> Path:
    """The same explicit-path / CONVOBOX_CONFIG / convobox.yaml fallback
    load_config() uses, exposed so callers that need to know WHICH file
    would be loaded (not just its parsed contents) don't have to
    duplicate the resolution order -- settings_tui.py's own
    default_config_path() and run_convobox.py's AEC-estimate sidecar path
    both need this."""
    return Path(path) if path else Path(os.environ.get("CONVOBOX_CONFIG", "convobox.yaml"))


def load_config(path: str | Path | None = None) -> AppConfig:
    candidate = resolve_config_path(path)
    if not candidate.exists():
        return AppConfig()
    with candidate.open() as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


def aec_estimate_path(config_path: Path) -> Path:
    """A diagnostic sidecar next to the config file, not part of the
    config schema itself: run_convobox.py writes the AEC delay it
    actually auto-estimated (aec_delay_ms=None, the auto-tune case) here
    on every startup, so the Settings TUI can show "last auto-detected"
    for a value that only ever exists at runtime, without either process
    mutating convobox.yaml itself (that file should only ever reflect
    what the user deliberately set) or the two processes needing a live
    connection to each other."""
    return config_path.with_name(config_path.name + ".aec-estimate.json")


def write_aec_estimate(
    config_path: Path, delay_ms: int, output_latency_ms: float, input_latency_ms: float
) -> None:
    """Best-effort only -- a diagnostic write must never crash the voice
    loop over a permissions error or a read-only filesystem."""
    try:
        aec_estimate_path(config_path).write_text(
            json.dumps(
                {
                    "delay_ms": delay_ms,
                    "output_latency_ms": round(output_latency_ms, 1),
                    "input_latency_ms": round(input_latency_ms, 1),
                    "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        )
    except OSError:
        pass


def read_aec_estimate(config_path: Path) -> dict[str, Any] | None:
    """The counterpart read, for the Settings TUI -- also best-effort:
    a missing/corrupt sidecar (never written yet, or from a stale format)
    just means "nothing to show," never a crash."""
    try:
        path = aec_estimate_path(config_path)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text())
        return data
    except (OSError, json.JSONDecodeError):
        return None
