from __future__ import annotations

import os
from pathlib import Path

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
    # acoustic path). APM adapts around it; the default suits typical
    # Windows onboard audio. Tune per machine during UAT if suppression
    # is weak.
    aec_delay_ms: int = 100


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


class TTSConfig(BaseModel):
    engine: str = "piper"
    voice: str | None = None
    rate: float = 1.0
    volume: float = 1.0


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


class BackendProfileConfig(BaseModel):
    # Per-backend memory for the settings TUI. `url` matters for opencode;
    # `command` matters for claude-code and codex.
    url: str | None = None
    command: list[str] | None = None


class AppConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    safeword: SafewordConfig = Field(default_factory=SafewordConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    backend_profiles: dict[str, BackendProfileConfig] = Field(default_factory=dict)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)


def load_config(path: str | Path | None = None) -> AppConfig:
    candidate = Path(path) if path else Path(os.environ.get("CONVOBOX_CONFIG", "convobox.yaml"))
    if not candidate.exists():
        return AppConfig()
    with candidate.open() as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)
