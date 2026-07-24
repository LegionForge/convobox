from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch

import numpy as np
import pytest

from convobox.tts.kokoro import KokoroTTSEngine


class _FakeKokoro:
    """Mimics kokoro_onnx.Kokoro: a create_stream() async generator yielding
    (samples, sample_rate) tuples, same shape as the real package's own
    AsyncGenerator[tuple[NDArray[float32], int], None] signature (verified
    directly against the installed kokoro-onnx package, not guessed).
    """

    def __init__(
        self, chunks_per_call: list[list[tuple[np.ndarray, int]]]
    ) -> None:
        self._chunks_per_call = chunks_per_call
        self.calls: list[dict[str, object]] = []
        self.per_chunk_delay = 0.0

    def create_stream(
        self, text: str, voice: str, speed: float, lang: str
    ) -> AsyncIterator[tuple[np.ndarray, int]]:
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        chunks = self._chunks_per_call[len(self.calls) - 1]
        return self._stream(chunks)

    async def _stream(
        self, chunks: list[tuple[np.ndarray, int]]
    ) -> AsyncIterator[tuple[np.ndarray, int]]:
        for samples, sample_rate in chunks:
            if self.per_chunk_delay:
                await asyncio.sleep(self.per_chunk_delay)
            yield samples, sample_rate


def _make_engine(
    chunks_per_call: list[list[tuple[np.ndarray, int]]],
    voice: str = "af_sarah",
    speed: float = 1.0,
    lang: str = "en-us",
) -> tuple[KokoroTTSEngine, _FakeKokoro]:
    fake_kokoro = _FakeKokoro(chunks_per_call)
    with patch("kokoro_onnx.Kokoro", return_value=fake_kokoro):
        engine = KokoroTTSEngine(
            "model.onnx", "voices.bin", voice=voice, speed=speed, lang=lang
        )
    return engine, fake_kokoro


def test_init_constructs_kokoro_with_model_and_voices_path() -> None:
    fake_kokoro = _FakeKokoro([])
    with patch("kokoro_onnx.Kokoro", return_value=fake_kokoro) as mock_kokoro:
        engine = KokoroTTSEngine("model.onnx", "voices.bin")

    mock_kokoro.assert_called_once_with("model.onnx", "voices.bin")
    assert engine.is_speaking() is False


def test_sample_rate_defaults_before_any_synthesis() -> None:
    engine, _ = _make_engine([])
    # Kokoro-82M's known native rate -- see kokoro.py's _DEFAULT_SAMPLE_RATE
    # comment for why this must be a real value, not an arbitrary placeholder.
    assert engine.sample_rate == 24000


@pytest.mark.asyncio
async def test_synthesize_stream_yields_chunks_and_updates_sample_rate() -> None:
    chunk_a = np.array([0.1, 0.2], dtype=np.float32)
    chunk_b = np.array([0.3], dtype=np.float32)
    engine, kokoro = _make_engine([[(chunk_a, 22050), (chunk_b, 22050)]])

    received = [chunk async for chunk in engine.synthesize_stream("hello there")]

    assert len(received) == 2
    assert received[0].dtype == np.float32
    assert np.array_equal(received[0], chunk_a)
    assert np.array_equal(received[1], chunk_b)
    assert engine.sample_rate == 22050
    assert kokoro.calls == [
        {"text": "hello there", "voice": "af_sarah", "speed": 1.0, "lang": "en-us"}
    ]


@pytest.mark.asyncio
async def test_synthesize_passes_voice_speed_lang_through() -> None:
    chunk = np.array([0.1], dtype=np.float32)
    engine, kokoro = _make_engine(
        [[(chunk, 24000)]], voice="am_adam", speed=1.5, lang="en-gb"
    )

    await engine.synthesize("hi")

    assert kokoro.calls == [
        {"text": "hi", "voice": "am_adam", "speed": 1.5, "lang": "en-gb"}
    ]


@pytest.mark.asyncio
async def test_synthesize_concatenates_all_chunks() -> None:
    chunk_a = np.array([0.0, 0.5], dtype=np.float32)
    chunk_b = np.array([-0.5], dtype=np.float32)
    engine, _ = _make_engine([[(chunk_a, 24000), (chunk_b, 24000)]])

    audio = await engine.synthesize("hello there")

    assert audio.dtype == np.float32
    assert audio.shape == (3,)
    assert audio == pytest.approx([0.0, 0.5, -0.5])


@pytest.mark.asyncio
async def test_synthesize_sanitizes_text_before_reaching_kokoro() -> None:
    chunk = np.array([0.0], dtype=np.float32)
    engine, kokoro = _make_engine([[(chunk, 24000)]])

    await engine.synthesize("hi\x00\x07 there")

    assert kokoro.calls[0]["text"] == "hi there"


@pytest.mark.asyncio
async def test_empty_text_after_sanitization_yields_no_audio() -> None:
    engine, kokoro = _make_engine([[]])

    audio = await engine.synthesize("\x00\x07\x1f")

    assert audio.shape == (0,)
    assert kokoro.calls == []  # never reaches kokoro at all


@pytest.mark.asyncio
async def test_stop_interrupts_mid_stream() -> None:
    chunks = [(np.array([float(i)], dtype=np.float32), 24000) for i in range(20)]
    engine, kokoro = _make_engine([chunks])
    kokoro.per_chunk_delay = 0.01  # give the consumer a chance to call stop()

    received: list[np.ndarray] = []
    async for chunk in engine.synthesize_stream("a long utterance"):
        received.append(chunk)
        if len(received) == 3:
            engine.stop()

    # The producer checks _stopped between chunks, so it stops soon after
    # the 3rd chunk rather than exactly at it -- but well short of all 20.
    assert 3 <= len(received) < 20


@pytest.mark.asyncio
async def test_is_speaking_reflects_state() -> None:
    chunks = [(np.array([float(i)], dtype=np.float32), 24000) for i in range(3)]
    engine, _ = _make_engine([chunks])

    assert engine.is_speaking() is False
    seen_speaking = False
    async for _ in engine.synthesize_stream("hi"):
        seen_speaking = seen_speaking or engine.is_speaking()
    assert seen_speaking is True
    assert engine.is_speaking() is False


@pytest.mark.asyncio
async def test_is_speaking_false_after_empty_synthesis() -> None:
    engine, _ = _make_engine([[]])

    await engine.synthesize("\x00")

    assert engine.is_speaking() is False
