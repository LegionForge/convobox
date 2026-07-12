from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from convobox.audio.capture import MicrophoneStream
from convobox.audio.playback import AudioPlayer


class FakeInputStream:
    """Records construction kwargs and captures the capture callback."""

    instances: list["FakeInputStream"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callback = kwargs.get("callback")
        self.started = False
        self.stopped = False
        self.closed = False
        FakeInputStream.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def emit(self, samples: np.ndarray) -> None:
        """Simulate the audio driver delivering a block to the callback."""
        indata = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        assert self.callback is not None
        self.callback(indata, len(indata), None, None)


@pytest.fixture(autouse=True)
def patch_input_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeInputStream.instances = []
    # Substitutes the deferred-import seam rather than the real module's
    # attribute: the real sounddevice can't even be imported on hosts
    # without PortAudio (e.g. Linux CI runners).
    monkeypatch.setattr(
        "convobox.audio.capture.import_sounddevice",
        lambda: SimpleNamespace(InputStream=FakeInputStream),
    )


def test_start_constructs_input_stream_with_config() -> None:
    mic = MicrophoneStream(sample_rate=16000, blocksize=512, channels=1)
    mic.start()

    assert len(FakeInputStream.instances) == 1
    stream = FakeInputStream.instances[0]
    assert stream.kwargs["samplerate"] == 16000
    assert stream.kwargs["blocksize"] == 512
    assert stream.kwargs["channels"] == 1
    assert stream.kwargs["dtype"] == "float32"
    assert stream.started is True


def test_callback_chunks_are_read_in_order() -> None:
    mic = MicrophoneStream()
    mic.start()
    stream = FakeInputStream.instances[0]

    first = np.arange(4, dtype=np.float32)
    second = np.arange(4, 8, dtype=np.float32)
    stream.emit(first)
    stream.emit(second)

    np.testing.assert_array_equal(mic.read(timeout=1), first)
    np.testing.assert_array_equal(mic.read(timeout=1), second)


def test_callback_reshapes_to_1d() -> None:
    mic = MicrophoneStream()
    mic.start()
    stream = FakeInputStream.instances[0]

    stream.emit(np.arange(5, dtype=np.float32))
    chunk = mic.read(timeout=1)
    assert chunk.ndim == 1
    assert chunk.shape == (5,)


@pytest.mark.asyncio
async def test_stream_yields_chunks_in_order() -> None:
    mic = MicrophoneStream()
    mic.start()
    stream = FakeInputStream.instances[0]

    first = np.arange(3, dtype=np.float32)
    second = np.arange(3, 6, dtype=np.float32)
    stream.emit(first)
    stream.emit(second)

    gen = mic.stream()
    np.testing.assert_array_equal(await gen.__anext__(), first)
    np.testing.assert_array_equal(await gen.__anext__(), second)


def test_close_stops_and_closes_underlying_stream() -> None:
    mic = MicrophoneStream()
    mic.start()
    stream = FakeInputStream.instances[0]

    mic.close()
    assert stream.stopped is True
    assert stream.closed is True


def test_close_is_idempotent() -> None:
    mic = MicrophoneStream()
    mic.start()
    mic.close()
    mic.close()  # must not raise


def test_context_manager_starts_and_closes() -> None:
    with MicrophoneStream() as mic:
        assert isinstance(mic, MicrophoneStream)
        assert len(FakeInputStream.instances) == 1
        assert FakeInputStream.instances[0].started is True

    assert FakeInputStream.instances[0].closed is True


def test_context_manager_closes_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with MicrophoneStream():
            assert FakeInputStream.instances[0].started is True
            raise RuntimeError("boom")

    assert FakeInputStream.instances[0].closed is True


class FakeOutputStream:
    """OutputStream whose write() records blocks and can be gated for timing."""

    instances: list["FakeOutputStream"] = []
    # Read at construction time so a test can arm a delay BEFORE calling
    # play() and have it apply from the very first write. Setting
    # instance.per_write_delay only after play() has already started races
    # the writer thread: with no delay yet, it can blast through every
    # block before the assignment lands, making the test flake under load.
    default_per_write_delay = 0.0

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False
        self.writes: list[np.ndarray] = []
        self.per_write_delay = FakeOutputStream.default_per_write_delay
        FakeOutputStream.instances.append(self)

    def start(self) -> None:
        self.started = True

    def write(self, block: np.ndarray) -> None:
        self.writes.append(np.asarray(block).copy())
        if self.per_write_delay:
            time.sleep(self.per_write_delay)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def total_written(self) -> int:
        return sum(len(w) for w in self.writes)


@pytest.fixture(autouse=True)
def patch_output_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOutputStream.instances = []
    FakeOutputStream.default_per_write_delay = 0.0
    monkeypatch.setattr(
        "convobox.audio.playback.import_sounddevice",
        lambda: SimpleNamespace(OutputStream=FakeOutputStream),
    )


def test_play_writes_all_samples_without_blocking() -> None:
    player = AudioPlayer()
    samples = np.arange(3000, dtype=np.float32)
    player.play(samples, sample_rate=16000)
    player.wait()

    assert len(FakeOutputStream.instances) == 1
    stream = FakeOutputStream.instances[0]
    assert stream.total_written() == 3000
    assert stream.started is True
    assert stream.stopped is True
    assert stream.closed is True


def test_play_writes_in_blocks() -> None:
    player = AudioPlayer()
    player.play(np.zeros(2500, dtype=np.float32), sample_rate=16000)
    player.wait()

    stream = FakeOutputStream.instances[0]
    # 2500 samples at blocksize 1024 -> 1024, 1024, 452
    assert [len(w) for w in stream.writes] == [1024, 1024, 452]


def test_stop_halts_in_progress_playback_promptly() -> None:
    # Arm the delay before play() starts so it applies from the first write —
    # setting it after play() has already started races the writer thread.
    FakeOutputStream.default_per_write_delay = 0.01
    player = AudioPlayer()
    samples = np.zeros(1024 * 50, dtype=np.float32)
    player.play(samples, sample_rate=16000)
    time.sleep(0.02)  # let the write loop observably begin before stopping

    player.stop()
    assert player.is_playing() is False
    # Should have stopped well before writing all 50 blocks.
    stream = FakeOutputStream.instances[0]
    assert stream.total_written() < len(samples)


def test_is_playing_reflects_state() -> None:
    player = AudioPlayer()
    assert player.is_playing() is False

    started = threading.Event()
    release = threading.Event()

    original_write = FakeOutputStream.write

    def gated_write(self: FakeOutputStream, block: np.ndarray) -> None:
        original_write(self, block)
        started.set()
        release.wait(timeout=1)

    FakeOutputStream.write = gated_write  # type: ignore[method-assign]
    try:
        player.play(np.zeros(4096, dtype=np.float32), sample_rate=16000)
        assert started.wait(timeout=1)
        assert player.is_playing() is True
        release.set()
    finally:
        FakeOutputStream.write = original_write  # type: ignore[method-assign]
    player.wait()
    assert player.is_playing() is False


# --- streaming playback (play_stream) ---


async def _chunks_from(arrays: list[np.ndarray]):  # type: ignore[no-untyped-def]
    for array in arrays:
        yield array


def test_play_stream_writes_all_chunks() -> None:
    player = AudioPlayer()
    chunks = [np.arange(1500, dtype=np.float32), np.arange(700, dtype=np.float32)]

    asyncio.run(player.play_stream(_chunks_from(chunks), 16000))
    player.wait()

    stream = FakeOutputStream.instances[0]
    assert stream.total_written() == 2200
    assert stream.kwargs["samplerate"] == 16000
    assert stream.stopped and stream.closed


def test_play_stream_starts_audio_before_the_source_finishes() -> None:
    # The whole point of streaming: the first chunk must be playing while
    # later chunks are still being synthesized. The source parks on an
    # event after chunk 1; the test only releases it once chunk 1's audio
    # has demonstrably reached the output stream.
    player = AudioPlayer()
    release = asyncio.Event()

    async def slow_source():  # type: ignore[no-untyped-def]
        yield np.arange(2048, dtype=np.float32)
        await release.wait()
        yield np.arange(1024, dtype=np.float32)

    async def scenario() -> None:
        feed_task = asyncio.ensure_future(player.play_stream(slow_source(), 16000))
        for _ in range(200):  # up to 2s for the playback thread to spin up
            if FakeOutputStream.instances and FakeOutputStream.instances[0].writes:
                break
            await asyncio.sleep(0.01)
        assert FakeOutputStream.instances[0].writes, "no audio before source finished"
        assert not feed_task.done()  # source is still mid-response
        release.set()
        await asyncio.wait_for(feed_task, timeout=5)

    asyncio.run(scenario())
    player.wait()
    assert FakeOutputStream.instances[0].total_written() == 3072


def test_play_stream_stop_aborts_playback_and_pull() -> None:
    player = AudioPlayer()
    pulled = 0

    async def endless_source():  # type: ignore[no-untyped-def]
        nonlocal pulled
        while True:
            pulled += 1
            yield np.zeros(256, dtype=np.float32)
            await asyncio.sleep(0.01)

    async def scenario() -> None:
        feed_task = asyncio.ensure_future(player.play_stream(endless_source(), 16000))
        await asyncio.sleep(0.15)
        player.stop()
        # stop() must also end the FEEDING loop (stop pulling from
        # synthesis), not just silence the output.
        await asyncio.wait_for(feed_task, timeout=5)

    asyncio.run(scenario())
    assert player.is_playing() is False
    pulls_at_stop = pulled
    time.sleep(0.05)
    assert pulled == pulls_at_stop  # nothing kept pulling after stop


def test_play_stream_with_no_chunks_never_touches_the_device() -> None:
    player = AudioPlayer()

    async def empty_source():  # type: ignore[no-untyped-def]
        return
        yield  # pragma: no cover -- makes this an async generator

    asyncio.run(player.play_stream(empty_source(), 16000))
    player.wait()
    assert FakeOutputStream.instances == []


# --- output resampling (device-rate conformance; the WASAPI/DirectSound fix) ---

from convobox.audio.playback import _device_output_rate, _resample  # noqa: E402


def _resampling_sd(rate: float) -> SimpleNamespace:
    return SimpleNamespace(
        OutputStream=FakeOutputStream,
        query_devices=lambda device, kind: {"default_samplerate": rate},
    )


def test_resample_is_noop_when_rates_match() -> None:
    audio = np.arange(100, dtype=np.float32)
    assert _resample(audio, 16000, 16000) is audio


def test_resample_empty_input_stays_empty() -> None:
    assert len(_resample(np.zeros(0, dtype=np.float32), 22050, 44100)) == 0


def test_resample_doubles_length_on_2x_upsample() -> None:
    audio = np.ones(1000, dtype=np.float32)
    out = _resample(audio, 22050, 44100)
    assert len(out) == 2000
    assert out.dtype == np.float32


def test_resample_preserves_a_ramp_monotonically() -> None:
    ramp = np.linspace(0.0, 1.0, 500, dtype=np.float32)
    out = _resample(ramp, 22050, 48000)
    assert len(out) == round(500 * 48000 / 22050)
    assert out[0] == 0.0
    assert np.all(np.diff(out) >= -1e-6)  # still monotonically non-decreasing


def test_device_output_rate_uses_device_default() -> None:
    sd = _resampling_sd(48000.0)
    assert _device_output_rate(sd, "some-device", source_rate=22050) == 48000


def test_device_output_rate_falls_back_when_query_unavailable() -> None:
    sd = SimpleNamespace(OutputStream=FakeOutputStream)  # no query_devices
    assert _device_output_rate(sd, None, source_rate=22050) == 22050


def test_play_resamples_buffer_to_device_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Device wants 44100; we hand it 22050 -> must open at 44100 and write
    # ~2x the samples. This is the exact scenario that silenced DirectSound
    # and crashed WASAPI when we opened at the source rate.
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(44100.0))
    player = AudioPlayer(device="pinned")
    player.play(np.ones(2000, dtype=np.float32), sample_rate=22050)
    player.wait()
    stream = FakeOutputStream.instances[0]
    assert stream.kwargs["samplerate"] == 44100
    assert stream.total_written() == 4000  # 2000 @ 22050 -> 4000 @ 44100


def test_play_stream_resamples_chunks_to_device_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(48000.0))
    player = AudioPlayer(device="pinned")

    async def chunks():  # type: ignore[no-untyped-def]
        yield np.ones(2205, dtype=np.float32)  # 0.1s at 22050

    asyncio.run(player.play_stream(chunks(), sample_rate=22050))
    player.wait()
    stream = FakeOutputStream.instances[0]
    assert stream.kwargs["samplerate"] == 48000
    # 0.1s of audio at 48000 = 4800 samples (+/- rounding).
    assert abs(stream.total_written() - 4800) <= 2


def test_on_block_played_reference_uses_device_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    # The AEC far-end reference must be reported at the rate actually sent
    # to the device (post-resample), so the canceller models what the
    # speaker really emits.
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(44100.0))
    player = AudioPlayer(device="pinned")
    seen_rates: list[int] = []
    player.on_block_played = lambda block, rate: seen_rates.append(rate)
    player.play(np.ones(2000, dtype=np.float32), sample_rate=22050)
    player.wait()
    assert seen_rates and all(r == 44100 for r in seen_rates)
