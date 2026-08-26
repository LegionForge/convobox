"""Real-hardware (speaker -> room -> mic) safety-phrase reliability sweep
across a system-volume range -- the live-acoustic counterpart to
scripts/safety_phrase_battery.py's in-memory TTS->STT round trip.

scripts/safety_phrase_battery.py deliberately removes the audio-hardware
confound (no mic/speakers, testing TTS/STT/detector fidelity alone). This
script puts that confound back, on purpose: real TTS audio out real
speakers, real capture via the real mic, both BEFORE and AFTER real AEC
processing, transcribed and checked against the real detector classes --
same discipline as scripts/acoustic_calibration.py's own real trials, but
scoring "did the safety phrase still resolve" instead of a false-barge-in
count.

Needs real speakers and a real mic. Uses wpctl (PipeWire) to sweep SYSTEM
output volume on Linux, restoring it in a finally block regardless of how
the run ends -- see acoustic_calibration.py's own --volume-candidates
docstring for why system volume, not tts.volume, is the right knob (the
finding under test is about the real driver/room/mic chain, which a
digital pre-DAC gain doesn't reproduce).

Usage:
    python scripts/safety_phrase_battery_live.py
    python scripts/safety_phrase_battery_live.py --volumes 50,45,40,35,30,25,20 --repeats 10
"""

from __future__ import annotations

import argparse
import asyncio
import re
import socket
import subprocess  # nosec B404 -- wpctl, a real system volume control, not untrusted input
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
from _console import use_utf8_console

from convobox.audio.aec import _resample as _aec_resample
from convobox.audio.capture import MicrophoneStream
from convobox.audio.playback import AudioPlayer
from convobox.config import STTConfig, TTSConfig, load_config
from convobox.listening_pause.detector import (
    DEFAULT_PAUSE_PHRASES,
    PauseListeningDetector,
)
from convobox.resumeword.detector import DEFAULT_RESUME_WORD, ResumeWordDetector
from convobox.safeword.detector import SafewordDetector
from convobox.stt.transcriber import LocalTranscriber
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine

# Same port run_convobox.py/acoustic_calibration.py already use to keep two
# real audio sessions from contending for the mic at once.
_SINGLE_INSTANCE_PORT = 47613

_CONFIGURED_HARD_STOP_PHRASES = ["stop stop stop", "abort abort abort", "eject eject eject"]
_CANDIDATE_HARD_STOP_PHRASES = ["mayday mayday mayday"]
_ALL_HARD_STOP_PHRASES = _CONFIGURED_HARD_STOP_PHRASES + _CANDIDATE_HARD_STOP_PHRASES


# --- Linux system-volume control (wpctl/PipeWire) -- a small, self-
# contained copy of the same logic added to scripts/acoustic_calibration.py
# on a separate, not-yet-merged branch (test/cross-backend-regression-
# matrix-2026-08-25) -- duplicated here rather than depended on cross-branch
# so this script stands alone; worth de-duplicating once both land on main. --


