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


# --- stream() uses a dedicated single-worker executor for its blocking
# queue.get(), not asyncio.to_thread()'s shared, process-wide default pool
# (GitHub issue #235, finding B3) -- during any silence, one worker sits
# blocked in that get() for the whole silence duration; sharing the
# default pool with everything else that offloads to it (faster-whisper's
# transcribe() calls, Piper's own chunk pump) means this one long-lived
# occupant reduces real capacity for short ones under load, the
# diagnosed mechanism for "fine under normal use, hangs under rapid-fire
# stress". ---


def test_stream_executor_is_dedicated_and_single_worker() -> None:
    mic = MicrophoneStream()
    assert mic._stream_executor._max_workers == 1
    mic.close()


@pytest.mark.asyncio
async def test_stream_actually_routes_through_its_dedicated_executor() -> None:
    mic = MicrophoneStream()
    mic.start()
    stream = FakeInputStream.instances[0]
    stream.emit(np.arange(3, dtype=np.float32))

    submitted: list[object] = []
    real_submit = mic._stream_executor.submit

    def spy_submit(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        submitted.append(fn)
        return real_submit(fn, *args, **kwargs)

    mic._stream_executor.submit = spy_submit  # type: ignore[method-assign]

    await mic.stream().__anext__()

    assert submitted, "stream() must submit its blocking get() to its own dedicated executor"
    mic.close()


@pytest.mark.asyncio
async def test_close_after_stream_does_not_hang_on_executor_shutdown() -> None:
    # Regression guard: close()'s executor.shutdown() must be wait=False
    # -- a blocking shutdown() would defeat the point of stream() not
    # holding up other work, and could hang indefinitely if a consumer's
    # get() is still outstanding (matching this class's existing
    # non-blocking close() semantics elsewhere).
    mic = MicrophoneStream()
    mic.start()
    task = asyncio.ensure_future(mic.stream().__anext__())
    await asyncio.sleep(0.05)  # let the worker actually reach queue.get()

    start = time.monotonic()
    mic.close()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    with pytest.raises(StopAsyncIteration):
        await task


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


def test_read_after_close_raises_and_lets_a_second_waiter_observe_it_too() -> None:
    # close() puts one sentinel on the queue, but read() re-puts it after
    # consuming it -- otherwise only the FIRST of several concurrent
    # readers (a real shape: e.g. the main capture loop and a diagnostic
    # tap both calling read()) would ever see the stream closed; every
    # later caller would just block forever on an empty queue.
    mic = MicrophoneStream()
    mic.start()
    mic.close()

    with pytest.raises(RuntimeError, match="closed"):
        mic.read(timeout=1)
    with pytest.raises(RuntimeError, match="closed"):
        mic.read(timeout=1)  # a second waiter must see it too, not block


@pytest.mark.asyncio
async def test_stream_close_wakes_all_concurrent_waiters_via_requeue() -> None:
    # Async counterpart of the read() re-queue behavior above. close() only
    # puts ONE sentinel, so if two consumers are already blocked inside
    # stream()'s queue.get() when it lands, only the first can dequeue it
    # directly -- the requeue at the "if chunk is _CLOSE_SENTINEL" branch
    # is what lets the second one see it too, instead of blocking forever.
    mic = MicrophoneStream()
    mic.start()
    task_a = asyncio.ensure_future(mic.stream().__anext__())
    task_b = asyncio.ensure_future(mic.stream().__anext__())
    await asyncio.sleep(0.05)  # let both reach queue.get() in their worker threads
    mic.close()

    with pytest.raises(StopAsyncIteration):
        await task_a
    with pytest.raises(StopAsyncIteration):
        await task_b


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


def test_play_accepts_a_synthesized_ack_tone() -> None:
    # P8 (docs/DESIGN-barge-in.md): generate_ack_tone()'s whole contract is
    # that its output is directly playable -- no WAV decode, no extra
    # conversion -- confirm that against the real AudioPlayer.play() path.
    from convobox.audio.ack_tones import SAMPLE_RATE_HZ, generate_ack_tone

    tone = generate_ack_tone("listening")
    player = AudioPlayer()
    player.play(tone, sample_rate=SAMPLE_RATE_HZ)
    player.wait()

    assert len(FakeOutputStream.instances) == 1
    stream = FakeOutputStream.instances[0]
    assert stream.total_written() == len(tone)
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


def test_on_first_block_played_fires_again_on_a_new_play_call() -> None:
    # first_block is a local reset at the top of _run() each call, so this
    # is structurally guaranteed rather than needing its own instance-state
    # reset (unlike the superseded has_played_audio flag this replaced) --
    # still worth pinning as a regression test for the actual behavior.
    player = AudioPlayer()
    calls = 0

    def on_first() -> None:
        nonlocal calls
        calls += 1

    player.on_first_block_played = on_first
    player.play(np.zeros(64, dtype=np.float32), sample_rate=16000)
    player.wait()
    assert calls == 1

    player.play(np.zeros(64, dtype=np.float32), sample_rate=16000)
    player.wait()
    assert calls == 2


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


def test_play_stream_on_first_block_played_has_not_fired_before_the_first_chunk_arrives() -> None:
    # The streaming path's version of [G8]'s gap is even wider than play()'s:
    # is_playing() goes True the instant play_stream() starts the playback
    # thread, before the source has yielded even ONE chunk -- the device
    # stream isn't opened at all yet (it's lazy, on the first real chunk).
    player = AudioPlayer()
    became_audible = False

    def on_first() -> None:
        nonlocal became_audible
        became_audible = True

    player.on_first_block_played = on_first
    release = asyncio.Event()

    async def slow_source():  # type: ignore[no-untyped-def]
        await release.wait()
        yield np.zeros(256, dtype=np.float32)

    async def scenario() -> None:
        feed_task = asyncio.ensure_future(player.play_stream(slow_source(), 16000))
        for _ in range(200):
            if player.is_playing():
                break
            await asyncio.sleep(0.01)
        assert player.is_playing() is True
        assert became_audible is False
        assert FakeOutputStream.instances == []  # device never even opened yet
        release.set()
        await asyncio.wait_for(feed_task, timeout=5)

    asyncio.run(scenario())
    player.wait()
    assert became_audible is True


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

from convobox.audio.playback import (  # noqa: E402
    _device_output_rate,
    _resample,
    _StreamResampler,
)


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


def test_stream_resampler_passthrough_when_rates_match() -> None:
    r = _StreamResampler(48000, 48000)
    chunk = np.arange(100, dtype=np.float32)
    assert r.process(chunk) is chunk


def test_stream_resampler_matches_whole_buffer_at_noninteger_ratio() -> None:
    # THE bug: streaming 22050->48000 in many small chunks must match a
    # single whole-buffer resample. Per-chunk resampling (the old code) fails
    # this with ~0.02 RMS error -- audible as garbled static on WASAPI.
    sig = (0.5 * np.sin(2 * np.pi * 200 * np.linspace(0, 1, 22050, endpoint=False))).astype(np.float32)
    truth = _resample(sig, 22050, 48000)
    r = _StreamResampler(22050, 48000)
    streamed = np.concatenate([r.process(sig[i : i + 1200]) for i in range(0, len(sig), 1200)])
    n = min(len(streamed), len(truth))
    rms = float(np.sqrt(np.mean((streamed[:n] - truth[:n]) ** 2)))
    assert rms < 1e-4                      # essentially exact
    assert abs(len(streamed) - len(truth)) <= 2


def test_stream_resampler_matches_whole_buffer_at_integer_ratio() -> None:
    sig = np.linspace(0.0, 1.0, 4410, dtype=np.float32)
    truth = _resample(sig, 22050, 44100)
    r = _StreamResampler(22050, 44100)
    streamed = np.concatenate([r.process(sig[i : i + 700]) for i in range(0, len(sig), 700)])
    n = min(len(streamed), len(truth))
    assert float(np.sqrt(np.mean((streamed[:n] - truth[:n]) ** 2))) < 1e-4


def test_stream_resampler_total_length_tracks_duration() -> None:
    # 0.5s @22050 fed in odd-sized chunks -> ~0.5s @48000
    r = _StreamResampler(22050, 48000)
    total = sum(len(r.process(np.ones(min(333, 11025 - i), dtype=np.float32)))
                for i in range(0, 11025, 333))
    assert abs(total - round(11025 * 48000 / 22050)) <= 3


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


def test_play_stream_on_block_played_reference_uses_device_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # play() has its own on_block_played call site (_run); play_stream has a
    # separate one (_run_stream) that was previously untested in isolation --
    # same AEC-reference contract applies to both.
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(44100.0))
    player = AudioPlayer(device="pinned")
    seen_rates: list[int] = []
    player.on_block_played = lambda block, rate: seen_rates.append(rate)

    async def chunks():  # type: ignore[no-untyped-def]
        yield np.ones(2000, dtype=np.float32)

    asyncio.run(player.play_stream(chunks(), sample_rate=22050))
    player.wait()
    assert seen_rates and all(r == 44100 for r in seen_rates)


