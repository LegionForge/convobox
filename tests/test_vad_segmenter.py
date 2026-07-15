from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from convobox.config import VADConfig
from convobox.vad import segmenter as segmenter_module
from convobox.vad.segmenter import _PREFIX_PADDING_WINDOWS, UtteranceSegmenter

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


def _make_capped_segmenter(
    monkeypatch: pytest.MonkeyPatch, probs: list[float], max_utterance_s: float
) -> tuple[UtteranceSegmenter, FakeSileroModel]:
    model = FakeSileroModel(probs)
    monkeypatch.setattr(segmenter_module, "load_silero_vad", lambda **kwargs: model)
    return (
        UtteranceSegmenter(VADConfig(max_utterance_s=max_utterance_s)),
        model,
    )


def test_max_utterance_cap_splits_continuous_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1 second cap = round(16000/512) = 31 windows. 70 windows of continuous
    # speech (never a silence gap) must force-emit at the cap instead of
    # buffering until an external flush: two full capped utterances, with the
    # remainder still in progress.
    cap_windows = 31
    probs = [0.9] * 70
    seg, _ = _make_capped_segmenter(monkeypatch, probs, max_utterance_s=1.0)

    utterances = seg.feed(_windows(70))

    assert len(utterances) == 2
    assert all(u.shape[0] == cap_windows * _WINDOW for u in utterances)
    # was_forced reflects the LAST completed utterance in this batch (both
    # of which were cap-triggered here, so no ambiguity in this case).
    assert seg.was_forced is True
    # The remainder (70 - 62 = 8 windows) is a new in-progress run.
    assert seg.in_speech
    tail = seg.flush()
    assert tail is not None
    assert tail.shape[0] == (70 - 2 * cap_windows) * _WINDOW
    # flush() is neither a natural silence-end nor a cap -- always False.
    assert seg.was_forced is False


def test_natural_silence_end_wins_over_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Speech ends via min_silence well before the cap is reached: the cap
    # must not change the emitted utterance at all.
    speech_windows = 10
    probs = [0.9] * speech_windows + [0.0] * _MIN_SILENCE_WINDOWS
    seg, _ = _make_capped_segmenter(monkeypatch, probs, max_utterance_s=60.0)

    total_windows = speech_windows + _MIN_SILENCE_WINDOWS
    utterances = seg.feed(_windows(total_windows))

    assert len(utterances) == 1
    assert utterances[0].shape[0] == total_windows * _WINDOW
    assert seg.was_forced is False


def test_was_forced_false_before_any_utterance_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seg, _ = _make_capped_segmenter(monkeypatch, [0.9] * 5, max_utterance_s=1.0)
    assert seg.was_forced is False  # nothing has completed yet


def test_was_forced_true_only_for_the_capped_utterance_not_the_next_natural_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A capped utterance followed by a second, naturally-ended one in a
    # SEPARATE feed() call must correctly flip was_forced back to False --
    # it's not "sticky" from the earlier forced emit. The second run uses
    # exactly _MIN_SPEECH_WINDOWS of speech so it clears the noise floor
    # and actually emits (a shorter run would be silently discarded).
    cap_windows = 31
    probs = (
        [0.9] * cap_windows
        + [0.9] * _MIN_SPEECH_WINDOWS
        + [0.0] * _MIN_SILENCE_WINDOWS
    )
    seg, _ = _make_capped_segmenter(monkeypatch, probs, max_utterance_s=1.0)

    first_batch = seg.feed(_windows(cap_windows))
    assert len(first_batch) == 1
    assert seg.was_forced is True

    second_batch = seg.feed(_windows(_MIN_SPEECH_WINDOWS + _MIN_SILENCE_WINDOWS))
    assert len(second_batch) == 1
    assert seg.was_forced is False


def test_no_cap_preserves_unbounded_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default config (max_utterance_s=None): continuous speech buffers
    # indefinitely and only flush() ends it — the pre-cap contract.
    probs = [0.9] * 100
    seg, _ = _make_segmenter(monkeypatch, probs)

    assert seg.feed(_windows(100)) == []
    assert seg.in_speech
    tail = seg.flush()
    assert tail is not None
    assert tail.shape[0] == 100 * _WINDOW


def test_leading_silence_up_to_the_padding_cap_is_prepended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exactly _PREFIX_PADDING_WINDOWS of silence before the trigger: all of
    # it should be kept, none discarded.
    speech_windows = 10
    probs = (
        [0.0] * _PREFIX_PADDING_WINDOWS
        + [0.9] * speech_windows
        + [0.0] * _MIN_SILENCE_WINDOWS
    )
    seg, _ = _make_segmenter(monkeypatch, probs)

    utterances = seg.feed(_windows(len(probs)))

    assert len(utterances) == 1
    expected = (_PREFIX_PADDING_WINDOWS + speech_windows + _MIN_SILENCE_WINDOWS) * _WINDOW
    assert utterances[0].shape[0] == expected


