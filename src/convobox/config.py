from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AudioConfig(BaseModel):
    input_device: str | None = None
    output_device: str | None = None
    sample_rate: int = 16000


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


class AppConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    safeword: SafewordConfig = Field(default_factory=SafewordConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)


def load_config(path: str | Path | None = None) -> AppConfig:
    candidate = Path(path) if path else Path(os.environ.get("CONVOBOX_CONFIG", "convobox.yaml"))
    if not candidate.exists():
        return AppConfig()
    with candidate.open() as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)
