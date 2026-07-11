from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_convobox import (
    ECHO_GRACE_S,
    EchoAwarePlayer,
    MutePlayer,
    utterance_overlapped_playback,
)


def test_overlap_true_when_utterance_started_during_playback() -> None:
    # Playback ended at t=100. Utterance: 2s long, 0.5s trailing silence,
    # 0.4s STT latency, transcript arrives at t=101.5 -- its audio began at
    # t=98.6, squarely inside playback.
    assert utterance_overlapped_playback(
        now=101.5,
        duration_s=2.0,
        stt_latency_ms=400,
        min_silence_ms=500,
        playback_ended_at=100.0,
    )


def test_overlap_true_just_after_playback_within_grace() -> None:
    # Audio began 0.2s after playback ended -- inside the reverb grace.
    assert utterance_overlapped_playback(
        now=100.2 + 1.0 + 0.5 + 0.3,  # start + duration + silence + stt
        duration_s=1.0,
        stt_latency_ms=300,
        min_silence_ms=500,
        playback_ended_at=100.0,
    )


def test_overlap_false_for_clearly_later_utterance() -> None:
    # Audio began a full second after playback ended (beyond grace).
    start = 100.0 + ECHO_GRACE_S + 1.0
    assert not utterance_overlapped_playback(
        now=start + 1.0 + 0.5 + 0.3,
        duration_s=1.0,
        stt_latency_ms=300,
        min_silence_ms=500,
        playback_ended_at=100.0,
    )


def test_overlap_false_when_nothing_ever_played() -> None:
    # playback_ended_at=0 (never played): even the first utterance after
    # startup must pass.
    assert not utterance_overlapped_playback(
        now=time.monotonic(),
        duration_s=2.0,
        stt_latency_ms=500,
        min_silence_ms=500,
        playback_ended_at=0.0,
    )


@pytest.fixture()
def silent_output_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def __init__(self, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def write(self, samples: object) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "convobox.audio.playback.import_sounddevice",
        lambda: SimpleNamespace(OutputStream=FakeStream),
    )


def test_echo_aware_player_estimates_end_from_duration(silent_output_stream: None) -> None:
    player = EchoAwarePlayer()
    samples = np.zeros(22050 * 2, dtype=np.float32)  # 2s at 22050 Hz
    before = time.monotonic()
    player.play(samples, sample_rate=22050)
    assert player.playback_ended_at == pytest.approx(before + 2.0, abs=0.25)
    player.stop()


def test_echo_aware_player_stop_clamps_estimate_to_now(silent_output_stream: None) -> None:
    player = EchoAwarePlayer()
    samples = np.zeros(22050 * 60, dtype=np.float32)  # 60s estimate
    player.play(samples, sample_rate=22050)
    player.stop()  # hard stop long before the estimate
    assert player.playback_ended_at <= time.monotonic()


def test_mute_player_never_marks_playback(silent_output_stream: None) -> None:
    player = MutePlayer()
    player.play(np.zeros(22050, dtype=np.float32), sample_rate=22050)
    assert player.playback_ended_at == 0.0
    assert player.is_playing() is False
