"""Smoke-test scripts/spike.py end to end without a physical microphone.

Every pipeline component has been validated individually (real STT/TTS
round trip, unit tests), but scripts/spike.py's own async wiring — the
actual mic -> VAD -> STT -> safeword loop, run as a script — has never
been executed even once, mocked or real. This synthesizes real speech
with Piper, feeds it into MicrophoneStream through a fake sounddevice
InputStream (the only thing substituted is the physical hardware), and
runs spike.run() for real against real Silero VAD and real faster-whisper.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT))

import numpy as np

from convobox.tts.piper import PiperTTSEngine

PHRASES = ["Run the test suite please.", "Stop stop stop."]
LEAD_SILENCE_S = 1.0
GAP_SILENCE_S = 1.0
TRAIL_SILENCE_S = 1.0
SAMPLE_RATE = 16000
BLOCKSIZE = 512


def _resample_to_16k(audio: np.ndarray, source_rate: int) -> np.ndarray:
    if source_rate == SAMPLE_RATE:
        return audio
    duration = len(audio) / source_rate
    target_len = int(duration * SAMPLE_RATE)
    source_x = np.linspace(0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


class FakeInputStream:
    """Drop-in for sounddevice.InputStream that feeds pre-recorded audio.

    Once started, autonomously pumps `audio` through the real capture
    callback in blocksize chunks from a background thread, the same shape
    a real PortAudio callback thread would use — MicrophoneStream can't
    tell the difference.
    """

    def __init__(self, audio: np.ndarray, **kwargs: Any) -> None:
        self._audio = audio
        self._callback = kwargs["callback"]
        self._blocksize = kwargs["blocksize"]
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        for start in range(0, len(self._audio), self._blocksize):
            if self._stop.is_set():
                return
            block = self._audio[start : start + self._blocksize]
            if len(block) < self._blocksize:
                block = np.pad(block, (0, self._blocksize - len(block)))
            self._callback(block.reshape(-1, 1), len(block), None, None)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def close(self) -> None:
        pass


async def build_fake_mic_audio() -> np.ndarray:
    tts = PiperTTSEngine(
        model_path=".models/piper/en_US-lessac-medium.onnx",
        config_path=".models/piper/en_US-lessac-medium.onnx.json",
    )
    segments = [np.zeros(int(LEAD_SILENCE_S * SAMPLE_RATE), dtype=np.float32)]
    for i, phrase in enumerate(PHRASES):
        audio = await tts.synthesize(phrase)
        segments.append(_resample_to_16k(audio, tts.sample_rate))
        gap = GAP_SILENCE_S if i < len(PHRASES) - 1 else TRAIL_SILENCE_S
        segments.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
    return np.concatenate(segments)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("convobox.spike_smoketest")

    log.info("synthesizing fake mic feed from real Piper audio...")
    fake_audio = await build_fake_mic_audio()
    log.info("fake mic feed is %.1fs of real synthesized audio", len(fake_audio) / SAMPLE_RATE)

    import sounddevice as sd

    import scripts.spike as spike_module

    def make_fake_stream(**kwargs: Any) -> FakeInputStream:
        return FakeInputStream(fake_audio, **kwargs)

    sd.InputStream = make_fake_stream  # type: ignore[assignment]

    log.info("running scripts/spike.py's run() for real (no --config, defaults)...")
    try:
        await asyncio.wait_for(spike_module.run(None, None), timeout=60.0)
        log.info("PASS: run() exited cleanly (safeword path reached and broke the loop)")
    except asyncio.TimeoutError:
        log.error("FAIL: run() did not exit within 60s — safeword was likely never detected")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
