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
utterances whose audio OVERLAPPED a playing response are dropped -- there
is no echo cancellation yet, so an open mic transcribes the assistant's
own voice back into the loop. Overlap, not "is playing right now": the
VAD only emits an utterance after its trailing silence, so echo of the
response usually arrives just AFTER playback ended (confirmed in the
first same-room UAT), and a naive is_playing() check misses it. The one
exception is the safeword: a hard stop is honored mid-playback, always,
which is exactly the barge-in that matters for safety. Full barge-in for
ordinary speech needs echo cancellation first (future work).

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
import time
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


# Utterances that started up to this long after playback ended still count
# as overlapping it: room reverb plus VAD/timestamp slop.
ECHO_GRACE_S = 0.3


class EchoAwarePlayer(AudioPlayer):
    """AudioPlayer that remembers when its playback ends.

    The loop needs "did this utterance's audio overlap a response?", and
    AudioPlayer's own thread offers no end-of-playback hook. The end time
    is estimated up front from the sample count (playback is realtime) and
    clamped to now by stop(), which is exact for the case that matters
    (a hard stop cutting playback short).
    """

    def __init__(self, device: str | int | None = None) -> None:
        super().__init__(device)
        self.playback_ended_at = 0.0  # time.monotonic() scale; 0 = never played

    def play(self, samples, sample_rate) -> None:  # type: ignore[no-untyped-def]
        # Estimate set AFTER super().play(): AudioPlayer.play() begins by
        # calling self.stop() to replace any current playback, and that
        # lands in our stop() override, which would clamp a
        # freshly-written estimate straight back down to "now".
        super().play(samples, sample_rate)
        self.playback_ended_at = time.monotonic() + len(samples) / sample_rate

    def stop(self) -> None:
        super().stop()
        # If stopped mid-playback the estimate is in the future; the real
        # end is now. Never pushes the timestamp later.
        self.playback_ended_at = min(self.playback_ended_at, time.monotonic())


class MutePlayer(EchoAwarePlayer):
    """Synthesizes but never opens an output stream (--mute).

    Produces no sound, therefore no echo: playback_ended_at stays 0 and
    nothing gets dropped for overlap in --mute runs.
    """

    def play(self, samples, sample_rate) -> None:  # type: ignore[no-untyped-def]
        log.info("muted playback: %d samples @ %d Hz", len(samples), sample_rate)

    def stop(self) -> None:
        pass

    def is_playing(self) -> bool:
        return False


def utterance_overlapped_playback(
    now: float,
    duration_s: float,
    stt_latency_ms: float,
    min_silence_ms: int,
    playback_ended_at: float,
    grace_s: float = ECHO_GRACE_S,
) -> bool:
    """Did an utterance's audio overlap the response that was playing?

    Works backwards from transcript-arrival time to when the utterance's
    audio actually began: now, minus the time STT spent transcribing,
    minus the trailing silence the VAD waited for before emitting, minus
    the utterance's own duration. If that start predates the end of
    playback (plus grace for reverb/slop), the mic was hearing the
    response for at least part of it.
    """
    capture_started_at = now - stt_latency_ms / 1000 - min_silence_ms / 1000 - duration_s
    return capture_started_at < playback_ended_at + grace_s


def _resolve_device(cli_device: str | None, config_device: str | None) -> str | int | None:
    device = cli_device if cli_device is not None else config_device
    if device is not None and device.isdigit():
        return int(device)
    return device


async def run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    adapter = create_backend_adapter(config.backend)
    tts = create_tts_engine(config.tts, DEFAULT_VOICES_DIR)
    player: EchoAwarePlayer = MutePlayer() if args.mute else EchoAwarePlayer(
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
                if player.is_playing() or utterance_overlapped_playback(
                    now=time.monotonic(),
                    duration_s=result.duration_s,
                    stt_latency_ms=result.latency_ms,
                    min_silence_ms=config.vad.min_silence_ms,
                    playback_ended_at=player.playback_ended_at,
                ):
                    log.info(
                        "dropped (overlapped response playback, no echo cancellation): %r",
                        text,
                    )
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
