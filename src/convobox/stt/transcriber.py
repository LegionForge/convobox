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
    # Mean per-segment avg_logprob from the decoder. Unlike
    # language_probability (hardcoded to 1.0 whenever the language is
    # pinned), this reflects how confident the decoder was in the words
    # themselves, so it stays meaningful in pinned-language mode.
    # exp(avg_logprob) maps it to a (0, 1] confidence-like score.
    avg_logprob: float
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
        # transcribe() returns a lazy generator; materializing it here is
        # what actually runs the decode, so it must stay inside the timing.
        segment_list = list(segments)
        latency_ms = (time.perf_counter() - start) * 1000.0

        segment_texts = [segment.text.strip() for segment in segment_list]
        # -10.0 when nothing decoded: exp(-10) ~= 0, i.e. zero confidence,
        # without the -inf that would poison downstream arithmetic.
        avg_logprob = (
            sum(segment.avg_logprob for segment in segment_list) / len(segment_list)
            if segment_list
            else -10.0
        )

        return TranscriptResult(
            text=" ".join(segment_texts).strip(),
            language=info.language,
            language_probability=info.language_probability,
            latency_ms=latency_ms,
            duration_s=len(audio) / SAMPLE_RATE,
            avg_logprob=float(avg_logprob),
            segments=segment_texts,
        )
