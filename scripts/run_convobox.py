"""The full ConvoBox loop: mic -> VAD -> STT -> Orchestrator -> backend -> TTS -> speakers.

This is the missing top of the stack: spike.py stops at the transcript
(mic front-half), and the adapters were UAT'd by injecting text into the
Orchestrator (back-half). This script is the first entrypoint that runs
the whole product loop against a real backend.

    python scripts/run_convobox.py                  # opencode on localhost:4096 (config default)
    python scripts/run_convobox.py --config convobox.yaml
    python scripts/run_convobox.py --text "run the tests"   # one utterance, no mic
    python scripts/run_convobox.py --text "..." --mute      # and no speakers

Half-duplex on purpose (UAT-mode simplification, not product doctrine):
utterances captured WHILE a response is playing are dropped -- there is
no echo cancellation yet, so an open mic would transcribe the assistant's
own voice back into the loop. The one exception is the safeword: a hard
stop is honored mid-playback, always, which is exactly the barge-in that
matters for safety. Full barge-in for ordinary speech needs echo
cancellation first (future work).

Exit with Ctrl+C. The safeword does NOT exit the app -- it hard-stops the
backend's current work and keeps listening, per the Orchestrator contract
(spike.py exits on it because spike.py has no backend to stop).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from pathlib import Path

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/run_convobox.py`) and imported as
# scripts.run_convobox (e.g. from a pytest test).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console

from convobox.adapters import create_backend_adapter
from convobox.audio.playback import AudioPlayer
from convobox.config import load_config
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.safeword.detector import SafewordDetector
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine

log = logging.getLogger("convobox.run")


class MutePlayer(AudioPlayer):
    """Synthesizes but never opens an output stream (--mute)."""

    def play(self, samples, sample_rate) -> None:  # type: ignore[no-untyped-def]
        log.info("muted playback: %d samples @ %d Hz", len(samples), sample_rate)

    def stop(self) -> None:
        pass

    def is_playing(self) -> bool:
        return False


def _resolve_device(cli_device: str | None, config_device: str | None) -> str | int | None:
    device = cli_device if cli_device is not None else config_device
    if device is not None and device.isdigit():
        return int(device)
    return device


async def run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    adapter = create_backend_adapter(config.backend)
    tts = create_tts_engine(config.tts, DEFAULT_VOICES_DIR)
    player: AudioPlayer = MutePlayer() if args.mute else AudioPlayer(
        device=config.audio.output_device
    )
    safeword = SafewordDetector(config.safeword.hard_stop_phrases)
    orchestrator = Orchestrator(adapter=adapter, safeword=safeword, tts=tts, player=player)

    log.info(
        "backend=%s  voice=%s  safeword=%r",
        config.backend.name,
        config.tts.voice,
        config.safeword.hard_stop_phrases[0],
    )

    if args.text is not None:
        # Scriptable single-shot validation: the full Orchestrator/backend/
        # TTS path with the mic taken out of the equation.
        await orchestrator.handle_transcript(args.text)
        await _drain_until_idle(adapter, timeout_s=args.timeout)
        player.wait()
        await orchestrator.stop_event_loop()
        return

    # Imported lazily so --text mode works on hosts without PortAudio.
    from convobox.audio.capture import MicrophoneStream
    from convobox.stt.transcriber import LocalTranscriber
    from convobox.vad.segmenter import UtteranceSegmenter

    transcriber = LocalTranscriber(config.stt)
    segmenter = UtteranceSegmenter(config.vad)
    device = _resolve_device(args.device, config.audio.input_device)

    log.info("listening (Ctrl+C to exit; %r hard-stops the agent)",
             config.safeword.hard_stop_phrases[0])
    with MicrophoneStream(sample_rate=config.audio.sample_rate, device=device) as mic:
        async for utterance in segmenter.segment(mic.stream()):
            result = transcriber.transcribe(utterance)
            text = result.text
            is_hard_stop = safeword.check(text) is not None

            # Safeword is checked on the raw transcript BEFORE any quality
            # gate or half-duplex drop: a hard stop must never be swallowed.
            if not is_hard_stop:
                if player.is_playing():
                    log.info("dropped (response playing, no echo cancellation): %r", text)
                    continue
                if result.language_probability < config.stt.min_language_probability:
                    log.info(
                        "dropped low-confidence transcript=%r lang=%s (%.2f < %.2f)",
                        text, result.language,
                        result.language_probability, config.stt.min_language_probability,
                    )
                    continue

            log.info(
                "transcript=%r lang=%s (%.2f) dec=%.2f busy=%s%s",
                text, result.language, result.language_probability,
                math.exp(result.avg_logprob), adapter.is_busy(),
                "  [HARD STOP]" if is_hard_stop else "",
            )
            await orchestrator.handle_transcript(text)


async def _drain_until_idle(adapter, timeout_s: float) -> None:  # type: ignore[no-untyped-def]
    """Wait until the backend finishes responding (or the timeout passes)."""
    for _ in range(int(timeout_s * 4)):
        await asyncio.sleep(0.25)
        if not adapter.is_busy():
            # One extra beat so a trailing TEXT event's TTS task gets started.
            await asyncio.sleep(0.5)
            return
    log.warning("backend still busy after %.0fs; giving up the wait", timeout_s)


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default=None, help="path to a convobox.yaml config file")
    parser.add_argument("--device", default=None, help="input device name or index")
    parser.add_argument(
        "--text", default=None,
        help="send this single utterance instead of listening on the mic",
    )
    parser.add_argument(
        "--mute", action="store_true",
        help="synthesize TTS but do not play it (scripted validation)",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0,
        help="--text mode: max seconds to wait for the backend response",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("exiting")


if __name__ == "__main__":
    main()
