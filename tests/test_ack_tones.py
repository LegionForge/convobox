from __future__ import annotations

import numpy as np
import pytest

from convobox.audio.ack_tones import (
    _GAP_S,
    _NOTE_S,
    _NOTES_HZ,
    SAMPLE_RATE_HZ,
    generate_ack_tone,
)


def _dominant_freq_hz(segment: np.ndarray, sample_rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(segment))
    freqs = np.fft.rfftfreq(len(segment), d=1.0 / sample_rate)
    return float(freqs[int(np.argmax(spectrum))])


def _note_segments(tone: np.ndarray, sample_rate: int) -> list[np.ndarray]:
    note_n = round(_NOTE_S * sample_rate)
    gap_n = round(_GAP_S * sample_rate)
    stride = note_n + gap_n
    return [tone[i * stride : i * stride + note_n] for i in range(len(_NOTES_HZ))]


def test_listening_tone_is_the_triad_ascending() -> None:
    tone = generate_ack_tone("listening")
    freqs = [_dominant_freq_hz(seg, SAMPLE_RATE_HZ) for seg in _note_segments(tone, SAMPLE_RATE_HZ)]
    assert freqs[0] < freqs[1] < freqs[2]
    for measured, expected in zip(freqs, _NOTES_HZ, strict=True):
        assert measured == pytest.approx(expected, abs=5.0)


def test_paused_tone_is_the_same_triad_descending() -> None:
    # Same three notes as "listening", reversed -- a matched pair, not two
    # unrelated sounds (docs/DESIGN-barge-in.md's P8 ruling).
    tone = generate_ack_tone("paused")
    freqs = [_dominant_freq_hz(seg, SAMPLE_RATE_HZ) for seg in _note_segments(tone, SAMPLE_RATE_HZ)]
    assert freqs[0] > freqs[1] > freqs[2]
    for measured, expected in zip(freqs, reversed(_NOTES_HZ), strict=True):
        assert measured == pytest.approx(expected, abs=5.0)


def test_tone_length_is_three_notes_plus_two_gaps() -> None:
    tone = generate_ack_tone("listening")
    note_n = round(_NOTE_S * SAMPLE_RATE_HZ)
    gap_n = round(_GAP_S * SAMPLE_RATE_HZ)
    assert len(tone) == 3 * note_n + 2 * gap_n


def test_tone_never_clips() -> None:
    for direction in ("listening", "paused"):
        tone = generate_ack_tone(direction)
        assert np.max(np.abs(tone)) <= 1.0


def test_tone_fades_in_and_out_to_avoid_clicks() -> None:
    tone = generate_ack_tone("listening")
    assert abs(float(tone[0])) < 0.05
    assert abs(float(tone[-1])) < 0.05


def test_tone_is_float32() -> None:
    assert generate_ack_tone("listening").dtype == np.float32


def test_custom_sample_rate_changes_the_output_length() -> None:
    tone_default = generate_ack_tone("listening")
    tone_half_rate = generate_ack_tone("listening", sample_rate=SAMPLE_RATE_HZ // 2)
    assert len(tone_half_rate) == pytest.approx(len(tone_default) / 2, rel=0.01)
