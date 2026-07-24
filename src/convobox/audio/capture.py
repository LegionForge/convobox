from __future__ import annotations

import asyncio
import queue
from collections.abc import AsyncIterator
from types import TracebackType
from typing import TYPE_CHECKING, Self

import numpy as np

from convobox.audio._sounddevice import import_sounddevice

if TYPE_CHECKING:
    import sounddevice as sd

# float32 in [-1, 1] because Silero VAD and faster-whisper both consume
# float32 numpy arrays directly; int16 would force a conversion on every chunk.
_DTYPE = "float32"

# Unblocks a consumer parked in queue.get() when close() is called — without
# this, stream()/read() can hang forever after close() since nothing else
# ever wakes a blocking get() on an empty queue.
_CLOSE_SENTINEL = object()


class MicrophoneStream:
    """Continuous microphone capture over a single sounddevice InputStream.

    Streaming (not record-then-stop) because segmentation into utterances is
    done downstream by VAD, not by a fixed-length capture window.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        blocksize: int = 512,
        device: str | int | None = None,
        channels: int = 1,
    ) -> None:
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.device = device
        self.channels = channels
        self._queue: queue.Queue[np.ndarray | object] = queue.Queue()
        self._stream: sd.InputStream | None = None

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        self._queue.put(indata.copy().reshape(-1))

    def start(self) -> None:
        sd = import_sounddevice()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            device=self.device,
            channels=self.channels,
            dtype=_DTYPE,
            callback=self._callback,
        )
        self._stream.start()
        # Actual capture latency reported by the host API, for consumers
        # that need real timing (the AEC delay estimate). None when the
        # backend doesn't report one.
        self.input_latency_s = getattr(self._stream, "latency", None)

    def read(self, timeout: float | None = None) -> np.ndarray:
        """Block until the next captured chunk is available and return it."""
        chunk = self._queue.get(timeout=timeout)
        if chunk is _CLOSE_SENTINEL:
            self._queue.put(_CLOSE_SENTINEL)  # let other waiters observe it too
            raise RuntimeError("microphone stream is closed")
        return chunk  # type: ignore[return-value]

    async def stream(self) -> AsyncIterator[np.ndarray]:
        """Yield captured float32 chunks without blocking the event loop."""
        while self._stream is not None:
            chunk = await asyncio.to_thread(self._queue.get)
            if chunk is _CLOSE_SENTINEL:
                self._queue.put(_CLOSE_SENTINEL)  # let other waiters observe it too
                return
            yield chunk  # type: ignore[misc]

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._queue.put(_CLOSE_SENTINEL)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
