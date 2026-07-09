from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from faster_whisper import WhisperModel

from convobox.config import STTConfig

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    language_probability: float
    latency_ms: float
    duration_s: float
    segments: list[str] = field(default_factory=list)


class LocalTranscriber:
    def __init__(self, config: STTConfig) -> None:
        self._config = config
        self._model = WhisperModel(
            config.model,
            device=config.device,
            compute_type=config.compute_type,
        )

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        # faster-whisper expects a contiguous float32 mono array at 16kHz.
        audio = np.ascontiguousarray(audio, dtype=np.float32)

        start = time.perf_counter()
        segments, info = self._model.transcribe(
            audio,
            language=self._config.language,
        )
        segment_texts = [segment.text.strip() for segment in segments]
        latency_ms = (time.perf_counter() - start) * 1000.0

        return TranscriptResult(
            text=" ".join(segment_texts).strip(),
            language=info.language,
            language_probability=info.language_probability,
            latency_ms=latency_ms,
            duration_s=len(audio) / SAMPLE_RATE,
            segments=segment_texts,
        )
