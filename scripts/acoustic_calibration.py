"""Unattended real-room AEC/VAD calibration for ConvoBox.

This uses the configured microphone, speakers, Piper voice, WebRTC AEC, and
Silero VAD.  It deliberately does not contact a coding-agent backend.

The run has two phases:

1. Capture room ambience with no playback and check whether it becomes a VAD
   utterance at a small, bounded set of thresholds.
2. Play a known assistant response, capture the microphone before and after
   AEC, and simulate ConvoBox's sustained-speech barge-in monitor.  If real
   speaker echo is measurable, repeat around the stream-latency delay estimate
   and rank the results.

WAV and JSON evidence is written under ``uat-acoustic-calibration/``.  The
script never edits convobox.yaml; applying a result remains a reviewed change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import socket
import statistics
import sys
import threading
import time
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from convobox.audio.aec import EchoCanceller, _resample as _aec_resample
from convobox.audio.capture import MicrophoneStream
from convobox.audio.playback import AudioPlayer
from convobox.config import VADConfig, load_config
from convobox.interrupt_presets import resolve_preset
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine
from convobox.vad.segmenter import UtteranceSegmenter
from run_convobox import AEC_MEASURABLE_ECHO_DB, BargeInMonitor, SINGLE_INSTANCE_PORT

_TEST_TEXT = (
    "This is an automated acoustic echo cancellation test. The assistant is "
    "speaking through the configured output while the configured microphone "
    "listens. ConvoBox should remove this response from microphone input, while "
    "leaving the microphone ready for a real person to interrupt naturally. "
)


@dataclass
class VadResult:
    utterances: int
    utterance_seconds: list[float]
    false_barge_ins: int
    barge_in_times_s: list[float]


@dataclass
class SignalDiagnostics:
    ambient_rms: float
    suppression_p10_db: float | None
    suppression_p50_db: float | None
    suppression_p90_db: float | None
    first_second_suppression_db: float | None
    steady_state_suppression_db: float | None
    processed_above_ambient_db: float | None
    peak_vad_probability_during_playback: float | None
    p95_vad_probability_during_playback: float | None
    estimated_echo_lag_ms: float | None
    raw_reference_correlation: float | None
    processed_reference_correlation: float | None
    reference_correlation_reduction_percent: float | None


@dataclass
class TrialResult:
    label: str
    delay_ms: int
    input_latency_ms: float | None
    output_latency_ms: float | None
    attenuation_db: float | None
    measurable_ceiling_db: float | None
    external_suppression_db: float | None
    raw_playback_rms: float
    processed_playback_rms: float
    raw_vad: VadResult
    processed_vad: VadResult
    signal: SignalDiagnostics
    raw_wav: str
    processed_wav: str
    diagnostics_npz: str


def _rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float64) ** 2)))


def _db_ratio(before: float, after: float) -> float | None:
    if before < 1e-6:
        return None
    return 20.0 * math.log10(before / max(after, 1e-9))


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    samples = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _simulate_vad(
    chunks: list[np.ndarray],
    playing: list[bool],
    config: VADConfig,
    on_current_turn: str,
    barge_in_min_speech_ms: int,
    sample_rate: int,
) -> VadResult:
    segmenter = UtteranceSegmenter(config)
    monitor = BargeInMonitor(on_current_turn, barge_in_min_speech_ms)
    utterances: list[np.ndarray] = []
    fired = 0
    fired_at: list[float] = []
    elapsed_s = 0.0
    for chunk, is_playing in zip(chunks, playing, strict=True):
        chunk_ms = 1000.0 * len(chunk) / sample_rate
        if monitor.observe(segmenter.in_speech, is_playing, chunk_ms):
            fired += 1
            fired_at.append(round(elapsed_s, 3))
        utterances.extend(segmenter.feed(chunk))
        elapsed_s += len(chunk) / sample_rate
    return VadResult(
        utterances=len(utterances),
        utterance_seconds=[round(len(item) / sample_rate, 3) for item in utterances],
        false_barge_ins=fired,
        barge_in_times_s=fired_at,
    )


def _signal_diagnostics(
    raw_chunks: list[np.ndarray],
    processed_chunks: list[np.ndarray],
    playing: list[bool],
    reference_active: list[bool],
    reference_audio: np.ndarray,
    vad_config: VADConfig,
    sample_rate: int,
) -> tuple[SignalDiagnostics, dict[str, np.ndarray]]:
    raw_rms = np.asarray([_rms(chunk) for chunk in raw_chunks], dtype=np.float64)
    processed_rms = np.asarray([_rms(chunk) for chunk in processed_chunks], dtype=np.float64)
    active = np.asarray(playing, dtype=np.bool_)
    suppression = np.full(len(raw_rms), np.nan, dtype=np.float64)
    usable = active & (raw_rms >= 1e-6)
    suppression[usable] = 20.0 * np.log10(
        raw_rms[usable] / np.maximum(processed_rms[usable], 1e-9)
    )
    active_suppression = suppression[np.isfinite(suppression)]
    inactive_raw = raw_rms[~active]
    ambient = float(np.mean(inactive_raw)) if inactive_raw.size else 0.0

    segmenter = UtteranceSegmenter(vad_config)
    probabilities: list[float] = []
    in_speech_before: list[bool] = []
    for chunk in processed_chunks:
        in_speech_before.append(segmenter.in_speech)
        segmenter.feed(chunk)
        probabilities.append(segmenter.last_probability or 0.0)
    probability_array = np.asarray(probabilities, dtype=np.float64)
    active_probabilities = probability_array[active]

    correlation_active = np.asarray(reference_active, dtype=np.bool_)
    raw_for_correlation = np.concatenate(
        [chunk for chunk, is_active in zip(raw_chunks, correlation_active, strict=True) if is_active]
    )
    processed_for_correlation = np.concatenate(
        [
            chunk
            for chunk, is_active in zip(processed_chunks, correlation_active, strict=True)
            if is_active
        ]
    )
    lag_ms, raw_correlation = _estimate_reference_lag(
        reference_audio, raw_for_correlation, sample_rate
    )
    processed_correlation = _correlation_at_lag(
        reference_audio, processed_for_correlation, sample_rate, lag_ms
    )
    correlation_reduction = (
        None
        if raw_correlation is None or processed_correlation is None or raw_correlation <= 1e-9
        else 100.0 * (1.0 - abs(processed_correlation) / abs(raw_correlation))
    )

    active_indices = np.flatnonzero(active)
    early_mask = np.zeros(len(active), dtype=np.bool_)
    steady_mask = np.zeros(len(active), dtype=np.bool_)
    if active_indices.size:
        chunks_per_second = max(1, round(sample_rate / len(raw_chunks[active_indices[0]])))
        early_mask[active_indices[:chunks_per_second]] = True
        steady_mask[active_indices[chunks_per_second:]] = True

    def combined_suppression(mask: np.ndarray) -> float | None:
        if not np.any(mask):
            return None
        return _db_ratio(float(np.mean(raw_rms[mask])), float(np.mean(processed_rms[mask])))

    processed_active = float(np.mean(processed_rms[active])) if np.any(active) else 0.0
    diagnostics = SignalDiagnostics(
        ambient_rms=round(ambient, 7),
        suppression_p10_db=(
            None if not active_suppression.size else round(float(np.percentile(active_suppression, 10)), 3)
        ),
        suppression_p50_db=(
            None if not active_suppression.size else round(float(np.percentile(active_suppression, 50)), 3)
        ),
        suppression_p90_db=(
            None if not active_suppression.size else round(float(np.percentile(active_suppression, 90)), 3)
        ),
        first_second_suppression_db=(
            None if combined_suppression(early_mask) is None else round(combined_suppression(early_mask) or 0.0, 3)
        ),
        steady_state_suppression_db=(
            None if combined_suppression(steady_mask) is None else round(combined_suppression(steady_mask) or 0.0, 3)
        ),
        processed_above_ambient_db=(
            None if ambient < 1e-6 else round(20.0 * math.log10(max(processed_active, 1e-9) / ambient), 3)
        ),
        peak_vad_probability_during_playback=(
            None if not active_probabilities.size else round(float(np.max(active_probabilities)), 6)
        ),
        p95_vad_probability_during_playback=(
            None if not active_probabilities.size else round(float(np.percentile(active_probabilities, 95)), 6)
        ),
        estimated_echo_lag_ms=None if lag_ms is None else round(lag_ms, 3),
        raw_reference_correlation=(
            None if raw_correlation is None else round(raw_correlation, 6)
        ),
        processed_reference_correlation=(
            None if processed_correlation is None else round(processed_correlation, 6)
        ),
        reference_correlation_reduction_percent=(
            None if correlation_reduction is None else round(correlation_reduction, 3)
        ),
    )
    trace = {
        "chunk_sizes": np.asarray([len(chunk) for chunk in raw_chunks], dtype=np.int32),
        "playing": active,
        "reference_active": correlation_active,
        "reference_audio": reference_audio.astype(np.float32),
        "raw_rms": raw_rms,
        "processed_rms": processed_rms,
        "suppression_db": suppression,
        "vad_probability": probability_array,
        "vad_in_speech_before": np.asarray(in_speech_before, dtype=np.bool_),
        "raw_audio": np.concatenate(raw_chunks).astype(np.float32),
        "processed_audio": np.concatenate(processed_chunks).astype(np.float32),
    }
    return diagnostics, trace


def _estimate_reference_lag(
    reference: np.ndarray,
    observed: np.ndarray,
    sample_rate: int,
    max_lag_ms: int = 500,
) -> tuple[float | None, float | None]:
    if reference.size < sample_rate or observed.size < sample_rate:
        return None, None
    # Speech-band correlation does not need 16kHz resolution.  Decimating to
    # ~1kHz makes an exhaustive 0..500ms normalized-lag search cheap while
    # retaining 1ms timing resolution.
    stride = max(1, sample_rate // 1000)
    ref = np.asarray(reference[::stride], dtype=np.float64)
    obs = np.asarray(observed[::stride], dtype=np.float64)
    ref -= np.mean(ref)
    obs -= np.mean(obs)
    max_lag = min(round(max_lag_ms * sample_rate / 1000 / stride), len(obs) - 2)
    best_lag = 0
    best_correlation: float | None = None
    for lag in range(max_lag + 1):
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


def _correlation_at_lag(
    reference: np.ndarray,
    observed: np.ndarray,
    sample_rate: int,
    lag_ms: float | None,
) -> float | None:
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


def _acquire_audio_lock() -> socket.socket:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError as exc:
        lock.close()
        raise RuntimeError(
            "another ConvoBox microphone session is running; stop it before calibration"
        ) from exc
    return lock


def _capture_ambient(
    mic: MicrophoneStream, seconds: float, sample_rate: int
) -> list[np.ndarray]:
    chunks: list[np.ndarray] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunks.append(mic.read(timeout=3.0))
    captured_s = sum(len(chunk) for chunk in chunks) / sample_rate
    print(f"ambient capture complete: {captured_s:.1f}s, rms={_rms(np.concatenate(chunks)):.6f}")
    return chunks


def _run_trial(
    *,
    label: str,
    requested_delay_ms: int | None,
    audio: np.ndarray,
    audio_rate: int,
    mic: MicrophoneStream,
    input_latency_s: float | None,
    output_device: str | int | None,
    sample_rate: int,
    vad_config: VADConfig,
    on_current_turn: str,
    barge_in_min_speech_ms: int,
    tail_seconds: float,
    output_dir: Path,
) -> TrialResult:
    auto = requested_delay_ms is None
    canceller = EchoCanceller(delay_ms=requested_delay_ms or 100)
    player = AudioPlayer(device=output_device)
    delay_ready = threading.Event()
    reference_started = threading.Event()
    reference_blocks: list[np.ndarray] = []

    def feed_reference(block: np.ndarray, block_rate: int) -> None:
        if auto and not delay_ready.is_set():
            out_latency = player.output_latency_s
            if out_latency is not None and input_latency_s is not None:
                estimate = int((float(out_latency) + float(input_latency_s)) * 1000) + 10
                canceller.set_delay(estimate)
                delay_ready.set()
        reference_blocks.append(_aec_resample(block.copy(), block_rate, sample_rate))
        reference_started.set()
        canceller.feed_reverse(block, block_rate)

    player.on_block_played = feed_reference
    raw_chunks: list[np.ndarray] = []
    processed_chunks: list[np.ndarray] = []
    playing_flags: list[bool] = []
    reference_active_flags: list[bool] = []

    # Give the canceller a real ambient floor before playback.
    pre_deadline = time.monotonic() + 2.0
    while time.monotonic() < pre_deadline:
        raw = mic.read(timeout=3.0)
        raw_chunks.append(raw)
        processed_chunks.append(canceller.process(raw))
        playing_flags.append(False)
        reference_active_flags.append(False)

    print(f"trial {label}: playing known response")
    player.play(audio, audio_rate)
    playback_seen = False
    tail_deadline: float | None = None
    hard_deadline = time.monotonic() + len(audio) / audio_rate + 15.0
    while time.monotonic() < hard_deadline:
        raw = mic.read(timeout=3.0)
        is_playing = player.is_playing()
        playback_seen = playback_seen or reference_started.is_set() or is_playing
        raw_chunks.append(raw)
        processed_chunks.append(canceller.process(raw))
        playing_flags.append(is_playing)
        reference_active_flags.append(reference_started.is_set() and is_playing)
        if playback_seen and not is_playing:
            if tail_deadline is None:
                tail_deadline = time.monotonic() + tail_seconds
            elif time.monotonic() >= tail_deadline:
                break
        else:
            tail_deadline = None
    player.wait()
    if not playback_seen:
        raise RuntimeError("output stream never started; check the configured output device")

    active_raw = [chunk for chunk, active in zip(raw_chunks, playing_flags, strict=True) if active]
    active_processed = [
        chunk for chunk, active in zip(processed_chunks, playing_flags, strict=True) if active
    ]
    raw_active_audio = np.concatenate(active_raw) if active_raw else np.zeros(0, dtype=np.float32)
    processed_active_audio = (
        np.concatenate(active_processed) if active_processed else np.zeros(0, dtype=np.float32)
    )
    raw_rms = _rms(raw_active_audio)
    processed_rms = _rms(processed_active_audio)

    raw_vad = _simulate_vad(
        raw_chunks,
        playing_flags,
        vad_config,
        on_current_turn,
        barge_in_min_speech_ms,
        sample_rate,
    )
    processed_vad = _simulate_vad(
        processed_chunks,
        playing_flags,
        vad_config,
        on_current_turn,
        barge_in_min_speech_ms,
        sample_rate,
    )
    safe_label = label.replace(" ", "-")
    raw_path = output_dir / f"{safe_label}-raw-mic.wav"
    processed_path = output_dir / f"{safe_label}-aec-mic.wav"
    diagnostics_path = output_dir / f"{safe_label}-diagnostics.npz"
    _write_wav(raw_path, np.concatenate(raw_chunks), sample_rate)
    _write_wav(processed_path, np.concatenate(processed_chunks), sample_rate)
    reference_audio = (
        np.concatenate(reference_blocks) if reference_blocks else np.zeros(0, dtype=np.float32)
    )
    signal, trace = _signal_diagnostics(
        raw_chunks,
        processed_chunks,
        playing_flags,
        reference_active_flags,
        reference_audio,
        vad_config,
        sample_rate,
    )
    np.savez_compressed(diagnostics_path, **trace)

    result = TrialResult(
        label=label,
        delay_ms=canceller.delay_ms,
        input_latency_ms=None if input_latency_s is None else round(input_latency_s * 1000, 2),
        output_latency_ms=(
            None if player.output_latency_s is None else round(player.output_latency_s * 1000, 2)
        ),
        attenuation_db=(
            None if canceller.attenuation_db() is None else round(canceller.attenuation_db() or 0.0, 2)
        ),
        measurable_ceiling_db=(
            None
            if canceller.measurable_ceiling_db() is None
            else round(canceller.measurable_ceiling_db() or 0.0, 2)
        ),
        external_suppression_db=(
            None if _db_ratio(raw_rms, processed_rms) is None else round(_db_ratio(raw_rms, processed_rms) or 0.0, 2)
        ),
        raw_playback_rms=round(raw_rms, 7),
        processed_playback_rms=round(processed_rms, 7),
        raw_vad=raw_vad,
        processed_vad=processed_vad,
        signal=signal,
        raw_wav=str(raw_path),
        processed_wav=str(processed_path),
        diagnostics_npz=str(diagnostics_path),
    )
    print(
        f"trial {label}: delay={result.delay_ms}ms attenuation={result.attenuation_db}dB "
        f"ceiling={result.measurable_ceiling_db}dB suppression={result.external_suppression_db}dB "
        f"echo-lag={signal.estimated_echo_lag_ms}ms corr={signal.raw_reference_correlation}/"
        f"{signal.processed_reference_correlation} "
        f"false-barge raw/aec={raw_vad.false_barge_ins}/{processed_vad.false_barge_ins} "
        f"utterances raw/aec={raw_vad.utterances}/{processed_vad.utterances}"
    )
    return result


def _trial_rank(trial: TrialResult) -> tuple[int, int, float, float]:
    suppression = trial.external_suppression_db if trial.external_suppression_db is not None else -999.0
    residual = trial.processed_playback_rms
    return (
        trial.processed_vad.false_barge_ins,
        trial.processed_vad.utterances,
        -suppression,
        residual,
    )


def _aggregate_trials(trials: list[TrialResult]) -> dict[str, dict[str, Any]]:
    grouped: dict[int, list[TrialResult]] = {}
    for trial in trials:
        grouped.setdefault(trial.delay_ms, []).append(trial)
    result: dict[str, dict[str, Any]] = {}
    for delay, group in sorted(grouped.items()):
        suppressions = [
            item.external_suppression_db
            for item in group
            if item.external_suppression_db is not None
        ]
        residuals = [item.processed_playback_rms for item in group]
        raw_barges = sum(item.raw_vad.false_barge_ins for item in group)
        processed_barges = sum(item.processed_vad.false_barge_ins for item in group)
        raw_utterances = sum(item.raw_vad.utterances for item in group)
        processed_utterances = sum(item.processed_vad.utterances for item in group)
        mean_residual = statistics.fmean(residuals)
        result[str(delay)] = {
            "trials": len(group),
            "raw_false_barge_ins": raw_barges,
            "processed_false_barge_ins": processed_barges,
            "self_barge_rejection_percent": (
                None if raw_barges == 0 else round(100.0 * (1.0 - processed_barges / raw_barges), 3)
            ),
            "raw_utterances": raw_utterances,
            "processed_utterances": processed_utterances,
            "self_input_rejection_percent": (
                None
                if raw_utterances == 0
                else round(100.0 * (1.0 - processed_utterances / raw_utterances), 3)
            ),
            "mean_suppression_db": (
                None if not suppressions else round(statistics.fmean(suppressions), 3)
            ),
            "suppression_population_stdev_db": (
                None if len(suppressions) < 2 else round(statistics.pstdev(suppressions), 3)
            ),
            "mean_processed_rms": round(mean_residual, 7),
            "processed_rms_population_stdev_percent": (
                0.0
                if len(residuals) < 2 or mean_residual == 0
                else round(100.0 * statistics.pstdev(residuals) / mean_residual, 3)
            ),
        }
    return result


async def _synthesize(config: Any, repeats: int) -> tuple[np.ndarray, int]:
    tts = create_tts_engine(config.tts, DEFAULT_VOICES_DIR)
    audio = await tts.synthesize(_TEST_TEXT * repeats)
    return np.asarray(audio, dtype=np.float32), tts.sample_rate


def run(args: argparse.Namespace) -> Path:
    config = load_config(args.config)
    if not config.audio.echo_cancellation:
        print("note: config currently has audio.echo_cancellation=false; calibration will still test AEC")
    output_dir = Path(args.output_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=False)
    lock = _acquire_audio_lock()
    try:
        audio, audio_rate = asyncio.run(_synthesize(config, args.response_repeats))
        _write_wav(output_dir / "assistant-reference.wav", audio, audio_rate)
        mic = MicrophoneStream(
            sample_rate=config.audio.sample_rate,
            blocksize=512,
            device=config.audio.input_device,
            channels=1,
        )
        mic.start()
        try:
            print(
                f"input={config.audio.input_device!r} output={config.audio.output_device!r} "
                f"voice={config.tts.voice!r}"
            )
            ambient_chunks = _capture_ambient(mic, args.ambient_seconds, config.audio.sample_rate)
            ambient_audio = np.concatenate(ambient_chunks)
            _write_wav(output_dir / "ambient-raw.wav", ambient_audio, config.audio.sample_rate)
            ambient_playing = [False] * len(ambient_chunks)
            ambient_thresholds: dict[str, dict[str, Any]] = {}
            axes = resolve_preset(config.interaction.interrupt_preset)
            for threshold in (0.5, 0.55, 0.6, 0.65):
                candidate = config.vad.model_copy(update={"threshold": threshold})
                outcome = _simulate_vad(
                    ambient_chunks,
                    ambient_playing,
                    candidate,
                    axes.on_current_turn,
                    config.interaction.barge_in_min_speech_ms,
                    config.audio.sample_rate,
                )
                ambient_thresholds[f"{threshold:.2f}"] = asdict(outcome)
                print(f"ambient VAD threshold={threshold:.2f}: utterances={outcome.utterances}")

            def execute_trial(label: str, delay: int | None) -> TrialResult:
                return _run_trial(
                    label=label,
                    requested_delay_ms=delay,
                    audio=audio,
                    audio_rate=audio_rate,
                    mic=mic,
                    input_latency_s=mic.input_latency_s,
                    output_device=config.audio.output_device,
                    sample_rate=config.audio.sample_rate,
                    vad_config=config.vad,
                    on_current_turn=axes.on_current_turn,
                    barge_in_min_speech_ms=config.interaction.barge_in_min_speech_ms,
                    tail_seconds=args.tail_seconds,
                    output_dir=output_dir,
                )

            trials: list[TrialResult] = []
            if args.delay_candidates:
                candidate_tokens = [item.strip().lower() for item in args.delay_candidates.split(",")]
                for token in candidate_tokens:
                    if token == "auto":
                        delay = None
                    else:
                        delay = int(token)
                        if not 0 <= delay <= 500:
                            raise ValueError("delay candidates must be between 0 and 500ms")
                    for repeat in range(1, args.repeat_each + 1):
                        trials.append(execute_trial(f"{token}-r{repeat}", delay))
            else:
                auto_trial = execute_trial("auto", None)
                trials.append(auto_trial)
                ceiling = auto_trial.measurable_ceiling_db
                if args.force_delay_sweep or (
                    ceiling is not None and ceiling >= AEC_MEASURABLE_ECHO_DB
                ):
                    offsets = (-100, -50, 50, 100)
                    delays = []
                    for offset in offsets:
                        value = max(0, min(500, auto_trial.delay_ms + offset))
                        if value != auto_trial.delay_ms and value not in delays:
                            delays.append(value)
                    for delay in delays[: max(0, args.max_trials - 1)]:
                        trials.append(execute_trial(f"delay-{delay}ms", delay))
                else:
                    print(
                        "no measurable speaker echo reached the mic; "
                        "skipping meaningless delay sweep"
                    )
        finally:
            mic.close()
    finally:
        lock.close()

    aggregates = _aggregate_trials(trials)
    aggregate_rank = min(
        aggregates.items(),
        key=lambda item: (
            item[1]["processed_false_barge_ins"],
            item[1]["processed_utterances"],
            item[1]["mean_processed_rms"],
        ),
    )
    best_delay = int(aggregate_rank[0])
    best = min((trial for trial in trials if trial.delay_ms == best_delay), key=_trial_rank)
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "config": str(Path(args.config).resolve()),
        "devices": {
            "input": config.audio.input_device,
            "output": config.audio.output_device,
            "sample_rate": config.audio.sample_rate,
        },
        "ambient": {
            "seconds": round(len(ambient_audio) / config.audio.sample_rate, 3),
            "rms": round(_rms(ambient_audio), 7),
            "threshold_results": ambient_thresholds,
        },
        "trials": [asdict(trial) for trial in trials],
        "aggregates_by_delay_ms": aggregates,
        "best_trial": best.label,
        "recommendation": {
            "aec_delay_ms": best.delay_ms,
            "vad_threshold": config.vad.threshold,
            "automatic_config_edit": False,
            "reason": (
                "A real human double-talk sample is required before raising the VAD threshold; "
                "this unattended run only rejects unsafe settings, it does not prove sensitivity."
            ),
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report: {report_path}")
    print(f"best trial: {best.label} ({best.delay_ms}ms)")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="convobox.yaml")
    parser.add_argument("--ambient-seconds", type=float, default=45.0)
    parser.add_argument("--response-repeats", type=int, default=2)
    parser.add_argument("--tail-seconds", type=float, default=2.0)
    parser.add_argument("--max-trials", type=int, default=5)
    parser.add_argument(
        "--delay-candidates",
        help="comma-separated explicit delay candidates, with optional 'auto' (for example auto,222,272)",
    )
    parser.add_argument("--repeat-each", type=int, default=1)
    parser.add_argument(
        "--force-delay-sweep",
        action="store_true",
        help="test delays around auto even when the current echo-to-noise ceiling is low",
    )
    parser.add_argument("--output-dir", default="uat-acoustic-calibration")
    args = parser.parse_args()
    if (
        args.ambient_seconds <= 0
        or args.response_repeats <= 0
        or args.max_trials <= 0
        or args.repeat_each <= 0
    ):
        parser.error("durations, repeats, and max-trials must be positive")
    run(args)


if __name__ == "__main__":
    main()
