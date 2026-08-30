"""Standalone speaker/mic hardware profiling: THD and ESS/Farina sweep.

Consolidates two ad hoc scratch scripts built during the 2026-08-29 AEC/
speaker acoustic-testing campaign (see docs/field-notes/2026-08-29-*.md)
into one committed tool, per JP's own suggestion in that campaign's field
notes. Not part of the ConvoBox runtime package -- a standalone hardware
diagnostic, using only numpy + sounddevice (both already project
dependencies). Does not import anything from convobox.* on purpose: this
measures a speaker/mic pair directly, independent of ConvoBox's own AEC/
VAD pipeline.

Two subcommands:

  thd    Pure-tone total harmonic distortion at one or more frequencies,
         with noise-floor SNR gating (see measure_thd's docstring for why
         gating is required -- an earlier ungated version produced
         backwards results, THD appearing to *increase* as volume
         *decreased*, purely from a weak tone sinking toward a roughly
         volume-independent room noise floor).

  sweep  Exponential Sine Sweep (Farina method): one measurement gives a
         continuous frequency response, per-harmonic distortion, and an
         RT60 estimate (Schroeder backward integration with noise-floor
         compensation -- see schroeder_rt60()'s docstring for the
         real bug this had and how it was found/fixed). See the
         2026-08-29 ESS field note for two earlier bugs (a missing 2*pi
         in the sweep phase, and an over-long frequency-response window)
         found and fixed while building this.

WAV/JSON evidence should be written under a gitignored path (e.g.
`uat-hardware-profile/`), consistent with acoustic_calibration.py.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import time
from pathlib import Path

import numpy as np

try:
    # sounddevice loads the native PortAudio library the instant it's
    # imported and raises OSError if that library is absent -- CI Linux
    # runners don't have it, so importing this module (e.g. for its pure-
    # math functions under test) must not require real audio hardware.
    # Same failure mode documented in src/convobox/audio/_sounddevice.py;
    # not reused here since this script deliberately imports nothing from
    # convobox.* (see module docstring).
    import sounddevice as sd
except OSError:

    class _NoPortAudio:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError(
                "sounddevice/PortAudio unavailable in this environment "
                "(no native PortAudio library found) -- hardware_profile.py "
                "needs real audio hardware for this operation"
            )

    sd = _NoPortAudio()  # type: ignore[assignment]


def resolve_device(name_substring: str, want_output: bool) -> int:
    for i, d in enumerate(sd.query_devices()):
        if name_substring.lower() in d["name"].lower():
            if want_output and d["max_output_channels"] > 0:
                return i
            if not want_output and d["max_input_channels"] > 0:
                return i
    kind = "output" if want_output else "input"
    raise RuntimeError(f"no matching {kind} device for {name_substring!r}")


def set_macos_output_volume(percent: int) -> None:
    """Set the SYSTEM output volume via `osascript` (macOS only).

    sounddevice/PortAudio has no cross-platform volume control, and
    acoustic_calibration.py's own --volume-candidates sweep is Windows
    (pycaw) / Linux (wpctl) only -- macOS was never wired up there. This
    is the same approach used as an external driver during the 2026-08-29
    campaign, folded in here since a volume sweep is a core part of both
    the thd and sweep subcommands' value.
    """
    if platform.system() != "Darwin":
        raise NotImplementedError(
            "--volumes controls the SYSTEM output volume via `osascript`, "
            "which is macOS-only. On other platforms, set the output level "
            "by hand between runs and invoke this script once per level."
        )
    percent = max(0, min(100, percent))
    subprocess.run(
        ["osascript", "-e", f"set volume output volume {percent}"],
        check=True,
    )


def _fade_edges(signal: np.ndarray, sample_rate: int, fade_s: float = 0.02) -> np.ndarray:
    fade_len = max(1, int(fade_s * sample_rate))
    fade = np.linspace(0, 1, fade_len)
    signal[:fade_len] *= fade
    signal[-fade_len:] *= fade[::-1]
    return signal


# --- THD -------------------------------------------------------------------


def generate_tone(freq: float, duration_s: float, sample_rate: int, amplitude: float) -> np.ndarray:
    t = np.linspace(0, duration_s, int(duration_s * sample_rate), endpoint=False)
    tone = amplitude * np.sin(2 * np.pi * freq * t)
    return _fade_edges(tone.astype(np.float32), sample_rate)


def spectrum_mag(segment: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(segment)
    window = np.hanning(n)
    spectrum = np.fft.rfft(segment * window)
    freqs = np.fft.rfftfreq(n, 1 / sample_rate)
    return freqs, np.abs(spectrum)


def measure_thd(
    tone_segment: np.ndarray,
    noise_segment: np.ndarray,
    sample_rate: int,
    fundamental_freq: float,
    n_harmonics: int = 5,
    min_snr_db: float = 20.0,
) -> dict | None:
    """THD of `tone_segment`, gated against `noise_segment`'s own noise
    floor at each harmonic's frequency bin.

    Gating is not optional: room noise is roughly volume-independent, so
    a quiet tone at low output level sinks toward it and produces a
    spuriously large THD ratio that looks like distortion but is really
    just low signal-to-noise. `noise_segment` must be captured from the
    same trial (immediately before the tone), not a stale/shared sample,
    so each measurement is gated by its own actual conditions.
    """
    if len(tone_segment) < sample_rate * 0.1 or len(noise_segment) < sample_rate * 0.05:
        return None
    freqs, mag = spectrum_mag(tone_segment, sample_rate)
    noise_freqs, noise_mag = spectrum_mag(noise_segment, sample_rate)

    def peak_near(m: np.ndarray, f: np.ndarray, target_freq: float, tol_hz: float = 15.0) -> float:
        mask = (f > target_freq - tol_hz) & (f < target_freq + tol_hz)
        if not mask.any():
            return 0.0
        return float(m[mask].max())

    fundamental_mag = peak_near(mag, freqs, fundamental_freq)
    if fundamental_mag <= 1e-9:
        return None
    harmonic_freqs = [fundamental_freq * k for k in range(2, n_harmonics + 2)]
    harmonic_mags = [peak_near(mag, freqs, hf) for hf in harmonic_freqs]
    noise_at_fundamental = peak_near(noise_mag, noise_freqs, fundamental_freq)
    noise_at_harmonics = [peak_near(noise_mag, noise_freqs, hf) for hf in harmonic_freqs]
    fundamental_snr_db = (
        float(20 * np.log10(fundamental_mag / noise_at_fundamental)) if noise_at_fundamental > 1e-9 else float("inf")
    )
    snr_ok = bool(fundamental_snr_db >= min_snr_db)
    # Subtract the noise floor in power from each harmonic bin before
    # computing THD -- a bin at or below its own noise floor contributes
    # ~0 rather than a spurious full-strength "harmonic."
    denoised_harmonics = [
        float(np.sqrt(max(0.0, h**2 - nf**2))) for h, nf in zip(harmonic_mags, noise_at_harmonics)
    ]
    thd_ratio = float(np.sqrt(sum(h**2 for h in denoised_harmonics))) / fundamental_mag
    return {
        "fundamental_mag": fundamental_mag,
        "harmonic_mags": harmonic_mags,
        "noise_at_fundamental": noise_at_fundamental,
        "noise_at_harmonics": noise_at_harmonics,
        "denoised_harmonic_mags": denoised_harmonics,
        "fundamental_snr_db": fundamental_snr_db,
        "snr_ok": snr_ok,
        "thd_ratio": thd_ratio,
        "thd_percent": thd_ratio * 100,
        "thd_db": (20 * np.log10(thd_ratio)) if thd_ratio > 0 else None,
        "peak_amplitude": float(np.max(np.abs(tone_segment))),
        "rms": float(np.sqrt(np.mean(tone_segment.astype(np.float64) ** 2))),
    }


def run_thd_trial(
    freq: float,
    tone_s: float,
    tail_s: float,
    lead_silence_s: float,
    sample_rate: int,
    output_device: int,
    input_device: int,
    amplitude: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Returns (noise_floor_segment, tone_segment) from one played+recorded trial."""
    lead_silence = np.zeros(int(lead_silence_s * sample_rate), dtype=np.float32)
    tone = generate_tone(freq, tone_s, sample_rate, amplitude)
    tail_silence = np.zeros(int(tail_s * sample_rate), dtype=np.float32)
    playback = np.concatenate([lead_silence, tone, tail_silence])
    recording = sd.playrec(playback, samplerate=sample_rate, channels=1, device=(input_device, output_device))
    sd.wait()
    recording = recording[:, 0]
    lead_frames = int(lead_silence_s * sample_rate)
    noise_segment = recording[int(lead_frames * 0.2) : int(lead_frames * 0.9)]
    tone_frames = int(tone_s * sample_rate)
    tone_segment = recording[lead_frames + int(tone_frames * 0.3) : lead_frames + int(tone_frames * 0.8)]
    return noise_segment, tone_segment


