from __future__ import annotations

import time

import numpy as np
import pytest

from convobox.tts.base import MAX_TEXT_LENGTH, sanitize_text
from convobox.tts.piper import PiperTTSEngine


class _FakeConfig:
    sample_rate = 22050


class _FakeChunk:
    def __init__(self, samples: np.ndarray) -> None:
        self.audio_int16_bytes = samples.astype(np.int16).tobytes()


class _FakeVoice:
    """Mimics piper's PiperVoice: .config.sample_rate + a synthesize() generator."""

    def __init__(self, chunks_per_call: list[list[np.ndarray]]) -> None:
        self.config = _FakeConfig()
        self._chunks_per_call = chunks_per_call
        self.calls: list[str] = []
        self.syn_configs: list[object] = []
        self.per_chunk_delay = 0.0

    def synthesize(self, text: str, syn_config: object = None):
        self.calls.append(text)
        self.syn_configs.append(syn_config)
        chunks = self._chunks_per_call[len(self.calls) - 1]
        for samples in chunks:
            if self.per_chunk_delay:
                time.sleep(self.per_chunk_delay)
            yield _FakeChunk(samples)


def _make_engine(
    chunks_per_call: list[list[np.ndarray]], rate: float = 1.0, volume: float = 1.0
) -> tuple[PiperTTSEngine, _FakeVoice]:
    engine = PiperTTSEngine.__new__(PiperTTSEngine)
    voice = _FakeVoice(chunks_per_call)
    engine._model_path = "unused"
    engine._config_path = None
    engine._voice = voice
    engine._sample_rate = voice.config.sample_rate
    engine._speaking = False
    engine._stopped = False
    engine._syn_config = None
    if rate != 1.0 or volume != 1.0:
        from piper.config import SynthesisConfig

        engine._syn_config = SynthesisConfig(
            length_scale=None if rate == 1.0 else 1.0 / rate, volume=volume
        )
    return engine, voice


def test_sanitize_text_strips_control_chars_and_caps_length() -> None:
    assert sanitize_text("hello\x00\x07world") == "helloworld"
    assert sanitize_text("keep\ttab\nand\rnewline") == "keep\ttab\nand\rnewline"
    assert len(sanitize_text("x" * (MAX_TEXT_LENGTH + 500))) == MAX_TEXT_LENGTH


@pytest.mark.asyncio
async def test_synthesize_stream_yields_chunks_incrementally() -> None:
    chunk_a = np.array([100, 200, 300], dtype=np.int16)
    chunk_b = np.array([400, 500], dtype=np.int16)
    engine, voice = _make_engine([[chunk_a, chunk_b]])

    received = [chunk async for chunk in engine.synthesize_stream("hello there")]

    assert len(received) == 2
    assert received[0].dtype == np.float32
    assert received[0].shape == (3,)
    assert received[1].shape == (2,)
    assert voice.calls == ["hello there"]


@pytest.mark.asyncio
async def test_synthesize_concatenates_all_chunks() -> None:
    chunk_a = np.array([0, 16384], dtype=np.int16)
    chunk_b = np.array([-16384], dtype=np.int16)
    engine, _ = _make_engine([[chunk_a, chunk_b]])

    audio = await engine.synthesize("hello there")

    assert audio.dtype == np.float32
    assert audio.shape == (3,)
    assert audio == pytest.approx([0.0, 0.5, -0.5], abs=1e-3)


@pytest.mark.asyncio
async def test_synthesize_sanitizes_text_before_reaching_voice() -> None:
    engine, voice = _make_engine([[np.array([0], dtype=np.int16)]])

    await engine.synthesize("hi\x00\x07 there")

    assert voice.calls == ["hi there"]


@pytest.mark.asyncio
async def test_empty_text_after_sanitization_yields_no_audio() -> None:
    engine, voice = _make_engine([[]])

    audio = await engine.synthesize("\x00\x07\x1f")

    assert audio.shape == (0,)
    assert voice.calls == []  # never reaches the voice at all


@pytest.mark.asyncio
async def test_stop_interrupts_mid_stream() -> None:
    chunks = [np.array([i], dtype=np.int16) for i in range(20)]
    engine, voice = _make_engine([chunks])
    voice.per_chunk_delay = 0.01  # give the consumer a chance to call stop()

    received: list[np.ndarray] = []
    async for chunk in engine.synthesize_stream("a long utterance"):
        received.append(chunk)
        if len(received) == 3:
            engine.stop()

    # The producer thread checks _stopped between chunks, so it stops soon
    # after the 3rd chunk rather than exactly at it — but well short of all 20.
    assert 3 <= len(received) < 20


@pytest.mark.asyncio
async def test_default_rate_and_volume_pass_no_syn_config() -> None:
    # rate=1.0/volume=1.0 (the default) must reach piper as syn_config=None,
    # not an explicit SynthesisConfig(length_scale=1.0, volume=1.0) -- None
    # means "use this voice's own trained default," which is what today's
    # (pre-rate/volume) behavior already was and must stay byte-identical to.
    engine, voice = _make_engine([[np.array([1], dtype=np.int16)]])

    await engine.synthesize("hi")

    assert voice.syn_configs == [None]


@pytest.mark.asyncio
async def test_custom_rate_and_volume_build_a_syn_config() -> None:
    from piper.config import SynthesisConfig

    engine, voice = _make_engine([[np.array([1], dtype=np.int16)]], rate=2.0, volume=0.5)

    await engine.synthesize("hi")

    assert len(voice.syn_configs) == 1
    syn_config = voice.syn_configs[0]
    assert isinstance(syn_config, SynthesisConfig)
    assert syn_config.length_scale == pytest.approx(0.5)  # rate 2.0 -> half the duration
    assert syn_config.volume == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_is_speaking_reflects_state() -> None:
    chunks = [np.array([i], dtype=np.int16) for i in range(3)]
    engine, _ = _make_engine([chunks])

    assert engine.is_speaking() is False
    seen_speaking = False
    async for _ in engine.synthesize_stream("hi"):
        seen_speaking = seen_speaking or engine.is_speaking()

    assert seen_speaking is True
    assert engine.is_speaking() is False
