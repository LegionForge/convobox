from __future__ import annotations

import queue
import threading
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

import numpy as np

from convobox.audio._sounddevice import import_sounddevice

if TYPE_CHECKING:
    import sounddevice as sd

# Wakes the streaming playback thread so a stop() can't sit unnoticed
# behind a blocking queue.get() while synthesis is slow to produce.
_QUEUE_POLL_S = 0.1


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample of a mono float32 block.

    Playback-grade, not audiophile: TTS speech at modest ratios
    (22050->44100/48000) tolerates linear interp fine, and it keeps
    playback dependency-free (no scipy). Returns the input untouched when
    the rates already match.
    """
    if src_rate == dst_rate or len(audio) == 0:
        return audio
    n_dst = int(round(len(audio) * dst_rate / src_rate))
    if n_dst <= 0:
        return np.zeros(0, dtype=np.float32)
    src_x = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    dst_x = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(dst_x, src_x, audio).astype(np.float32)


class _StreamResampler:
    """Phase-continuous linear resampler for chunked/streaming playback.

    Resampling each chunk in isolation (a fresh 0..1 interpolation per
    chunk, as a bare _resample() call does) injects a phase discontinuity at
    every chunk boundary. At an integer ratio (22050->44100 = 2.0x) the
    seams land on whole samples and are inaudible; at a non-integer ratio
    (22050->48000 = 2.177x, i.e. any 48 kHz WASAPI device) each seam lands
    mid-sample and clicks -- dozens of clicks a second, heard as garbled
    static over otherwise-correct-duration speech (confirmed live: MME clean,
    WASAPI garbled, from the identical code). This carries the fractional
    read position and the unconsumed source tail across chunks so the
    interpolation is continuous, matching a whole-buffer resample to ~0 RMS
    error. Mono float32 only, which is all TTS playback feeds it.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        # source samples advanced per output sample
        self._step = src_rate / dst_rate
        self._passthrough = src_rate == dst_rate
        # unconsumed source samples
        self._buf = np.zeros(0, dtype=np.float32)
        # next output sample's position within _buf (source-sample units)
        self._pos = 0.0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        if self._passthrough:
            return chunk
        self._buf = np.concatenate([self._buf, np.asarray(chunk, dtype=np.float32)])
        # Need at least two samples to interpolate between; hold the chunk
        # back until there's a right-hand neighbour for the last position.
        if len(self._buf) < 2:
            return np.zeros(0, dtype=np.float32)
        last = len(self._buf) - 1
        n = int(np.floor((last - self._pos) / self._step)) + 1
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        idx = self._pos + self._step * np.arange(n)
        out = np.interp(idx, np.arange(len(self._buf)), self._buf).astype(np.float32)
        # Advance: drop fully-consumed source samples, keep the fractional
        # remainder (and the anchor sample the next chunk interpolates from).
        next_pos = self._pos + self._step * n
        drop = min(int(np.floor(next_pos)), len(self._buf) - 1)
        self._buf = self._buf[drop:]
        self._pos = next_pos - drop
        return out


def _device_output_rate(sd: object, device: str | int | None, source_rate: int) -> int:
    """Pick a sample rate the device will actually accept.

    Devices dictate the rate; you conform the audio to them. WASAPI
    *rejects* a foreign rate outright (PaErrorCode -9997) and DirectSound
    "accepts" then mis-resamples it to silence -- both observed live on a
    Realtek endpoint fed Piper's 22050 Hz. Opening at the device's own
    default rate (and resampling our audio to match) is what makes
    ConvoBox work across arbitrary device/host-API combinations, and it
    unlocks WASAPI's low latency. Falls back to the source rate if the
    device can't be queried (e.g. a fake sounddevice in tests) -- which
    reproduces the pre-fix behavior for anything that was already fine.
    """
    try:
        info = sd.query_devices(device, "output")  # type: ignore[attr-defined]
        native = int(info["default_samplerate"])
    except Exception:
        return source_rate
    return native if native > 0 else source_rate


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
        # Called from the PLAYBACK THREAD with each block as it is written
        # to the device -- i.e. aligned with what the speakers actually
        # emit, not with what was queued. Exists for consumers that need a
        # realtime far-end reference (acoustic echo cancellation); must be
        # fast and must not raise.
        self.on_block_played: Callable[[np.ndarray, int], None] | None = None
        # Actual render latency reported by the host API once a stream is
        # open (None before first playback / when unreported). Consumers:
        # the AEC delay estimate.
        self.output_latency_s: float | None = None

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
        device_rate = _device_output_rate(sd, self.device, sample_rate)
        # Resample the whole buffer once (clean -- no per-block boundary
        # discontinuities) so the stream can open at a rate the device
        # actually accepts.
        samples = _resample(samples, sample_rate, device_rate)
        channels = 1 if samples.ndim == 1 else samples.shape[1]
        blocksize = 1024
        stream = sd.OutputStream(
            samplerate=device_rate,
            device=self.device,
            channels=channels,
            dtype="float32",
        )
        self._stream = stream
        stream.start()
        self.output_latency_s = getattr(stream, "latency", None)
        try:
            for start in range(0, len(samples), blocksize):
                if self._stop.is_set():
                    break
                block = samples[start : start + blocksize]
                stream.write(block)
                if self.on_block_played is not None:
                    # Reference is what actually hits the speaker: the
                    # resampled block at the device rate.
                    self.on_block_played(block, device_rate)
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
        device_rate = _device_output_rate(sd, self.device, sample_rate)
        # Phase-continuous across chunks: resampling each chunk in isolation
        # clicks at non-integer ratios (e.g. 22050->48000 on WASAPI), which
        # reads as garbled static. See _StreamResampler.
        resampler = _StreamResampler(sample_rate, device_rate)
        stream = None
        try:
            while not self._stop.is_set():
                try:
                    chunk = feed.get(timeout=_QUEUE_POLL_S)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                chunk = resampler.process(chunk)
                if len(chunk) == 0:
                    continue  # resampler is buffering; nothing to play yet
                if stream is None:
                    # Opened lazily on the first real chunk: channel count
                    # isn't known until then, and an empty stream (stopped
                    # before any audio) should never touch the device.
                    channels = 1 if chunk.ndim == 1 else chunk.shape[1]
                    stream = sd.OutputStream(
                        samplerate=device_rate,
                        device=self.device,
                        channels=channels,
                        dtype="float32",
                    )
                    self._stream = stream
                    stream.start()
                    self.output_latency_s = getattr(stream, "latency", None)
                blocksize = 1024
                for start in range(0, len(chunk), blocksize):
                    if self._stop.is_set():
                        return
                    block = chunk[start : start + blocksize]
                    stream.write(block)
                    if self.on_block_played is not None:
                        # Reference is what actually hits the speaker: the
                        # resampled block at the device rate.
                        self.on_block_played(block, device_rate)
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