# --- ESS / Farina sweep ------------------------------------------------------


def generate_ess(f1: float, f2: float, duration: float, sample_rate: int, amplitude: float) -> tuple[np.ndarray, float]:
    n = int(duration * sample_rate)
    t = np.linspace(0, duration, n, endpoint=False)
    r = np.log(f2 / f1)
    # 2*pi is required: phase must be in radians for np.sin, and the
    # instantaneous-frequency derivation f(t) = f1 * exp(t*R/T) only holds
    # with it. Omitting it (a real bug found 2026-08-29) scales the WHOLE
    # sweep down by 2*pi (~6.28x) -- a nominal "100-8000Hz" sweep actually
    # only covers ~16-1270Hz, silently turning everything above that into
    # a noise-floor measurement rather than a real speaker response.
    k = 2 * np.pi * duration * f1 / r
    sweep = amplitude * np.sin(k * (np.exp(t * r / duration) - 1.0))
    return _fade_edges(sweep.astype(np.float32), sample_rate), r


def build_inverse_filter(sweep: np.ndarray, r: float, duration: float, sample_rate: int) -> np.ndarray:
    n = len(sweep)
    t = np.linspace(0, duration, n, endpoint=False)
    # -6dB/octave amplitude envelope compensates the sweep's own rising-
    # frequency energy -- the standard Farina matched inverse filter.
    envelope = np.exp(-t * r / duration)
    return (sweep[::-1] * envelope).astype(np.float32)


