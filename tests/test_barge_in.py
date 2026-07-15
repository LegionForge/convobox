from __future__ import annotations

import pytest

from convobox.config import AppConfig, InteractionConfig
from convobox.listening_pause import PauseListeningDetector
from convobox.wakeword import WakewordDetector
from scripts.run_convobox import (
    BARGE_IN_MARKER,
    BargeInMonitor,
    ListeningGate,
    QueuedInterjection,
    is_backchannel,
)

CHUNK_MS = 32.0  # 512 samples at 16kHz, the real capture chunk size


def _feed(monitor: BargeInMonitor, in_speech: bool, playing: bool, chunks: int) -> list[bool]:
    return [monitor.observe(in_speech, playing, CHUNK_MS) for _ in range(chunks)]


def test_fires_once_after_sustained_speech_during_playback() -> None:
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    results = _feed(monitor, in_speech=True, playing=True, chunks=12)  # 384ms
    assert results.count(True) == 1
    assert results.index(True) == 7  # 8th chunk crosses 250ms (8*32=256)


def test_never_fires_below_threshold() -> None:
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    assert not any(_feed(monitor, in_speech=True, playing=True, chunks=7))  # 224ms


def test_brief_noise_between_silence_never_accumulates() -> None:
    # cough (3 chunks) / silence / cough -- the runs must not add up.
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    for _ in range(5):
        assert not any(_feed(monitor, in_speech=True, playing=True, chunks=3))
        assert not any(_feed(monitor, in_speech=False, playing=True, chunks=2))


def test_fires_again_for_a_second_distinct_episode() -> None:
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    assert any(_feed(monitor, in_speech=True, playing=True, chunks=10))
    _feed(monitor, in_speech=False, playing=True, chunks=3)  # speech ended
    assert any(_feed(monitor, in_speech=True, playing=True, chunks=10))


def test_does_not_refire_within_one_episode() -> None:
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    results = _feed(monitor, in_speech=True, playing=True, chunks=100)
    assert results.count(True) == 1


def test_let_finish_never_fires() -> None:
    monitor = BargeInMonitor("let-finish", min_speech_ms=250)
    assert not any(_feed(monitor, in_speech=True, playing=True, chunks=100))


def test_speech_while_idle_never_fires() -> None:
    # Talking with nothing playing is a normal command, not a barge-in.
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    assert not any(_feed(monitor, in_speech=True, playing=False, chunks=100))


def test_playback_starting_mid_speech_requires_fresh_sustain() -> None:
    # User was already talking when a (queued) response starts playing:
    # the pre-playback speech must not count toward the threshold.
    monitor = BargeInMonitor("mute", min_speech_ms=250)
    _feed(monitor, in_speech=True, playing=False, chunks=50)
    results = _feed(monitor, in_speech=True, playing=True, chunks=8)
    assert results.index(True) == 7  # full 250ms counted from playback start


def test_abort_mode_also_fires() -> None:
    monitor = BargeInMonitor("abort", min_speech_ms=250)
    assert any(_feed(monitor, in_speech=True, playing=True, chunks=10))


# --- config surface ---


def test_interaction_config_defaults_to_half_duplex() -> None:
    # do-not-disturb (let-finish + drop) is behaviorally identical to the
    # old interrupt_mode="none" default -- see config.py's InteractionConfig
    # docstring for why this migration didn't switch the shipped default.
    config = AppConfig()
    assert config.interaction.interrupt_preset == "do-not-disturb"
    assert config.interaction.barge_in_min_speech_ms == 250


def test_interrupt_preset_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        InteractionConfig(interrupt_preset="shout_louder")


def test_interaction_config_wake_word_and_pause_phrase_defaults() -> None:
    config = AppConfig()
    assert config.interaction.wake_word == "Athena"
    assert config.interaction.pause_listening_phrases == ["stop listening", "pause listening"]


