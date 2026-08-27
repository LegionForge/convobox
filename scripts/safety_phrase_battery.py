"""Real TTS -> STT round-trip reliability battery for every safety-critical
spoken phrase (safewords/hard-stop, kill_phrase, pause phrase, resume word),
checked against the REAL detector classes -- not a mocked transcript.

No microphone/speakers needed (same "remove the audio-hardware confound
entirely" discipline as scripts/roundtrip_smoketest.py): each phrase is
synthesized via the configured TTS engine, transcribed via the configured
STT engine, and the resulting transcript is checked against
SafewordDetector/PauseListeningDetector/ResumeWordDetector -- exactly the
question that matters (does this phrase, AS SPOKEN AND TRANSCRIBED, still
fire the detector it's meant to), not just "does the string match" (already
covered by tests/test_safeword.py and friends).

This is the first COMMITTED version of a methodology this project has used
before but never kept: docs/field-notes/2026-08-15-safety-phrase-
reliability-battery-halt-and-bare-athena-unreliable.md's own harness
(`_test_safety_phrase_battery.py`) was explicitly "not committed" --
findings from that session (halt/bare-Athena unreliable, stop/abort
solid) are recorded in that field note but can't be re-run without
rebuilding the harness from scratch. This script is that harness, kept.

Usage:
    python scripts/safety_phrase_battery.py                # N=5 per case
    python scripts/safety_phrase_battery.py --repeats 10
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console
from roundtrip_smoketest import resample_to_16k

from convobox.config import STTConfig, TTSConfig
from convobox.listening_pause.detector import (
    DEFAULT_PAUSE_PHRASES,
    PauseListeningDetector,
)
from convobox.resumeword.detector import DEFAULT_RESUME_WORD, ResumeWordDetector
from convobox.safeword.detector import SafewordDetector
from convobox.stt.transcriber import LocalTranscriber
from convobox.tts.base import TTSEngine
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine

# Real, currently-shipped config as of this script's writing:
# safeword.hard_stop_phrases default is ("stop stop stop", "abort abort
# abort") -- see src/convobox/config.py's SafewordConfig -- plus
# "eject eject eject", added 2026-08-25 as this session's own
# safeword.kill_phrase (see convobox.yaml). "mayday mayday mayday" is NOT
# configured anywhere -- included here only because it was tried live,
# unscripted, during that same session's real human-voice test (see
# docs/field-notes/2026-08-25-linux-first-real-human-speech-demo-*.md) and
# the operator asked whether it (and other interrupt-style words) transcribe
# reliably enough to be worth considering. Treated as a CANDIDATE, not a
# default -- same "battery informs, doesn't silently ship" discipline the
# 2026-08-15 note already established for "halt halt halt".
_CONFIGURED_HARD_STOP_PHRASES = ["stop stop stop", "abort abort abort", "eject eject eject"]
_CANDIDATE_HARD_STOP_PHRASES = ["mayday mayday mayday"]
_ALL_HARD_STOP_PHRASES = _CONFIGURED_HARD_STOP_PHRASES + _CANDIDATE_HARD_STOP_PHRASES


@dataclass
class Case:
    label: str
    spoken_text: str
    check: Callable[[str], bool]  # closes over the real detector, built per-case below


def _matches_phrase(safeword: SafewordDetector, phrase: str) -> Callable[[str], bool]:
    # A real factory function, not a default-argument lambda trick -- the
    # latter is a common late-binding footgun in a loop (every lambda
    # sharing the loop variable itself, not a per-iteration snapshot) and
    # mypy can't infer a lambda's own default-argument type either. This
    # closes over `phrase` at call time, correctly, once per case.
    def _check(transcript: str) -> bool:
        return safeword.check(transcript) == phrase
    return _check


def _build_cases() -> list[Case]:
    safeword = SafewordDetector(_ALL_HARD_STOP_PHRASES)
    pause = PauseListeningDetector()
    resume = ResumeWordDetector()

    cases: list[Case] = []

    # 1. Configured safewords/kill_phrase -- MUST fire reliably; these are
    #    the actual shipped/configured safety phrases.
    for phrase in _CONFIGURED_HARD_STOP_PHRASES:
        spoken = phrase.capitalize().replace(" ", ", ", 2) + "."
        cases.append(Case(
            label=f"safeword (configured): {phrase!r}",
            spoken_text=spoken,
            check=_matches_phrase(safeword, phrase),
        ))

    # 2. Candidate phrase(s) -- NOT configured; testing reliability only,
    #    to inform whether they'd be worth adding.
    for phrase in _CANDIDATE_HARD_STOP_PHRASES:
        spoken = phrase.capitalize().replace(" ", ", ", 2) + "."
        cases.append(Case(
            label=f"safeword (CANDIDATE, not configured): {phrase!r}",
            spoken_text=spoken,
            check=_matches_phrase(safeword, phrase),
        ))

    # 3. Pause phrases (both defaults).
    for phrase in DEFAULT_PAUSE_PHRASES:
        cases.append(Case(
            label=f"pause phrase: {phrase!r}",
            spoken_text=phrase.capitalize() + ".",
            check=lambda t: pause.check(t) is not None,
        ))

    # 4. Resume word (default).
    cases.append(Case(
        label=f"resume word: {DEFAULT_RESUME_WORD!r}",
        spoken_text=DEFAULT_RESUME_WORD.capitalize() + ".",
        check=lambda t: resume.check(t),
    ))

    # 5. Benign near-misses -- MUST NOT fire (false-positive check), same
    #    discipline as the 2026-08-15 battery.
    cases.append(Case(
        label="benign near-miss: bare 'stop' (not tripled)",
        spoken_text="Stop.",
        check=lambda t: safeword.check(t) is None,
    ))
    cases.append(Case(
        label="benign near-miss: bare 'mayday' (not tripled)",
        spoken_text="Mayday!",
        check=lambda t: safeword.check(t) is None,
    ))

    return cases


async def _run_case(
    tts: TTSEngine, stt: LocalTranscriber, case: Case, repeats: int
) -> tuple[int, int, list[str]]:
    passed = 0
    transcripts: list[str] = []
    for _ in range(repeats):
        audio = await tts.synthesize(case.spoken_text)
        audio_16k = resample_to_16k(audio, tts.sample_rate)
        result = stt.transcribe(audio_16k)
        transcripts.append(result.text)
        if case.check(result.text):
            passed += 1
    return passed, repeats, transcripts


async def main(repeats: int) -> int:
    print("loading TTS engine (default config: kokoro/af_sarah)...")
    tts = create_tts_engine(TTSConfig(), DEFAULT_VOICES_DIR)
    print(f"tts sample_rate={tts.sample_rate}")
    print("loading faster-whisper (base, auto device, auto language -- matches real ConvoBox defaults)...")
    stt = LocalTranscriber(STTConfig())

    print(f"\nRunning {len(_build_cases())} cases x {repeats} repeats each...\n")
    results: list[tuple[Case, int, int, list[str]]] = []
    for case in _build_cases():
        passed, total, transcripts = await _run_case(tts, stt, case, repeats)
        results.append((case, passed, total, transcripts))
        marker = "✅" if passed == total else ("⚠️" if passed > 0 else "❌")
        print(f"{marker} {case.label}: {passed}/{total}")
        if passed != total:
            counts = Counter(transcripts)
            for transcript, n in counts.most_common():
                print(f"      {n}x transcribed as: {transcript!r}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    any_unreliable = False
    for case, passed, total, _ in results:
        status = "OK" if passed == total else "UNRELIABLE"
        if passed != total:
            any_unreliable = True
        print(f"  {status:>10}  {passed}/{total}  {case.label}")
    return 1 if any_unreliable else 0


if __name__ == "__main__":
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeats", type=int, default=5,
        help="TTS->STT round trips per phrase (default 5, matching this "
        "project's own established battery methodology)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.repeats)))
