"""Real end-to-end smoke test: text -> Piper TTS -> AudioPlayer -> real speakers.

AudioPlayer has only ever been tested against a mocked sounddevice
OutputStream. This plays real synthesized audio through whatever real
output device is available (this machine has no mic, but it does have
real speaker output) and, separately, proves barge-in (stop() mid-playback)
against real hardware timing, not a scripted fake.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from convobox.audio.playback import AudioPlayer
from convobox.tts.piper import PiperTTSEngine


async def main() -> None:
    print("loading Piper voice...")
    tts = PiperTTSEngine(
        model_path=".models/piper/en_US-lessac-medium.onnx",
        config_path=".models/piper/en_US-lessac-medium.onnx.json",
    )
    player = AudioPlayer()

    print("\n--- test 1: play a short phrase to completion ---")
    audio = await tts.synthesize("Testing real audio playback through actual speakers.")
    duration_s = len(audio) / tts.sample_rate
    print(f"synthesized {duration_s:.2f}s of audio, playing...")

    assert player.is_playing() is False, "should not be playing before play() is called"
    player.play(audio, tts.sample_rate)
    assert player.is_playing() is True, "should be playing immediately after play()"
    player.wait()
    assert player.is_playing() is False, "should not be playing after wait() returns"
    print("PASS: played to completion, is_playing() correct before/during/after")

    print("\n--- test 2: barge-in (stop() mid-playback) against real hardware timing ---")
    long_text = (
        "This is a much longer sentence specifically designed to take several seconds "
        "to speak out loud, so that we have enough time to interrupt it before it "
        "finishes playing naturally, which is exactly what barge-in is supposed to do."
    )
    audio = await tts.synthesize(long_text)
    duration_s = len(audio) / tts.sample_rate
    print(f"synthesized {duration_s:.2f}s of audio")

    t0 = time.perf_counter()
    player.play(audio, tts.sample_rate)
    await asyncio.sleep(0.5)
    assert player.is_playing() is True, "should still be playing 0.5s in"
    player.stop()
    stopped_at = time.perf_counter() - t0
    assert player.is_playing() is False, "should not be playing after stop()"

    print(f"stop() called at ~0.5s, playback actually halted at {stopped_at:.2f}s")
    if stopped_at >= duration_s:
        print(f"FAIL: stop() did not cut playback short (full duration was {duration_s:.2f}s)")
        raise SystemExit(1)
    print(f"PASS: barge-in cut off {duration_s - stopped_at:.2f}s of audio that would have played")


if __name__ == "__main__":
    asyncio.run(main())
