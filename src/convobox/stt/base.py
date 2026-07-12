from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    language_probability: float
    latency_ms: float
    duration_s: float
    # Mean per-segment avg_logprob from the decoder. Unlike
    # language_probability (hardcoded to 1.0 whenever the language is
    # pinned), this reflects how confident the decoder was in the words
    # themselves, so it stays meaningful in pinned-language mode.
    # exp(avg_logprob) maps it to a (0, 1] confidence-like score.
    avg_logprob: float
    segments: list[str] = field(default_factory=list)


class STTEngine(ABC):
    """One implementation per local speech-to-text backend.

    Audio in is float32 PCM mono at SAMPLE_RATE. The symmetric counterpart
    to TTSEngine: modeling STT as an interface + factory (see
    convobox.stt.factory) is what lets the engine become pluggable and
    installable per user, the same way TTS engines are -- see
    docs/ROADMAP.md's "pluggable STT/TTS engines".
    """

    @abstractmethod
    def transcribe(self, audio: np.ndarray) -> TranscriptResult: ...
