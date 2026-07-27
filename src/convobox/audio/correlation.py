"""Normalized cross-correlation between a far-end reference signal and an
observed (mic) signal -- shared by the live acoustic-calibration harness
(scripts/acoustic_calibration.py) and post-hoc incident analysis
(scripts/analyze_incident.py), so both use the exact same, already-vetted
algorithm rather than two versions drifting apart.

Pure numpy, no audio-device or product dependencies, so it stays importable
without the full runtime stack.
"""

from __future__ import annotations

import numpy as np


def estimate_reference_lag(
    reference: np.ndarray,
    observed: np.ndarray,
    sample_rate: int,
    max_lag_ms: float = 500,
) -> tuple[float | None, float | None]:
    """Find the lag (0..max_lag_ms) that best aligns `reference` inside
    `observed`, by normalized dot-product correlation at each lag.

    `observed` must start at or before wherever `reference`'s content
    actually lands -- e.g. a continuous mic capture is a valid `observed`
    for a `reference` that only contains the samples fed while audio was
    actually playing (silence excised), since the search itself locates
    the offset. Widen `max_lag_ms` to cover the full `observed` duration
    when that offset isn't known in advance.
    """
    if reference.size < sample_rate or observed.size < sample_rate:
        return None, None
    # Speech-band correlation does not need the input sample rate. Decimating
    # to ~1kHz makes an exhaustive lag search cheap while keeping 1ms timing
    # resolution.
    stride = max(1, sample_rate // 1000)
    ref = np.asarray(reference[::stride], dtype=np.float64)
    obs = np.asarray(observed[::stride], dtype=np.float64)
    ref -= np.mean(ref)
    obs -= np.mean(obs)
    max_lag = min(round(max_lag_ms * sample_rate / 1000 / stride), len(obs) - 2)
    best_lag = 0
    best_correlation: float | None = None
    for lag in range(max(0, max_lag) + 1):
        length = min(len(ref), len(obs) - lag)
        if length < 100:
            break
        left = ref[:length]
        right = obs[lag : lag + length]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator <= 1e-12:
            continue
        correlation = float(np.dot(left, right) / denominator)
        if best_correlation is None or abs(correlation) > abs(best_correlation):
            best_lag = lag
            best_correlation = correlation
    if best_correlation is None:
        return None, None
    lag_ms = 1000.0 * best_lag * stride / sample_rate
    return lag_ms, best_correlation


def correlation_at_lag(
    reference: np.ndarray,
    observed: np.ndarray,
    sample_rate: int,
    lag_ms: float | None,
) -> float | None:
    """Correlation of `reference` against `observed` at a fixed, already-known
    lag -- e.g. re-checking `mic-processed` at the lag `estimate_reference_lag`
    found against `mic-raw`, to see how much AEC reduced it."""
    if lag_ms is None or reference.size == 0 or observed.size == 0:
        return None
    stride = max(1, sample_rate // 1000)
    lag = round(lag_ms * sample_rate / 1000 / stride)
    ref = np.asarray(reference[::stride], dtype=np.float64)
    obs = np.asarray(observed[::stride], dtype=np.float64)
    length = min(len(ref), len(obs) - lag)
    if length < 100:
        return None
    left = ref[:length] - np.mean(ref[:length])
    right = obs[lag : lag + length] - np.mean(obs[lag : lag + length])
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return None if denominator <= 1e-12 else float(np.dot(left, right) / denominator)
