"""Real end-to-end smoke test: text -> Piper TTS -> faster-whisper STT -> text.

No microphone needed. Validates the two heaviest real components (TTS
synthesis, local STT) actually work together, independent of live audio
capture (which this development machine can't provide — see README).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from convobox.config import STTConfig
from convobox.stt.transcriber import LocalTranscriber
from convobox.tts.piper import PiperTTSEngine

PHRASES = [
    "Run the test suite and show me the results.",
    "Stop stop stop.",
    "Please refactor the authentication module to use async handlers.",
]


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


async def main() -> None:
    print("loading Piper voice...")
    tts = PiperTTSEngine(
        model_path=".models/piper/en_US-lessac-medium.onnx",
        config_path=".models/piper/en_US-lessac-medium.onnx.json",
    )
    print(f"piper sample_rate={tts.sample_rate}")

    print("loading faster-whisper (tiny.en, cpu, int8)...")
    stt = LocalTranscriber(STTConfig(model="tiny.en", device="cpu", compute_type="int8"))

    for phrase in PHRASES:
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
    asyncio.run(main())