def test_on_first_block_played_fires_once_for_play(monkeypatch: pytest.MonkeyPatch) -> None:
    # Multiple blocks get written (2000 samples / 1024 blocksize = 2 blocks)
    # but the audible-start signal must fire exactly once, on the first one.
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(22050.0))
    player = AudioPlayer(device="pinned")
    calls = 0

    def on_first() -> None:
        nonlocal calls
        calls += 1

    player.on_first_block_played = on_first
    player.play(np.ones(2000, dtype=np.float32), sample_rate=22050)
    player.wait()
    assert calls == 1


def test_on_first_block_played_fires_once_for_play_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(22050.0))
    player = AudioPlayer(device="pinned")
    calls = 0

    def on_first() -> None:
        nonlocal calls
        calls += 1

    player.on_first_block_played = on_first

    async def chunks():  # type: ignore[no-untyped-def]
        # Several chunks, each spanning multiple 1024-sample blocks --
        # on_first_block_played must still fire only once, on the very
        # first block of the very first chunk.
        yield np.ones(2000, dtype=np.float32)
        yield np.ones(2000, dtype=np.float32)

    asyncio.run(player.play_stream(chunks(), sample_rate=22050))
    player.wait()
    assert calls == 1


def test_play_stream_resampler_buffering_chunk_produces_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _StreamResampler needs >=2 buffered source samples before it can
    # interpolate anything; a first chunk too small to clear that bar makes
    # play_stream's per-chunk resample come back empty, which must be
    # skipped (no device write, no stream opened) rather than treated as
    # real silence -- distinct from test_play_stream_with_no_chunks_never_
    # touches_the_device, which covers zero chunks total, not "one chunk,
    # still buffering."
    monkeypatch.setattr("convobox.audio.playback.import_sounddevice", lambda: _resampling_sd(48000.0))
    player = AudioPlayer(device="pinned")

    async def chunks():  # type: ignore[no-untyped-def]
        yield np.ones(1, dtype=np.float32)  # too short to interpolate yet
        yield np.ones(2204, dtype=np.float32)  # completes the buffer

    asyncio.run(player.play_stream(chunks(), sample_rate=22050))
    player.wait()
    stream = FakeOutputStream.instances[0]
    # Only one device stream opened (on the second, non-empty chunk) and
    # 0.1s worth of audio at 48000 (+/- rounding) -- not two streams, and
    # not silence spliced in for the buffering chunk.
    assert len(FakeOutputStream.instances) == 1
    assert abs(stream.total_written() - 4800) <= 2


