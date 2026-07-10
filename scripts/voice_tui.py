"""Live voice-input clarity dashboard: mic -> VAD -> STT with quality verdicts.

scripts/spike.py answers "does the pipeline work"; this answers "how clearly
is it hearing me, right now". It runs the same capture/VAD/STT/safeword
pipeline but renders a live terminal dashboard instead of log lines:

- an input level meter and capture state, so mic/gain problems are visible
  before any transcript arrives;
- a per-utterance verdict derived from live-test data (language confidence
  bands, decoder confidence, real-time-factor >= 1.0 marking Whisper
  fallback re-decodes, and queue wait, which compounds under bursts of
  short utterances);
- session-level stats (fallback/empty/dropped counts, language histogram);
- a language-wander marker (~): auto-detect (the default -- STT is never
  pinned to one language unless you pass --language) is real per-utterance
  detection, so it can legitimately flip languages on a genuine switch,
  but it can also wander mid-monologue on ONE language it can't confidently
  place. LanguageTracker (see src/convobox/stt/language_tracker.py) tracks
  the session's dominant confidently-detected language purely for display
  and NEVER feeds back into what the decoder is asked to assume -- it
  exists to help you tell "real switch" from "wander" apart, not to force
  a language onto the decoder the way --language does.

Deliberately stdlib-only (ANSI escapes, no curses/rich/textual): a test
utility should not add dependencies to the project it is testing.

Exit by saying the safeword ("stop stop stop") or Ctrl+C.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from convobox.audio.capture import MicrophoneStream
from convobox.config import load_config
from convobox.safeword import SafewordDetector
from convobox.stt.language_tracker import LanguageTracker
from convobox.stt.transcriber import LocalTranscriber, TranscriptResult
from convobox.vad.segmenter import UtteranceSegmenter

# Verdict thresholds, from live Windows testing (2026-07-09, TESTING.md):
# detections >= 0.80 were consistently faithful; below ~0.40 they were
# usually hallucinations (sometimes in a different script entirely); and
# rtf >= 1.0 marks a Whisper temperature-fallback re-decode, the expensive
# path that low-content audio triggers.
_CONF_GOOD = 0.80
_CONF_FAIR = 0.40
_RTF_FALLBACK = 1.0

# Decoder-confidence bands: exp(avg_logprob) mapped against Whisper's
# conventional avg_logprob quality cutoffs (~-0.35 solid, ~-0.8 shaky,
# below that unreliable). This signal works in pinned-language mode too,
# where language_probability is hardcoded to 1.0 and says nothing.
_DEC_GOOD = 0.70
_DEC_FAIR = 0.45

# Level meter range: -60 dBFS (silence-ish) to 0 dBFS (clipping).
_METER_FLOOR_DB = -60.0

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_MAGENTA = "\x1b[35m"
_CYAN = "\x1b[36m"


@dataclass
class _Row:
    clock: str
    verdict: str
    color: str
    language: str
    probability: float
    decoder_conf: float
    latency_ms: float
    rtf: float
    queue_wait_ms: float
    text: str
    # True when this utterance's language disagrees with the session's
    # established (LanguageTracker) dominant language. Distinct from the
    # verdict: this flags "the decoder may be wandering off the language
    # you've actually been speaking" without ever changing what language
    # the decoder is asked to assume -- auto-detect stays real auto-detect.
    # Always False when a language is pinned (nothing to track).
    wander: bool = False


@dataclass
class _State:
    started: float
    model: str
    language: str
    gate: float
    device_label: str
    level_db: float = _METER_FLOOR_DB
    peak_db: float = _METER_FLOOR_DB
    capturing: bool = False
    queued: int = 0
    transcribing: bool = False
    rows: list[_Row] = field(default_factory=list)
    total: int = 0
    empty: int = 0
    dropped: int = 0
    fallbacks: int = 0
    latency_sum: float = 0.0
    latency_max: float = 0.0
    languages: Counter[str] = field(default_factory=Counter)
    stopping: str | None = None
    dominant_language: str | None = None
    wanders: int = 0


def _verdict(
    result: TranscriptResult, rtf: float, gated: bool, pinned: bool
) -> tuple[str, str]:
    if gated:
        return "DROP", _MAGENTA
    if not result.text:
        return "EMPTY", _DIM
    # Two independent signals, verdict takes the worse: decoder confidence
    # (always meaningful) and detected-language probability (meaningless
    # when the language is pinned, so skipped entirely in that mode; a pin
    # makes faster-whisper report exactly 1.0).
    decoder_conf = math.exp(result.avg_logprob)
    good = decoder_conf >= _DEC_GOOD and rtf < _RTF_FALLBACK
    fair = decoder_conf >= _DEC_FAIR
    if not pinned:
        good = good and result.language_probability >= _CONF_GOOD
        fair = fair and result.language_probability >= _CONF_FAIR
    if good:
        return "GOOD", _GREEN
    if fair:
        return "FAIR", _YELLOW
    return "POOR", _RED


def _meter(db: float, peak: float, width: int) -> str:
    span = -_METER_FLOOR_DB
    filled = max(0, min(width, round((db - _METER_FLOOR_DB) / span * width)))
    peak_pos = max(0, min(width - 1, round((peak - _METER_FLOOR_DB) / span * width)))
    bar = ["#" if i < filled else ("|" if i == peak_pos else "-") for i in range(width)]
    return "".join(bar)


def _draw(state: _State) -> None:
    cols = shutil.get_terminal_size().columns
    elapsed = int(time.monotonic() - state.started)
    if state.stopping is not None:
        status = f"{_RED}{_BOLD}* {state.stopping}{_RESET}"
    elif state.transcribing or state.queued:
        status = f"{_YELLOW}* TRANSCRIBING{_RESET} (queue {state.queued})"
    elif state.capturing:
        status = f"{_GREEN}{_BOLD}* CAPTURING{_RESET}"
    else:
        status = f"{_CYAN}* LISTENING{_RESET}"

    dominant = f" session-lang={state.dominant_language}" if state.dominant_language else ""
    lines = [
        f"{_BOLD}ConvoBox voice clarity monitor{_RESET}   "
        f"model={state.model} lang={state.language} gate={state.gate:.2f} "
        f"device={state.device_label}{dominant}",
        f"state: {status}   level [{_meter(state.level_db, state.peak_db, 24)}] "
        f"{state.level_db:5.0f} dB   elapsed {elapsed // 60:02d}:{elapsed % 60:02d}",
        "-" * min(cols, 100),
    ]

    for row in state.rows[-10:]:
        text_width = max(10, cols - 66)
        text = row.text if len(row.text) <= text_width else row.text[: text_width - 1] + "~"
        mark = f"{_MAGENTA}~{_RESET}" if row.wander else " "
        lines.append(
            f"{row.clock}  {row.color}{row.verdict:<5}{_RESET}{mark} "
            f"{row.language} {row.probability:.2f}  dec {row.decoder_conf:.2f}  "
            f"{row.latency_ms:5.0f}ms  rtf {row.rtf:4.2f}  q {row.queue_wait_ms:4.0f}ms  {text}"
        )
    if not state.rows:
        lines.append(f"{_DIM}(no utterances yet: speak into the mic){_RESET}")

    lines.append("-" * min(cols, 100))
    avg = state.latency_sum / state.total if state.total else 0.0
    top = "  ".join(f"{lang} x{n}" for lang, n in state.languages.most_common(3))
    lines.append(
        f"utterances {state.total} ({state.empty} empty, {state.dropped} dropped, "
        f"{state.wanders} {_MAGENTA}~{_RESET} wander)  "
        f"avg {avg:.0f}ms  max {state.latency_max:.0f}ms  "
        f"fallbacks(rtf>={_RTF_FALLBACK:.0f}) {state.fallbacks}  langs: {top or '-'}"
    )
    lines.append(
        f"{_DIM}say the safeword to exit (default: 'stop stop stop')   "
        f"{_MAGENTA}~{_RESET}{_DIM} = broke from session-lang (real switch, or decoder wander){_RESET}"
    )

    frame = "\x1b[H" + "\x1b[K\n".join(line[: cols + 32] for line in lines) + "\x1b[K\x1b[J"
    sys.stdout.write(frame)
    sys.stdout.flush()


async def run(config_path: str | None, cli_device: str | None,
              cli_language: str | None, cli_gate: float | None) -> _State:
    config = load_config(config_path)
    stt_config = config.stt.model_copy(
        update={
            k: v
            for k, v in (
                ("language", cli_language),
                ("min_language_probability", cli_gate),
            )
            if v is not None
        }
    )
    device: str | int | None = cli_device if cli_device is not None else config.audio.input_device
    if isinstance(device, str) and device.isdigit():
        device = int(device)

    print("loading models (first run downloads them)...", flush=True)
    transcriber = LocalTranscriber(stt_config)
    segmenter = UtteranceSegmenter(config.vad)
    safeword = SafewordDetector(config.safeword.hard_stop_phrases)
    # Purely observational (see LanguageTracker docstring): never fed back
    # into transcribe(), so it cannot force or bias what the decoder tries.
    # Meaningless (and skipped) when a language is pinned -- there is only
    # ever one language to be dominant, so "wander" can't mean anything.
    language_tracker = LanguageTracker() if stt_config.language is None else None

    state = _State(
        started=time.monotonic(),
        model=stt_config.model,
        language=stt_config.language or "auto",
        gate=stt_config.min_language_probability,
        device_label=str(device) if device is not None else "default",
    )
    # (utterance, enqueue time) or None to unblock the worker at shutdown.
    work: asyncio.Queue[tuple[np.ndarray, float] | None] = asyncio.Queue()
    stop = asyncio.Event()

    mic = MicrophoneStream(sample_rate=config.audio.sample_rate, device=device)
    mic.start()

    async def capture() -> None:
        async for chunk in mic.stream():
            rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
            db = 20.0 * math.log10(rms) if rms > 0 else _METER_FLOOR_DB
            state.level_db = max(_METER_FLOOR_DB, min(0.0, db))
            # Peak-hold with a slow decay so brief spikes stay readable.
            state.peak_db = max(state.level_db, state.peak_db - 0.5)
            for utterance in segmenter.feed(chunk):
                work.put_nowait((utterance, time.monotonic()))
                state.queued = work.qsize()
            state.capturing = segmenter.in_speech
        work.put_nowait(None)

    async def stt_worker() -> None:
        while True:
            item = await work.get()
            if item is None:
                return
            utterance, enqueued = item
            state.queued = work.qsize()
            queue_wait_ms = (time.monotonic() - enqueued) * 1000.0
            state.transcribing = True
            # to_thread keeps the render loop animating during the blocking
            # decode; a single worker keeps utterances in order and the
            # (not thread-safe) model accessed serially.
            result = await asyncio.to_thread(transcriber.transcribe, utterance)
            state.transcribing = False

            rtf = (result.latency_ms / 1000.0) / result.duration_s if result.duration_s else 0.0
            # Safeword first, on the raw transcript, before the confidence
            # gate: a quality filter must never swallow a hard stop.
            stopped = safeword.check(result.text) is not None
            gated = (
                not stopped
                and result.language_probability < stt_config.min_language_probability
            )
            verdict, color = _verdict(result, rtf, gated, stt_config.language is not None)

            state.total += 1
            state.latency_sum += result.latency_ms
            state.latency_max = max(state.latency_max, result.latency_ms)
            if not result.text:
                state.empty += 1
            if gated:
                state.dropped += 1
            if rtf >= _RTF_FALLBACK:
                state.fallbacks += 1
            state.languages[result.language] += 1

            wander = False
            if language_tracker is not None and result.text:
                wander = not language_tracker.agrees(result.language)
                language_tracker.observe(result.language, result.language_probability)
                state.dominant_language = language_tracker.dominant
                if wander:
                    state.wanders += 1

            state.rows.append(
                _Row(
                    clock=time.strftime("%H:%M:%S"),
                    verdict=verdict,
                    color=color,
                    language=result.language,
                    probability=result.language_probability,
                    decoder_conf=math.exp(result.avg_logprob),
                    latency_ms=result.latency_ms,
                    rtf=rtf,
                    queue_wait_ms=queue_wait_ms,
                    text=result.text or "(no speech recognized)",
                    wander=wander,
                )
            )
            if stopped:
                state.stopping = "SAFEWORD: stopping"
                stop.set()
                return

    async def render() -> None:
        while not stop.is_set():
            _draw(state)
            await asyncio.sleep(0.1)

    os.system("")  # enables ANSI escape processing in legacy Windows consoles
    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alt screen, hide cursor
    try:
        worker_task = asyncio.create_task(stt_worker())
        capture_task = asyncio.create_task(capture())
        render_task = asyncio.create_task(render())
        await worker_task
        stop.set()
        mic.close()
        await capture_task
        render_task.cancel()
        _draw(state)
        await asyncio.sleep(0.5)  # let the final frame (safeword banner) register
    finally:
        stop.set()
        mic.close()
        sys.stdout.write("\x1b[?25h\x1b[?1049l")  # restore cursor + main screen
        sys.stdout.flush()
    return state


def _summary(state: _State) -> None:
    avg = state.latency_sum / state.total if state.total else 0.0
    langs = "  ".join(f"{lang} x{n}" for lang, n in state.languages.most_common())
    print("session summary")
    print(f"  utterances : {state.total} ({state.empty} empty, {state.dropped} dropped)")
    print(f"  latency    : avg {avg:.0f}ms, max {state.latency_max:.0f}ms")
    print(f"  fallbacks  : {state.fallbacks} (rtf >= {_RTF_FALLBACK:.0f})")
    print(f"  languages  : {langs or '-'}")
    if state.dominant_language is not None:
        print(f"  session-lang: {state.dominant_language}  ({state.wanders} utterance(s) broke from it)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="path to a convobox.yaml config file")
    parser.add_argument(
        "--device", default=None, help="input device name or index (overrides config)"
    )
    parser.add_argument(
        "--language",
        default=None,
        help=(
            "pin the STT language (e.g. 'en'); overrides config, disables "
            "auto-detect. Default is auto-detect (recommended for "
            "multilingual use) -- pinning forces every utterance to decode "
            "AS that language, which can mangle non-matching speech into "
            "false-confident nonsense rather than leaving it unrecognized. "
            "See the language-wander (~) marker for a non-forcing way to "
            "spot decoder confusion while staying on auto-detect."
        ),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="drop transcripts below this detected-language probability (overrides config)",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="list audio devices and exit"
    )
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    try:
        state = asyncio.run(run(args.config, args.device, args.language, args.min_confidence))
    except KeyboardInterrupt:
        # The alt-screen/cursor restore in run()'s finally already ran (or the
        # loop never started); nothing was captured worth summarizing reliably.
        print("interrupted")
        return
    _summary(state)


if __name__ == "__main__":
    main()