def deconvolve(recording: np.ndarray, inverse_filter: np.ndarray) -> np.ndarray:
    n = len(recording) + len(inverse_filter) - 1
    n_fft = 1
    while n_fft < n:
        n_fft *= 2
    spec = np.fft.rfft(recording, n_fft) * np.fft.rfft(inverse_filter, n_fft)
    return np.fft.irfft(spec, n_fft)


def harmonic_offset_samples(n_harmonic: int, r: float, duration: float, sample_rate: int) -> int:
    delta_t = duration * np.log(n_harmonic) / r
    return int(round(delta_t * sample_rate))


def schroeder_rt60(
    ir_segment: np.ndarray,
    sample_rate: int,
    noise_fraction: float = 0.1,
    min_snr_db_t20: float = 30.0,
    min_snr_db_t30: float = 40.0,
) -> dict:
    """Schroeder backward-integration RT60 estimate from a linear impulse
    response's decay tail, with noise-floor compensation (Chu 1978).

    RESOLVED 2026-08-29 (previously flagged unverified in this same
    session): the earlier, uncorrected version of this function
    overestimated RT60 by roughly 10x against an independently trusted
    room measurement (T20/T30-derived RT60 of 3.4-5.1s vs. an established
    ~0.2-0.46s). Root cause: plain (uncorrected) Schroeder backward
    integration of a tail that is mostly background noise (mic self-noise
    + room ambience, once the real acoustic decay has already died out)
    is a well-known trap -- backward-integrated energy of stationary
    noise over a FINITE window decreases roughly LINEARLY with time, not
    exponentially, so its dB curve stays almost flat for most of the
    window and only plunges sharply right at the very end (as the
    remaining-samples-to-sum shrinks toward zero). That produces exactly
    the signature this session's real capture showed: T20 (-5 to -25dB)
    took 1.70s, but the NEXT 10dB down to -35dB (T30) took only another
    0.023s -- a near-vertical late plunge, not a real decay. Any T20/T30
    window that lands even partly in that noise-dominated region reads
    back a grossly inflated decay time.

    Fix: estimate the background noise power from the LAST
    `noise_fraction` of the segment (assumed noise-dominated once real
    decay has finished), then subtract N*(T-t) -- the noise's own linear
    contribution to the backward integral -- from the RAW (uncorrected)
    cumulative sum, clipping the resulting CUMULATIVE curve (not each
    individual sample) to zero. This is the standard Chu (1978)
    correction. Clipping must happen after the cumulative sum, not
    before: energy is squared-Gaussian per sample, so per-sample
    clip-then-sum (an earlier, wrong version of this fix) discards
    legitimate negative fluctuations that should cancel positive ones in
    the sum, and systematically under-corrects -- verified by a unit
    test that still failed (RT60 ~4.8s on a synthetic 0.3s-decay-plus-
    noise signal) until the clip was moved to after the cumsum.

    STILL NOT ENOUGH on its own, verified live 2026-08-30: a real
    external-speaker ESS capture after this fix still read RT60 ~2-4s
    against a trusted ~0.2-0.46s room measurement. Root cause, isolated
    by inspecting the raw decay curve directly: this specific setup
    (safe/comfortable playback level, near-field small speakers, ordinary
    room noise) has only ~18dB of dynamic range between the fundamental
    peak and the true noise floor -- far short of the ~30-45dB ISO 3382
    generally wants for a trustworthy T20/T30 extraction. With that
    little headroom, the Chu correction's own noise estimate (from a
    short trailing window) is itself imprecise, and Schroeder's own
    total-energy normalization gets dominated by accumulated background
    noise summed across a multi-second tail rather than by the real
    (likely sub-100ms) direct decay -- a known limitation of the plain
    Chu method without full ISO 3382-2 Lundeby-style adaptive noise-floor
    detection. Rather than silently returning a confident-looking wrong
    number, this function now computes the achieved peak-to-noise-floor
    SNR and flags `t20_reliable`/`t30_reliable` accordingly -- same
    "flag reliability, don't just report" discipline as this file's
    `measure_thd`.
    """
    energy = ir_segment.astype(np.float64) ** 2
    n = len(energy)
    noise_win = max(1, int(n * noise_fraction))
    noise_power = float(np.mean(energy[-noise_win:]))
    schroeder_raw = np.cumsum((energy - noise_power)[::-1])[::-1]
    schroeder = np.maximum(schroeder_raw, 0.0)
    schroeder = schroeder / (schroeder[0] + 1e-20)
    with np.errstate(divide="ignore"):
        db = 10 * np.log10(schroeder + 1e-20)
    t = np.arange(len(db)) / sample_rate

    def find_decay(from_db: float, to_db: float) -> float | None:
        try:
            i_from = np.where(db <= from_db)[0][0]
            i_to = np.where(db <= to_db)[0][0]
        except IndexError:
            return None
        if i_to <= i_from:
            return None
        return float(t[i_to] - t[i_from])

    t20 = find_decay(-5, -25)
    t30 = find_decay(-5, -35)
    peak_power = float(np.max(energy[: min(n, 100)]))
    snr_db = float(10 * np.log10(peak_power / noise_power)) if noise_power > 1e-20 else float("inf")
    return {
        "t20_s": t20,
        "t30_s": t30,
        "rt60_from_t20": t20 * 3 if t20 else None,
        "rt60_from_t30": t30 * 2 if t30 else None,
        "noise_power": noise_power,
        "snr_db": snr_db,
        "t20_reliable": snr_db >= min_snr_db_t20,
        "t30_reliable": snr_db >= min_snr_db_t30,
    }


