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


class STTConfig(BaseModel):
    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = None


class TTSConfig(BaseModel):
    engine: str = "piper"
    voice: str | None = None
    rate: float = 1.0
    volume: float = 1.0


class SafewordConfig(BaseModel):
    hard_stop_phrases: list[str] = Field(default_factory=lambda: ["stop stop stop"])


class BackendConfig(BaseModel):
    name: str = "opencode"
    url: str = "http://localhost:4096"


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
