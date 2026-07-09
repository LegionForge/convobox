from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator

import numpy as np
import torch
from silero_vad import load_silero_vad

from convobox.config import VADConfig

# Silero's 16kHz ONNX model only accepts 512-sample windows (32ms). Incoming
# capture chunks may be any length, so they are buffered and consumed in
# exactly-512-sample windows before being fed to the model.
_WINDOW_SAMPLES = 512
_SAMPLE_RATE = 16000

# Silero's own streaming iterator releases the "speech" latch 0.15 below the
# entry threshold, giving a hysteresis band so a probability hovering near the
# threshold does not chatter between speech and silence mid-utterance.
_EXIT_HYSTERESIS = 0.15


class UtteranceSegmenter:
    """Turns a stream of 16kHz mono float32 audio chunks into utterances.

    A speech run only ends once silence has persisted for at least
    ``min_silence_ms``; brief pauses or disfluencies shorter than that stay
    inside a single utterance. Completed runs shorter than ``min_speech_ms``
    are discarded as noise rather than emitted.

    Emitted utterances include the trailing ``min_silence_ms`` of silence
    that triggered end-of-speech detection (deliberate: a little trailing
    silence helps STT models avoid clipping the last phoneme), so callers
    should expect each utterance to run ~``min_silence_ms`` longer than the
    actual speech.
    """

    def __init__(self, config: VADConfig | None = None) -> None:
        self._config = config or VADConfig()
        self._model = load_silero_vad(onnx=True)
        self._threshold = self._config.threshold
        self._min_silence_windows = _ms_to_windows(self._config.min_silence_ms)
        self._min_speech_windows = _ms_to_windows(self._config.min_speech_ms)

        self._carry = np.empty(0, dtype=np.float32)
        self._speech: list[np.ndarray] = []
        self._triggered = False
        self._speech_windows = 0
        self._trailing_silence_windows = 0

    def feed(self, chunk: np.ndarray) -> list[np.ndarray]:
        """Push one capture chunk; return any utterances it completes.

        ``chunk`` is a 1-D float32 array at 16kHz of any length. Returns a list
        because a single large chunk can span the end of one utterance and the
        start (and end) of another; it is usually empty or length one.
        """
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        buffer = np.concatenate((self._carry, chunk)) if self._carry.size else chunk

        completed: list[np.ndarray] = []
        offset = 0
        total = buffer.shape[0]
        while total - offset >= _WINDOW_SAMPLES:
            window = buffer[offset : offset + _WINDOW_SAMPLES]
            offset += _WINDOW_SAMPLES
            utterance = self._process_window(window)
            if utterance is not None:
                completed.append(utterance)

        self._carry = buffer[offset:].copy()
        return completed

    def flush(self) -> np.ndarray | None:
        """End any in-progress utterance and return it (e.g. on stream close).

        Ignores the ``min_speech_ms`` floor: if the stream ended mid-speech,
        the audio captured so far is real and worth emitting.
        """
        if not self._triggered or not self._speech:
            self._reset_run()
            return None
        utterance = np.concatenate(self._speech)
        self._reset_run()
        return utterance

    def _process_window(self, window: np.ndarray) -> np.ndarray | None:
        prob = float(self._model(torch.from_numpy(window), _SAMPLE_RATE).item())
        is_speech = prob >= self._threshold
        is_silence = prob < self._threshold - _EXIT_HYSTERESIS

        if not self._triggered:
            if is_speech:
                self._triggered = True
                self._speech.append(window)
                self._speech_windows = 1
                self._trailing_silence_windows = 0
            return None

        self._speech.append(window)
        if is_silence:
            self._trailing_silence_windows += 1
        elif is_speech:
            self._speech_windows += 1
            self._trailing_silence_windows = 0
        # else: probability sits in the hysteresis band itself (neither
        # confidently speech nor confidently silence) — leave both counters
        # untouched. Treating a band window as speech (the old behavior)
        # reset the silence timer on every ambiguous frame, so a speaker
        # trailing off gradually — or noise hovering near threshold — could
        # keep _trailing_silence_windows from ever reaching min_silence_ms,
        # and the run would only end via an external flush().

        if self._trailing_silence_windows >= self._min_silence_windows:
            return self._finish_run()
        return None

    def _finish_run(self) -> np.ndarray | None:
        emit = self._speech_windows >= self._min_speech_windows
        utterance = np.concatenate(self._speech) if emit else None
        self._reset_run()
        return utterance

    def _reset_run(self) -> None:
        self._model.reset_states()
        self._speech = []
        self._triggered = False
        self._speech_windows = 0
        self._trailing_silence_windows = 0

    async def segment(
        self, chunks: AsyncIterable[np.ndarray]
    ) -> AsyncIterator[np.ndarray]:
        """Consume an async chunk stream, yielding one array per utterance."""
        async for chunk in chunks:
            for utterance in self.feed(chunk):
                yield utterance
        tail = self.flush()
        if tail is not None:
            yield tail


def _ms_to_windows(ms: int) -> int:
    samples = _SAMPLE_RATE * ms / 1000
    return max(1, round(samples / _WINDOW_SAMPLES))
