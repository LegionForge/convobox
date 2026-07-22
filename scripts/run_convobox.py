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

Barge-in for ordinary speech (interaction.interrupt_preset = "conversational" /
"halt" / "take-over") requires echo_cancellation on -- otherwise the
assistant's own voice would interrupt it. The safeword is the exception: a
hard stop is honored mid-playback ALWAYS, regardless of AEC, which is the
barge-in that matters for safety. See docs/DESIGN-echo-and-barge-in.md and
docs/DESIGN-barge-in.md (the two-axis preset grid).

Exit with Ctrl+C. The safeword does NOT exit the app -- it hard-stops the
backend's current work and keeps listening, per the Orchestrator contract
(spike.py exits on it because spike.py has no backend to stop).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import logging
import math
import os
import re
import shutil
import socket
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/run_convobox.py`) and imported as
# scripts.run_convobox (e.g. from a pytest test).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console

from convobox.adapters import create_backend_adapter
from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.approval import ApprovalDetector
from convobox.audio.playback import AudioPlayer
from convobox.config import load_config, resolve_config_path, write_aec_estimate
from convobox.interrupt_presets import resolve_preset
from convobox.listening_pause import PauseListeningDetector
from convobox.orchestrator.orchestrator import Orchestrator, strip_code_for_speech
from convobox.response_tiering import ContinueDetector
from convobox.safeword.detector import SafewordDetector
from convobox.stt.corrections import TranscriptCorrector
from convobox.tts.base import TTSEngine
from convobox.tts.factory import DEFAULT_VOICES_DIR, create_tts_engine
from convobox.tui import ConversationTuiState, render_conversation_frame
from convobox.resumeword import ResumeWordDetector

log = logging.getLogger("convobox.run")

_TUI_LOG_FILE = "convobox-tui.log"

# Utterances that started up to this long after playback ended still count
# as overlapping it: room reverb plus VAD/timestamp slop.
ECHO_GRACE_S = 0.3

# Bounds for grace_s_for_last_response()'s extension: how much extra grace
# an UNDER-CANCELLING verdict earns, per dB of remaining echo headroom, and
# the hard ceiling on the total window regardless of how bad the reading
# was. NOT live-tuned -- derived from the [E8] incident log's own numbers
# (headroom commonly 8-14dB during a bad mic+speaker session, which this
# maps to roughly 0.4-0.7s of extra grace) but the exact constants need a
# real UAT pass on real hardware to confirm; see docs/UAT-checklist.md.
_GRACE_EXTENSION_PER_DB = 0.05
_MAX_GRACE_S = 1.0

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


def token_overlap_ratio(transcript: str, response: str) -> float:
    """Share of transcript tokens that occur in the response text."""
    transcript_tokens = _norm_tokens(transcript)
    response_tokens = _norm_tokens(response)
    if not transcript_tokens or not response_tokens:
        return 0.0
    return len(transcript_tokens & response_tokens) / len(transcript_tokens)


def _echo_match_suffix(transcript: str, last_response: str) -> str:
    overlap = token_overlap_ratio(transcript, last_response)
    if overlap == 0.0:
        return "[echo-match: 0.00 -- does NOT match what was playing; possible real speech]"
    return f"[echo-match: {overlap:.2f} of tokens in last response]"


@dataclass
class LastSpokenResponse:
    """Shared observer state; the mic loop must only inspect the latest response."""

    text: str = ""


# Backchannels/continuers -- "mm-hmm", "yeah", "right" -- signal "I'm
# listening, keep going," the OPPOSITE of a bid for the floor (Schegloff
# 1982; Ward & Tsukahara 2000; see docs/CONVERSATION-DESIGN-REFERENCES.md).
# A speech-triggered barge-in must not fire on them (docs/DESIGN-barge-in.md,
# "Backchannel filtering"). Deliberately small and auditable, like the
# detector classes' phrase lists -- not exhaustive, covers the common
# English continuers.
_BACKCHANNEL_TOKENS: frozenset[str] = frozenset(
    {
        "mm", "mhm", "mmhmm", "uh", "huh", "uhhuh", "hmm",
        "yeah", "yep", "yup", "right", "oh", "ok", "okay",
        "sure", "wow", "really", "gotcha",
    }
)

# Short acknowledgment PHRASES -- same continuer role as _BACKCHANNEL_TOKENS
# above, but multi-word, so a single-token subset check can't catch them.
# Live-confirmed, 2026-07-20 (UAT session .aec-dumps/20260720-205724 +
# convobox-tui.log): "Okay, get it.", "Thank you very much." and similar
# short acknowledgments were each missing exactly one word from
# _BACKCHANNEL_TOKENS ("get"/"it", "thank"/"you"/"very"/"much"), so the
# whole-utterance subset check failed and interrupt_preset=conversational
# treated them as full barge-ins -- the user's own live description was
# "I'm waiting for your playback to finish, but it never does." Matched
# by EXACT token-set equality (not subset, unlike _BACKCHANNEL_TOKENS)
# specifically so adding a phrase here can't accidentally swallow real
# commands that merely happen to contain one of these words (see
# is_backchannel's docstring for why "yeah, but stop the deploy" must
# stay a real interrupt). Deliberately small and auditable, same spirit
# as _BACKCHANNEL_TOKENS -- not exhaustive.
_BACKCHANNEL_PHRASES: frozenset[frozenset[str]] = frozenset(
    frozenset(_norm_tokens(phrase))
    for phrase in (
        "get it", "got it", "ok get it", "okay get it", "ok got it", "okay got it",
        "thank you", "thanks", "thank you very much", "thanks a lot", "thanks so much",
        "no problem", "you're welcome", "sounds good", "got you", "understood",
        "makes sense", "fair enough", "good point", "noted", "will do", "roger that",
    )
)


def is_backchannel(text: str) -> bool:
    """True when `text` is made up ENTIRELY of backchannel/continuer tokens,
    or is an EXACT match (as a whole utterance) for one of the short
    acknowledgment phrases in _BACKCHANNEL_PHRASES.

    Whole-utterance classification, not phrase matching: "yeah, but stop
    the deploy" is NOT a backchannel (real content beyond the continuer),
    but "yeah" or "okay, right" alone is. Empty transcripts are not
    backchannels -- other gates already drop empty text; this only
    classifies utterances that actually said something.
    """
    tokens = _norm_tokens(text)
    if not tokens:
        return False
    if tokens.issubset(_BACKCHANNEL_TOKENS):
        return True
    return tokens in _BACKCHANNEL_PHRASES


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


def grace_s_for_last_response(
    attenuation_db: float | None, ceiling_db: float | None, base_grace_s: float = ECHO_GRACE_S
) -> float:
    """How long to protect the overlap gate's window after the NEXT
    playback ends, given the AEC verdict from the response that JUST
    finished (see interpret_aec_stats -- same thresholds, so a reading
    that logs FLOOR-LIMITED here also returns base_grace_s here).

    A response that came back FLOOR-LIMITED or with no measurable echo at
    all (NO ECHO DETECTED) gives no reason to extend past the base grace
    window -- nothing suggests residual echo is likely to leak through as
    apparent "new speech" in the tail right after playback. A response
    that came back UNDER-CANCELLING means real, uncancelled echo energy
    was present; extends proportionally to the remaining headroom,
    capped at _MAX_GRACE_S so a single bad reading can't suppress
    listening for an unbounded stretch. This is a bounded, well-reasoned
    default derived directly from the [E8] incident's own log data, NOT
    a live-tuned value -- see the module-level constants' own comment.
    """
    if attenuation_db is None or ceiling_db is None or ceiling_db < AEC_MEASURABLE_ECHO_DB:
        return base_grace_s
    headroom_db = ceiling_db - attenuation_db
    if headroom_db <= 2.0:  # matches interpret_aec_stats's FLOOR-LIMITED threshold
        return base_grace_s
    return min(base_grace_s + headroom_db * _GRACE_EXTENSION_PER_DB, _MAX_GRACE_S)


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

    Governs Axis 1 only (docs/DESIGN-barge-in.md's two-axis grid) --
    "what happens to the assistant's CURRENT turn". ``on_current_turn``
    is one of ``interrupt_presets.OnCurrentTurn``'s three values,
    resolved by the caller from the configured preset
    (``resolve_preset(config.interaction.interrupt_preset).on_current_turn``),
    not a raw string the caller invents. Axis 2 ("what happens to the
    user's NEW words" -- drop/queue/now) is a separate concern handled
    by the main loop + ``QueuedInterjection``, not this class -- the two
    axes fire at different pipeline stages (this one at the raw-audio
    level pre-STT, the other post-STT once there's a transcript to act
    on).
    """

    def __init__(self, on_current_turn: str, min_speech_ms: int) -> None:
        self.on_current_turn = on_current_turn
        self._min_speech_ms = min_speech_ms
        self._run_ms = 0.0
        self._fired = False

    def observe(self, in_speech: bool, playing: bool, chunk_ms: float) -> bool:
        if self.on_current_turn == "let-finish":
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


class QueuedInterjection:
    """Axis 2's "queue" behavior (docs/DESIGN-barge-in.md's two-axis grid,
    the ``patient`` preset: let-finish + queue) -- "it finishes, then does
    your thing".

    Pure state machine, like BargeInMonitor, so the hold/flush logic is
    unit-testable without a real mic loop. A single pending slot, not a
    list: a second queued utterance while one is already pending REPLACES
    it (most-recent-wins) rather than stacking multiple deliveries for one
    flush, since ``Orchestrator.handle_transcript`` takes one transcript
    and delivering several back-to-back the instant the backend goes idle
    would make only the first a fresh turn and turn the rest into
    interjects on THAT new turn -- not the "queued and delivered once
    idle" behavior this exists to provide.
    """

    def __init__(self) -> None:
        self._pending: str | None = None

    def offer(self, text: str) -> None:
        if self._pending is not None:
            log.info(
                "queued interjection replaced by a newer one (most-recent-wins): "
                "%r -> %r",
                self._pending, text,
            )
        self._pending = text

    def flush_if_idle(self, busy: bool, playing: bool) -> str | None:
        """Call once per loop tick; returns (and clears) the pending
        utterance once the backend is fully idle (not busy AND nothing
        playing -- "finished", not just "no longer generating text" with a
        TTS tail still running), else None."""
        if self._pending is not None and not busy and not playing:
            text, self._pending = self._pending, None
            return text
        return None


class RecognitionErrorLadder:
    """Tracks consecutive no-input/no-match recognition failures and
    reports an escalation tier, per Google's Conversation Design error-
    escalation guidance (docs/CONVERSATION-DESIGN-REFERENCES.md, section 6,
    read from the real live docs pages 2026-07-14): "Users should
    experience no more than 3 No Input or No Match errors in a row, after
    which your Action should play the appropriate max error prompt and
    exit."

    "No input" = an utterance whose transcript is empty/whitespace-only --
    STT heard nothing recognizable (live-confirmed in UAT 2026-07-09: a
    VAD trigger on ambient/background audio yielded transcript=''). "No
    match" = a non-empty transcript dropped by the
    ``stt.min_language_probability`` gate -- STT heard something but
    wasn't confident what. Any utterance that clears both checks resets
    the streak to zero, regardless of what a LATER gate (overlap/echo/
    backchannel/queue) does with it -- those all imply STT already
    succeeded with confidence, a different concern from this ladder.

    Pure counter, no side effects, same family as BargeInMonitor/
    ListeningGate -- unit-testable independent of the mic loop. Currently
    surfaced as a log marker only (``[ERROR-LADDER: tier N]``); this is
    deliberately NOT wired to speak a reprompt or change any gate's
    behavior -- what to say at each tier (or whether to say anything at
    all) is a real product decision, same caution as
    ``UtteranceSegmenter.was_forced``'s log-only wiring (PR #69).
    """

    def __init__(self, max_tier: int = 3) -> None:
        if max_tier < 1:
            raise ValueError(f"max_tier must be >= 1, got {max_tier}")
        self.max_tier = max_tier
        self._consecutive = 0

    @property
    def tier(self) -> int:
        """0 = no active streak. 1..max_tier = the current streak's
        escalation tier, capped at max_tier (matching Google's "play the
        max error prompt" plateau rather than growing unbounded)."""
        return min(self._consecutive, self.max_tier)

    def observe_failure(self) -> int:
        """Call on a no-input or no-match event. Returns the new tier."""
        self._consecutive += 1
        return self.tier

    def reset(self) -> None:
        """Call on any utterance that cleared both the no-input and
        no-match checks."""
        self._consecutive = 0


class ListeningGate:
    """Tracks the paused (resume-word-only) listening state (docs/DESIGN-barge-in.md,
    "Pause/resume listening").

    Pure state machine, like BargeInMonitor, so it's unit-testable independent
    of the mic loop. Call observe() once per transcript that the SAFEWORD DID
    NOT match -- the caller checks the safeword first, unconditionally, outside
    this class, because pause state and hard-stop are orthogonal axes (a
    paused session must still be hard-stoppable; the safeword's own check
    already runs regardless of anything this class does).
    """

    def __init__(self, pause_detector: PauseListeningDetector, wake_detector: ResumeWordDetector) -> None:
        self._pause_detector = pause_detector
        self._wake_detector = wake_detector
        self.is_paused = False

    def observe(self, transcript: str) -> Literal["resume", "drop", "pause", "pass"]:
        if self.is_paused:
            if self._wake_detector.check(transcript):
                self.is_paused = False
                return "resume"
            return "drop"
        if self._pause_detector.check(transcript) is not None:
            self.is_paused = True
            return "pause"
        return "pass"


class ContinuePromptGate:
    """Tracks whether ConvoBox is waiting for a continue/decline reply
    after a tiered response (docs/DESIGN-0.3.0-interaction-and-safety.md,
    Phase 2's "silence-timeout-implies-no").

    Pure state machine, like BargeInMonitor/ListeningGate, so the wait/
    timeout logic is unit-testable independent of the mic loop and real
    clocks. Two separate call sites, matching the two ways the wait can
    end: `observe_transcript()` per heard utterance while waiting (an
    explicit reply -- continue, decline, or the user just said something
    else instead of answering), and `observe_timeout()` on the same 1s
    poll tick BargeInMonitor/QueuedInterjection/the TUI status already
    share (silence -- no utterance arrived at all).
    """

    def __init__(self, detector: ContinueDetector, timeout_s: float) -> None:
        self._detector = detector
        self.timeout_s = timeout_s
        self._waiting_since: float | None = None

    @property
    def is_waiting(self) -> bool:
        return self._waiting_since is not None

    def start_waiting(self, now: float) -> None:
        self._waiting_since = now

    def observe_timeout(self, now: float) -> bool:
        """Call once per watchdog tick. Returns True exactly once, the
        tick the wait silently expires (implied decline) -- the caller
        doesn't need to do anything further; there's nothing to speak and
        nothing to forward, unlike observe_transcript()'s "pass" outcome.
        """
        if self._waiting_since is None:
            return False
        if now - self._waiting_since >= self.timeout_s:
            self._waiting_since = None
            return True
        return False

    def observe_transcript(self, transcript: str) -> Literal["continue", "decline", "pass"]:
        """Call for every utterance while is_waiting (caller checks
        is_waiting first; calling this while not waiting is a caller bug,
        not handled specially here). ANY utterance ends the wait, matched
        or not -- unlike ListeningGate's paused state, which drops
        everything until the resume word, a non-continue/decline reply here
        means the user moved on to a new topic, not that they're still
        mid-answer, so "pass" tells the caller to forward it normally
        rather than dropping it.
        """
        outcome = self._detector.check(transcript)
        self._waiting_since = None
        if outcome == "continue":
            return "continue"
        if outcome == "decline":
            return "decline"
        return "pass"


class ApprovalPromptGate:
    """Tracks whether ConvoBox is waiting for an approve/deny/discuss reply
    to a pending destructive-action approval request
    (docs/DESIGN-0.3.0-interaction-and-safety.md, Phase 3).

    Pure state machine, same family as BargeInMonitor/ListeningGate/
    ContinuePromptGate -- unit-testable independent of the mic loop, real
    clocks, and (critically for this one) a real backend connection. NOT
    wired to codex.py's approval dispatch yet; this is the gate half of
    Phase 3 built ahead of that wiring, matching this session's established
    "primitive first" pacing (see ContinuePromptGate's own history).

    Deliberately shaped differently from ContinuePromptGate in three ways,
    because approvals are the high-stakes tier and continue/decline is not:

    1. `observe_timeout()` returns `"deny"` (an explicit outcome the caller
       MUST act on -- forward a decline to the pending backend request) on
       the tick it fires, not a silent `True`/nothing-to-do like
       ContinuePromptGate's. "Silence on an approval prompt must never be
       treated as consent, only as still waiting or an explicit
       timeout-implies-decline" -- the design doc's central safety
       invariant for this phase.
    2. `observe_transcript()` does NOT end the wait for "discuss" -- an
       approval prompt must stay open and answerable across a clarifying
       exchange (confirmed live against a real codex app-server, see the
       design doc's Phase 3 section: a 20s delay plus an interleaved
       unrelated request didn't invalidate a pending approval). A discuss
       reply also resets the waiting clock (`now` is re-recorded), so an
       ongoing back-and-forth can't be cut off mid-conversation by the
       timeout that exists to catch genuine silence, not genuine
       engagement.
    3. There is no "pass" outcome. `ApprovalDetector.check()` never falls
       through to normal command routing the way `ContinueDetector` does --
       every non-empty utterance while a decision is pending is
       approve/deny/discuss, by construction.
    """

    def __init__(self, detector: ApprovalDetector, timeout_s: float) -> None:
        self._detector = detector
        self.timeout_s = timeout_s
        self._waiting_since: float | None = None

    @property
    def is_waiting(self) -> bool:
        return self._waiting_since is not None

    def start_waiting(self, now: float) -> None:
        self._waiting_since = now

    def observe_timeout(self, now: float) -> Literal["deny"] | None:
        """Call once per watchdog tick. Returns "deny" exactly once, the
        tick the wait silently expires -- the caller must forward an
        explicit decline to the pending approval request, this is not a
        no-op like ContinuePromptGate's timeout.
        """
        if self._waiting_since is None:
            return None
        if now - self._waiting_since >= self.timeout_s:
            self._waiting_since = None
            return "deny"
        return None

    def observe_transcript(
        self, transcript: str, now: float
    ) -> Literal["approve", "deny", "discuss"] | None:
        """Call for every utterance while is_waiting (caller checks
        is_waiting first). "approve"/"deny" end the wait. "discuss" keeps
        waiting and resets the clock (see class docstring, point 2). An
        utterance that normalizes to nothing (pure noise/silence artifact)
        returns None and changes nothing -- still waiting, clock unchanged.
        """
        outcome = self._detector.check(transcript)
        if outcome == "approve":
            self._waiting_since = None
            return "approve"
        if outcome == "deny":
            self._waiting_since = None
            return "deny"
        if outcome == "discuss":
            self._waiting_since = now
            return "discuss"
        return None


# Heartbeat color thresholds, live-validated (JP, 2026-07-14/15 headset
# UAT): a real UX gap surfaced there -- the "backend still working" line
# is the only feedback during a silent-busy stretch, but when the user is
# interacting through a backend's own chat UI (not watching this
# terminal), that feedback is effectively invisible, so a long stall
# reads as "is it broken?" rather than "still thinking." Color makes the
# SAME log line readable at a glance without tailing it: green = just
# started, yellow = grinding a while, red = long stall worth a look.
_HEARTBEAT_GREEN_MAX_S = 10.0
_HEARTBEAT_YELLOW_MAX_S = 60.0
_ANSI_GREEN = "\x1b[32m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_RED = "\x1b[31m"
_ANSI_RESET = "\x1b[0m"


def _heartbeat_color(elapsed_s: float) -> str:
    """ANSI color for a heartbeat line's elapsed-seconds value.

    Pure function (no I/O), so the threshold boundaries are unit-testable
    without a real terminal.
    """
    if elapsed_s < _HEARTBEAT_GREEN_MAX_S:
        return _ANSI_GREEN
    if elapsed_s < _HEARTBEAT_YELLOW_MAX_S:
        return _ANSI_YELLOW
    return _ANSI_RED


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

    @property
    def silent_busy_s(self) -> float:
        """The continuous elapsed silent-busy time, updated every observe()
        call -- unlike observe()'s own return value, which is None except
        on the sparse notification ticks. For a continuously-redrawn
        consumer (the TUI's heartbeat indicator) that needs a live number
        every frame, not just at first_notice_s/repeat_s intervals."""
        return self._silent_busy_s

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


def _resolve_convobox_version() -> str:
    """Best-effort package version for the startup announcement.

    Falls back to "dev" rather than raising -- a source checkout without
    installed metadata (e.g. a fresh clone before `uv sync`/`pip install
    -e .` has registered the package) must never crash startup over a
    cosmetic version string.
    """
    try:
        return importlib.metadata.version("convobox")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def startup_announcement(version: str) -> str:
    """The spoken "I'm ready" line, once STT/TTS/backend setup is done.

    Exists because the FIRST utterance being silently discarded (root
    cause: cuBLAS delay-loading on the first real transcribe() call,
    fixed at its source in LocalTranscriber._warm_up) still left no
    signal for the user that ConvoBox was actually ready to hear them --
    "say something and see if it works" isn't a great first experience.
    A pure function (not inlined at the call site) so the exact wording
    is unit-testable without a real TTS/audio stack.
    """
    return f"LegionForge ConvoBox, version {version}, ready and standing by."


async def _working_watchdog(  # type: ignore[no-untyped-def]
    adapter, player, indicator: WorkingIndicator, orchestrator, interject_queue: QueuedInterjection,
    segmenter=None, listening_gate=None, tui_state: ConversationTuiState | None = None,
    continue_gate: ContinuePromptGate | None = None,
    approval_gate: ApprovalPromptGate | None = None,
) -> None:
    """Heartbeat: remind the user a silently-busy backend is still alive.
    Also flushes any Axis-2 ``queue``-preset interjection once the backend
    is fully idle (docs/DESIGN-barge-in.md's "patient" preset), derives
    the TUI's status label when a TUI is active, and starts/expires the
    response-tiering continue-prompt wait -- all sharing one 1s poll
    rather than each growing its own timer: unrelated jobs piggybacking
    on the SAME tick as the original heartbeat, not one job growing new
    responsibilities. The status label and continue-wait start are both
    poll-based rather than threaded through every call site in the main
    loop because of a real constraint, not laziness: transcriber.transcribe()
    blocks the event loop synchronously today (no asyncio.to_thread
    offload), so nothing can react DURING that decode regardless of
    whether it's poll- or event-driven; polling is the lower-risk
    mechanism either way.

    Runs for the process lifetime; asyncio.run() cancels and awaits it on
    shutdown, so no explicit teardown is needed here.
    """
    # Color only for a REAL, unpiped terminal -- not just "not --tui".
    # `--tui` mode redirects logging to a FileHandler (_TUI_LOG_FILE),
    # which doesn't change the real sys.stderr fd, so isatty() alone
    # can't detect that case; checking tui_state too covers it. isatty()
    # also correctly disables color for JP's own UAT crib's own
    # `2>&1 | Tee-Object -Append uat-echo.log` pattern -- piping makes
    # stderr not a tty, so the same check that enables color on a real
    # terminal also keeps the diffable log file clean, with no separate
    # "am I being redirected" logic needed.
    use_color = tui_state is None and sys.stderr.isatty()
    if use_color:
        # Same VT-mode-enable idiom as the --tui branch below / voice_tui.py
        # / settings_tui.py -- os.system("") with a hardcoded empty-string
        # literal enables ANSI/VT100 escape processing in legacy Windows
        # console hosts; it never executes a program.
        os.system("")  # nosec B605 B607
    interval = 1.0
    was_playing = False
    while True:
        await asyncio.sleep(interval)
        busy, playing = adapter.is_busy(), player.is_playing()
        elapsed = indicator.observe(busy, playing, interval)
        if tui_state is not None:
            # Continuous, unlike observe()'s own sparse return value --
            # the TUI redraws every 0.1s (_tui_render_loop) and needs a
            # live number every frame, not just at first_notice_s/repeat_s.
            tui_state.heartbeat_elapsed_s = indicator.silent_busy_s if (busy and not playing) else None
        if elapsed is not None:
            color = _heartbeat_color(elapsed) if use_color else ""
            reset = _ANSI_RESET if color else ""
            log.info(
                "%sbackend still working (%.0fs, no audio yet) -- thinking or "
                "running a tool; say the safeword to abort%s",
                color, elapsed, reset,
            )
        queued_text = interject_queue.flush_if_idle(busy, playing)
        if queued_text is not None:
            log.info("delivering queued interjection now that the turn is idle: %r", queued_text)
            try:
                await orchestrator.handle_transcript(queued_text)
            except Exception as exc:  # noqa: BLE001
                # Same defensive policy as the main loop's handle_transcript
                # call: one delivery failure must not kill the watchdog (and
                # with it, the heartbeat + all future queue flushes).
                log.error(
                    "couldn't deliver queued interjection to the backend (%s: %s)",
                    type(exc).__name__, exc,
                )
        if continue_gate is not None:
            now = time.monotonic()
            # The response JUST finished speaking (not "isn't playing" on
            # every idle tick -- that would restart the wait indefinitely).
            # Only worth a wait if there's actually something held back;
            # a response that said everything already has nothing to
            # prompt for.
            if was_playing and not playing and orchestrator.has_more_to_reveal():
                continue_gate.start_waiting(now)
                log.info(
                    "say 'continue'/'go on' within %.1fs for more, or just carry on -- "
                    "silence means done",
                    continue_gate.timeout_s,
                )
            if continue_gate.observe_timeout(now):
                log.debug("continue-prompt window expired with no reply -- assuming done")
        if approval_gate is not None:
            now = time.monotonic()
            if approval_gate.observe_timeout(now) == "deny":
                # Silence on a pending approval must never be treated as
                # consent (ApprovalPromptGate's own central invariant) --
                # the timeout itself IS the explicit decline to forward.
                log.info("approval prompt window expired with no reply -- denying")
                await orchestrator.resolve_pending_approval(False)
        was_playing = playing
        if tui_state is not None:
            if listening_gate is not None and listening_gate.is_paused:
                tui_state.status = "paused"
            elif playing:
                tui_state.status = "speaking"
            elif busy:
                tui_state.status = "working"
            elif segmenter is not None and segmenter.in_speech:
                tui_state.status = "capturing"
            else:
                tui_state.status = "listening"


def _on_backend_event(
    tui_state: ConversationTuiState | None,
    last_spoken_response: LastSpokenResponse,
    event: BackendEvent,
    approval_gate: ApprovalPromptGate | None = None,
) -> None:
    """Orchestrator's on_event hook (PR #55): feeds the TUI's transcript
    and full-detail panes from the real backend event stream, AND records
    the assistant's response to the UAT/echo log. Only TEXT is handled --
    TOOL_CALL/TOOL_RESULT visibility is future work, not dropped silently
    forever, just out of this pass's scope (matches the design doc's
    "deliberately minimal, built to be extended" phase-1 mandate).

    full_detail accumulates WITHIN one turn (a backend can emit more than
    one agentMessage per turn -- e.g. reasoning-then-answer) and is reset
    by the caller when a new user utterance starts a fresh turn, so it
    always reflects "the current response," not a running transcript of
    the whole session (that's what the transcript pane is for).

    Logging the response text (not just the spoken form) is what makes a
    UAT session replayable: the live mic loop used to forward assistant
    TEXT straight to TTS and capture it nowhere, so the agent's replies --
    the most insightful lines for audio UAT -- were invisible in the log.
    We log the raw content AND the stripped-for-speech form so a reader can
    compare what the backend said against what was actually spoken aloud
    (e.g. catch markdown read-out bugs like Piper saying "asterisk
    asterisk"). tui_state may be None (non-TUI UAT mode), in which case we
    only log.

    APPROVAL_REQUEST starts approval_gate's wait right here, the instant
    the event arrives -- not on some later poll tick the way
    continue_gate's wait starts (that one is tied to playback finishing,
    which this isn't). Orchestrator itself does the TTS announcement (see
    its own _on_event) -- this hook's job is only the gate bookkeeping,
    same division of labor as everything else it does here (TUI/logging,
    not speech).
    """
    if event.type == BackendEventType.APPROVAL_REQUEST:
        if approval_gate is not None:
            approval_gate.start_waiting(time.monotonic())
        return
    if event.type != BackendEventType.TEXT or not event.content:
        return
    log.info("response: %s", event.content)
    spoken = strip_code_for_speech(event.content)
    last_spoken_response.text = spoken
    if spoken and spoken != event.content:
        log.info("response(spoken): %s", spoken)
    if tui_state is not None:
        tui_state.add_turn("assistant", event.content)
        tui_state.full_detail = (
            f"{tui_state.full_detail}\n\n{event.content}"
            if tui_state.full_detail
            else event.content
        )


def _draw_conversation_tui(tui_state: ConversationTuiState) -> None:
    cols, rows = shutil.get_terminal_size()
    lines = render_conversation_frame(tui_state, cols, rows, time.monotonic())
    frame = "\x1b[H" + "\x1b[K\n".join(lines) + "\x1b[K\x1b[J"
    sys.stdout.write(frame)
    sys.stdout.flush()


_TUI_SCROLL_PAGE = 10  # lines per PgUp/PgDn -- roughly one screenful at typical sizes


def _handle_tui_key(tui_state: ConversationTuiState, key: str) -> None:
    """Apply one decoded key to the conversation TUI's scroll state.

    Pure state mutation (no I/O), same split as everything else in
    convobox.tui -- render.py clamps whatever offset lands here against
    the CURRENT frame's line count, so this function never needs to know
    pane heights or how much history exists; it just nudges a counter.
    """
    if key == "TAB":
        tui_state.focus_pane = "transcript" if tui_state.focus_pane == "detail" else "detail"
        return
    attr = "transcript_scroll" if tui_state.focus_pane == "transcript" else "detail_scroll"
    if key == "HOME":
        setattr(tui_state, attr, 1_000_000)  # clamped to the real max at render time
    elif key == "END":
        setattr(tui_state, attr, 0)
    else:
        delta = {"UP": 1, "DOWN": -1, "PGUP": _TUI_SCROLL_PAGE, "PGDN": -_TUI_SCROLL_PAGE}.get(key)
        if delta is not None:
            setattr(tui_state, attr, max(0, getattr(tui_state, attr) + delta))


# Windows scan codes for the "\x00"/"\xe0"-prefixed extended keys msvcrt.getwch()
# returns (conio.h getch() table): Home=71 'G', Up=72 'H', PgUp=73 'I',
# Left=75 'K', Right=77 'M', End=79 'O', Down=80 'P', PgDn=81 'Q'. Only the
# ones the conversation TUI actually binds are mapped -- Left/Right are
# reserved (unused today) rather than silently mapped to something odd.
_WIN_EXTENDED_KEYS = {"H": "UP", "P": "DOWN", "I": "PGUP", "Q": "PGDN", "G": "HOME", "O": "END"}

# POSIX CSI final-byte / tilde-code mappings for the same key set. Arrow
# keys are a single final byte after "ESC ["; PgUp/PgDn/Home/End on most
# real terminals (xterm, gnome-terminal, iTerm2, Windows Terminal's own
# POSIX-style reporting under WSL) are "ESC [ <digits> ~" instead --
# confirmed against the VT/xterm control-sequence conventions, not
# assumed, since this is exactly the class of "looks obviously right"
# escape-sequence claim that has burned this project before (see
# docs/DESIGN-echo-and-barge-in.md's PortAudio constraint-name mistake).
_POSIX_CSI_FINAL = {"A": "UP", "B": "DOWN", "H": "HOME", "F": "END"}
_POSIX_CSI_TILDE = {"5": "PGUP", "6": "PGDN"}


def _read_pending_key() -> str | None:
    """Non-blocking single-keypress read for the conversation TUI's scroll
    controls. Returns None immediately when no key is waiting -- safe to
    poll once per frame from _tui_render_loop without ever blocking the
    event loop (unlike scripts/settings_tui.py's read_key(), a BLOCKING
    read used by that script's synchronous, non-asyncio main loop; kept
    as a separate function rather than shared, since the two have
    incompatible blocking contracts).

    Assumes the terminal is already in raw/cbreak mode on POSIX for the
    whole --tui session (set once in main(), restored in its finally
    block) -- this function only polls and reads, it never changes
    terminal modes itself, so it can be called every 0.1s cheaply.
    """
    if sys.platform == "win32":
        import msvcrt

        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return _WIN_EXTENDED_KEYS.get(code)
        return "TAB" if ch == "\t" else None

    import select

    fd = sys.stdin.fileno()
    if not select.select([fd], [], [], 0)[0]:
        return None
    ch = sys.stdin.read(1)
    if ch == "\t":
        return "TAB"
    if ch != "\x1b":
        return None
    # Drain the rest of the escape sequence. A bare ESC keypress (no
    # follow-up bytes) times out the first select() below and is dropped
    # silently -- the conversation TUI has no ESC-bound action today.
    if not select.select([fd], [], [], 0.05)[0]:
        return None
    if sys.stdin.read(1) != "[":
        return None
    if not select.select([fd], [], [], 0.05)[0]:
        return None
    code = sys.stdin.read(1)
    if code in _POSIX_CSI_FINAL:
        return _POSIX_CSI_FINAL[code]
    if not code.isdigit():
        return None
    digits = code
    while select.select([fd], [], [], 0.05)[0]:
        nxt = sys.stdin.read(1)
        if nxt == "~":
            break
        digits += nxt
    return _POSIX_CSI_TILDE.get(digits)


async def _tui_render_loop(tui_state: ConversationTuiState) -> None:
    while True:
        key = _read_pending_key()
        if key is not None:
            _handle_tui_key(tui_state, key)
        _draw_conversation_tui(tui_state)
        await asyncio.sleep(0.1)


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _check_backend_working_dir(backend: object) -> None:
    """Validate backend.working_dir and warn about the dangerous cases.

    A coding-agent backend WRITES files in its working directory, so the
    default (unset -> the agent inherits ConvoBox's own cwd) can let a voice
    session silently edit ConvoBox's source. This raises on a nonexistent
    directory and warns loudly when the working dir is ConvoBox's own repo,
    is unset for a subprocess backend, or is set for opencode (no effect).
    See docs/DESIGN-backend-sandboxing.md.
    """
    name = getattr(backend, "name", "")
    working_dir = getattr(backend, "working_dir", None)
    if name == "opencode":
        if working_dir:
            log.warning(
                "backend.working_dir=%r has NO effect on the opencode backend -- "
                "opencode's directory is set by wherever `opencode serve` was "
                "launched. Start the server from your workspace instead.",
                working_dir,
            )
        return
    if working_dir is None:
        log.warning(
            "backend.working_dir is unset: the %s agent will run in ConvoBox's "
            "own directory and can modify its source. Set backend.working_dir "
            "(or pass --working-dir) to an isolated workspace. See "
            "docs/DESIGN-backend-sandboxing.md.",
            name,
        )
        return
    resolved = Path(working_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise SystemExit(
            f"backend.working_dir {working_dir!r} is not an existing directory"
        )
    if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
        log.warning(
            "backend.working_dir %r is inside ConvoBox's own source tree -- the "
            "agent can modify the product's code. Point it at a separate "
            "workspace.",
            str(resolved),
        )


async def run(args: argparse.Namespace) -> None:
    config_path = resolve_config_path(args.config)
    config = load_config(args.config)
    if args.working_dir is not None:
        config.backend.working_dir = args.working_dir
    _check_backend_working_dir(config.backend)
    # Phase 3 (docs/DESIGN-0.3.0-interaction-and-safety.md): voice-gated
    # tool approval. Off unless the operator deliberately set a phrase --
    # see InteractionConfig.approval_phrase's own field comment for why
    # there is no safe default. Currently only claude-code honors
    # interactive_approval (see create_backend_adapter); other backends
    # ignore it, same "not every adapter can do everything" stance as the
    # rest of this feature.
    approval_detector = (
        ApprovalDetector(config.interaction.approval_phrase)
        if config.interaction.approval_phrase
        else None
    )
    approval_gate = (
        ApprovalPromptGate(approval_detector, config.interaction.approval_timeout_s)
        if approval_detector is not None
        else None
    )
    adapter = create_backend_adapter(
        config.backend, interactive_approval=approval_detector is not None
    )
    echo_filter = SpokenEchoFilter()
    tts = SpokenTextRecorder(create_tts_engine(config.tts, DEFAULT_VOICES_DIR), echo_filter)
    player: EchoAwarePlayer = MutePlayer() if args.mute else EchoAwarePlayer(
        device=config.audio.output_device
    )
    safeword = SafewordDetector(config.safeword.hard_stop_phrases)
    transcript_corrector = TranscriptCorrector(config.stt.corrections)
    # --tui only applies to the live mic loop, not --text mode (which
    # returns before the mic loop even exists, below) -- constructed here
    # regardless so it's in scope for the Orchestrator on_event wiring,
    # but never entered into alt-screen or rendered unless the mic loop
    # actually runs. The on_event hook is wired UNCONDITIONALLY (even with
    # tui_state=None) so the assistant's responses are recorded to the log
    # in every mode -- without it, a plain listening/UAT session forwarded
    # replies straight to TTS and captured them nowhere, leaving the most
    # useful lines of an audio UAT invisible in the log.
    tui_state = ConversationTuiState() if (args.tui and args.text is None) else None
    last_spoken_response = LastSpokenResponse()
    if tui_state is not None:
        tui_state.backend_name = config.backend.name
        tui_state.aec_enabled = config.audio.echo_cancellation
        tui_state.aec_dump_active = args.aec_dump is not None and config.audio.echo_cancellation
    orchestrator = Orchestrator(
        adapter=adapter,
        safeword=safeword,
        tts=tts,
        player=player,
        on_event=lambda e: _on_backend_event(tui_state, last_spoken_response, e, approval_gate),
        tier_responses=config.interaction.tier_responses,
    )
    continue_gate = ContinuePromptGate(ContinueDetector(), config.interaction.continue_timeout_s)
    error_ladder = RecognitionErrorLadder()

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
        text = transcript_corrector.correct(args.text)
        if text != args.text:
            log.info("corrected transcript before command routing: %r -> %r", args.text, text)
        await orchestrator.handle_transcript(text)
        await _drain_until_idle(adapter, timeout_s=args.timeout)
        player.wait()
        await orchestrator.stop_event_loop()
        await adapter.aclose()
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
    aec_dump = None
    mic_holder: dict[str, object] = {}
    if args.aec_dump is not None and not config.audio.echo_cancellation:
        log.warning(
            "--aec-dump has no effect: audio.echo_cancellation is off, "
            "so there is no reference/capture stream to record"
        )
    if config.audio.echo_cancellation:
        from convobox.audio.aec import AecDumpWriter, EchoCanceller

        if args.aec_dump is not None:
            dump_root = Path(args.aec_dump) if args.aec_dump else Path(".aec-dumps")
            dump_dir = dump_root / time.strftime("%Y%m%d-%H%M%S")
            aec_dump = AecDumpWriter(dump_dir)
            log.info(
                "AEC dump ON -- recording reference.wav / mic-raw.wav / "
                "mic-processed.wav to %s (replay offline against any "
                "hypothesis -- no live session needed to test one)",
                dump_dir,
            )

        # aec_delay_ms=None (the default) means auto-tune -- confirmed
        # live (2026-07-15) that a stale/wrong FIXED hint (measured
        # ~222ms vs a leftover 100ms) keeps WebRTC AEC3 from converging,
        # so the assistant's own voice leaks into the mic and trips the
        # barge-in overlap gate. _INITIAL_AEC_DELAY_MS is only the
        # starting guess fed to APM before the real estimate lands on
        # first playback -- set_delay() below replaces it immediately in
        # the auto-tune case, same as before this was a sentinel.
        #
        # CORRECTION 2026-07-20: an explicit aec_delay_ms is NOT
        # necessarily stale/wrong -- uat-acoustic-calibration/'s real
        # on-hardware delay sweeps found 247-309ms clearly outperforming
        # the ~222ms auto-estimate on this device pair (see
        # docs/DESIGN-echo-and-barge-in.md). Re-run
        # scripts/acoustic_calibration.py before assuming either value is
        # right; don't silently override a configured value here.
        _INITIAL_AEC_DELAY_MS = 100
        delay_explicit = config.audio.aec_delay_ms is not None
        canceller = EchoCanceller(
            delay_ms=config.audio.aec_delay_ms if delay_explicit else _INITIAL_AEC_DELAY_MS,
            dump=aec_dump,
        )
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
                        write_aec_estimate(
                            config_path, estimate, float(out_lat) * 1000, float(in_lat) * 1000
                        )
            canceller.feed_reverse(block, sample_rate)

        player.on_block_played = _feed_reference
        log.info(
            "acoustic echo cancellation ON (delay hint %dms%s)",
            config.audio.aec_delay_ms if delay_explicit else _INITIAL_AEC_DELAY_MS,
            " explicit" if delay_explicit else ", will auto-estimate from stream latencies",
        )

    interrupt_axes = resolve_preset(config.interaction.interrupt_preset)
    monitor = BargeInMonitor(
        interrupt_axes.on_current_turn, config.interaction.barge_in_min_speech_ms
    )
    interject_queue = QueuedInterjection()
    if monitor.on_current_turn != "let-finish":
        if canceller is None:
            # Not refused outright -- headphones make it legitimate -- but
            # with open speakers and no AEC the assistant's own voice trips
            # the VAD and it interrupts itself. Loud, specific warning.
            log.warning(
                "interrupt_preset=%r (on_current_turn=%r) without "
                "audio.echo_cancellation: with open speakers the assistant "
                "will interrupt ITSELF. Only safe with headphones. See "
                "docs/DESIGN-echo-and-barge-in.md.",
                config.interaction.interrupt_preset, monitor.on_current_turn,
            )
        log.info(
            "barge-in ON (preset=%s: on_current_turn=%s, on_new_words=%s; "
            "after %dms of sustained speech)",
            config.interaction.interrupt_preset, monitor.on_current_turn,
            interrupt_axes.on_new_words, config.interaction.barge_in_min_speech_ms,
        )
    barge_in_pending = False
    # Updated after every response's AEC stats are read (in _mic_chunks,
    # below), consumed at the overlap-gate call site further down in this
    # same function -- see grace_s_for_last_response()'s own docstring.
    # Starts at the base grace: nothing's played yet to have a verdict on.
    next_overlap_grace_s = ECHO_GRACE_S

    listening_gate = ListeningGate(
        PauseListeningDetector(config.interaction.pause_listening_phrases),
        ResumeWordDetector(config.interaction.resume_word),
    )
    log.info(
        "say %r to pause listening (hard-stops in-flight work); say %r to resume",
        config.interaction.pause_listening_phrases[0],
        config.interaction.resume_word,
    )

    async def _mic_chunks(mic: MicrophoneStream):  # type: ignore[no-untyped-def]
        nonlocal barge_in_pending, next_overlap_grace_s
        was_playing = False
        async for chunk in mic.stream():
            processed = canceller.process(chunk) if canceller is not None else chunk
            if tui_state is not None:
                # Post-AEC (if on): the signal VAD/STT actually sees, not
                # the raw pre-cancellation mic. Decay-smoothed (see
                # ConversationTuiState.update_mic_level) per docs/UAT-
                # checklist.md [U7]'s flagged next step, now built rather
                # than left as raw per-chunk RMS. Speaker-side level is a
                # deliberately deferred candidate: it would need a
                # cross-thread write from AudioPlayer.on_block_played (the
                # playback THREAD, not this async loop), more care than
                # this same-thread mic update needs.
                import audio_devices as ad

                raw_db, _ = ad.level_meter(processed)
                tui_state.update_mic_level(raw_db)
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
                verdict = interpret_aec_stats(attenuation, ceiling)
                log.info(
                    "AEC stats for last response: attenuation=%s of ~%s measurable  "
                    "delay=%dms  frames(reverse=%d, capture=%d)%s",
                    f"{attenuation:.1f}dB" if attenuation is not None else "n/a",
                    f"{ceiling:.1f}dB" if ceiling is not None else "?",
                    canceller.delay_ms, canceller.reverse_frames, canceller.capture_frames,
                    verdict,
                )
                if tui_state is not None:
                    tui_state.aec_verdict = verdict
                if canceller.dump is not None:
                    # AEC3's native frame size is a fixed 10ms (see
                    # convobox.audio.aec's module docstring) -- frame
                    # count * 0.01s is duration, no need to import the
                    # internal _FRAME/_AEC_RATE constants for this.
                    log.info(
                        "AEC dump progress: reference=%d frames (%.1fs)  "
                        "capture=%d frames (%.1fs)  dir=%s",
                        canceller.dump.reference_frames,
                        canceller.dump.reference_frames * 0.01,
                        canceller.dump.capture_frames,
                        canceller.dump.capture_frames * 0.01,
                        canceller.dump.directory,
                    )
                    if tui_state is not None:
                        tui_state.aec_dump_frames = canceller.dump.capture_frames
                new_grace = grace_s_for_last_response(attenuation, ceiling)
                if new_grace != next_overlap_grace_s:
                    log.info(
                        "overlap-gate grace window: %.2fs -> %.2fs for the next utterance "
                        "(last response's AEC verdict)",
                        next_overlap_grace_s, new_grace,
                    )
                next_overlap_grace_s = new_grace
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
                if monitor.on_current_turn == "abort":
                    await adapter.send_hard_stop()
                barge_in_pending = True
                if tui_state is not None:
                    tui_state.barge_in_active = True
            yield processed

    # Spoken readiness cue: by this point STT has already absorbed any
    # GPU-fallback cost (LocalTranscriber's own construction-time warm-up)
    # and the backend/TTS/mic setup above is done, so this really does
    # mean "say something now, it'll be heard" -- not just "the process
    # didn't crash yet." Goes through the same tts/player path a normal
    # response would (echo-filter registration included, via
    # SpokenTextRecorder), so it can't be mistaken for a live barge-in
    # trigger and integrates with AEC's reference feed like any other
    # spoken turn.
    announcement = startup_announcement(_resolve_convobox_version())
    log.info("%s", announcement)
    await player.play_stream(tts.synthesize_stream(announcement), tts.sample_rate)

    log.info("listening (Ctrl+C to exit; %r hard-stops the agent)",
             config.safeword.hard_stop_phrases[0])

    # Heartbeat so a silently-busy backend (thinking, or grinding on a long
    # tool call) reads as "working", not "hung" -- the exact confusion a
    # philosophy.md write caused in live UAT.
    watchdog_task = asyncio.create_task(
        _working_watchdog(
            adapter, player, WorkingIndicator(), orchestrator, interject_queue,
            segmenter, listening_gate, tui_state, continue_gate, approval_gate,
        )
    )
    tui_render_task: asyncio.Task[None] | None = None
    # termios' own attribute-list type is platform-stub-conditional (POSIX
    # only) and this variable is only ever populated on POSIX -- Any
    # sidesteps fighting typeshed's win32/posix stub split rather than
    # asserting a precise type mypy can't agree on across platforms.
    tui_old_tty_settings: Any = None
    if tui_state is not None:
        # Same VT-mode-enable idiom as voice_tui.py/settings_tui.py --
        # os.system("") with a hardcoded empty-string literal has the
        # side effect of enabling ANSI/VT100 escape processing in legacy
        # Windows console hosts; it never executes a program.
        os.system("")  # nosec B605 B607
        sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alt screen, hide cursor
        if sys.platform != "win32":
            # msvcrt.getwch() (used by _read_pending_key on Windows) already
            # bypasses the console's line-buffering/echo, so only POSIX
            # needs an explicit mode change -- without this, a scroll
            # keypress would sit unread until Enter, and would echo into
            # the alt-screen TUI instead of being consumed silently.
            import termios
            import tty

            fd = sys.stdin.fileno()
            tui_old_tty_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        tui_render_task = asyncio.create_task(_tui_render_loop(tui_state))

    try:
        with MicrophoneStream(sample_rate=config.audio.sample_rate, device=device) as mic:
            mic_holder["mic"] = mic  # lets the AEC delay estimator read input latency
            async for utterance in segmenter.segment(_mic_chunks(mic)):
                result = transcriber.transcribe(utterance)
                text = result.text
                is_hard_stop = safeword.check(text) is not None
                barged_in, barge_in_pending = barge_in_pending, False
                if tui_state is not None:
                    tui_state.barge_in_active = False
                    if text.strip():
                        # Every utterance ConvoBox actually heard, even
                        # ones later dropped by a gate below -- "what was
                        # heard" per the design doc's scope, and a real
                        # debugging aid ("did it hear me right") distinct
                        # from "what got forwarded."
                        tui_state.add_turn("user", text)

                # Safeword is checked on the raw transcript BEFORE any quality
                # gate or half-duplex drop: a hard stop must never be swallowed.
                if not is_hard_stop:
                    # No-input (Google Conversation Design's term, see
                    # RecognitionErrorLadder's docstring): STT heard nothing
                    # recognizable at all. Checked before every other gate --
                    # empty text can't match a resume word/pause phrase/gate
                    # condition anyway, and previously flowed silently all
                    # the way to Orchestrator.handle_transcript's own empty
                    # guard with no log line at all. Now observable.
                    if not text.strip():
                        tier = error_ladder.observe_failure()
                        log.info(
                            "dropped (no input, STT heard nothing recognizable) "
                            "[ERROR-LADDER: tier %d]", tier,
                        )
                        continue
                    # Pause/resume gate runs before every other gate, same
                    # reasoning as the safeword: while paused, NOTHING except
                    # the resume word should reach the overlap/echo/confidence
                    # gates or the backend (docs/DESIGN-barge-in.md,
                    # "Pause/resume listening").
                    gate_action = listening_gate.observe(text)
                    if gate_action == "resume":
                        log.info("resumed listening (resume word matched): %r", text)
                        if tui_state is not None:
                            # Resume is otherwise completely silent (docs/
                            # DESIGN-barge-in.md's open question on this --
                            # "leaning toward a short acknowledgment"). A
                            # visual one only: an audio earcon would need to
                            # go through AudioPlayer/the AEC reference feed,
                            # which is out of scope here.
                            tui_state.add_turn("system", "resumed listening")
                        continue
                    if gate_action == "drop":
                        log.debug("dropped (paused, not the resume word): %r", text)
                        continue
                    if gate_action == "pause":
                        player.stop()
                        tts.stop()
                        await adapter.send_hard_stop()
                        log.info(
                            "paused listening (matched %r) -- hard-stopped in-flight "
                            "work; say %r to resume",
                            text, config.interaction.resume_word,
                        )
                        if tui_state is not None:
                            tui_state.add_turn(
                                "system",
                                f"paused listening -- say {config.interaction.resume_word!r} to resume",
                            )
                        continue
                    # The glossary is intentionally downstream of every
                    # safety-critical raw-transcript check above.  A configured
                    # correction can improve an ordinary command such as a
                    # project name, but it can never manufacture a hard stop,
                    # wake/pause action, or approval decision.
                    corrected_text = transcript_corrector.correct(text)
                    if corrected_text != text:
                        log.info(
                            "corrected transcript before command routing: %r -> %r",
                            text,
                            corrected_text,
                        )
                        text = corrected_text
                    # Voice-gated tool approval (Phase 3, docs/DESIGN-0.3.0-
                    # interaction-and-safety.md), checked BEFORE the
                    # continue-prompt: the higher-stakes decision wins if
                    # both were somehow pending at once (they shouldn't be
                    # in practice -- a tool call blocks the turn, so a
                    # tiered response can't be mid-reveal at the same
                    # time). "discuss" deliberately does NOT forward the
                    # utterance to the backend: Claude Code's own turn is
                    # blocked inside the hook for the whole approval wait
                    # (see claude_code.py's module docstring) -- there is
                    # no live channel to actually discuss it on, unlike
                    # Codex's app-server. Every other outcome (approve/
                    # deny/discuss/None) consumes the utterance here; none
                    # of them fall through to normal command routing.
                    if approval_gate is not None and approval_gate.is_waiting:
                        approval_outcome = approval_gate.observe_transcript(text, time.monotonic())
                        if approval_outcome == "approve":
                            log.info("voice approval: APPROVED -- %r", text)
                            await orchestrator.resolve_pending_approval(True)
                        elif approval_outcome == "deny":
                            log.info("voice approval: DENIED -- %r", text)
                            await orchestrator.resolve_pending_approval(False)
                        elif approval_outcome == "discuss":
                            log.info(
                                "voice approval: still waiting (discuss, no live "
                                "channel to answer on) -- %r", text,
                            )
                        continue
                    # Response-tiering continue-prompt (docs/DESIGN-0.3.0-
                    # interaction-and-safety.md, Phase 2). Checked here, same
                    # reasoning as barge-in below: an utterance answering the
                    # prompt arrives right after the response finished
                    # speaking, exactly the window the overlap gate would
                    # otherwise flag as suspiciously-close-to-playback -- so
                    # continue/decline bypass it here, same as a real
                    # barge-in does. A non-matching reply ("pass") is NOT
                    # bypassed: it falls through to the normal gates below,
                    # since we don't know yet whether it's genuine new
                    # speech or residual echo.
                    if continue_gate.is_waiting:
                        outcome = continue_gate.observe_transcript(text)
                        if outcome == "continue":
                            log.info("continuing tiered response: %r", text)
                            await orchestrator.speak_more()
                            continue
                        if outcome == "decline":
                            log.info("declined more detail: %r", text)
                            continue
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
                            grace_s=next_overlap_grace_s,
                        )
                    ):
                        # Reports the REAL canceller state, not a hardcoded
                        # "no echo cancellation" -- UAT needs to know whether
                        # AEC was actually in the path when an echo leaked.
                        aec_state = "echo-cancellation active" if canceller is not None else "no echo cancellation"
                        log.info(
                            "dropped (overlap gate, %s): %r %s",
                            aec_state,
                            text,
                            _echo_match_suffix(text, last_spoken_response.text),
                        )
                        continue
                    if echo_filter.is_echo(text):
                        if barged_in:
                            log.warning(
                                "dropped (spoken-echo filter, barge-in was our own echo): %r %s",
                                text,
                                _echo_match_suffix(text, last_spoken_response.text),
                            )
                        else:
                            log.info(
                                "dropped (spoken-echo filter, matches ConvoBox's own recent speech): %r %s",
                                text,
                                _echo_match_suffix(text, last_spoken_response.text),
                            )
                        continue
                    if barged_in and is_backchannel(text):
                        # Playback already stopped (BargeInMonitor decided
                        # from audio timing alone, before STT could know
                        # content) -- but a bare "mm-hmm"/"okay" was never a
                        # bid to redirect the conversation, so it must not be
                        # forwarded as if it were one.
                        log.info(
                            "dropped (backchannel, not a real interrupt attempt): %r", text
                        )
                        continue
                    if result.language_probability < config.stt.min_language_probability:
                        tier = error_ladder.observe_failure()
                        log.info(
                            "dropped low-confidence transcript=%r lang=%s (%.2f < %.2f) "
                            "[ERROR-LADDER: tier %d]",
                            text, result.language,
                            result.language_probability, config.stt.min_language_probability,
                            tier,
                        )
                        continue

                # Reached only once STT has cleared both no-input and
                # no-match (or the safeword matched, an even stronger
                # signal STT worked) -- resets the consecutive-failure
                # streak regardless of what a later gate (overlap/echo/
                # backchannel/queue) does with this utterance, since those
                # all imply recognition already succeeded.
                error_ladder.reset()
                log.info(
                    "transcript=%r lang=%s (%.2f) dec=%.2f busy=%s%s%s%s",
                    text, result.language, result.language_probability,
                    math.exp(result.avg_logprob), adapter.is_busy(),
                    "  [HARD STOP]" if is_hard_stop else "",
                    "  [BARGE-IN]" if barged_in and not is_hard_stop else "",
                    # was_forced reflects THIS utterance (set immediately
                    # before the yield we just consumed -- see
                    # UtteranceSegmenter.was_forced's own docstring for the
                    # batching caveat, not a practical concern for live mic
                    # chunks). vad.max_utterance_s is None by default, so
                    # this never fires unless a user opts in -- purely
                    # informational, see docs/UAT-checklist.md [V5].
                    "  [FORCED: cut at max_utterance_s, still your turn]"
                    if segmenter.was_forced else "",
                )
                if segmenter.was_forced and tui_state is not None:
                    # Otherwise purely a log-line signal (docs/UAT-checklist.md
                    # [V5]) -- easy to read as ConvoBox just stopped listening
                    # to you mid-sentence rather than a deliberate, resumable
                    # cutoff.
                    tui_state.add_turn("system", "cut off at the time limit -- still your turn")
                if barged_in and not is_hard_stop:
                    # The backend believes its whole response was delivered; it
                    # wasn't. The marker is our version of realtime APIs'
                    # history truncation (docs: "the truncation problem").
                    marked_text = BARGE_IN_MARKER + text
                    # Axis 2 (docs/DESIGN-barge-in.md's grid, "what happens to
                    # the user's NEW words") -- BargeInMonitor already decided
                    # Axis 1 (audio stopped/turn aborted, above); this decides
                    # whether the words that interrupted it get delivered now,
                    # held for later, or discarded.
                    if interrupt_axes.on_new_words == "drop":
                        log.info(
                            "dropped (on_new_words=drop, preset=%s): %r",
                            config.interaction.interrupt_preset, text,
                        )
                        continue
                    if interrupt_axes.on_new_words == "queue":
                        interject_queue.offer(marked_text)
                        log.info(
                            "queued (on_new_words=queue, preset=%s) for delivery "
                            "once the current turn is fully idle: %r",
                            config.interaction.interrupt_preset, text,
                        )
                        continue
                    text = marked_text
                if tui_state is not None and not is_hard_stop:
                    # A new response is about to start -- the full-detail
                    # pane shows "the current response," not a running log
                    # (that's the transcript pane's job), so clear it here
                    # rather than on every heard utterance above (a
                    # gate-dropped utterance never produces a new response;
                    # clearing there would blank the pane for nothing). Only
                    # reached for the non-barged-in path and the barged-in
                    # "now" path above -- "drop"/"queue" both `continue`
                    # before this point, correctly: neither delivers a fresh
                    # response right now.
                    tui_state.full_detail = ""
                try:
                    await orchestrator.handle_transcript(text)
                except Exception as exc:  # noqa: BLE001
                    # A single utterance failing to reach the backend (timeout,
                    # dropped connection, HTTP error) must NOT kill the whole
                    # voice session -- log it and keep listening so the user can
                    # just say it again. Observed live: an interject to a busy
                    # opencode timed out and the unhandled httpx.ReadTimeout
                    # crashed the app mid-conversation.
                    log.error(
                        "couldn't deliver to the backend (%s: %s) -- still "
                        "listening, say it again to retry",
                        type(exc).__name__, exc,
                    )
    finally:
        # Restore the terminal FIRST (even on an exception mid-loop) so
        # any log lines the rest of this block produces are visible on
        # the normal screen, not lost inside a still-active alt-screen.
        if tui_render_task is not None:
            tui_render_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tui_render_task
            if tui_old_tty_settings is not None:
                import termios

                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, tui_old_tty_settings)
            sys.stdout.write("\x1b[?25h\x1b[?1049l")  # restore cursor + main screen
            sys.stdout.flush()
        # AEC dump: finalize the WAV headers and log an after-action
        # summary. Runs even on a mid-session exception/Ctrl+C -- an
        # unclosed wave.Wave_write leaves the RIFF header's size fields
        # wrong, making the file unplayable, so this must not be skipped.
        if canceller is not None and canceller.dump is not None:
            summary = canceller.dump.close()
            log.info(
                "AEC dump closed -- %s: reference %.1fs (%d frames), "
                "capture %.1fs (%d frames), session %.1fs. Replay these "
                "offline against any hypothesis -- see "
                "docs/DESIGN-echo-and-barge-in.md.",
                summary["directory"], summary["reference_s"], summary["reference_frames"],
                summary["capture_s"], summary["capture_frames"], summary["duration_s"],
            )
        # Close the backend transport (subprocess pipes / HTTP client)
        # while the loop is still alive, so shutdown is quiet instead of
        # spraying 'Event loop is closed' tracebacks. Runs on Ctrl+C too
        # (KeyboardInterrupt cancels this task, triggering the finally).
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task
        await orchestrator.stop_event_loop()
        await adapter.aclose()
        # aclose() above already closes stdin/terminates/awaits the
        # subprocess, but on Windows ProactorEventLoop, closing a pipe
        # transport schedules the actual OS-handle teardown via
        # call_soon() rather than doing it inline -- asyncio.run() (our
        # caller, main()) tears the loop down immediately once this
        # coroutine returns, before that callback gets a turn to run.
        # The transport object survives (unclosed) until a later GC
        # pass finds it, by which point the loop and pipe are both gone
        # -- "Exception ignored in: ...__del__ ... ValueError: I/O
        # operation on closed pipe" (live-confirmed, 2026-07-20 UAT
        # session, codex backend). One more tick of the loop here is
        # enough for those scheduled callbacks to actually run before
        # shutdown, same practical mitigation used elsewhere for this
        # well-known CPython/Windows asyncio subprocess-transport
        # shutdown-ordering gap.
        await asyncio.sleep(0.1)
        instance_lock.close()


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
        "-d", "--working-dir", default=None,
        help="directory the spawned coding agent (codex/claude-code) runs and "
        "edits files in; overrides backend.working_dir. Use an isolated "
        "workspace so the agent cannot modify ConvoBox's own source. No effect "
        "on the opencode backend (set by where `opencode serve` was launched).",
    )
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
    parser.add_argument(
        "--tui", action="store_true",
        help=(
            "full-screen live conversation view (transcript, full response "
            "text, barge-in/warning status) instead of scrolling log lines. "
            "Only affects the mic loop, not --text mode. Log output moves to "
            f"{_TUI_LOG_FILE} -- interleaving ordinary log lines with the "
            "alt-screen redraw would garble the display."
        ),
    )
    parser.add_argument(
        "--aec-dump", nargs="?", const="", default=None, metavar="DIR",
        help=(
            "record the real AEC reference/mic streams to WAV for offline "
            "replay (reference.wav, mic-raw.wav, mic-processed.wav under a "
            "timestamped subdirectory of DIR, default .aec-dumps/). Requires "
            "audio.echo_cancellation on. See docs/DESIGN-echo-and-barge-in.md "
            "-> 'Capturing a live incident for offline analysis'."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log_level = logging.DEBUG if args.verbose else logging.INFO
    if args.tui and args.text is None:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(message)s",
            filename=_TUI_LOG_FILE,
        )
        print(f"--tui: log output redirected to {_TUI_LOG_FILE}", flush=True)
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(message)s",
        )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("exiting")


if __name__ == "__main__":
    main()
