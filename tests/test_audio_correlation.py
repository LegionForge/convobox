from __future__ import annotations

import numpy as np

from convobox.audio.correlation import correlation_at_lag, estimate_reference_lag

_SR = 16000


def _noise(seconds: float, seed: int = 7, sample_rate: int = _SR) -> np.ndarray:
    # Broadband, non-periodic (unlike a pure tone, whose autocorrelation
    # never really decays -- a sine shifted by an exact multiple of its own
    # period correlates ~1.0 again, which would make "wrong lag" tests
    # unreliable). Deterministic seed so every test run sees the same signal.
    rng = np.random.default_rng(seed)
    return rng.standard_normal(int(sample_rate * seconds))


def test_estimate_reference_lag_too_short_returns_none() -> None:
    # Both inputs must span at least one second at the given sample rate --
    # shorter than that and there's not enough signal to search a lag over.
    short = np.zeros(_SR // 2)
    long = np.zeros(_SR)
    assert estimate_reference_lag(short, long, _SR) == (None, None)
    assert estimate_reference_lag(long, short, _SR) == (None, None)


def test_estimate_reference_lag_finds_a_known_shift() -> None:
    reference = _noise(2.0)
    shift_ms = 120.0
    shift_samples = round(shift_ms * _SR / 1000)
    observed = np.concatenate([np.zeros(shift_samples), reference, np.zeros(_SR)])

    lag_ms, correlation = estimate_reference_lag(reference, observed, _SR, max_lag_ms=500)

    assert lag_ms is not None
    # 1ms search resolution (stride = sample_rate // 1000) -- allow one step.
    assert abs(lag_ms - shift_ms) <= 1.0
    assert correlation is not None
    assert correlation > 0.99


def test_estimate_reference_lag_on_pure_silence_returns_none() -> None:
    # Every lag's denominator (norm(left) * norm(right)) is ~0 for all-zero
    # signals -- best_correlation never gets set, so this must not raise or
    # report a spurious lag/correlation.
    silence = np.zeros(2 * _SR)
    assert estimate_reference_lag(silence, silence, _SR) == (None, None)


def test_estimate_reference_lag_zero_lag_when_aligned() -> None:
    reference = _noise(1.5)
    observed = np.concatenate([reference, np.zeros(_SR)])

    lag_ms, correlation = estimate_reference_lag(reference, observed, _SR)

    assert lag_ms is not None
    assert lag_ms <= 1.0  # essentially zero, within one search step
    assert correlation is not None
    assert correlation > 0.99


def test_correlation_at_lag_none_lag_returns_none() -> None:
    reference = _noise(1.0)
    assert correlation_at_lag(reference, reference, _SR, None) is None


def test_correlation_at_lag_empty_arrays_return_none() -> None:
    empty = np.zeros(0)
    reference = _noise(1.0)
    assert correlation_at_lag(empty, reference, _SR, 0.0) is None
    assert correlation_at_lag(reference, empty, _SR, 0.0) is None


def test_correlation_at_lag_too_short_overlap_returns_none() -> None:
    reference = _noise(1.0)
    observed = _noise(1.0, seed=8)
    # A lag beyond the signal length leaves under-100-sample overlap.
    assert correlation_at_lag(reference, observed, _SR, lag_ms=999_999) is None


def test_correlation_at_lag_matches_the_known_shift() -> None:
    reference = _noise(2.0)
    shift_ms = 75.0
    shift_samples = round(shift_ms * _SR / 1000)
    observed = np.concatenate([np.zeros(shift_samples), reference, np.zeros(_SR)])

    correlation = correlation_at_lag(reference, observed, _SR, lag_ms=shift_ms)

    assert correlation is not None
    assert correlation > 0.99


def test_correlation_at_lag_is_low_at_the_wrong_lag() -> None:
    # Same signal pair as the matching-shift test, but checked at a lag far
    # from the real one -- demonstrates the function actually discriminates
    # instead of always reporting a high correlation. Only reliable because
    # the reference is broadband noise, not a periodic tone (see _noise's
    # own docstring for why a tone would be a flaky choice here).
    reference = _noise(2.0)
    shift_samples = round(75.0 * _SR / 1000)
    observed = np.concatenate([np.zeros(shift_samples), reference, np.zeros(_SR)])

    wrong_lag_correlation = correlation_at_lag(reference, observed, _SR, lag_ms=400.0)

    assert wrong_lag_correlation is not None
    assert abs(wrong_lag_correlation) < 0.3


def test_correlation_at_lag_uncorrelated_signals_score_low() -> None:
    reference = _noise(2.0, seed=1)
    unrelated = _noise(2.0, seed=2)

    correlation = correlation_at_lag(reference, unrelated, _SR, lag_ms=0.0)

    assert correlation is not None
    assert abs(correlation) < 0.3
