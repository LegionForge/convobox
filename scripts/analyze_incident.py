"""Post-hoc analysis of a saved AEC capture directory (scripts/run_convobox.py
--capture-incidents or --aec-dump): reference.wav, mic-raw.wav, mic-processed.wav.

reference.wav only contains samples from when TTS audio was actually playing
(AudioPlayer.feed_reverse / IncidentCapture.observe_reference) -- silence
between responses is excised, so it has no fixed sample-index relationship to
mic-raw.wav/mic-processed.wav, which capture continuously. This script uses
convobox.audio.correlation's normalized cross-correlation search (the same
algorithm scripts/acoustic_calibration.py uses live) with a widened lag
window to find where the reference content actually lands in the mic
timeline, then reports how much that correlation dropped after AEC --
an objective, independent cross-check of the log's dB-based attenuation
estimate and of by-ear review.

Usage:
    python scripts/analyze_incident.py .incident-captures/20260726-200623
    python scripts/analyze_incident.py .aec-dumps/20260726-200446 --plot
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from convobox.audio.correlation import correlation_at_lag, estimate_reference_lag  # noqa: E402


def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, rate


def _windowed_correlation(
    reference: np.ndarray,
    observed: np.ndarray,
    lag_samples: int,
    window: int,
    hop: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Correlation over sliding windows -- reveals *when* within the capture
    cancellation quality changes, not just one number for the whole file."""
    times = []
    values = []
    for start in range(0, len(reference) - window, hop):
        obs_start = lag_samples + start
        if obs_start < 0 or obs_start + window > len(observed):
            continue
        r = reference[start : start + window]
        o = observed[obs_start : obs_start + window]
        r = r - np.mean(r)
        o = o - np.mean(o)
        denominator = np.linalg.norm(r) * np.linalg.norm(o)
        if denominator < 1e-9:
            continue
        values.append(float(np.dot(r, o) / denominator))
        times.append(start / 16000.0)
    return np.asarray(times), np.asarray(values)


def analyze(directory: Path, max_lag_ms: float | None, window_s: float, plot: bool) -> int:
    try:
        reference, rate = _load_wav(directory / "reference.wav")
        mic_raw, _ = _load_wav(directory / "mic-raw.wav")
        mic_processed, _ = _load_wav(directory / "mic-processed.wav")
    except FileNotFoundError as exc:
        print(f"missing expected file: {exc}")
        return 1

    if max_lag_ms is None:
        # Cover the whole mic-raw timeline plus a little slack -- the
        # reference content could have landed anywhere in it.
        max_lag_ms = 1000.0 * max(0.0, (len(mic_raw) - len(reference)) / rate) + 1000.0

    lag_ms, raw_corr = estimate_reference_lag(reference, mic_raw, rate, max_lag_ms=max_lag_ms)
    if lag_ms is None:
        print("No reliable alignment found (reference/mic-raw too short or too quiet).")
        return 1
    processed_corr = correlation_at_lag(reference, mic_processed, rate, lag_ms)

    print(f"reference.wav:      {len(reference) / rate:.2f}s of actual playback audio")
    print(f"mic-raw.wav:        {len(mic_raw) / rate:.2f}s continuous capture")
    print(f"best-fit lag:       {lag_ms:.1f}ms (reference located at this offset in mic-raw)")
    print(f"raw correlation (pre-AEC):        {raw_corr:+.4f}")
    if processed_corr is None:
        print("processed correlation (post-AEC): n/a")
    else:
        print(f"processed correlation (post-AEC): {processed_corr:+.4f}")
        if abs(raw_corr) > 1e-9:
            reduction = 100.0 * (1.0 - abs(processed_corr) / abs(raw_corr))
            print(f"correlation reduction from AEC:    {reduction:.1f}%")

    lag_samples = round(lag_ms * rate / 1000)
    window = max(1, int(window_s * rate))
    hop = max(1, window // 2)
    t_raw, c_raw = _windowed_correlation(reference, mic_raw, lag_samples, window, hop)
    t_proc, c_proc = _windowed_correlation(reference, mic_processed, lag_samples, window, hop)

    if plot:
        _make_plot(directory, reference, mic_raw, mic_processed, rate, lag_samples, t_raw, c_raw, t_proc, c_proc)
    else:
        print(f"\nwindowed correlation ({window_s:.1f}s windows) -- pass --plot for a figure instead:")
        for t, r, p in zip(t_raw, c_raw, c_proc, strict=False):
            print(f"  t={t:6.2f}s  raw={r:+.3f}  processed={p:+.3f}")
    return 0


def _make_plot(
    directory: Path,
    reference: np.ndarray,
    mic_raw: np.ndarray,
    mic_processed: np.ndarray,
    rate: int,
    lag_samples: int,
    t_raw: np.ndarray,
    c_raw: np.ndarray,
    t_proc: np.ndarray,
    c_proc: np.ndarray,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(11, 8))

    t_ref = np.arange(len(reference)) / rate
    axes[0].plot(t_ref, reference, linewidth=0.5)
    axes[0].set_title("reference.wav -- actual playback audio only (silent gaps excised)")
    axes[0].set_ylabel("amplitude")

    t_mic = np.arange(len(mic_raw)) / rate
    axes[1].plot(t_mic, mic_raw, linewidth=0.5, alpha=0.6, label="mic-raw (pre-AEC)")
    t_proc_mic = np.arange(len(mic_processed)) / rate
    axes[1].plot(t_proc_mic, mic_processed, linewidth=0.5, alpha=0.8, label="mic-processed (post-AEC)")
    axes[1].axvspan(
        lag_samples / rate,
        (lag_samples + len(reference)) / rate,
        color="orange",
        alpha=0.15,
        label="reference aligned here",
    )
    axes[1].set_title("mic-raw vs. mic-processed -- continuous capture timeline")
    axes[1].set_ylabel("amplitude")
    axes[1].legend(loc="upper right", fontsize=8)

    axes[2].plot(t_raw, c_raw, marker="o", markersize=3, label="raw correlation (pre-AEC)")
    axes[2].plot(t_proc, c_proc, marker="o", markersize=3, label="processed correlation (post-AEC)")
    axes[2].axhline(0, color="gray", linewidth=0.5)
    axes[2].set_title("correlation to reference over time -- echo strength before/after AEC")
    axes[2].set_xlabel("seconds into reference.wav")
    axes[2].set_ylabel("normalized correlation")
    axes[2].legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    out = directory / "analysis.png"
    fig.savefig(out, dpi=150)
    print(f"\nplot written to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "directory",
        type=Path,
        help="capture directory containing reference.wav/mic-raw.wav/mic-processed.wav",
    )
    parser.add_argument(
        "--max-lag-ms",
        type=float,
        default=None,
        help="widest lag to search, ms (default: covers the whole mic-raw duration)",
    )
    parser.add_argument("--window-s", type=float, default=1.0, help="windowed-correlation window size, seconds")
    parser.add_argument("--plot", action="store_true", help="write analysis.png (needs the 'analysis' extra)")
    args = parser.parse_args()
    sys.exit(analyze(args.directory, args.max_lag_ms, args.window_s, args.plot))


if __name__ == "__main__":
    main()
