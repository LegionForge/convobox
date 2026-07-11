from __future__ import annotations

import queue
import threading
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import numpy as np

from convobox.audio._sounddevice import import_sounddevice

if TYPE_CHECKING:
    import sounddevice as sd

# Wakes the streaming playback thread so a stop() can't sit unnoticed
# behind a blocking queue.get() while synthesis is slow to produce.
_QUEUE_POLL_S = 0.1


class AudioPlayer:
    """Plays float32 audio through sounddevice with interruptible playback.

    stop() enables barge-in: a response can be cut off the moment the user
    starts speaking, rather than waiting for playback to finish.

    Two entry points: play() takes a complete buffer; play_stream() takes
    an async iterator of chunks and starts audio on the FIRST one, which is
    what makes TTS latency proportional to the first sentence instead of
    the whole response.
    """

    def __init__(self, device: str | int | None = None) -> None:
        self.device = device
        self._stream: sd.OutputStream | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Start playing samples. Non-blocking; replaces any current playback."""
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(samples, sample_rate), daemon=True
        )
        self._thread.start()

    def _run(self, samples: np.ndarray, sample_rate: int) -> None:
        sd = import_sounddevice()
        channels = 1 if samples.ndim == 1 else samples.shape[1]
        blocksize = 1024
        stream = sd.OutputStream(
            samplerate=sample_rate,
            device=self.device,
            channels=channels,
            dtype="float32",
        )
        self._stream = stream
        stream.start()
        try:
            for start in range(0, len(samples), blocksize):
                if self._stop.is_set():
                    break
                stream.write(samples[start : start + blocksize])
        finally:
            stream.stop()
            stream.close()
            self._stream = None

    async def play_stream(self, chunks: AsyncIterator[np.ndarray], sample_rate: int) -> None:
        """Play chunks as they arrive; audio starts on the first chunk.

        Returns when the source iterator is exhausted (or playback was
        stopped); the playback thread keeps draining already-queued audio
        after that -- use wait()/is_playing() for end-of-audio, exactly
        like play(). Replaces any current playback, also like play().
        """
        self.stop()
        self._stop.clear()
        feed: queue.Queue[np.ndarray | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run_stream, args=(feed, sample_rate), daemon=True
        )
        self._thread.start()
        try:
            async for chunk in chunks:
                if self._stop.is_set():
                    # Stopped mid-response (barge-in/hard stop): also stop
                    # PULLING from synthesis, don't just go quiet.
                    break
                feed.put(chunk)
        finally:
            feed.put(None)  # end-of-stream

    def _run_stream(self, feed: queue.Queue[np.ndarray | None], sample_rate: int) -> None:
        sd = import_sounddevice()
        stream = None
        try:
            while not self._stop.is_set():
                try:
                    chunk = feed.get(timeout=_QUEUE_POLL_S)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                if stream is None:
                    # Opened lazily on the first real chunk: channel count
                    # isn't known until then, and an empty stream (stopped
                    # before any audio) should never touch the device.
                    channels = 1 if chunk.ndim == 1 else chunk.shape[1]
                    stream = sd.OutputStream(
                        samplerate=sample_rate,
                        device=self.device,
                        channels=channels,
                        dtype="float32",
                    )
                    self._stream = stream
                    stream.start()
                blocksize = 1024
                for start in range(0, len(chunk), blocksize):
                    if self._stop.is_set():
                        return
                    stream.write(chunk[start : start + blocksize])
        finally:
            if stream is not None:
                stream.stop()
                stream.close()
            self._stream = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()
