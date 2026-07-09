from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator

import numpy as np

from convobox.tts.base import TTSEngine, sanitize_text

_STREAM_DONE = object()


class PiperTTSEngine(TTSEngine):
    """Local TTS via the piper-tts Python package.

    Input text is sanitized (sanitize_text) and passed to piper's in-process
    Python API — never shelled out to. This keeps untrusted LLM-response text
    off any shell command line.

    Note: piper-tts's Python API surface has varied across releases. This
    wrapper targets the PiperVoice interface (PiperVoice.load(...) returning
    an object whose synthesize(...) yields audio chunks exposing int16 PCM).
    If a given piper-tts build exposes a different signature, only the two
    calls marked below (_load_voice and _synthesize_int16) need adjusting.
    """

    def __init__(self, model_path: str, config_path: str | None = None) -> None:
        self._model_path = model_path
        self._config_path = config_path
        self._voice = self._load_voice()
        self._sample_rate = int(self._voice.config.sample_rate)
        self._speaking = False
        self._stopped = False

    def _load_voice(self) -> object:
        from piper import PiperVoice

        return PiperVoice.load(self._model_path, config_path=self._config_path)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize_stream(self, text: str) -> AsyncIterator[np.ndarray]:
        clean = sanitize_text(text)
        if not clean.strip():
            return

        self._stopped = False
        self._speaking = True
        # piper's synthesize() is a blocking sync generator; running it in a
        # background thread and bridging through a queue (same pattern as
        # MicrophoneStream) lets each chunk reach the caller as soon as piper
        # produces it, instead of buffering the whole utterance before
        # returning anything — the previous version awaited one call that
        # collected every chunk into a list before yielding a single result,
        # which added full synthesis time to time-to-first-audio.
        chunk_queue: queue.Queue[np.ndarray | object] = queue.Queue()
        thread = threading.Thread(
            target=self._produce_chunks, args=(clean, chunk_queue), daemon=True
        )
        thread.start()
        try:
            while True:
                item = await asyncio.to_thread(chunk_queue.get)
                if item is _STREAM_DONE:
                    return
                yield item  # type: ignore[misc]
        finally:
            self._speaking = False
            thread.join(timeout=1.0)

    def _produce_chunks(self, text: str, chunk_queue: queue.Queue[np.ndarray | object]) -> None:
        try:
            for chunk in self._voice.synthesize(text):
                if self._stopped:
                    break
                pcm = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                chunk_queue.put((pcm.astype(np.float32) / 32768.0).clip(-1.0, 1.0))
        finally:
            chunk_queue.put(_STREAM_DONE)

    def stop(self) -> None:
        self._stopped = True

    def is_speaking(self) -> bool:
        return self._speaking
