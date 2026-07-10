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


def _resolve_device(cli_device: str | None, config_device: str | None) -> str | int | None:
    device = cli_device if cli_device is not None else config_device
    if device is not None and device.isdigit():
        return int(device)  # sounddevice takes a numeric device index as int, not str
    return device


async def run(config_path: str | None, cli_device: str | None) -> None:
    config = load_config(config_path)
    device = _resolve_device(cli_device, config.audio.input_device)
    transcriber = LocalTranscriber(config.stt)
    segmenter = UtteranceSegmenter(config.vad)
    safeword = SafewordDetector(config.safeword.hard_stop_phrases)

    log.info("listening (say %r to stop)", config.safeword.hard_stop_phrases[0])

    with MicrophoneStream(sample_rate=config.audio.sample_rate, device=device) as mic:
        async for utterance in segmenter.segment(mic.stream()):
            result = transcriber.transcribe(utterance)
            rtf = (result.latency_ms / 1000) / result.duration_s if result.duration_s else 0.0
            # Safeword first, on the raw transcript, before the confidence
            # gate: a hard stop must never be swallowed by a quality filter.
            if safeword.check(result.text):
                log.info("safeword detected, stopping")
                break
            if result.language_probability < config.stt.min_language_probability:
                log.info(
                    "dropped low-confidence transcript=%r lang=%s (%.2f < %.2f)",
                    result.text,
                    result.language,
                    result.language_probability,
                    config.stt.min_language_probability,
                )
                continue
            log.info(
                "transcript=%r latency_ms=%.0f duration_s=%.2f rtf=%.2f lang=%s (%.2f)",
                result.text,
                result.latency_ms,
                result.duration_s,
                rtf,
                result.language,
                result.language_probability,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="path to a convobox.yaml config file")
    parser.add_argument(
        "--device", default=None, help="input device name or index (overrides config)"
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="list audio devices and exit"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run(args.config, args.device))


if __name__ == "__main__":
    main()
