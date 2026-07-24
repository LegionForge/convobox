from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np

from convobox.tts.base import TTSEngine, sanitize_text

# Kokoro-82M's known native output rate; used only as the value exposed
# before any synthesis has happened. Every real chunk updates this from
# kokoro-onnx's own returned sample_rate, so a wrong initial guess here
# can't silently produce mis-pitched audio once synthesis actually runs.
_DEFAULT_SAMPLE_RATE = 24000


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
            async for samples, sample_rate in stream:
                if self._stopped:
                    return
                self._sample_rate = sample_rate
                yield np.asarray(samples, dtype=np.float32)
        finally:
            self._speaking = False

    def stop(self) -> None:
        self._stopped = True

    def is_speaking(self) -> bool:
        return self._speaking