def run_ess_trial(
    f1: float,
    f2: float,
    duration: float,
    lead_silence_s: float,
    tail_silence_s: float,
    sample_rate: int,
    amplitude: float,
    n_harmonics: int,
    output_device: int,
    input_device: int,
) -> dict:
    sweep, r = generate_ess(f1, f2, duration, sample_rate, amplitude)
    inverse_filter = build_inverse_filter(sweep, r, duration, sample_rate)

    lead = np.zeros(int(lead_silence_s * sample_rate), dtype=np.float32)
    tail = np.zeros(int(tail_silence_s * sample_rate), dtype=np.float32)
    playback = np.concatenate([lead, sweep, tail])

    recording = sd.playrec(playback, samplerate=sample_rate, channels=1, device=(input_device, output_device))
    sd.wait()
    recording = recording[:, 0]
    aligned = recording[len(lead) :]

    ir = deconvolve(aligned, inverse_filter)
    peak_idx = int(np.argmax(np.abs(ir)))

    # Frequency response: FFT of a SHORT window around the linear IR peak
    # (direct sound + at most one early reflection), NOT the reverberant
    # tail. A too-long window here (an earlier bug: 30% of sweep duration,
    # ~1.5s at a 5s sweep) captures mostly room decay, and since
    # reverberant energy decays faster at high frequencies in most rooms,
    # produces a spurious low-pass-shaped "frequency response."
    win_pre = int(0.005 * sample_rate)
    win_post = min(int(0.02 * sample_rate), len(ir) - peak_idx - 1)
    fr_segment = ir[max(0, peak_idx - win_pre) : peak_idx + win_post]
    fr_spectrum = np.abs(np.fft.rfft(fr_segment * np.hanning(len(fr_segment))))
    fr_freqs = np.fft.rfftfreq(len(fr_segment), 1 / sample_rate)

    def band_energy_db(f_lo: float, f_hi: float) -> float | None:
        mask = (fr_freqs >= f_lo) & (fr_freqs <= f_hi)
        if not mask.any():
            return None
        return float(20 * np.log10(np.mean(fr_spectrum[mask]) + 1e-12))

    bands = [(100, 300), (300, 700), (700, 1500), (1500, 3000), (3000, 5000), (5000, 8000)]
    freq_response_db = {f"{lo}-{hi}Hz": band_energy_db(lo, hi) for lo, hi in bands}

    rt60_window = ir[peak_idx : peak_idx + int(min(tail_silence_s * 0.9, 1.8) * sample_rate)]
    rt60 = schroeder_rt60(rt60_window, sample_rate)

    harmonics = {}
    fundamental_peak_mag = float(np.max(np.abs(ir[max(0, peak_idx - 50) : peak_idx + 50])))
    for n_harm in range(2, n_harmonics + 2):
        offset = harmonic_offset_samples(n_harm, r, duration, sample_rate)
        h_idx = peak_idx - offset
        if h_idx < 50:
            harmonics[str(n_harm)] = None
            continue
        h_peak_mag = float(np.max(np.abs(ir[h_idx - 50 : h_idx + 50])))
        harmonics[str(n_harm)] = {
            "sample_offset": offset,
            "peak_mag": h_peak_mag,
            "ratio_to_fundamental_percent": (h_peak_mag / fundamental_peak_mag * 100) if fundamental_peak_mag > 0 else None,
        }

    return {
        "peak_sample": peak_idx,
        "fundamental_peak_mag": fundamental_peak_mag,
        "freq_response_db": freq_response_db,
        "rt60": rt60,
        "harmonics": harmonics,
    }


