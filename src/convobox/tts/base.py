from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import numpy as np

MAX_TEXT_LENGTH = 10_000

_CONTROL_CHARS = {c for c in range(0x00, 0x20)} | {0x7F}
_CONTROL_TRANSLATION = {c: None for c in _CONTROL_CHARS if c not in (0x09, 0x0A, 0x0D)}


def sanitize_text(text: str) -> str:
    """Make untrusted text safe to hand to a TTS engine.

    LLM-response text is untrusted input: strip control characters
    (\\x00-\\x1F, \\x7F, keeping tab/newline/carriage-return) and cap length.
    Every concrete TTSEngine must call this before synthesizing.
    """
    return text.translate(_CONTROL_TRANSLATION)[:MAX_TEXT_LENGTH]


class TTSEngine(ABC):
    """One implementation per local TTS backend (Piper, Kokoro, ...).

    Audio is float32 PCM mono at self.sample_rate. Concrete engines
    implement synthesize_stream and must call sanitize_text on the input
    before synthesizing, since the text originates from untrusted LLM
    output. synthesize() is a concatenating convenience built on top of it
    here — prefer synthesize_stream directly when time-to-first-audio
    matters (e.g. feeding a live AudioPlayer), since it lets a caller start
    playback on the first chunk instead of waiting for the whole utterance
    to finish synthesizing.
    """

    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    @abstractmethod
    def synthesize_stream(self, text: str) -> AsyncIterator[np.ndarray]: ...

    async def synthesize(self, text: str) -> np.ndarray:
        chunks = [chunk async for chunk in self.synthesize_stream(text)]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_speaking(self) -> bool: ...
