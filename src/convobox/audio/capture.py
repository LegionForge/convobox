from __future__ import annotations

import asyncio
import logging
import queue
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from types import TracebackType
from typing import TYPE_CHECKING, Self

import numpy as np

from convobox.audio._sounddevice import import_sounddevice

if TYPE_CHECKING:
    import sounddevice as sd

logger = logging.getLogger(__name__)

# float32 in [-1, 1] because Silero VAD and faster-whisper both consume
# float32 numpy arrays directly; int16 would force a conversion on every chunk.
_DTYPE = "float32"

# Unblocks a consumer parked in queue.get() when close() is called — without
# this, stream()/read() can hang forever after close() since nothing else
# ever wakes a blocking get() on an empty queue.
_CLOSE_SENTINEL = object()

# stream()'s own stall warning, same two-stage shape as VAD segmenter.py's
# feed_async() (_SLOW_CALL_FIRST_WARNING_S/_SLOW_CALL_REPEAT_WARNING_S) --
# live-motivated by a 2026-08-12 UAT session that reproduced the
# still-open KNOWN-ISSUES.md mic-freeze bug three times AFTER feed_async's
# own instrumentation shipped (PR #269) and never once saw it fire. Under
# continuous capture, self._queue.get() below should return within about
# one blocksize's worth of audio (~32ms at the default 512/16000) whether
# or not anyone is speaking -- chunks arrive at a fixed hardware cadence
# regardless of silence vs. speech. So unlike a plain VAD/STT model call,
# THIS wait running long for real is not "waiting for the user to talk";
# it's either genuine executor contention (queued behind another job --
# unusual with this executor's single dedicated worker, see __init__'s own
# comment) or the underlying sounddevice callback has stopped delivering
# new chunks entirely, a capture-layer stall this project has never had
# direct evidence for before. See docs/KNOWN-ISSUES.md's VAD segmenter
# freeze entry and docs/field-notes/2026-08-12-vad-freeze-live-reproduced-
# three-times-pr269-did-not-fix-it.md for the live sessions that motivated
# this.
_STREAM_STALL_FIRST_WARNING_S = 0.5
_STREAM_STALL_REPEAT_WARNING_S = 5.0


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
        """Yield captured float32 chunks without blocking the event loop.

        Logs a stall warning (queued vs. running, same distinction
        feed_async() in vad/segmenter.py makes) if a single ``queue.get()``
        call takes unusually long -- see the module-level comment on
        `_STREAM_STALL_FIRST_WARNING_S` for why that's a meaningful signal
        here specifically, not just slow-as-usual.
        """
        loop = asyncio.get_running_loop()
        while self._stream is not None:
            execution_started: list[float] = []

            def _timed_get(started: list[float] = execution_started) -> np.ndarray | object:
                started.append(time.monotonic())
                return self._queue.get()

            task = loop.run_in_executor(self._stream_executor, _timed_get)
            start = time.monotonic()
            interval = _STREAM_STALL_FIRST_WARNING_S
            stalled = False
            while True:
                done, _pending = await asyncio.wait({task}, timeout=interval)
                if done:
                    break
                stalled = True
                now = time.monotonic()
                # qsize() is racy by nature (queue.Queue's own docs) but
                # exact precision doesn't matter here -- this is a coarse
                # "is a backlog piling up behind a stuck consumer, or is
                # the queue genuinely empty (capture itself stalled)"
                # signal, not a correctness-critical count. Directly tests
                # the live hypothesis from the 2026-08-12 UAT session
                # (docs/field-notes/2026-08-12-vad-freeze-live-reproduced-
                # three-times-pr269-did-not-fix-it.md): non-zero here means
                # `_callback` is still enqueueing chunks and something else
                # stopped consuming them; zero here (while genuinely
                # running long, not just queued) means the callback itself
                # has stopped delivering new chunks.
                qsize = self._queue.qsize()
                if execution_started:
                    queue_wait_s = execution_started[0] - start
                    running_s = now - execution_started[0]
                    logger.warning(
                        "MicrophoneStream.stream() queue.get() still running "
                        "after %.1fs total (queued %.1fs before the worker "
                        "picked it up, running %.1fs since -- under continuous "
                        "capture this should return within one blocksize, "
                        "~%.0fms) -- %d chunk(s) already backlogged in the "
                        "queue behind this call -- not abandoning it, just "
                        "reporting; see docs/KNOWN-ISSUES.md's VAD segmenter "
                        "freeze entry",
                        now - start, queue_wait_s, running_s,
                        1000 * self.blocksize / self.sample_rate, qsize,
                    )
                else:
                    logger.warning(
                        "MicrophoneStream.stream() queue.get() still QUEUED "
                        "after %.1fs -- the dedicated worker thread hasn't "
                        "started it yet (unexpected with a single-worker "
                        "executor unless another stream() consumer is also "
                        "active) -- %d chunk(s) backlogged -- not abandoning "
                        "it, just reporting; see docs/KNOWN-ISSUES.md's VAD "
                        "segmenter freeze entry",
                        now - start, qsize,
                    )
                interval = _STREAM_STALL_REPEAT_WARNING_S
            if stalled:
                logger.warning(
                    "MicrophoneStream.stream() queue.get() finally returned "
                    "after %.1fs total (%d chunk(s) still backlogged)",
                    time.monotonic() - start, self._queue.qsize(),
                )
            chunk = task.result()
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
