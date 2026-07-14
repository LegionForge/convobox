from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
from faster_whisper import WhisperModel

from convobox.config import STTConfig
from convobox.stt.base import SAMPLE_RATE, STTEngine, TranscriptResult

# Re-exported for backward compatibility: TranscriptResult and SAMPLE_RATE
# now live in convobox.stt.base (shared with the STTEngine interface), but
# code importing them from here keeps working.
__all__ = ["SAMPLE_RATE", "LocalTranscriber", "TranscriptResult"]

logger = logging.getLogger(__name__)


class _WhisperLikeModel(Protocol):
    """Structural type for the WhisperModel.transcribe() shape -- lets
    `model_factory` (below) accept a real WhisperModel or a test fake
    without either depending on the other.
    """

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> tuple[Any, Any]: ...


class LocalTranscriber(STTEngine):
    def __init__(
        self,
        config: STTConfig,
        model_factory: Callable[[], _WhisperLikeModel] | None = None,
    ) -> None:
        # `model_factory` is an injection point for tests (a fake model
        # with a `.transcribe()` method, no real Whisper weights needed) --
        # every real caller passes only `config` and gets the real
        # WhisperModel, unchanged from before this parameter existed.
        self._config = config
        self._model_factory = model_factory or (
            lambda: WhisperModel(config.model, device=config.device, compute_type=config.compute_type)
        )
        self._model = self._model_factory()

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        # faster-whisper expects a contiguous float32 mono array at 16kHz.
        audio = np.ascontiguousarray(audio, dtype=np.float32)

        start = time.perf_counter()
        try:
            segments, info = self._model.transcribe(
                audio,
                language=self._config.language,
            )
            # transcribe() returns a lazy generator; materializing it here
            # is what actually runs the decode, so it must stay inside the
            # try (ctranslate2's native encode() failure surfaces during
            # iteration, not the transcribe() call itself) and the timing.
            segment_list = list(segments)
        except RuntimeError:
            # Known, unresolved upstream issue: ctranslate2's native
            # (MKL on Windows) allocator leaks memory across repeated
            # transcribe() calls in a long-lived process, eventually
            # failing with "mkl_malloc: failed to allocate memory" /
            # "could not create a memory object"
            # (SYSTRAN/faster-whisper#660, #390) -- confirmed live,
            # 2026-07-14, crashing a real ~13-minute UAT session (~20
            # transcriptions in) with an unhandled traceback that killed
            # the whole voice loop. Not a ConvoBox bug and not something
            # Python-level garbage collection can fix (it's native heap).
            # The practical mitigation is recycling the model object,
            # which resets its allocator state. Broad `except RuntimeError`
            # is deliberate, not lazy: reloading-and-treating-as-unheard is
            # SAFE regardless of the actual cause (it can only make an STT
            # hiccup non-fatal, never mask a silent wrong answer), and the
            # full exception is still logged at WARNING with a traceback --
            # nothing here is silently swallowed, it's converted from a
            # fatal crash into a loud, recoverable one. One lost utterance
            # is a far better failure mode than losing the entire session.
            logger.warning(
                "faster-whisper native transcribe() failure -- reloading the "
                "STT model and treating this utterance as unheard "
                "(see SYSTRAN/faster-whisper#660 if this recurs)",
                exc_info=True,
            )
            self._model = self._model_factory()
            latency_ms = (time.perf_counter() - start) * 1000.0
            return TranscriptResult(
                text="",
                language="",
                language_probability=0.0,
                latency_ms=latency_ms,
                duration_s=len(audio) / SAMPLE_RATE,
                avg_logprob=-10.0,
                segments=[],
            )
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
