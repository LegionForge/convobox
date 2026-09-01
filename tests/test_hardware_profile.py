"""Unit tests for the pure-math parts of scripts/hardware_profile.py.

Covers only what doesn't need real audio hardware: tone/sweep generation,
THD measurement against synthetic signals, and the ESS deconvolution
round-trip. `run_thd_trial`/`run_ess_trial` (real sd.playrec calls) are
exercised live, not here -- see docs/field-notes/2026-08-29-*.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import hardware_profile as hp

SAMPLE_RATE = 48000


def _synthetic_tone_with_harmonics(freq: float, harmonic_ratios: list[float], duration_s: float = 1.0) -> np.ndarray:
    t = np.linspace(0, duration_s, int(duration_s * SAMPLE_RATE), endpoint=False)
    signal = np.sin(2 * np.pi * freq * t)
    for k, ratio in enumerate(harmonic_ratios, start=2):
        signal = signal + ratio * np.sin(2 * np.pi * freq * k * t)
    return signal.astype(np.float32)


def test_generate_tone_is_bounded_and_faded():
    tone = hp.generate_tone(1000.0, 0.5, SAMPLE_RATE, amplitude=0.8)
    assert np.max(np.abs(tone)) <= 0.8 + 1e-6
    assert abs(tone[0]) < 0.1
    assert abs(tone[-1]) < 0.1


def test_measure_thd_clean_tone_is_near_zero():
    tone = _synthetic_tone_with_harmonics(1000.0, harmonic_ratios=[])
    noise = (np.random.default_rng(0).normal(scale=1e-6, size=SAMPLE_RATE)).astype(np.float32)
    result = hp.measure_thd(tone, noise, SAMPLE_RATE, fundamental_freq=1000.0)
    assert result is not None
    assert result["snr_ok"] is True
    assert result["thd_percent"] < 1.0


def test_measure_thd_detects_known_harmonic_ratio():
    # A 10% 2nd harmonic should read back as roughly 10% THD.
    tone = _synthetic_tone_with_harmonics(1000.0, harmonic_ratios=[0.10])
    noise = (np.random.default_rng(1).normal(scale=1e-6, size=SAMPLE_RATE)).astype(np.float32)
    result = hp.measure_thd(tone, noise, SAMPLE_RATE, fundamental_freq=1000.0)
    assert result is not None
    assert 7.0 < result["thd_percent"] < 13.0


def test_measure_thd_flags_low_snr_as_unreliable():
    # A tone barely above a comparatively loud noise floor should be
    # gated as unreliable, not silently trusted -- this is the exact
    # regression the 2026-08-29 field note's "THD increases as volume
    # decreases" bug came from.
    rng = np.random.default_rng(2)
    tone = _synthetic_tone_with_harmonics(1000.0, harmonic_ratios=[]) * 0.001
    noise = rng.normal(scale=0.01, size=SAMPLE_RATE).astype(np.float32)
    result = hp.measure_thd(tone, noise, SAMPLE_RATE, fundamental_freq=1000.0, min_snr_db=20.0)
    assert result is not None
    assert result["snr_ok"] is False


def test_measure_thd_returns_none_for_silence():
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    result = hp.measure_thd(silence, silence, SAMPLE_RATE, fundamental_freq=1000.0)
    assert result is None


def test_measure_thd_returns_none_for_too_short_segment():
    tone = np.zeros(int(SAMPLE_RATE * 0.05), dtype=np.float32)
    noise = np.zeros(10, dtype=np.float32)
    assert hp.measure_thd(tone, noise, SAMPLE_RATE, fundamental_freq=1000.0) is None


def test_generate_ess_covers_the_full_requested_band():
    # Regression test for the missing-2*pi bug: the sweep's actual
    # instantaneous frequency must reach f2 near the end, not f2/(2*pi).
    f1, f2, duration = 100.0, 8000.0, 2.0
    sweep, _r = hp.generate_ess(f1, f2, duration, SAMPLE_RATE, amplitude=0.5)
    # Instantaneous frequency near the end of the sweep should be close to f2.
    tail = sweep[-int(0.05 * SAMPLE_RATE) :]
    freqs, mag = hp.spectrum_mag(tail, SAMPLE_RATE)
    peak_freq = float(freqs[np.argmax(mag)])
    assert peak_freq > f2 * 0.5, (
        f"sweep tail peak frequency {peak_freq}Hz is far below f2={f2}Hz -- "
        "looks like the missing-2*pi bug (sweep scaled down by ~6.28x)"
    )


def test_ess_deconvolution_recovers_impulse_at_zero_delay():
    f1, f2, duration = 200.0, 4000.0, 1.0
    sweep, r = hp.generate_ess(f1, f2, duration, SAMPLE_RATE, amplitude=0.5)
    inverse_filter = hp.build_inverse_filter(sweep, r, duration, SAMPLE_RATE)
    # A clean (no-echo, no-distortion) "recording" of the sweep played
    # straight back at itself should deconvolve to a single sharp impulse.
    ir = hp.deconvolve(sweep, inverse_filter)
    peak_idx = int(np.argmax(np.abs(ir)))
    peak_mag = float(np.abs(ir[peak_idx]))
    # Energy far from the peak should be much smaller than the peak itself.
    away = np.abs(ir[: max(0, peak_idx - 1000)])
    assert peak_mag > 10 * float(np.max(away)) if away.size else True


def test_harmonic_offset_samples_orders_correctly():
    r = np.log(8000.0 / 100.0)
    duration = 5.0
    offset_2 = hp.harmonic_offset_samples(2, r, duration, SAMPLE_RATE)
    offset_3 = hp.harmonic_offset_samples(3, r, duration, SAMPLE_RATE)
    # Higher harmonic orders arrive further ahead of the fundamental.
    assert 0 < offset_2 < offset_3


def test_schroeder_rt60_noise_floor_correction_prevents_gross_overestimate():
    # Regression test for the real 2026-08-29 bug: a short true decay
    # (RT60 ~0.3s) buried in a long noisy tail was, before noise-floor
    # correction, read back as RT60 ~3-5s -- because uncorrected backward
    # integration of a noise-dominated tail decreases almost linearly
    # (not exponentially) and only plunges sharply right at the very end
    # of the finite window, mimicking a long, slow decay.
    rng = np.random.default_rng(4)
    n = SAMPLE_RATE * 2  # 2s window, same order as the real capture's tail
    t = np.arange(n) / SAMPLE_RATE
    true_rt60 = 0.3
    decay_tau = true_rt60 / (3 * np.log(10))  # exp(-t/tau) crosses -60dB at t=RT60
    real_decay = rng.normal(size=n) * np.exp(-t / decay_tau)
    # scale=0.005 -> noise power ~46dB below peak power, enough dynamic
    # range for a fair T30 (-35dB) measurement; a noisier floor would
    # put -35dB AT the noise floor itself, which no correction can fix.
    noise_floor = rng.normal(scale=0.005, size=n)
    ir_segment = (real_decay + noise_floor).astype(np.float64)

    corrected = hp.schroeder_rt60(ir_segment, SAMPLE_RATE)
    assert corrected["rt60_from_t20"] is not None
    # Should land in the right order of magnitude (well under 1s), not
    # the ~3-5s the uncorrected version produced against real data.
    assert corrected["rt60_from_t20"] < 1.0
    assert corrected["rt60_from_t30"] < 1.0
    assert corrected["t20_reliable"] is True
    assert corrected["t30_reliable"] is True


def test_schroeder_rt60_flags_low_snr_as_unreliable():
    # Regression test for the real 2026-08-30 finding: a near-field,
    # safe-volume capture with only ~18dB of peak-to-noise-floor SNR
    # produced a plausible-LOOKING but wrong RT60 even after noise
    # correction. This must be flagged, not silently trusted.
    rng = np.random.default_rng(5)
    n = SAMPLE_RATE * 2
    t = np.arange(n) / SAMPLE_RATE
    # Loud, fast-decaying signal on top of a noise floor only ~18dB down
    # from the peak -- deliberately insufficient dynamic range.
    peak_amplitude = 8.0
    real_decay = rng.normal(size=n) * peak_amplitude * np.exp(-t / 0.02)
    noise_floor = rng.normal(scale=1.0, size=n)
    ir_segment = (real_decay + noise_floor).astype(np.float64)

    result = hp.schroeder_rt60(ir_segment, SAMPLE_RATE)
    assert result["snr_db"] < 30.0
    assert result["t20_reliable"] is False
    assert result["t30_reliable"] is False


def test_schroeder_rt60_shorter_decay_gives_shorter_rt60():
    rng = np.random.default_rng(3)
    n = SAMPLE_RATE * 2
    t = np.arange(n) / SAMPLE_RATE
    fast_decay = rng.normal(size=n) * np.exp(-t / 0.1)
    slow_decay = rng.normal(size=n) * np.exp(-t / 1.0)
    fast_rt60 = hp.schroeder_rt60(fast_decay, SAMPLE_RATE)
    slow_rt60 = hp.schroeder_rt60(slow_decay, SAMPLE_RATE)
    assert fast_rt60["rt60_from_t20"] is not None
    assert slow_rt60["rt60_from_t20"] is not None
    assert fast_rt60["rt60_from_t20"] < slow_rt60["rt60_from_t20"]


def test_resolve_device_raises_for_no_match(monkeypatch):
    monkeypatch.setattr(
        hp.sd, "query_devices",
        lambda: [{"name": "Some Other Device", "max_output_channels": 2, "max_input_channels": 0}],
    )
    with pytest.raises(RuntimeError, match="no matching output device"):
        hp.resolve_device("nonexistent", want_output=True)


def test_resolve_device_finds_matching_output(monkeypatch):
    monkeypatch.setattr(
        hp.sd, "query_devices",
        lambda: [
            {"name": "Built-in Mic", "max_output_channels": 0, "max_input_channels": 2},
            {"name": "Mac mini Speakers", "max_output_channels": 2, "max_input_channels": 0},
        ],
    )
    assert hp.resolve_device("mac mini", want_output=True) == 1


def test_set_macos_output_volume_raises_off_darwin(monkeypatch):
    monkeypatch.setattr(hp.platform, "system", lambda: "Linux")
    with pytest.raises(NotImplementedError):
        hp.set_macos_output_volume(50)
