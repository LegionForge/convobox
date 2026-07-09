from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from convobox.config import VADConfig
from convobox.vad import segmenter as segmenter_module
from convobox.vad.segmenter import UtteranceSegmenter

_WINDOW = 512
# With the default VADConfig (min_silence_ms=500, min_speech_ms=250) at 16kHz:
#   min_silence_windows = round(16000*500/1000/512) = 16
#   min_speech_windows  = round(16000*250/1000/512) = 8
_MIN_SILENCE_WINDOWS = 16
_MIN_SPEECH_WINDOWS = 8


class _Prob:
    def __init__(self, value: float) -> None:
        self._value = value

    def item(self) -> float:
        return self._value


class FakeSileroModel:
    """Returns a scripted probability per __call__, one entry per 512 window."""

    def __init__(self, probs: list[float]) -> None:
        self._probs = probs
        self.calls = 0
        self.reset_count = 0

    def __call__(self, window: Any, sample_rate: int) -> _Prob:
        assert sample_rate == 16000
        assert window.shape[0] == _WINDOW
        value = self._probs[self.calls] if self.calls < len(self._probs) else 0.0
        self.calls += 1
        return _Prob(value)

    def reset_states(self) -> None:
        self.reset_count += 1


def _make_segmenter(
    monkeypatch: pytest.MonkeyPatch, probs: list[float]
) -> tuple[UtteranceSegmenter, FakeSileroModel]:
    model = FakeSileroModel(probs)
    monkeypatch.setattr(
        segmenter_module, "load_silero_vad", lambda **kwargs: model
    )
    return UtteranceSegmenter(VADConfig()), model


def _windows(n: int) -> np.ndarray:
    """Contiguous float32 audio spanning ``n`` full 512-sample windows."""
    return np.ones(n * _WINDOW, dtype=np.float32)


def test_single_speech_run_produces_one_utterance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    speech_windows = 10
    probs = [0.9] * speech_windows + [0.0] * _MIN_SILENCE_WINDOWS
    seg, _ = _make_segmenter(monkeypatch, probs)

    total_windows = speech_windows + _MIN_SILENCE_WINDOWS
    utterances = seg.feed(_windows(total_windows))

    assert len(utterances) == 1
    # Speech run plus the trailing silence windows that were appended while
    # counting toward min_silence are all part of the emitted utterance.
    assert utterances[0].shape[0] == total_windows * _WINDOW


def test_brief_silence_dip_does_not_split_utterance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # speech, a short silence dip (< min_silence), then speech again, then a
    # full silence gap to close the run.
    dip = _MIN_SILENCE_WINDOWS - 1
    probs = (
        [0.9] * 5
        + [0.0] * dip
        + [0.9] * 5
        + [0.0] * _MIN_SILENCE_WINDOWS
    )
    seg, _ = _make_segmenter(monkeypatch, probs)

    total_windows = 5 + dip + 5 + _MIN_SILENCE_WINDOWS
    utterances = seg.feed(_windows(total_windows))

    assert len(utterances) == 1


def test_short_speech_run_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    speech_windows = _MIN_SPEECH_WINDOWS - 1
    probs = [0.9] * speech_windows + [0.0] * _MIN_SILENCE_WINDOWS
    seg, _ = _make_segmenter(monkeypatch, probs)

    total_windows = speech_windows + _MIN_SILENCE_WINDOWS
    utterances = seg.feed(_windows(total_windows))

    assert utterances == []


def test_flush_emits_in_progress_run_below_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    speech_windows = _MIN_SPEECH_WINDOWS - 2
    probs = [0.9] * speech_windows
    seg, _ = _make_segmenter(monkeypatch, probs)

    assert seg.feed(_windows(speech_windows)) == []
    tail = seg.flush()

    assert tail is not None
    assert tail.shape[0] == speech_windows * _WINDOW


def test_flush_with_no_speech_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seg, _ = _make_segmenter(monkeypatch, [0.0] * 3)
    assert seg.feed(_windows(3)) == []
    assert seg.flush() is None


def test_unaligned_chunks_process_on_window_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Feed audio in 700-sample chunks (not a multiple of 512). Enough total
    # audio to cover one full utterance so we can verify no samples are dropped
    # or duplicated by checking the emitted length is an exact window multiple.
    speech_windows = 10
    total_windows = speech_windows + _MIN_SILENCE_WINDOWS
    probs = [0.9] * speech_windows + [0.0] * _MIN_SILENCE_WINDOWS
    seg, model = _make_segmenter(monkeypatch, probs)

    audio = _windows(total_windows)
    emitted: list[np.ndarray] = []
    for start in range(0, len(audio), 700):
        emitted.extend(seg.feed(audio[start : start + 700]))

    assert len(emitted) == 1
    assert emitted[0].shape[0] == total_windows * _WINDOW
    # Model was called exactly once per full window, no partial/duplicate feeds.
    assert model.calls == total_windows


def test_hysteresis_band_does_not_reset_silence_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # threshold=0.5, exit hysteresis is fixed at 0.15, so [0.35, 0.5) is the
    # ambiguous band: neither confidently speech nor confidently silence.
    # Regression test for a bug where a band frame reset the silence timer to
    # 0 (treated as speech), so silence interspersed with band frames could
    # never accumulate to min_silence_windows and the run would never end
    # except via flush(). Band frames must be inert instead: true silence
    # frames should keep accumulating toward min_silence regardless of band
    # frames appearing in between.
    interleaved: list[float] = []
    for _ in range(_MIN_SILENCE_WINDOWS):
        interleaved.append(0.0)  # true silence: counted
        interleaved.append(0.40)  # ambiguous band: must be inert, not a reset

    probs = [0.9] * _MIN_SPEECH_WINDOWS + interleaved
    seg, _ = _make_segmenter(monkeypatch, probs)

    utterances = seg.feed(_windows(len(probs)))

    assert len(utterances) == 1


def test_reset_states_called_on_run_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probs = [0.9] * 10 + [0.0] * _MIN_SILENCE_WINDOWS
    seg, model = _make_segmenter(monkeypatch, probs)
    seg.feed(_windows(10 + _MIN_SILENCE_WINDOWS))
    assert model.reset_count >= 1
