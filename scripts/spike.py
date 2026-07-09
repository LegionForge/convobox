"""Measurement spike: mic -> VAD -> local STT -> logged transcript + latency.

No backend wiring yet (see README Status) — this exists to get real accuracy
and latency numbers before committing to any orchestrator/adapter design.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from convobox.audio.capture import MicrophoneStream
from convobox.config import load_config
from convobox.safeword import SafewordDetector
from convobox.stt.transcriber import LocalTranscriber
from convobox.vad.segmenter import UtteranceSegmenter

log = logging.getLogger("convobox.spike")


async def run(config_path: str | None) -> None:
    config = load_config(config_path)
    transcriber = LocalTranscriber(config.stt)
    segmenter = UtteranceSegmenter(config.vad)
    safeword = SafewordDetector(config.safeword.hard_stop_phrases)

    log.info("listening (say %r to stop)", config.safeword.hard_stop_phrases[0])

    with MicrophoneStream(
        sample_rate=config.audio.sample_rate, device=config.audio.input_device
    ) as mic:
        async for utterance in segmenter.segment(mic.stream()):
            result = transcriber.transcribe(utterance)
            rtf = (result.latency_ms / 1000) / result.duration_s if result.duration_s else 0.0
            log.info(
                "transcript=%r latency_ms=%.0f duration_s=%.2f rtf=%.2f lang=%s (%.2f)",
                result.text,
                result.latency_ms,
                result.duration_s,
                rtf,
                result.language,
                result.language_probability,
            )
            if safeword.check(result.text):
                log.info("safeword detected, stopping")
                break


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="path to a convobox.yaml config file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