def test_play_stream_stop_aborts_mid_block_loop() -> None:
    # A single chunk longer than blocksize (1024) is written across several
    # inner-loop iterations; stop() setting _stop between those writes must
    # abort that inner loop too, not just the outer per-chunk queue.get()
    # loop (already covered by test_play_stream_stop_aborts_playback_and_pull).
    player = AudioPlayer()
    wrote_first_block = threading.Event()
    release = threading.Event()
    original_write = FakeOutputStream.write

    def gated_write(self: FakeOutputStream, block: np.ndarray) -> None:
        original_write(self, block)
        wrote_first_block.set()
        release.wait(timeout=1)

    FakeOutputStream.write = gated_write  # type: ignore[method-assign]

    async def chunks():  # type: ignore[no-untyped-def]
        yield np.zeros(3000, dtype=np.float32)  # 1024 + 1024 + 952 across 3 blocks

    async def scenario() -> None:
        feed_task = asyncio.ensure_future(player.play_stream(chunks(), 16000))
        for _ in range(200):  # up to 2s for the writer thread to reach the gate
            if wrote_first_block.is_set():
                break
            await asyncio.sleep(0.01)
        assert wrote_first_block.is_set()
        player._stop.set()  # arm stop while the thread is parked mid-block-loop
        release.set()
        await asyncio.wait_for(feed_task, timeout=5)

    try:
        asyncio.run(scenario())
    finally:
        FakeOutputStream.write = original_write  # type: ignore[method-assign]
    player.wait()

    stream = FakeOutputStream.instances[0]
    assert stream.total_written() == 1024  # aborted before the 2nd/3rd block
