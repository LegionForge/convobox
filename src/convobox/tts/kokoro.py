from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np

from convobox.tts.base import TTSEngine, sanitize_text

# Kokoro-82M's known native output rate; used only as the value exposed
# before any synthesis has happened. Every real chunk updates this from
# kokoro-onnx's own returned sample_rate, so a wrong initial guess here
# can't silently produce mis-pitched audio once synthesis actually runs.
_DEFAULT_SAMPLE_RATE = 24000

# Live-confirmed 2026-07-24: kokoro-onnx's own create_stream() starts a
# detached `asyncio.create_task(process_batches())` with no reference kept
# and no exception handling. If a batch's internal _create_audio() call
# raises for ANY reason -- confirmed trigger: text producing more than the
# model's ~510-phoneme batch limit hits `voice = voice[len(tokens)]` ->
# IndexError, logged only as "Task exception was never retrieved" -- the
# task dies without ever calling `queue.put(None)` (the end-of-stream
# sentinel), so the consumer's `await queue.get()` blocks FOREVER. Isolated
# and confirmed live: normal short text streams a single chunk in ~1.8s;
# the over-length case hangs indefinitely with 0% CPU (not just slow).
# wait_for-ing each chunk bounds that hang to a real, catchable error
# instead of hanging the whole voice session. 30s is well above the ~2s
# observed for a single max-size batch on this machine's CPU, leaving
# headroom for slower hardware without letting a real hang go unnoticed
# for minutes.
_CHUNK_TIMEOUT_S = 30.0


class KokoroTTSEngine(TTSEngine):
    """Local TTS via the kokoro-onnx package.

    Default engine: kokoro-onnx itself is MIT, and the Kokoro-82M model
    weights it loads are Apache-2.0 — clean permissive licensing end to
    end, unlike PiperTTSEngine (GPL-3.0, opt-in only). See
    DEPENDENCY_LICENSE_AUDIT.md.
    """

    def __init__(
        self,
        model_path: str,
        voices_path: str,
        voice: str = "af_sarah",
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> None:
        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(model_path, voices_path)
        self._voice = voice
        self._speed = speed
        self._lang = lang
        self._sample_rate = _DEFAULT_SAMPLE_RATE
        self._speaking = False
        self._stopped = False

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize_stream(self, text: str) -> AsyncIterator[np.ndarray]:
        clean = sanitize_text(text)
        if not clean.strip():
            return

        self._stopped = False
        self._speaking = True
        try:
            stream = self._kokoro.create_stream(
                clean, voice=self._voice, speed=self._speed, lang=self._lang
            )
            while True:
                if self._stopped:
                    return
                try:
                    # See _CHUNK_TIMEOUT_S's comment: kokoro-onnx's own
                    # internal task can die silently and never signal
                    # end-of-stream, which would otherwise hang this
                    # `await` forever.
                    samples, sample_rate = await asyncio.wait_for(
                        stream.__anext__(), timeout=_CHUNK_TIMEOUT_S
                    )
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"Kokoro synthesis stalled (no audio chunk within "
                        f"{_CHUNK_TIMEOUT_S}s) -- likely kokoro-onnx's own "
                        "internal batch task died silently on this text "
                        "(known trigger: text producing more than the "
                        "model's ~510-phoneme batch limit)"
                    ) from exc
                self._sample_rate = sample_rate
                yield np.asarray(samples, dtype=np.float32)
        finally:
            self._speaking = False

    def stop(self) -> None:
        self._stopped = True

    def is_speaking(self) -> bool:
        return self._speaking
