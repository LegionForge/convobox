from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from convobox.config import STTConfig
from convobox.stt.transcriber import LocalTranscriber


@dataclass
class _FakeSegment:
    text: str
    avg_logprob: float


@dataclass
class _FakeInfo:
    language: str
    language_probability: float


class _FakeModel:
    """Stands in for faster_whisper.WhisperModel.

    `fail_times` lets a test make the first N calls raise RuntimeError
    (mirroring the real ctranslate2 native-allocator failure, which
    surfaces directly from the .transcribe() call itself -- confirmed
    from a real crash traceback, 2026-07-14 -- not from iterating the
    lazy segment generator) before succeeding.
    """

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def transcribe(self, audio: np.ndarray, language: str | None = None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("could not create a memory object")
        segments = [_FakeSegment(text="hello world", avg_logprob=-0.2)]
        info = _FakeInfo(language="en", language_probability=0.95)
        return segments, info


def _config() -> STTConfig:
    return STTConfig(model="tiny.en", device="cpu", compute_type="int8")


def test_normal_transcription_returns_expected_result() -> None:
    model = _FakeModel()
    transcriber = LocalTranscriber(_config(), model_factory=lambda: model)
    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == "hello world"
    assert result.language == "en"
    assert result.language_probability == 0.95
    assert result.avg_logprob == pytest.approx(-0.2)


def test_model_factory_called_once_at_construction() -> None:
    calls = []

    def factory():
        calls.append(1)
        return _FakeModel()

    LocalTranscriber(_config(), model_factory=factory)
    assert len(calls) == 1


def test_native_allocator_failure_is_recovered_not_raised() -> None:
    # The real crash (SYSTRAN/faster-whisper#660/#390, live-confirmed
    # 2026-07-14): ctranslate2's native allocator fails after many
    # transcribe() calls in a long-lived process. Must not propagate and
    # kill the whole voice loop.
    failing_model = _FakeModel(fail_times=1)
    transcriber = LocalTranscriber(_config(), model_factory=lambda: failing_model)
    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == ""
    assert result.segments == []
    assert result.language_probability == 0.0
    assert result.avg_logprob == pytest.approx(-10.0)
    assert result.duration_s == pytest.approx(1.0)  # 16000 samples @ 16kHz


def test_native_allocator_failure_reloads_the_model() -> None:
    # The reload must actually replace self._model (calling the factory
    # again), not just catch-and-ignore -- otherwise every subsequent
    # transcription would keep hitting the same broken allocator state.
    # Factory fails only the first model it ever builds, so construction
    # succeeds and the first transcribe() call is what triggers recovery.
    models_built: list[_FakeModel] = []
    built = {"count": 0}

    def flaky_factory():
        built["count"] += 1
        fail = 1 if built["count"] == 1 else 0
        m = _FakeModel(fail_times=fail)
        models_built.append(m)
        return m

    transcriber = LocalTranscriber(_config(), model_factory=flaky_factory)
    assert len(models_built) == 1  # constructed once, up front

    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == ""  # first model's only call failed
    assert len(models_built) == 2  # recovery built a replacement

    # The replacement model (fail_times=0) must now be the one in use.
    result2 = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result2.text == "hello world"
    assert len(models_built) == 2  # no further reload needed


def test_recovery_does_not_swallow_the_utterance_permanently() -> None:
    # One failed utterance must not corrupt the transcriber for future
    # calls -- exactly the "one lost utterance beats losing the whole
    # session" behavior this fix exists for.
    built = {"count": 0}

    def flaky_factory():
        built["count"] += 1
        fail = 1 if built["count"] == 1 else 0
        return _FakeModel(fail_times=fail)

    transcriber = LocalTranscriber(_config(), model_factory=flaky_factory)
    first = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    second = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert first.text == ""
    assert second.text == "hello world"