def test_interaction_config_tier_responses_off_by_default() -> None:
    # No behavior change for existing sessions: full responses spoken
    # exactly as before unless explicitly opted in.
    config = AppConfig()
    assert config.interaction.tier_responses is False
    assert config.interaction.continue_timeout_s == 2.5


# --- QueuedInterjection: Axis 2's "queue" behavior, the `patient` preset
# (docs/DESIGN-barge-in.md) ---


def test_queue_holds_until_fully_idle() -> None:
    q = QueuedInterjection()
    q.offer("tell it to also add tests")
    assert q.flush_if_idle(busy=True, playing=False) is None
    assert q.flush_if_idle(busy=False, playing=True) is None
    assert q.flush_if_idle(busy=True, playing=True) is None
    assert q.flush_if_idle(busy=False, playing=False) == "tell it to also add tests"


def test_queue_empty_never_flushes() -> None:
    q = QueuedInterjection()
    assert q.flush_if_idle(busy=False, playing=False) is None


def test_queue_flush_clears_the_pending_slot() -> None:
    q = QueuedInterjection()
    q.offer("one thing")
    assert q.flush_if_idle(busy=False, playing=False) == "one thing"
    # Already flushed -- a second idle tick must not redeliver it.
    assert q.flush_if_idle(busy=False, playing=False) is None


def test_queue_second_offer_replaces_the_first_most_recent_wins() -> None:
    q = QueuedInterjection()
    q.offer("first thing")
    q.offer("second thing")
    assert q.flush_if_idle(busy=False, playing=False) == "second thing"


# --- ListeningGate: pause/resume listening (docs/DESIGN-barge-in.md) ---


def _gate(pause_phrases: list[str] | None = None, wake_word: str = "Athena") -> ListeningGate:
    return ListeningGate(
        PauseListeningDetector(pause_phrases),
        WakewordDetector(wake_word),
    )


def test_pause_phrase_pauses() -> None:
    gate = _gate()
    assert gate.observe("stop listening") == "pause"
    assert gate.is_paused is True


def test_ordinary_speech_passes_through_when_not_paused() -> None:
    gate = _gate()
    assert gate.observe("run the tests please") == "pass"
    assert gate.is_paused is False


def test_wake_word_resumes_from_paused() -> None:
    gate = _gate()
    gate.observe("stop listening")
    assert gate.observe("hey Athena") == "resume"
    assert gate.is_paused is False


def test_non_wake_word_speech_drops_while_paused() -> None:
    gate = _gate()
    gate.observe("stop listening")
    assert gate.observe("run the tests please") == "drop"
    assert gate.is_paused is True  # stays paused


def test_pause_phrase_itself_is_ignored_while_already_paused() -> None:
    # While paused, ONLY the wake word is checked -- the pause phrase is
    # just more ignored speech, not a special case.
    gate = _gate()
    gate.observe("stop listening")
    assert gate.observe("pause listening") == "drop"
    assert gate.is_paused is True


def test_resume_then_pause_again_is_a_fresh_cycle() -> None:
    gate = _gate()
    gate.observe("stop listening")
    gate.observe("Athena")  # resume
    assert gate.observe("stop listening") == "pause"
    assert gate.is_paused is True


def test_custom_pause_phrases_and_wake_word() -> None:
    gate = _gate(pause_phrases=["go to sleep"], wake_word="Athena")
    assert gate.observe("go to sleep") == "pause"
    assert gate.observe("run the tests") == "drop"
    assert gate.observe("hey Athena") == "resume"


def test_marker_is_nonempty_and_readable() -> None:
    # The truncation-problem marker: prefixed to forwarded barge-in text.
    assert BARGE_IN_MARKER.startswith("(")
    assert "interrupt" in BARGE_IN_MARKER


# --- working-indicator heartbeat (silently-busy backend feedback) ---

from scripts.run_convobox import WorkingIndicator  # noqa: E402


def _feed_working(ind: WorkingIndicator, busy: bool, playing: bool, n: int, dt: float = 1.0):
    return [ind.observe(busy, playing, dt) for _ in range(n)]