# --- CLI ---------------------------------------------------------------------


def _write_json(path: str, data: dict) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"wrote {out_path}")


def _resolve_volumes(raw: str | None) -> list[int | None]:
    if not raw:
        return [None]
    return [int(v.strip()) for v in raw.split(",")]


def cmd_thd(args: argparse.Namespace) -> None:
    output_device = resolve_device(args.output_name, want_output=True)
    input_device = resolve_device(args.input_name, want_output=False)
    print(f"output_device={sd.query_devices(output_device)['name']!r} (#{output_device})")
    print(f"input_device={sd.query_devices(input_device)['name']!r} (#{input_device})")

    frequencies = [float(f.strip()) for f in args.frequencies.split(",")]
    volumes = _resolve_volumes(args.volumes)
    results = []
    for volume in volumes:
        if volume is not None:
            set_macos_output_volume(volume)
            time.sleep(0.3)
        for freq in frequencies:
            for rep in range(1, args.repeat_each + 1):
                noise_segment, tone_segment = run_thd_trial(
                    freq, args.tone_seconds, args.tail_seconds, args.lead_silence_seconds,
                    args.sample_rate, output_device, input_device, args.amplitude,
                )
                thd = measure_thd(tone_segment, noise_segment, args.sample_rate, freq, min_snr_db=args.min_snr_db)
                if thd is None:
                    print(f"  vol={volume} {freq:>6.0f}Hz r{rep}: NO SIGNAL DETECTED")
                    results.append({"volume": volume, "freq": freq, "rep": rep, "thd": None})
                    continue
                flag = "" if thd["snr_ok"] else "  ** LOW SNR, UNRELIABLE **"
                print(
                    f"  vol={volume} {freq:>6.0f}Hz r{rep}: thd={thd['thd_percent']:.3f}% "
                    f"({thd['thd_db']:.1f}dB) snr={thd['fundamental_snr_db']:.1f}dB{flag}"
                )
                results.append({"volume": volume, "freq": freq, "rep": rep, "thd": thd})
                time.sleep(0.3)

    _write_json(args.output_json, {
        "mode": "thd",
        "label": args.label,
        "output_device": sd.query_devices(output_device)["name"],
        "input_device": sd.query_devices(input_device)["name"],
        "amplitude": args.amplitude,
        "sample_rate": args.sample_rate,
        "frequencies": frequencies,
        "volumes": volumes,
        "repeat_each": args.repeat_each,
        "results": results,
    })


