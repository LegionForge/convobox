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

A capture directory can span more than one playback burst (e.g. two
interrupted responses inside one --capture-incidents window): reference.wav
is just their concatenation, with no stored boundary. A single global lag
can only align with the strongest one, diluting the numbers for the rest --
this script splits on internal silence and analyzes each burst separately.

Usage:
    python scripts/analyze_incident.py .incident-captures/20260726-200623
    python scripts/analyze_incident.py .aec-dumps/20260726-200446 --plot
"""

from __future__ import annotations

import argparse
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from convobox.audio.correlation import correlation_at_lag, estimate_reference_lag  # noqa: E402

_SEGMENT_WINDOW_S = 0.02  # 20ms short-term RMS window for silence detection


@dataclass
class SegmentResult:
    start: int  # sample index into `reference`
    end: int
    lag_ms: float | None
    raw_corr: float | None
    processed_corr: float | None
    energy_ratio: float | None  # aligned-window RMS / whole-file RMS baseline
    confident: bool


def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, rate


def _clipping_report(name: str, audio: np.ndarray) -> str:
    """Peak level plus a flat-top check: isolated samples near full-scale can
    just be a loud legitimate transient, but a *run* of consecutive samples
    pinned at the same near-max value is the actual signature of clipping
    (the waveform got chopped flat, not just loud)."""
    if audio.size == 0:
        return f"{name}: empty"
    peak = float(np.max(np.abs(audio)))
    near_max = np.abs(audio) >= 0.98
    longest_run = 0
    current_run = 0
    for flag in near_max:
        current_run = current_run + 1 if flag else 0
        longest_run = max(longest_run, current_run)
    clipped_fraction = 100.0 * float(np.mean(near_max))
    verdict = "likely clipping" if longest_run >= 3 else "no flat-topping detected"
    return (
        f"{name}: peak={peak:.3f} (full scale=1.0)  "
        f"samples>=0.98={clipped_fraction:.3f}%  longest flat-top run={longest_run} samples  [{verdict}]"
    )


def _split_segments(
    reference: np.ndarray,
    sample_rate: int,
    min_gap_s: float = 0.3,
    min_segment_s: float = 0.2,
) -> list[tuple[int, int]]:
    """Split into contiguous non-silent runs via a short-term RMS gate.

    reference.wav carries no explicit segment markers, so this recovers
    playback-burst boundaries well enough to analyze each one on its own
    instead of diluting a single global lag/correlation across all of them.
    """
    window = max(1, int(_SEGMENT_WINDOW_S * sample_rate))
    n_windows = len(reference) // window
    if n_windows == 0:
        return [(0, len(reference))]
    trimmed = reference[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(trimmed**2, axis=1))
    threshold = max(1e-4, float(np.max(rms)) * 0.05)
    active = rms > threshold
    min_gap_windows = max(1, round(min_gap_s / _SEGMENT_WINDOW_S))
    min_segment_windows = max(1, round(min_segment_s / _SEGMENT_WINDOW_S))

    segments: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for i, is_active in enumerate(active):
        if is_active:
            start = i if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap_windows:
                end = i - gap + 1
                if end - start >= min_segment_windows:
                    segments.append((start * window, end * window))
                start = None
                gap = 0
    if start is not None:
        end = n_windows - gap
        if end - start >= min_segment_windows:
            segments.append((start * window, end * window))
    return segments or [(0, len(reference))]


def _windowed_correlation(
    reference: np.ndarray,
    observed: np.ndarray,
    sample_rate: int,
    lag_samples: int,
    window: int,
    hop: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Correlation over sliding windows -- reveals *when* within a segment
    cancellation quality changes, not just one number for the whole thing."""
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
        times.append(start / sample_rate)
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

    print(f"reference.wav:      {len(reference) / rate:.2f}s of actual playback audio")
    print(f"mic-raw.wav:        {len(mic_raw) / rate:.2f}s continuous capture")
    print()
    print(_clipping_report("reference.wav", reference))
    print(_clipping_report("mic-raw.wav", mic_raw))
    print()

    baseline_rms = float(np.sqrt(np.mean(mic_raw**2))) if mic_raw.size else 0.0

    bounds = _split_segments(reference, rate)
    results = []
    for start, end in bounds:
        seg_ref = reference[start:end]
        lag_ms, raw_corr = estimate_reference_lag(seg_ref, mic_raw, rate, max_lag_ms=max_lag_ms)
        processed_corr = None if lag_ms is None else correlation_at_lag(seg_ref, mic_processed, rate, lag_ms)
        energy_ratio = None
        confident = False
        if lag_ms is not None and baseline_rms > 1e-9:
            lag_samples = round(lag_ms * rate / 1000)
            aligned_window = mic_raw[lag_samples : lag_samples + (end - start)]
            if aligned_window.size:
                energy_ratio = float(np.sqrt(np.mean(aligned_window**2))) / baseline_rms
                # An aligned window that isn't meaningfully louder than the
                # capture's own baseline is a strong sign the search locked
                # onto a spurious match rather than the real echo -- this is
                # a real failure mode of an unconstrained lag search when the
                # true match is already quiet (well-cancelled).
                confident = energy_ratio >= 1.3
        results.append(SegmentResult(start, end, lag_ms, raw_corr, processed_corr, energy_ratio, confident))

    if len(results) > 1:
        print(f"{len(results)} playback segments found in reference.wav (split on internal silence):")
    for i, r in enumerate(results):
        label = f"segment {i}" if len(results) > 1 else "reference.wav"
        seg_time = f"{r.start / rate:.2f}-{r.end / rate:.2f}s"
        if r.lag_ms is None:
            print(f"  {label} ({seg_time}): too short/quiet for a reliable alignment")
            continue
        # estimate_reference_lag() only ever returns (None, None) or
        # (float, float) together (convobox/audio/correlation.py) -- lag_ms
        # non-None here guarantees raw_corr is too.
        assert r.raw_corr is not None  # nosec B101
        line = f"  {label} ({seg_time}): lag={r.lag_ms:.1f}ms  raw={r.raw_corr:+.4f}"
        if r.processed_corr is not None:
            line += f"  processed={r.processed_corr:+.4f}"
            if abs(r.raw_corr) > 1e-9:
                reduction = 100.0 * (1.0 - abs(r.processed_corr) / abs(r.raw_corr))
                line += f"  reduction={reduction:.1f}%"
        if not r.confident:
            ratio = "n/a" if r.energy_ratio is None else f"{r.energy_ratio:.2f}x"
            line += f"  [LOW CONFIDENCE: aligned window only {ratio} baseline energy -- likely a spurious match, not the real echo]"
        print(line)

    window = max(1, int(window_s * rate))
    hop = max(1, window // 2)
    segment_traces = []
    for r in results:
        if r.lag_ms is None:
            continue
        lag_samples = round(r.lag_ms * rate / 1000)
        seg_ref = reference[r.start : r.end]
        t_raw, c_raw = _windowed_correlation(seg_ref, mic_raw, rate, lag_samples, window, hop)
        t_proc, c_proc = _windowed_correlation(seg_ref, mic_processed, rate, lag_samples, window, hop)
        segment_traces.append((r, t_raw + r.start / rate, c_raw, t_proc + r.start / rate, c_proc))

    if plot:
        _make_plot(directory, reference, mic_raw, mic_processed, rate, segment_traces)
    else:
        print(f"\nwindowed correlation ({window_s:.1f}s windows) -- pass --plot for a figure instead:")
        for _, t_raw, c_raw, _, c_proc in segment_traces:
            for t, raw_v, proc_v in zip(t_raw, c_raw, c_proc, strict=False):
                print(f"  t={t:6.2f}s  raw={raw_v:+.3f}  processed={proc_v:+.3f}")
    return 0


def _make_plot(
    directory: Path,
    reference: np.ndarray,
    mic_raw: np.ndarray,
    mic_processed: np.ndarray,
    rate: int,
    segment_traces: list[tuple[SegmentResult, np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(11, 8))
    # get_cmap()'s declared return type is the base Colormap class, but
    # "tab10" is always a real ListedColormap at runtime (a fixed, named
    # matplotlib qualitative palette) -- .colors only exists on that
    # subclass, not the base one mypy sees statically.
    colors = plt.get_cmap("tab10").colors  # type: ignore[attr-defined]

    t_ref = np.arange(len(reference)) / rate
    axes[0].plot(t_ref, reference, linewidth=0.5)
    axes[0].set_title("reference.wav -- actual playback audio only (silent gaps excised)")
    axes[0].set_ylabel("amplitude")

    t_mic = np.arange(len(mic_raw)) / rate
    axes[1].plot(t_mic, mic_raw, linewidth=0.5, alpha=0.6, label="mic-raw (pre-AEC)")
    t_proc_mic = np.arange(len(mic_processed)) / rate
    axes[1].plot(t_proc_mic, mic_processed, linewidth=0.5, alpha=0.8, label="mic-processed (post-AEC)")
    for i, (r, *_rest) in enumerate(segment_traces):
        lag_samples = round((r.lag_ms or 0.0) * rate / 1000)
        color = colors[i % len(colors)]
        suffix = "" if r.confident else " (LOW CONFIDENCE)"
        label = (f"segment {i}" if len(segment_traces) > 1 else "reference") + f" aligned here{suffix}"
        axes[1].axvspan(
            (lag_samples + r.start) / rate,
            (lag_samples + r.end) / rate,
            color=color,
            alpha=0.15 if r.confident else 0.06,
            hatch=None if r.confident else "//",
            label=label,
        )
    axes[1].set_title("mic-raw vs. mic-processed -- continuous capture timeline")
    axes[1].set_ylabel("amplitude")
    axes[1].legend(loc="upper right", fontsize=8)

    for i, (_r, t_raw, c_raw, t_proc, c_proc) in enumerate(segment_traces):
        color = colors[i % len(colors)]
        prefix = f"seg {i} " if len(segment_traces) > 1 else ""
        axes[2].plot(t_raw, c_raw, marker="o", markersize=3, color=color, label=f"{prefix}raw")
        axes[2].plot(t_proc, c_proc, marker="s", markersize=3, color=color, alpha=0.6, label=f"{prefix}processed")
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