def test_working_no_notice_while_idle() -> None:
    ind = WorkingIndicator(first_notice_s=3.0, repeat_s=5.0)
    assert all(r is None for r in _feed_working(ind, busy=False, playing=False, n=10))


def test_working_no_notice_while_playing() -> None:
    # Busy but playing: audio is its own feedback, no heartbeat needed.
    ind = WorkingIndicator(first_notice_s=3.0, repeat_s=5.0)
    assert all(r is None for r in _feed_working(ind, busy=True, playing=True, n=10))


def test_working_notice_after_first_grace() -> None:
    ind = WorkingIndicator(first_notice_s=3.0, repeat_s=5.0)
    results = _feed_working(ind, busy=True, playing=False, n=3)
    assert results[0] is None and results[1] is None
    assert results[2] == pytest.approx(3.0)


def test_working_repeats_at_interval() -> None:
    ind = WorkingIndicator(first_notice_s=3.0, repeat_s=5.0)
    fires = [i for i, r in enumerate(_feed_working(ind, True, False, 20), start=1) if r is not None]
    assert fires == [3, 8, 13, 18]  # first at grace, then every repeat_s


def test_working_resets_when_playback_starts() -> None:
    ind = WorkingIndicator(first_notice_s=3.0, repeat_s=5.0)
    _feed_working(ind, busy=True, playing=False, n=2)  # 2s, no notice yet
    assert ind.observe(busy=True, playing=True, dt_s=1.0) is None  # playing resets
    after = _feed_working(ind, busy=True, playing=False, n=3)
    assert after[2] is not None  # needs a fresh full grace


def test_working_resets_when_idle() -> None:
    ind = WorkingIndicator(first_notice_s=3.0, repeat_s=5.0)
    _feed_working(ind, busy=True, playing=False, n=2)
    assert ind.observe(busy=False, playing=False, dt_s=1.0) is None  # idle resets
    after = _feed_working(ind, busy=True, playing=False, n=3)
    assert after[2] is not None


# --- heartbeat coloring (live-validated thresholds, JP's 2026-07-14/15
# headset UAT: the heartbeat is the only feedback during a silent-busy
# stretch, but is invisible when interacting through a backend's own chat
# UI rather than watching this terminal -- color makes it glanceable) ---

from scripts.run_convobox import (  # noqa: E402
    _ANSI_GREEN,
    _ANSI_RED,
    _ANSI_YELLOW,
    _heartbeat_color,
)


def test_heartbeat_color_green_just_under_ten_seconds() -> None:
    assert _heartbeat_color(9.9) == _ANSI_GREEN


def test_heartbeat_color_yellow_at_ten_seconds() -> None:
    assert _heartbeat_color(10.0) == _ANSI_YELLOW


def test_heartbeat_color_yellow_just_under_sixty_seconds() -> None:
    assert _heartbeat_color(59.9) == _ANSI_YELLOW


def test_heartbeat_color_red_at_sixty_seconds() -> None:
    assert _heartbeat_color(60.0) == _ANSI_RED


def test_heartbeat_color_red_for_a_long_stall() -> None:
    assert _heartbeat_color(600.0) == _ANSI_RED


# --- backchannel filtering (docs/DESIGN-barge-in.md, "Backchannel filtering") ---


@pytest.mark.parametrize(
    "text",
    ["mm-hmm", "yeah", "okay", "right", "uh-huh", "Mm-hmm!", "YEAH", "gotcha", "okay right"],
)
def test_pure_backchannel_utterances_are_detected(text: str) -> None:
    assert is_backchannel(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "yeah, but stop the deploy",  # real content beyond the continuer
        "stop",  # a real short command, not a continuer
        "run the tests",
        "",
        "   ",
    ],
)
def test_non_backchannel_utterances_are_not_flagged(text: str) -> None:
    assert is_backchannel(text) is False
