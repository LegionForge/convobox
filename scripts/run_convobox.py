"""The full ConvoBox loop: mic -> VAD -> STT -> Orchestrator -> backend -> TTS -> speakers.

This is the missing top of the stack: spike.py stops at the transcript
(mic front-half), and the adapters were UAT'd by injecting text into the
Orchestrator (back-half). This script is the first entrypoint that runs
the whole product loop against a real backend.

    python scripts/run_convobox.py                  # opencode on localhost:4096 (config default)
    python scripts/run_convobox.py --config convobox.yaml
    python scripts/run_convobox.py --text "run the tests"   # one utterance, no mic
    python scripts/run_convobox.py --text "..." --mute      # and no speakers

Echo handling is layered, and how much duplex you get depends on config:

1. Overlap gate (always on): utterances whose audio OVERLAPPED a playing
   response are dropped. Overlap, not "is playing right now" -- the VAD
   only emits an utterance after its trailing silence, so echo of the
   response usually arrives just AFTER playback ended (confirmed in the
   first same-room UAT), and a naive is_playing() check misses it.
2. Text-level echo filter (always on): a transcript whose tokens mostly
   match what we just spoke is treated as echo and dropped (catches echo
   that slips past the timing window through long reverb / delayed devices).
3. Signal-level AEC (opt-in via audio.echo_cancellation): a WebRTC echo
   canceller fed the playback as a far-end reference. When on, it removes
   the assistant's voice from the mic signal so full-duplex barge-in is
   safe; without it the open mic would transcribe the assistant back.

Barge-in for ordinary speech (interaction.interrupt_mode = stop_audio /
abort_turn) requires echo_cancellation on -- otherwise the assistant's own
voice would interrupt it. The safeword is the exception: a hard stop is
honored mid-playback ALWAYS, regardless of AEC, which is the barge-in that
matters for safety. See docs/DESIGN-echo-and-barge-in.md.

Exit with Ctrl+C. The safeword does NOT exit the app -- it hard-stops the
backend's current work and keeps listening, per the Orchestrator contract
(spike.py exits on it because spike.py has no backend to stop).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import re
import socket
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np

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
from convobox.tts.base import TTSEngine
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine

log = logging.getLogger("convobox.run")


# Utterances that started up to this long after playback ended still count
# as overlapping it: room reverb plus VAD/timestamp slop.
ECHO_GRACE_S = 0.3

# Cross-process mutex for mic mode: an arbitrary fixed localhost port held
# for the process lifetime. A socket bind (unlike a lockfile) can't go
# stale -- the OS releases it the instant the holder dies, however it dies.
SINGLE_INSTANCE_PORT = 47613


def acquire_single_instance_lock(port: int = SINGLE_INSTANCE_PORT) -> socket.socket | None:
    """Try to become THE listening instance; None means someone already is.

    Mic mode refuses to start a second logical instance (mic contention,
    split conversation -- docs/UAT-checklist.md [O1]; note the corrected
    process-counting guidance there). The port is injectable so tests can
    exercise exclusivity on a throwaway port -- the default port is
    legitimately held whenever a real ConvoBox is listening on this
    machine, which is exactly when the dev suite tends to be running.
    """
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", port))
    except OSError:
        lock.close()
        return None
    return lock

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _norm_tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(text)}


# Below this echo-to-ambient headroom there's effectively no echo present
# to cancel -- reading attenuation as "success" here is meaningless.
AEC_MEASURABLE_ECHO_DB = 3.0


def interpret_aec_stats(attenuation_db: float | None, ceiling_db: float | None) -> str:
    """One-line verdict tag for an AEC stats line.

    Three real cases, learned the hard way in live UAT where the old
    two-way logic screamed "success" at total silence:

    - ceiling near zero: no echo reached the mic AT ALL. The usual cause
      is that no audio is reaching the room (dead/muted/misrouted output
      device) -- NOT successful cancellation. This is the case that would
      have flagged the silent-endpoint problem immediately.
    - positive ceiling, attenuation near it: genuine floor-limited
      success -- echo cancelled down to the room's own noise.
    - positive ceiling, attenuation well below it: AEC underperforming;
      real echo headroom remains (usually a wrong delay hint).
    """
    if attenuation_db is None or ceiling_db is None:
        return ""
    if ceiling_db < AEC_MEASURABLE_ECHO_DB:
        return (
            "  [NO ECHO DETECTED: barely any speaker sound is reaching the mic -- "
            "check the output device is audible; this is NOT a cancellation result]"
        )
    if attenuation_db >= ceiling_db - 2.0:
        return "  [FLOOR-LIMITED: echo cancelled down to room noise -- success]"
    return (
        f"  [UNDER-CANCELLING: ~{ceiling_db - attenuation_db:.1f}dB of echo headroom "
        "remains -- try tuning aec_delay_ms]"
    )


# Prefixed to a barge-in utterance so the backend knows its previous
# response wasn't fully heard (we can't edit backend session history the
# way realtime APIs truncate theirs -- see docs/DESIGN-echo-and-barge-in.md,
# "the truncation problem"). Wording provisional pending barge-in UAT.
BARGE_IN_MARKER = "(I interrupted your spoken response midway) "


class BargeInMonitor:
    """Decides when sustained user speech during playback should barge in.

    Pure state machine so the decision is unit-testable: feed it the
    VAD's in_speech flag and whether audio is playing, once per mic
    chunk; it returns True exactly once per sustained-speech episode
    that crosses the threshold while a response is playing. The
    threshold is what keeps a cough or chair creak from killing a
    response (see docs/DESIGN-echo-and-barge-in.md).
    """

    def __init__(self, mode: str, min_speech_ms: int) -> None:
        self.mode = mode
        self._min_speech_ms = min_speech_ms
        self._run_ms = 0.0
        self._fired = False

    def observe(self, in_speech: bool, playing: bool, chunk_ms: float) -> bool:
        if self.mode == "none":
            return False
        if not in_speech:
            # Speech episode ended: reset for the next one.
            self._run_ms = 0.0
            self._fired = False
            return False
        if not playing:
            # Speech with nothing playing is just... talking. Track
            # nothing; there is no response to interrupt. (Speech that
            # STARTED during playback and outlives it already fired.)
            self._run_ms = 0.0
            return False
        self._run_ms += chunk_ms
        if self._run_ms >= self._min_speech_ms and not self._fired:
            self._fired = True
            return True
        return False


class WorkingIndicator:
    """Decides when to remind the user the backend is still working.

    When the backend is busy but nothing is playing -- it's thinking, or
    grinding on a long tool call (a file write, a build) -- the user gets
    zero feedback and can't tell "working" from "broken". Observed live:
    a philosophy.md append left the loop silently busy for minutes while
    the user repeatedly asked "did I break something?". This emits a
    heartbeat after an initial quiet grace, then at a steady interval,
    and resets the moment audio plays or the backend goes idle.

    Pure state machine (like BargeInMonitor) so the timing is unit
    testable without real clocks.
    """

    def __init__(self, first_notice_s: float = 6.0, repeat_s: float = 12.0) -> None:
        self._first_notice_s = first_notice_s
        self._repeat_s = repeat_s
        self._silent_busy_s = 0.0
        self._next_notice_at = first_notice_s

    def observe(self, busy: bool, playing: bool, dt_s: float) -> float | None:
        """Advance by dt_s; return elapsed silent-busy seconds when a
        heartbeat is due, else None.

        "Silent busy" = backend busy AND nothing playing. Playing audio is
        its own feedback, so it resets the timer -- the heartbeat only
        covers the feedback gap.
        """
        if not busy or playing:
            self._silent_busy_s = 0.0
            self._next_notice_at = self._first_notice_s
            return None
        self._silent_busy_s += dt_s
        if self._silent_busy_s >= self._next_notice_at:
            self._next_notice_at += self._repeat_s
            return self._silent_busy_s
        return None


class SpokenEchoFilter:
    """Text-level echo suppression: does a transcript match our own speech?

    ConvoBox knows exactly what its TTS just said -- an advantage no
    generic echo canceller has. If most of a transcript's words appear in
    a recently spoken response, the mic almost certainly heard US, not
    the user, no matter when the utterance landed (this backstops the
    playback-overlap window against long reverb, delayed audio devices,
    and estimate slop). Token overlap rather than exact match because STT
    garbles echo: it hears a lossy far-field copy of the response.

    Deliberately NOT applied to transcripts under MIN_TOKENS words: a
    short genuine reply like "yes" or "run it" has a decent chance of
    appearing verbatim inside a long response, and swallowing a real
    user confirmation is worse than passing a scrap of echo through.
    (Short echo scraps are mostly caught by the overlap window anyway.)
    This is stage 1 of echo handling -- signal-level cancellation (true
    barge-in) is future work.
    """

    MIN_TOKENS = 3
    OVERLAP_THRESHOLD = 0.7
    MAX_AGE_S = 30.0

    def __init__(self) -> None:
        self._spoken: deque[tuple[float, set[str]]] = deque(maxlen=8)

    def note_spoken(self, text: str, now: float | None = None) -> None:
        tokens = _norm_tokens(text)
        if tokens:
            self._spoken.append((time.monotonic() if now is None else now, tokens))

    def is_echo(self, transcript: str, now: float | None = None) -> bool:
        tokens = _norm_tokens(transcript)
        if len(tokens) < self.MIN_TOKENS:
            return False
        now = time.monotonic() if now is None else now
        for spoken_at, spoken_tokens in self._spoken:
            if now - spoken_at > self.MAX_AGE_S:
                continue
            overlap = len(tokens & spoken_tokens) / len(tokens)
            if overlap >= self.OVERLAP_THRESHOLD:
                return True
        return False


class SpokenTextRecorder(TTSEngine):
    """Transparent TTSEngine wrapper that tells the echo filter what was said.

    Wraps the engine handed to the Orchestrator, so the text recorded is
    exactly the text spoken (post strip_code_for_speech), with no second
    integration point in the orchestrator itself.
    """

    def __init__(self, inner: TTSEngine, echo_filter: SpokenEchoFilter) -> None:
        self._inner = inner
        self._filter = echo_filter

    @property
    def sample_rate(self) -> int:
        return self._inner.sample_rate

    def synthesize_stream(self, text: str) -> AsyncIterator[np.ndarray]:
        self._filter.note_spoken(text)
        return self._inner.synthesize_stream(text)

    async def synthesize(self, text: str) -> np.ndarray:
        self._filter.note_spoken(text)
        return await self._inner.synthesize(text)

    def stop(self) -> None:
        self._inner.stop()

    def is_speaking(self) -> bool:
        return self._inner.is_speaking()


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

    async def play_stream(self, chunks, sample_rate) -> None:  # type: ignore[no-untyped-def]
        # Streaming playback's end is a moving target: each arriving chunk
        # extends the estimate. max(estimate, now) restarts the clock after
        # a synthesis stall (playback caught up and went silent, so the
        # next chunk plays from "now", not from the stale estimate).
        async def tracked():  # type: ignore[no-untyped-def]
            async for chunk in chunks:
                base = max(self.playback_ended_at, time.monotonic())
                self.playback_ended_at = base + len(chunk) / sample_rate
                yield chunk

        await super().play_stream(tracked(), sample_rate)

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

    async def play_stream(self, chunks, sample_rate) -> None:  # type: ignore[no-untyped-def]
        first_at: float | None = None
        total = 0
        started = time.monotonic()
        async for chunk in chunks:
            if first_at is None:
                first_at = time.monotonic() - started
                log.info("muted stream: first audio chunk after %.2fs", first_at)
            total += len(chunk)
        log.info("muted stream: %d samples total @ %d Hz", total, sample_rate)

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


async def _working_watchdog(adapter, player, indicator: WorkingIndicator) -> None:  # type: ignore[no-untyped-def]
    """Heartbeat: remind the user a silently-busy backend is still alive.

    Runs for the process lifetime; asyncio.run() cancels and awaits it on
    shutdown, so no explicit teardown is needed here.
    """
    interval = 1.0
    while True:
        await asyncio.sleep(interval)
        elapsed = indicator.observe(adapter.is_busy(), player.is_playing(), interval)
        if elapsed is not None:
            log.info(
                "backend still working (%.0fs, no audio yet) -- thinking or running a "
                "tool; say the safeword to abort",
                elapsed,
            )


async def run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    adapter = create_backend_adapter(config.backend)
    echo_filter = SpokenEchoFilter()
    tts = SpokenTextRecorder(create_tts_engine(config.tts, DEFAULT_VOICES_DIR), echo_filter)
    player: EchoAwarePlayer = MutePlayer() if args.mute else EchoAwarePlayer(
        device=config.audio.output_device
    )
    safeword = SafewordDetector(config.safeword.hard_stop_phrases)
    orchestrator = Orchestrator(adapter=adapter, safeword=safeword, tts=tts, player=player)

    log.info(
        "backend=%s  voice=%s  safeword=%r  pid=%d",
        config.backend.name,
        config.tts.voice,
        config.safeword.hard_stop_phrases[0],
        os.getpid(),
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
    from convobox.stt.factory import create_stt_engine
    from convobox.vad.segmenter import UtteranceSegmenter

    # Guarded BEFORE the heavyweight setup: the second instance should
    # fail in milliseconds, not after loading Whisper. Only mic mode is
    # guarded -- a one-shot --text run alongside a listening instance is
    # legitimate (it touches no microphone).
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        log.error(
            "another run_convobox.py is already listening (instance lock "
            "127.0.0.1:%d is held). Two LOGICAL instances contend for the "
            "mic and split the conversation. NOTE when checking processes: "
            "on Windows uv venvs, ONE instance always shows as TWO python "
            "processes (launcher parent + interpreter child) -- count by "
            "ParentProcessId, see docs/UAT-checklist.md [O1].",
            SINGLE_INSTANCE_PORT,
        )
        raise SystemExit(2)
    log.info("single-instance lock acquired (pid=%d)", os.getpid())

    transcriber = create_stt_engine(config.stt)
    segmenter = UtteranceSegmenter(config.vad)
    device = _resolve_device(args.device, config.audio.input_device)

    canceller = None
    mic_holder: dict[str, object] = {}
    if config.audio.echo_cancellation:
        from convobox.audio.aec import EchoCanceller

        canceller = EchoCanceller(delay_ms=config.audio.aec_delay_ms)
        # A wrong delay hint is the #1 cause of weak in-room suppression
        # (F1 in the 2026-07-11 UAT): Windows host APIs commonly buffer
        # 100-500ms of output, dwarfing any fixed guess. Once both streams
        # report their real latencies, estimate the true render-to-capture
        # delay and apply it -- unless the user explicitly configured
        # aec_delay_ms, in which case respect it but still LOG the
        # estimate so UAT learns the right number.
        delay_explicit = "aec_delay_ms" in config.audio.model_fields_set
        delay_estimated = False

        def _feed_reference(block, sample_rate) -> None:  # type: ignore[no-untyped-def]
            nonlocal delay_estimated
            if not delay_estimated:
                out_lat = player.output_latency_s
                mic = mic_holder.get("mic")
                in_lat = getattr(mic, "input_latency_s", None)
                if out_lat is not None and in_lat is not None:
                    # +10ms for acoustics (a few meters) and framing slop.
                    estimate = int((float(out_lat) + float(in_lat)) * 1000) + 10
                    delay_estimated = True
                    if delay_explicit and estimate != canceller.delay_ms:
                        log.info(
                            "AEC delay: measured stream latencies suggest ~%dms "
                            "(out %.0fms + in %.0fms + 10ms); keeping configured %dms "
                            "-- consider updating aec_delay_ms or removing it to auto-tune",
                            estimate, float(out_lat) * 1000, float(in_lat) * 1000,
                            canceller.delay_ms,
                        )
                    elif not delay_explicit:
                        canceller.set_delay(estimate)
                        log.info(
                            "AEC delay auto-estimated: %dms (out %.0fms + in %.0fms + 10ms)",
                            estimate, float(out_lat) * 1000, float(in_lat) * 1000,
                        )
            canceller.feed_reverse(block, sample_rate)

        player.on_block_played = _feed_reference
        log.info(
            "acoustic echo cancellation ON (delay hint %dms%s)",
            config.audio.aec_delay_ms,
            " explicit" if delay_explicit else ", will auto-estimate from stream latencies",
        )

    monitor = BargeInMonitor(
        config.interaction.interrupt_mode, config.interaction.barge_in_min_speech_ms
    )
    if monitor.mode != "none":
        if canceller is None:
            # Not refused outright -- headphones make it legitimate -- but
            # with open speakers and no AEC the assistant's own voice trips
            # the VAD and it interrupts itself. Loud, specific warning.
            log.warning(
                "interrupt_mode=%r without audio.echo_cancellation: with open "
                "speakers the assistant will interrupt ITSELF. Only safe with "
                "headphones. See docs/DESIGN-echo-and-barge-in.md.",
                monitor.mode,
            )
        log.info(
            "barge-in ON (%s after %dms of sustained speech)",
            monitor.mode, config.interaction.barge_in_min_speech_ms,
        )
    barge_in_pending = False

    async def _mic_chunks(mic: MicrophoneStream):  # type: ignore[no-untyped-def]
        nonlocal barge_in_pending
        was_playing = False
        async for chunk in mic.stream():
            processed = canceller.process(chunk) if canceller is not None else chunk
            playing = player.is_playing()
            if canceller is not None and was_playing and not playing:
                # Response just finished: one stats line per response.
                # CRITICAL interpretation note (learned from a night of
                # perfectly-executed clean-window runs that looked like
                # failure): the reading is CEILING-LIMITED by how far the
                # echo rises above room ambience at the mic. Room noise
                # isn't in the reference and can't be cancelled, so
                # attenuation ~= ceiling means the canceller removed
                # essentially everything measurable -- success, not a
                # weak filter.
                attenuation = canceller.attenuation_db()
                ceiling = canceller.measurable_ceiling_db()
                log.info(
                    "AEC stats for last response: attenuation=%s of ~%s measurable  "
                    "delay=%dms  frames(reverse=%d, capture=%d)%s",
                    f"{attenuation:.1f}dB" if attenuation is not None else "n/a",
                    f"{ceiling:.1f}dB" if ceiling is not None else "?",
                    canceller.delay_ms, canceller.reverse_frames, canceller.capture_frames,
                    interpret_aec_stats(attenuation, ceiling),
                )
                canceller.reset_stats()
            was_playing = playing
            # in_speech reflects the segmenter's state as of the PREVIOUS
            # chunk (this one hasn't been fed yet) -- one chunk (~32ms) of
            # lag, irrelevant against the sustained-speech threshold.
            chunk_ms = 1000 * len(chunk) / config.audio.sample_rate
            if monitor.observe(segmenter.in_speech, playing, chunk_ms):
                log.info("barge-in: sustained speech during playback -- stopping audio")
                player.stop()
                tts.stop()
                if monitor.mode == "abort_turn":
                    await adapter.send_hard_stop()
                barge_in_pending = True
            yield processed

    log.info("listening (Ctrl+C to exit; %r hard-stops the agent)",
             config.safeword.hard_stop_phrases[0])

    # Heartbeat so a silently-busy backend (thinking, or grinding on a long
    # tool call) reads as "working", not "hung" -- the exact confusion a
    # philosophy.md write caused in live UAT.
    asyncio.create_task(_working_watchdog(adapter, player, WorkingIndicator()))

    with MicrophoneStream(sample_rate=config.audio.sample_rate, device=device) as mic:
        mic_holder["mic"] = mic  # lets the AEC delay estimator read input latency
        async for utterance in segmenter.segment(_mic_chunks(mic)):
            result = transcriber.transcribe(utterance)
            text = result.text
            is_hard_stop = safeword.check(text) is not None
            barged_in, barge_in_pending = barge_in_pending, False

            # Safeword is checked on the raw transcript BEFORE any quality
            # gate or half-duplex drop: a hard stop must never be swallowed.
            if not is_hard_stop:
                # A barge-in utterance overlapped playback BY DEFINITION --
                # that's the user exercising their right to interrupt, not
                # echo -- so the overlap gate steps aside for it. The
                # spoken-text echo check below still applies: if what
                # "interrupted" us matches our own words, it was self-echo
                # that tripped the barge-in (AEC not converged / headphones
                # assumption violated) and must not be forwarded.
                if not barged_in and (
                    player.is_playing()
                    or utterance_overlapped_playback(
                        now=time.monotonic(),
                        duration_s=result.duration_s,
                        stt_latency_ms=result.latency_ms,
                        min_silence_ms=config.vad.min_silence_ms,
                        playback_ended_at=player.playback_ended_at,
                    )
                ):
                    # Reports the REAL canceller state, not a hardcoded
                    # "no echo cancellation" -- UAT needs to know whether
                    # AEC was actually in the path when an echo leaked.
                    aec_state = "echo-cancellation active" if canceller is not None else "no echo cancellation"
                    log.info(
                        "dropped (overlap gate, %s): %r", aec_state, text
                    )
                    continue
                if echo_filter.is_echo(text):
                    if barged_in:
                        log.warning(
                            "dropped (spoken-echo filter, barge-in was our own echo): %r", text
                        )
                    else:
                        log.info("dropped (spoken-echo filter, matches ConvoBox's own recent speech): %r", text)
                    continue
                if result.language_probability < config.stt.min_language_probability:
                    log.info(
                        "dropped low-confidence transcript=%r lang=%s (%.2f < %.2f)",
                        text, result.language,
                        result.language_probability, config.stt.min_language_probability,
                    )
                    continue

            log.info(
                "transcript=%r lang=%s (%.2f) dec=%.2f busy=%s%s%s",
                text, result.language, result.language_probability,
                math.exp(result.avg_logprob), adapter.is_busy(),
                "  [HARD STOP]" if is_hard_stop else "",
                "  [BARGE-IN]" if barged_in and not is_hard_stop else "",
            )
            if barged_in and not is_hard_stop:
                # The backend believes its whole response was delivered; it
                # wasn't. The marker is our version of realtime APIs'
                # history truncation (docs: "the truncation problem").
                text = BARGE_IN_MARKER + text
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
