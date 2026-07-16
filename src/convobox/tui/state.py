"""Pure state for the live conversation TUI (docs/DESIGN-0.3.0-interaction-and-safety.md
Phase 1). Holds no rendering or terminal logic -- see render.py, which is a
pure function of this state, same split as scripts/settings_tui.py's
render_modal()/_draw_modal().
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

# What the loop is doing right now, for the status line. Mirrors the real
# pipeline states run_convobox.py's main loop passes through: idle
# (LISTENING) -> VAD detects speech (CAPTURING) -> STT decodes
# (TRANSCRIBING) -> backend processes (WORKING) -> TTS plays (SPEAKING) --
# plus PAUSED for the "pause listening" state (docs/DESIGN-barge-in.md).
TuiStatus = Literal["listening", "capturing", "transcribing", "working", "speaking", "paused"]

Speaker = Literal["user", "assistant", "system"]

# Fraction of the gap to a QUIETER reading closed per chunk (docs/UAT-
# checklist.md [U7]: raw per-chunk RMS "reads as too flickery"). Asymmetric
# on purpose -- a real VU meter's attack/decay behavior: jump immediately to
# a louder reading (never lag behind actual speech onset) but ease down from
# a louder one, so a single quiet chunk between words doesn't make the meter
# visibly stutter. Tuned by feel, not measurement; retune here if a live
# session still reads as flickery or as too sluggish to reflect real level
# changes.
_MIC_LEVEL_DECAY = 0.3


@dataclass(frozen=True)
class TranscriptTurn:
    """One entry in the transcript pane. "system" is for session-level
    events worth seeing inline (paused/resumed, barge-in, hard stop) --
    NOT the same vocabulary as backend "system" messages; this is purely
    a rendering-side speaker tag."""

    speaker: Speaker
    text: str
    timestamp: str  # HH:MM:SS, caller's clock (testable without real time)


@dataclass
class ConversationTuiState:
    """Everything render_conversation_frame() needs. Deliberately minimal
    per the design doc's phase-1 scope ("built to be extended by phases
    2-3, not rebuilt"):

    - turns: the transcript pane (what was heard, what was said).
    - full_detail: phase 2's untruncated response text. For phase 1 (no
      response tiering shipped yet) this is simply the full text of the
      latest response -- a real, useful pane today (see everything
      ConvoBox received, not just what it chose to speak), not a stub
      that does nothing until tiering exists.
    - warning: phase 3's approval banner. None = no active warning; the
      render function reserves no space for it when unset, so it costs
      nothing before approvals ship.
    - backend_name/aec_enabled: set once at startup (config.backend.name,
      config.audio.echo_cancellation) -- static for the process lifetime,
      unlike everything else here.
    - aec_verdict: the last response's interpret_aec_stats() tag
      (run_convobox.py) -- "" until the first response with AEC on.
    - heartbeat_elapsed_s: continuous silent-busy seconds from
      WorkingIndicator.silent_busy_s, None when not silently busy (backend
      idle, or audio is already playing its own feedback).
    - mic_level_db: live mic RMS in dBFS, smoothed (see update_mic_level)
      from the raw per-chunk reading (post-AEC if echo cancellation is
      on -- the signal VAD/STT actually sees). None until the first chunk
      arrives. Speaker-side live level is a deliberately deferred
      candidate (see run_convobox.py's comment at the wiring site) -- it
      would need a cross-thread write from the playback callback, more
      care than mic level's same-thread update.
    """

    turns: list[TranscriptTurn] = field(default_factory=list)
    full_detail: str = ""
    status: TuiStatus = "listening"
    warning: str | None = None
    barge_in_active: bool = False
    started: float = field(default_factory=time.monotonic)
    backend_name: str = ""
    aec_enabled: bool = False
    aec_verdict: str = ""
    heartbeat_elapsed_s: float | None = None
    mic_level_db: float | None = None

    def update_mic_level(self, raw_db: float) -> None:
        """Feed one raw per-chunk dBFS reading through the decay smoothing.

        First reading is taken as-is (no prior level to decay from).
        """
        if self.mic_level_db is None or raw_db >= self.mic_level_db:
            self.mic_level_db = raw_db
        else:
            self.mic_level_db += (raw_db - self.mic_level_db) * _MIC_LEVEL_DECAY

    def add_turn(self, speaker: Speaker, text: str, timestamp: str | None = None) -> None:
        self.turns.append(
            TranscriptTurn(
                speaker=speaker,
                text=text,
                timestamp=timestamp if timestamp is not None else time.strftime("%H:%M:%S"),
            )
        )