def cmd_sweep(args: argparse.Namespace) -> None:
    output_device = resolve_device(args.output_name, want_output=True)
    input_device = resolve_device(args.input_name, want_output=False)
    print(f"output_device={sd.query_devices(output_device)['name']!r} (#{output_device})")
    print(f"input_device={sd.query_devices(input_device)['name']!r} (#{input_device})")

    volumes = _resolve_volumes(args.volumes)
    results = []
    for volume in volumes:
        if volume is not None:
            set_macos_output_volume(volume)
            time.sleep(0.3)
        trial = run_ess_trial(
            args.f1, args.f2, args.duration, args.lead_silence, args.tail_silence,
            args.sample_rate, args.amplitude, args.n_harmonics, output_device, input_device,
        )
        print(f"volume={volume}: {json.dumps({'freq_response_db': trial['freq_response_db'], 'rt60': trial['rt60']}, indent=2)}")
        if not trial["rt60"]["t20_reliable"]:
            print(
                f"  ** RT60 UNRELIABLE: only {trial['rt60']['snr_db']:.1f}dB peak-to-noise-floor SNR "
                "(need ~30dB+ for T20, ~40dB+ for T30) -- try a louder sweep/output level or a quieter room **"
            )
        results.append({"volume": volume, **trial})

    _write_json(args.output_json, {
        "mode": "sweep",
        "label": args.label,
        "output_device": sd.query_devices(output_device)["name"],
        "input_device": sd.query_devices(input_device)["name"],
        "f1": args.f1,
        "f2": args.f2,
        "duration": args.duration,
        "amplitude": args.amplitude,
        "sample_rate": args.sample_rate,
        "volumes": volumes,
        "results": results,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output-name", required=True, help="substring of the output device name")
    common.add_argument("--input-name", default="AIRHUG", help="substring of the input device name")
    common.add_argument("--sample-rate", type=int, default=48000)
    common.add_argument("--amplitude", type=float, default=0.6, help="digital signal amplitude, 0-1 full scale")
    common.add_argument("--label", required=True, help="label for this run, e.g. internal-vol100")
    common.add_argument("--output-json", required=True)
    common.add_argument(
        "--volumes", default=None,
        help="comma-separated macOS system output volumes (0-100) to sweep; omit to use the current volume",
    )

    p_thd = subparsers.add_parser("thd", parents=[common], help="pure-tone THD measurement")
    p_thd.add_argument("--frequencies", default="200,1000,4000")
    p_thd.add_argument("--repeat-each", type=int, default=3)
    p_thd.add_argument("--tone-seconds", type=float, default=2.0)
    p_thd.add_argument("--tail-seconds", type=float, default=0.5)
    p_thd.add_argument("--lead-silence-seconds", type=float, default=1.0)
    p_thd.add_argument("--min-snr-db", type=float, default=20.0)
    p_thd.set_defaults(func=cmd_thd)

    p_sweep = subparsers.add_parser("sweep", parents=[common], help="ESS/Farina frequency response + RT60 + harmonics")
    p_sweep.add_argument("--f1", type=float, default=100.0)
    p_sweep.add_argument("--f2", type=float, default=8000.0)
    p_sweep.add_argument("--duration", type=float, default=5.0)
    p_sweep.add_argument("--lead-silence", type=float, default=1.0)
    p_sweep.add_argument("--tail-silence", type=float, default=2.0)
    p_sweep.add_argument("--n-harmonics", type=int, default=4)
    p_sweep.set_defaults(func=cmd_sweep)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
