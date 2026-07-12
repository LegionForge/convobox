from __future__ import annotations

import pytest

from convobox.config import AppConfig, InteractionConfig
from scripts.run_convobox import BARGE_IN_MARKER, BargeInMonitor

CHUNK_MS = 32.0  # 512 samples at 16kHz, the real capture chunk size


def _feed(monitor: BargeInMonitor, in_speech: bool, playing: bool, chunks: int) -> list[bool]:
    return [monitor.observe(in_speech, playing, CHUNK_MS) for _ in range(chunks)]


def test_fires_once_after_sustained_speech_during_playback() -> None:
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    results = _feed(monitor, in_speech=True, playing=True, chunks=12)  # 384ms
    assert results.count(True) == 1
    assert results.index(True) == 7  # 8th chunk crosses 250ms (8*32=256)


def test_never_fires_below_threshold() -> None:
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    assert not any(_feed(monitor, in_speech=True, playing=True, chunks=7))  # 224ms


def test_brief_noise_between_silence_never_accumulates() -> None:
    # cough (3 chunks) / silence / cough -- the runs must not add up.
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    for _ in range(5):
        assert not any(_feed(monitor, in_speech=True, playing=True, chunks=3))
        assert not any(_feed(monitor, in_speech=False, playing=True, chunks=2))


def test_fires_again_for_a_second_distinct_episode() -> None:
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    assert any(_feed(monitor, in_speech=True, playing=True, chunks=10))
    _feed(monitor, in_speech=False, playing=True, chunks=3)  # speech ended
    assert any(_feed(monitor, in_speech=True, playing=True, chunks=10))


def test_does_not_refire_within_one_episode() -> None:
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    results = _feed(monitor, in_speech=True, playing=True, chunks=100)
    assert results.count(True) == 1


def test_mode_none_never_fires() -> None:
    monitor = BargeInMonitor("none", min_speech_ms=250)
    assert not any(_feed(monitor, in_speech=True, playing=True, chunks=100))


def test_speech_while_idle_never_fires() -> None:
    # Talking with nothing playing is a normal command, not a barge-in.
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    assert not any(_feed(monitor, in_speech=True, playing=False, chunks=100))


def test_playback_starting_mid_speech_requires_fresh_sustain() -> None:
    # User was already talking when a (queued) response starts playing:
    # the pre-playback speech must not count toward the threshold.
    monitor = BargeInMonitor("stop_audio", min_speech_ms=250)
    _feed(monitor, in_speech=True, playing=False, chunks=50)
    results = _feed(monitor, in_speech=True, playing=True, chunks=8)
    assert results.index(True) == 7  # full 250ms counted from playback start


def test_abort_turn_mode_also_fires() -> None:
    monitor = BargeInMonitor("abort_turn", min_speech_ms=250)
    assert any(_feed(monitor, in_speech=True, playing=True, chunks=10))


# --- config surface ---


def test_interaction_config_defaults_to_half_duplex() -> None:
    config = AppConfig()
    assert config.interaction.interrupt_mode == "none"
    assert config.interaction.barge_in_min_speech_ms == 250


def test_interrupt_mode_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        InteractionConfig(interrupt_mode="shout_louder")  # type: ignore[arg-type]


def test_marker_is_nonempty_and_readable() -> None:
    # The truncation-problem marker: prefixed to forwarded barge-in text.
    assert BARGE_IN_MARKER.startswith("(")
    assert "interrupt" in BARGE_IN_MARKER
