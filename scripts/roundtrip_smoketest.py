"""Real end-to-end smoke test: text -> Piper TTS -> faster-whisper STT -> text.

No microphone needed. Validates the two heaviest real components (TTS
synthesis, local STT) actually work together, independent of live audio
capture (which this development machine can't provide — see README).

Defaults to en_US-lessac-medium/English phrases (the original smoke
test), but --voice runs any installed Piper voice through the same round
trip with phrases matched to that voice's language where available --
this is the way to test whether a voice picked with scripts/voice_picker.py
is actually intelligible, not just that it produces audio. See
scripts/voice_picker.py to browse/download voices and
STT_TEST_PHRASES below to add coverage for a new language.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/roundtrip_smoketest.py`, where it's __main__
# and Python auto-adds its own directory) and imported as
# scripts.roundtrip_smoketest (e.g. from a pytest test), where nothing does
# that automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console

import numpy as np

from convobox.config import STTConfig, TTSConfig
from convobox.stt.transcriber import LocalTranscriber
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine

DEFAULT_VOICE = "en_US-lessac-medium"

# One representative phrase per language this project has voices/STT
# coverage for. Falls back to the English phrases (translated meaning,
# not translated text) for any voice whose language isn't listed here --
# still a valid TTS/STT round trip, just not testing STT's language
# auto-detection landing correctly, only that audio in *some* form round-trips.
STT_TEST_PHRASES: dict[str, list[str]] = {
    "en": [
        "Run the test suite and show me the results.",
        "Stop stop stop.",
        "Please refactor the authentication module to use async handlers.",
    ],
    "fr": ["Lancez la suite de tests et montrez-moi les résultats.", "Stop stop stop."],
    "ru": ["Запустите набор тестов и покажите мне результаты.", "Стоп стоп стоп."],
    "es": ["Ejecuta las pruebas y muéstrame los resultados.", "Alto alto alto."],
    "de": ["Führe die Tests aus und zeig mir die Ergebnisse.", "Stopp stopp stopp."],
    "it": ["Esegui i test e mostrami i risultati.", "Stop stop stop."],
    "zh": ["运行测试并显示结果。", "停止停止停止。"],
}


def resample_to_16k(audio: np.ndarray, source_rate: int) -> np.ndarray:
    # Linear interpolation, not a proper sinc resampler — fine for a smoke
    # test, not for production audio quality (no scipy dependency for this).
    if source_rate == 16000:
        return audio
    duration = len(audio) / source_rate
    target_len = int(duration * 16000)
    source_x = np.linspace(0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def _phrases_for_voice(voice: str) -> tuple[str, list[str], bool]:
    """Return (language_code, phrases, used_fallback) for a Piper voice key.

    Piper voice keys are "<lang>_<REGION>-<name>-<quality>" (e.g.
    "fr_FR-siwis-medium"); the language code is everything before the
    first underscore.
    """
    lang_code = voice.split("_")[0]
    if lang_code in STT_TEST_PHRASES:
        return lang_code, STT_TEST_PHRASES[lang_code], False
    return lang_code, STT_TEST_PHRASES["en"], True


async def main(voice: str) -> None:
    lang_code, phrases, used_fallback = _phrases_for_voice(voice)
    if used_fallback:
        print(f"no phrases for language {lang_code!r} yet, falling back to English phrases "
              f"(valid round trip, but won't exercise STT language detection for {voice!r})")

    print(f"loading Piper voice {voice!r}...")
    tts = create_tts_engine(TTSConfig(voice=voice), DEFAULT_VOICES_DIR)
    print(f"piper sample_rate={tts.sample_rate}")

    # tiny.en is English-only and noticeably faster; every other language
    # needs the multilingual model, pinned to the phrase's own language so
    # this is testing TTS/STT round-trip fidelity, not STT's separate (and
    # separately tested, see TESTING.md) language auto-detection accuracy.
    if lang_code == "en":
        print("loading faster-whisper (tiny.en, cpu, int8)...")
        stt = LocalTranscriber(STTConfig(model="tiny.en", device="cpu", compute_type="int8"))
    else:
        print(f"loading faster-whisper (base, cpu, int8, language={lang_code!r})...")
        stt = LocalTranscriber(
            STTConfig(model="base", device="cpu", compute_type="int8", language=lang_code)
        )

    for phrase in phrases:
        t0 = time.perf_counter()
        audio = await tts.synthesize(phrase)
        tts_ms = (time.perf_counter() - t0) * 1000

        audio_16k = resample_to_16k(audio, tts.sample_rate)
        result = stt.transcribe(audio_16k)

        print("-" * 70)
        print(f"input:      {phrase!r}")
        print(f"tts_ms:     {tts_ms:.0f}  (audio duration {len(audio) / tts.sample_rate:.2f}s)")
        print(f"transcript: {result.text!r}")
        print(f"stt_ms:     {result.latency_ms:.0f}  rtf={result.latency_ms / 1000 / result.duration_s:.2f}")


if __name__ == "__main__":
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--voice", default=DEFAULT_VOICE, help=f"installed Piper voice key (default: {DEFAULT_VOICE})"
    )
    args = parser.parse_args()
    asyncio.run(main(args.voice))
