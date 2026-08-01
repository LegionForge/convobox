from __future__ import annotations

from typing import Literal

import numpy as np

# P8 (docs/DESIGN-barge-in.md, "Open questions"): a short earcon so
# pause/resume don't feel silently dead. A-major triad (root, major 3rd,
# 5th) -- ascending on resume ("listening again"), the same three notes
# reversed on pause, so the two read as a matched pair rather than
# unrelated sounds.
_NOTES_HZ = (440.0, 554.365, 659.255)  # A4, C#5, E5
_NOTE_S = 0.3
_FADE_S = 0.01  # attack/decay per note, avoids audible clicks at note edges
_GAP_S = 0.015  # brief silence between notes, keeps the arpeggio legible
_PEAK_AMPLITUDE = 0.3  # modest relative to spoken TTS; tune after live listening

# AudioPlayer resamples to the device rate itself (audio/playback.py's
# _resample), so this only needs to be internally consistent with whatever
# rate generate_ack_tone() actually returned -- callers pass both together.
SAMPLE_RATE_HZ = 24000


def _note(freq_hz: float, sample_rate: int) -> np.ndarray:
    n = round(_NOTE_S * sample_rate)
    t = np.arange(n, dtype=np.float32) / sample_rate
    wave = np.sin(2.0 * np.pi * freq_hz * t).astype(np.float32)
    fade_n = min(round(_FADE_S * sample_rate), n // 2)
    if fade_n > 0:
        envelope = np.ones(n, dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        envelope[:fade_n] = ramp
        envelope[-fade_n:] = ramp[::-1]
        wave *= envelope
    return wave * _PEAK_AMPLITUDE


def generate_ack_tone(direction: Literal["listening", "paused"], sample_rate: int = SAMPLE_RATE_HZ) -> np.ndarray:
    """A 3-note earcon for interaction.pause_resume_ack == "tone".

    "listening" (on resume) plays the triad ascending; "paused" plays the
    same three notes in reverse -- opposite direction from the same pair,
    not two unrelated sounds. Feed the result straight to
    AudioPlayer.play(samples, sample_rate); AudioPlayer resamples to the
    device rate itself, so the sample_rate here doesn't need to match
    anything else in the pipeline.
    """
    freqs = _NOTES_HZ if direction == "listening" else tuple(reversed(_NOTES_HZ))
    gap = np.zeros(round(_GAP_S * sample_rate), dtype=np.float32)
    notes = [_note(f, sample_rate) for f in freqs]
    parts: list[np.ndarray] = []
    for i, note in enumerate(notes):
        parts.append(note)
        if i < len(notes) - 1:
            parts.append(gap)
    return np.concatenate(parts)