def test_leading_silence_beyond_the_padding_cap_is_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # More silence precedes the trigger than the padding buffer holds: only
    # the LAST _PREFIX_PADDING_WINDOWS windows (closest to the trigger) are
    # kept, the older ones fall off the rolling buffer.
    extra_silence = 5
    speech_windows = 10
    probs = (
        [0.0] * (extra_silence + _PREFIX_PADDING_WINDOWS)
        + [0.9] * speech_windows
        + [0.0] * _MIN_SILENCE_WINDOWS
    )
    seg, _ = _make_segmenter(monkeypatch, probs)

    utterances = seg.feed(_windows(len(probs)))

    assert len(utterances) == 1
    # NOT (extra_silence + _PREFIX_PADDING_WINDOWS + ...) -- the extra
    # leading silence must not appear in the emitted utterance at all.
    expected = (_PREFIX_PADDING_WINDOWS + speech_windows + _MIN_SILENCE_WINDOWS) * _WINDOW
    assert utterances[0].shape[0] == expected


def test_no_leading_silence_prepends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Speech starts on the very first window fed (no pre-trigger context
    # available at all) -- the padding buffer is empty, so this must behave
    # exactly as before the padding feature existed.
    speech_windows = 10
    probs = [0.9] * speech_windows + [0.0] * _MIN_SILENCE_WINDOWS
    seg, _ = _make_segmenter(monkeypatch, probs)

    utterances = seg.feed(_windows(len(probs)))

    assert len(utterances) == 1
    assert utterances[0].shape[0] == (speech_windows + _MIN_SILENCE_WINDOWS) * _WINDOW


def _tagged_windows(n: int) -> np.ndarray:
    """n windows of 512 samples, every sample in window i equal to float(i) --
    lets a test recover exactly which ORIGINAL window indices survived into
    an emitted utterance (unlike `_windows()`'s all-ones fixture, where every
    window is indistinguishable, so a length match alone can't prove which
    windows -- old, stale ones or fresh ones -- actually ended up there)."""
    return np.concatenate([np.full(_WINDOW, float(i), dtype=np.float32) for i in range(n)])


def _window_indices(utterance: np.ndarray) -> list[int]:
    return [int(utterance[i * _WINDOW]) for i in range(utterance.shape[0] // _WINDOW)]


def test_forced_split_does_not_reuse_stale_prefix_padding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Leading silence pads the FIRST (capped) utterance correctly, but the
    # cap-triggered second utterance re-triggers on the very next window of
    # continuous speech -- nothing passes through the pre-trigger buffer in
    # between, so it must be empty, not reusing the first utterance's
    # already-consumed lead-up silence. NOTE: the cap (`max_run_windows`)
    # bounds TOTAL buffered windows including padding (matches the existing
    # "bounds memory... grows with everything buffered" rationale), so both
    # utterances still come out to exactly `cap_windows` long either way --
    # length alone can't distinguish stale-padding-reused from correct, only
    # WHICH original windows ended up inside can, hence `_tagged_windows`.
    cap_windows = 31
    leading_silence = _PREFIX_PADDING_WINDOWS + 3
    probs = [0.0] * leading_silence + [0.9] * 70
    seg, _ = _make_capped_segmenter(monkeypatch, probs, max_utterance_s=1.0)

    utterances = seg.feed(_tagged_windows(len(probs)))

    assert len(utterances) == 2
    assert utterances[0].shape[0] == cap_windows * _WINDOW
    assert utterances[1].shape[0] == cap_windows * _WINDOW

    first_indices = _window_indices(utterances[0])
    second_indices = _window_indices(utterances[1])

    # First utterance: the LAST _PREFIX_PADDING_WINDOWS silence windows
    # (closest to the trigger -- indices 3,4, not 0,1) prepended, then real
    # speech windows starting at `leading_silence`.
    assert first_indices[:_PREFIX_PADDING_WINDOWS] == list(
        range(leading_silence - _PREFIX_PADDING_WINDOWS, leading_silence)
    )
    assert first_indices[_PREFIX_PADDING_WINDOWS:] == list(
        range(leading_silence, leading_silence + (cap_windows - _PREFIX_PADDING_WINDOWS))
    )
    # Second utterance: purely contiguous real speech windows immediately
    # following the first utterance's -- none of the stale leading-silence
    # indices (0..leading_silence-1) reappear.
    first_real_speech_count = cap_windows - _PREFIX_PADDING_WINDOWS
    second_start = leading_silence + first_real_speech_count
    assert second_indices == list(range(second_start, second_start + cap_windows))


def test_in_speech_reflects_run_state(monkeypatch: pytest.MonkeyPatch) -> None:
    probs = [0.0] * 2 + [0.9] * 5 + [0.0] * _MIN_SILENCE_WINDOWS
    seg, _ = _make_segmenter(monkeypatch, probs)

    assert not seg.in_speech
    seg.feed(_windows(2))
    assert not seg.in_speech
    seg.feed(_windows(5))
    assert seg.in_speech
    seg.feed(_windows(_MIN_SILENCE_WINDOWS))
    assert not seg.in_speech