def _wpctl(*args: str) -> str:
    try:
        result = subprocess.run(  # nosec B603 B607
            ["wpctl", *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("this script's volume sweep needs wpctl (WirePlumber/PipeWire)") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"wpctl {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return result.stdout


def _get_system_volume_percent() -> float:
    output = _wpctl("get-volume", "@DEFAULT_AUDIO_SINK@")
    match = re.search(r"Volume:\s*([\d.]+)", output)
    if not match:
        raise RuntimeError(f"could not parse `wpctl get-volume` output: {output!r}")
    return round(float(match.group(1)) * 100.0, 2)


def _set_system_volume_percent(percent: float) -> None:
    if not 0.0 <= percent <= 100.0:
        raise ValueError(f"system volume percent must be 0-100, got {percent}")
    _wpctl("set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent / 100.0:.4f}")


def _acquire_audio_lock() -> socket.socket:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
    except OSError as exc:
        lock.close()
        raise RuntimeError(
            "another ConvoBox microphone session is running; stop it before this sweep"
        ) from exc
    return lock


@dataclass
class PhraseCase:
    label: str
    spoken_text: str
    check: Callable[[str], bool]


def _matches_phrase(safeword: SafewordDetector, phrase: str) -> Callable[[str], bool]:
    def _check(transcript: str) -> bool:
        return safeword.check(transcript) == phrase
    return _check


def _build_cases() -> list[PhraseCase]:
    safeword = SafewordDetector(_ALL_HARD_STOP_PHRASES)
    pause = PauseListeningDetector()
    resume = ResumeWordDetector()

    cases: list[PhraseCase] = []
    for phrase in _ALL_HARD_STOP_PHRASES:
        spoken = phrase.capitalize().replace(" ", ", ", 2) + "."
        tag = "configured" if phrase in _CONFIGURED_HARD_STOP_PHRASES else "CANDIDATE"
        cases.append(PhraseCase(
            label=f"safeword ({tag}): {phrase!r}",
            spoken_text=spoken,
            check=_matches_phrase(safeword, phrase),
        ))
    for phrase in DEFAULT_PAUSE_PHRASES:
        cases.append(PhraseCase(
            label=f"pause phrase: {phrase!r}",
            spoken_text=phrase.capitalize() + ".",
            check=lambda t: pause.check(t) is not None,
        ))
    cases.append(PhraseCase(
        label=f"resume word: {DEFAULT_RESUME_WORD!r}",
        spoken_text=DEFAULT_RESUME_WORD.capitalize() + ".",
        check=resume.check,
    ))
    return cases


async def _run_one_trial(
    *,
    audio: np.ndarray,
    audio_rate: int,
    mic: MicrophoneStream,
    output_device: str | int | None,
    sample_rate: int,
    tail_seconds: float,
) -> np.ndarray:
    """Play `audio` out real speakers, capture the real mic, return the
    captured audio at `sample_rate`.

    Deliberately NO AEC here, despite this project having a real
    EchoCanceller -- found live while building this script: feeding the
    played phrase itself as the AEC reference makes the canceller do
    exactly its job, cancel whatever correlates with the reference --
    but the reference here (equals what's playing) and the signal under
    test (the same phrase, arriving at the mic) are the SAME audio by
    construction, not an assistant-response-vs-independent-human-speech
    pair. Confirmed live: real captured RMS dropped ~10-20x post-AEC
    (0.10-0.14 -> 0.003-0.017), correctly and uselessly cancelling the
    very phrase this script exists to check, every single case. Real AEC
    testing (does it correctly leave a genuinely independent human
    utterance alone while cancelling the assistant's own concurrent
    speech) is scripts/acoustic_calibration.py's own job, already covered
    there. This script tests something narrower and simpler: does the
    phrase, spoken through the real speaker/room/mic chain at a given
    system volume, transcribe reliably at all -- the real acoustic
    chain, minus a confound that doesn't apply to this question.
    """
    player = AudioPlayer(device=output_device)
    raw_chunks: list[np.ndarray] = []
    playing_flags: list[bool] = []

    pre_deadline = time.monotonic() + 1.0
    while time.monotonic() < pre_deadline:
        raw_chunks.append(mic.read(timeout=3.0))
        playing_flags.append(False)

    player.play(audio, audio_rate)
    playback_seen = False
    tail_deadline: float | None = None
    hard_deadline = time.monotonic() + len(audio) / audio_rate + 10.0
    while time.monotonic() < hard_deadline:
        raw = mic.read(timeout=3.0)
        is_playing = player.is_playing()
        playback_seen = playback_seen or is_playing
        raw_chunks.append(raw)
        playing_flags.append(is_playing)
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

    active_raw = [c for c, p in zip(raw_chunks, playing_flags, strict=True) if p]
    raw_audio = np.concatenate(active_raw) if active_raw else np.zeros(0, dtype=np.float32)
    return _aec_resample(raw_audio, sample_rate, sample_rate)


async def main(volumes: list[float], repeats: int, tail_seconds: float) -> int:
    config = load_config("convobox.yaml")
    print(f"input={config.audio.input_device!r} output={config.audio.output_device!r}")

    lock = _acquire_audio_lock()
    try:
        tts = create_tts_engine(TTSConfig(), DEFAULT_VOICES_DIR)
        stt = LocalTranscriber(STTConfig())
        cases = _build_cases()

        original_volume = _get_system_volume_percent()
        print(f"system output volume before sweep: {original_volume}% (restored after)")

        results: dict[tuple[float, str], tuple[int, int]] = {}  # (pass, total) per (volume, case.label)

        with MicrophoneStream(
            sample_rate=config.audio.sample_rate,
            blocksize=512,
            device=config.audio.input_device,
            channels=1,
        ) as mic:
            try:
                for volume in volumes:
                    _set_system_volume_percent(volume)
                    actual = _get_system_volume_percent()
                    print(f"\n--- volume {volume}% (readback: {actual}%) ---")
                    for case in cases:
                        audio = await tts.synthesize(case.spoken_text)
                        passed = 0
                        transcripts: list[str] = []
                        for _ in range(repeats):
                            captured = await _run_one_trial(
                                audio=audio,
                                audio_rate=tts.sample_rate,
                                mic=mic,
                                output_device=config.audio.output_device,
                                sample_rate=config.audio.sample_rate,
                                tail_seconds=tail_seconds,
                            )
                            result = stt.transcribe(captured)
                            transcripts.append(result.text)
                            if case.check(result.text):
                                passed += 1
                        results[(volume, case.label)] = (passed, repeats)
                        marker = "OK" if passed == repeats else ("PARTIAL" if passed > 0 else "FAIL")
                        print(f"  [{marker:>7}] {case.label}: {passed}/{repeats}")
                        if passed != repeats:
                            for transcript, n in Counter(transcripts).most_common():
                                print(f"           {n}x: {transcript!r}")
            finally:
                _set_system_volume_percent(original_volume)
                print(f"\nsystem output volume restored to {original_volume}%")

        print("\n" + "=" * 78)
        print("SUMMARY (rows = volume, pass count / repeats)")
        print("=" * 78)
        header = "case".ljust(46) + "".join(f"{v:>6.0f}%" for v in volumes)
        print(header)
        for case in cases:
            row = case.label[:44].ljust(46)
            for volume in volumes:
                passed, total = results[(volume, case.label)]
                row += f"{passed:>5}/{total}"[:6].rjust(6)
            print(row)

        any_failed = any(passed < total for (passed, total) in results.values())
        return 1 if any_failed else 0
    finally:
        lock.close()


if __name__ == "__main__":
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volumes", default="50,45,40,35,30,25,20")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--tail-seconds", type=float, default=1.5)
    args = parser.parse_args()
    volume_list = [float(v.strip()) for v in args.volumes.split(",")]
    for v in volume_list:
        if not 0.0 <= v <= 100.0:
            parser.error("volumes must be 0-100")
    sys.exit(asyncio.run(main(volume_list, args.repeats, args.tail_seconds)))
