from __future__ import annotations

import asyncio
import queue
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
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
        # Dedicated single-worker executor for stream()'s blocking
        # self._queue.get() below -- NOT asyncio.to_thread's default,
        # process-wide pool (GitHub issue #235, finding B3). During any
        # silence, stream() has a worker parked in a blocking get() with
        # no timeout for the whole silence duration; sharing the default
        # pool with everything else that uses asyncio.to_thread()
        # (Piper's own chunk pump, faster-whisper's thread-offloaded
        # transcribe() calls) means this one long-lived occupant reduces
        # real capacity for short ones under load -- the review's own
        # diagnosed mechanism for "fine under normal use, hangs under
        # rapid-fire stress". A dedicated executor with exactly the
        # capacity this loop actually needs (one call in flight at a
        # time) can't starve anything else, and vice versa.
        self._stream_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="convobox-mic-stream"
        )

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
        loop = asyncio.get_running_loop()
        while self._stream is not None:
            chunk = await loop.run_in_executor(self._stream_executor, self._queue.get)
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
        # wait=False (not blocking this synchronous close() call), but
        # deliberately NOT cancel_futures=True: with a single worker and
        # more than one concurrent stream() consumer (a real, tested case
        # -- see test_stream_close_wakes_all_concurrent_waiters_via_requeue),
        # a second consumer's queue.get() job can still be QUEUED, not yet
        # running, when close() is called. cancel_futures=True would cancel
        # that job outright instead of letting it run once the first
        # consumer's requeue (the "if chunk is _CLOSE_SENTINEL" branch
        # above) puts the sentinel back -- live-caught: broke that exact
        # test when first written this way.
        self._stream_executor.shutdown(wait=False)

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
