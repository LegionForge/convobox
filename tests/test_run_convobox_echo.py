from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run_convobox import (
    ECHO_GRACE_S,
    EchoAwarePlayer,
    EchoTailGuard,
    MutePlayer,
    token_overlap_ratio,
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


# --- dropped-transcript match observability ---

def test_token_overlap_is_zero_for_empty_text() -> None:
    assert token_overlap_ratio("", "The response has words") == 0.0
    assert token_overlap_ratio("A transcript", "") == 0.0


def test_token_overlap_ignores_punctuation_and_case() -> None:
    assert token_overlap_ratio("HELLO, world!", "hello -- WORLD.") == 1.0


def test_token_overlap_reports_partial_transcript_match() -> None:
    assert token_overlap_ratio("the pipeline needs a retry", "The pipeline works") == pytest.approx(0.4)


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
    # [G8]: --mute never opens a real device stream, so on_first_block_played
    # never fires -- audible must stay False, matching is_playing()'s
    # always-False behavior for this player.
    assert player.audible is False


# --- [G8]: audible (BargeInMonitor's gate, not is_playing()'s thread liveness) ---
#
# These all gate the fake stream mid-flight (a second write() call, or
# start() itself) rather than calling wait() for full natural completion,
# because audible no longer stays True after playback genuinely finishes
# (see the dedicated on_playback_complete tests below for that fix) --
# checking post-wait() would now correctly see False, not True, for any
# of these "is it audible right now" scenarios.


def test_echo_aware_player_becomes_audible_once_a_block_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    became_audible = threading.Event()
    release = threading.Event()

    class GatedStream:
        def __init__(self, **kwargs: object) -> None:
            self._writes = 0

        def start(self) -> None:
            pass

        def write(self, samples: object) -> None:
            self._writes += 1
            if self._writes == 2:
                # Held here (not on write #1) so on_first_block_played has
                # already fired for block 1 by the time we check -- and the
                # thread can't reach natural completion while we're looking.
                release.wait(timeout=1)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "convobox.audio.playback.import_sounddevice",
        lambda: SimpleNamespace(OutputStream=GatedStream),
    )
    player = EchoAwarePlayer()
    original_callback = player.on_first_block_played
    def on_first_block() -> None:
        assert original_callback is not None
        original_callback()
        became_audible.set()
    player.on_first_block_played = on_first_block

    player.play(np.zeros(4096, dtype=np.float32), sample_rate=16000)
    assert became_audible.wait(timeout=1)
    assert player.audible is True
    assert player.is_playing() is True  # still mid-flight, not yet complete
    release.set()
    player.stop()


def test_echo_aware_player_audible_is_false_until_the_first_block_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The actual [G8] gap: is_playing() (thread alive, device opened) can be
    # True for a real TTS-synthesis-latency window before any block has
    # actually reached the speaker. audible must stay False through that
    # window -- gate the fake stream's start() to pause there and check.
    stream_started = threading.Event()
    release_start = threading.Event()
    became_audible = threading.Event()
    release_write = threading.Event()

    class GatedStream:
        def __init__(self, **kwargs: object) -> None:
            self._writes = 0

        def start(self) -> None:
            stream_started.set()
            release_start.wait(timeout=1)

        def write(self, samples: object) -> None:
            self._writes += 1
            if self._writes == 2:
                # Held after block 1 (which fires on_first_block_played),
                # same reasoning as the test above.
                release_write.wait(timeout=1)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "convobox.audio.playback.import_sounddevice",
        lambda: SimpleNamespace(OutputStream=GatedStream),
    )
    player = EchoAwarePlayer()
    original_callback = player.on_first_block_played
    def on_first_block() -> None:
        assert original_callback is not None
        original_callback()
        became_audible.set()
    player.on_first_block_played = on_first_block

    player.play(np.zeros(4096, dtype=np.float32), sample_rate=16000)
    assert stream_started.wait(timeout=1)
    assert player.is_playing() is True
    assert player.audible is False  # device open, but nothing written yet
    release_start.set()
    assert became_audible.wait(timeout=1)
    assert player.audible is True
    release_write.set()
    player.stop()


def test_echo_aware_player_audible_resets_on_a_new_play_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The explicit, synchronous reset at the top of play() (belt-and-
    # suspenders alongside on_playback_complete's own reset, which by
    # this point has already fired too -- play()'s internal stop() call
    # joins the old thread, running its finally block, before this new
    # one ever starts): a second play() call's audible must read False
    # immediately, before its own first block has written anything.
    became_audible = threading.Event()
    release = threading.Event()

    class GatedStream:
        def __init__(self, **kwargs: object) -> None:
            self._writes = 0

        def start(self) -> None:
            pass

        def write(self, samples: object) -> None:
            self._writes += 1
            if self._writes == 2:
                release.wait(timeout=1)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "convobox.audio.playback.import_sounddevice",
        lambda: SimpleNamespace(OutputStream=GatedStream),
    )
    player = EchoAwarePlayer()
    original_callback = player.on_first_block_played
    def on_first_block() -> None:
        assert original_callback is not None
        original_callback()
        became_audible.set()
    player.on_first_block_played = on_first_block

    player.play(np.zeros(4096, dtype=np.float32), sample_rate=16000)
    assert became_audible.wait(timeout=1)
    assert player.audible is True

    release.set()  # let the first (blocked) thread finish so stop() can join it
    player.on_first_block_played = original_callback
    player.play(np.zeros(64, dtype=np.float32), sample_rate=16000)
    assert player.audible is False
    player.stop()


# --- on_playback_complete: the actual [G8] false-barge-in-tag fix,
# 2026-07-29 -- audible must reset to False when playback ends NATURALLY,
# not just at the start of the next play() call, or every utterance
# spoken into the gap between one response finishing and the next
# starting reads as "during playback" to BargeInMonitor. ---


def test_echo_aware_player_audible_resets_after_natural_completion(
    silent_output_stream: None,
) -> None:
    player = EchoAwarePlayer()
    player.play(np.zeros(64, dtype=np.float32), sample_rate=16000)
    player.wait()  # silent_output_stream's write() never blocks -- finishes fast
    assert player.audible is False


def test_echo_aware_player_audible_resets_after_a_hard_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    became_audible = threading.Event()
    release = threading.Event()

    class GatedStream:
        def __init__(self, **kwargs: object) -> None:
            self._writes = 0

        def start(self) -> None:
            pass

        def write(self, samples: object) -> None:
            self._writes += 1
            if self._writes == 2:
                # Held here so the thread can't race ahead to natural
                # completion before stop() below gets a chance to
                # interrupt it mid-flight -- that's the actual scenario
                # this test needs (a HARD stop, not a natural end).
                release.wait(timeout=1)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "convobox.audio.playback.import_sounddevice",
        lambda: SimpleNamespace(OutputStream=GatedStream),
    )
    player = EchoAwarePlayer()
    original_callback = player.on_first_block_played
    def on_first_block() -> None:
        assert original_callback is not None
        original_callback()
        became_audible.set()
    player.on_first_block_played = on_first_block

    player.play(np.zeros(4096, dtype=np.float32), sample_rate=16000)
    assert became_audible.wait(timeout=1)
    assert player.audible is True
    release.set()
    player.stop()  # hard stop, mid-playback -- also reaches the finally block
    assert player.audible is False


# --- stage-1 text-level echo suppression ---

from scripts.run_convobox import SpokenEchoFilter, SpokenTextRecorder  # noqa: E402


def _filter_with(spoken: str, at: float = 100.0) -> SpokenEchoFilter:
    f = SpokenEchoFilter()
    f.note_spoken(spoken, now=at)
    return f


RESPONSE = "The pipeline works. All tests passed and the coverage threshold was reached."


def test_exact_echo_is_detected() -> None:
    f = _filter_with(RESPONSE)
    assert f.is_echo("all tests passed and the coverage threshold was reached", now=105.0)


def test_garbled_partial_echo_is_detected() -> None:
    # STT hears a lossy far-field copy: some words wrong, most survive.
    f = _filter_with(RESPONSE)
    assert f.is_echo("the pipeline works all tests past the coverage", now=105.0)


def test_novel_user_sentence_passes() -> None:
    f = _filter_with(RESPONSE)
    assert not f.is_echo("please refactor the audio capture module next", now=105.0)


def test_short_confirmations_never_filtered() -> None:
    # "the coverage" appears verbatim in the response, but 2 tokens is
    # below MIN_TOKENS -- a real user's short reply must never be eaten.
    f = _filter_with(RESPONSE)
    assert not f.is_echo("the coverage", now=105.0)
    assert not f.is_echo("yes", now=105.0)


def test_old_speech_ages_out() -> None:
    f = _filter_with(RESPONSE, at=100.0)
    assert not f.is_echo(
        "all tests passed and the coverage threshold was reached",
        now=100.0 + SpokenEchoFilter.MAX_AGE_S + 5.0,
    )


def test_recorder_notes_text_and_delegates() -> None:
    class FakeTTS:
        sample_rate = 22050

        async def synthesize(self, text: str):
            return np.zeros(4, dtype=np.float32)

        def synthesize_stream(self, text: str):
            raise NotImplementedError

        def stop(self) -> None:
            pass

        def is_speaking(self) -> bool:
            return False

    f = SpokenEchoFilter()
    recorder = SpokenTextRecorder(FakeTTS(), f)  # type: ignore[arg-type]
    import asyncio as _asyncio

    audio = _asyncio.run(recorder.synthesize(RESPONSE))
    assert len(audio) == 4
    assert recorder.sample_rate == 22050
    assert f.is_echo("all tests passed and the coverage threshold was reached")


# --- single-instance guard ---

from scripts.run_convobox import acquire_single_instance_lock  # noqa: E402


def test_single_instance_lock_is_exclusive_and_releases() -> None:
    # A throwaway port, NOT the real one: the real port is legitimately
    # held whenever a live ConvoBox is listening on this machine -- which
    # is exactly when the dev suite tends to be running. (Discovered the
    # obvious way: this test failed while a live UAT session was up.)
    port = 47991
    first = acquire_single_instance_lock(port)
    assert first is not None
    try:
        assert acquire_single_instance_lock(port) is None  # second caller refused
    finally:
        first.close()
    third = acquire_single_instance_lock(port)  # released -> acquirable again
    assert third is not None
    third.close()


# --- AEC stats verdict (three-way; the false-success-on-silence fix) ---

from scripts.run_convobox import interpret_aec_stats  # noqa: E402


def test_aec_verdict_empty_without_numbers() -> None:
    assert interpret_aec_stats(None, None) == ""
    assert interpret_aec_stats(4.0, None) == ""
    assert interpret_aec_stats(None, 4.0) == ""


def test_aec_verdict_flags_no_echo_when_ceiling_near_zero() -> None:
    # The silent-device case from live UAT: ceiling ~0 means no speaker
    # sound reached the mic -- must NOT read as success.
    verdict = interpret_aec_stats(attenuation_db=8.2, ceiling_db=-0.3)
    assert "NO ECHO DETECTED" in verdict
    assert "success" not in verdict.lower() or "NOT a cancellation" in verdict


def test_aec_verdict_floor_limited_when_attenuation_near_ceiling() -> None:
    # Real room with audible speakers: positive ceiling, attenuation at it.
    verdict = interpret_aec_stats(attenuation_db=4.1, ceiling_db=4.6)
    assert "FLOOR-LIMITED" in verdict and "success" in verdict


def test_aec_verdict_floor_limited_when_attenuation_exceeds_ceiling() -> None:
    # AEC3's residual suppressor can gate below the ambient floor.
    verdict = interpret_aec_stats(attenuation_db=5.7, ceiling_db=4.7)
    assert "FLOOR-LIMITED" in verdict


def test_aec_verdict_under_cancelling_when_headroom_remains() -> None:
    # Positive ceiling but attenuation well below it -> real residual echo.
    verdict = interpret_aec_stats(attenuation_db=2.0, ceiling_db=15.0)
    assert "UNDER-CANCELLING" in verdict
    assert "13.0dB" in verdict  # 15.0 - 2.0


# --- overlap-gate grace window, extended by the last response's AEC
# verdict ([E8]: a mic+speaker session stayed UNDER-CANCELLING almost the
# whole time even after fixing the delay hint, so residual echo in the
# reverb tail right after playback is a real remaining risk) ---

from scripts.run_convobox import _MAX_GRACE_S, grace_s_for_last_response  # noqa: E402


def test_grace_unchanged_without_numbers() -> None:
    assert grace_s_for_last_response(None, None) == ECHO_GRACE_S
    assert grace_s_for_last_response(4.0, None) == ECHO_GRACE_S
    assert grace_s_for_last_response(None, 4.0) == ECHO_GRACE_S


def test_grace_unchanged_when_no_echo_detected() -> None:
    # Same threshold as interpret_aec_stats's NO ECHO DETECTED case --
    # nothing measurable means nothing to extend the window for.
    assert grace_s_for_last_response(attenuation_db=8.2, ceiling_db=-0.3) == ECHO_GRACE_S


def test_grace_unchanged_when_floor_limited() -> None:
    # Same threshold as interpret_aec_stats's FLOOR-LIMITED case -- fully
    # cancelled echo is no reason to protect a longer window.
    assert grace_s_for_last_response(attenuation_db=4.1, ceiling_db=4.6) == ECHO_GRACE_S
    assert grace_s_for_last_response(attenuation_db=5.7, ceiling_db=4.7) == ECHO_GRACE_S


def test_grace_extends_proportionally_when_under_cancelling() -> None:
    # 13dB of headroom remaining -> base + 13*0.05 = 0.3 + 0.65 = 0.95s.
    grace = grace_s_for_last_response(attenuation_db=2.0, ceiling_db=15.0)
    assert grace == pytest.approx(0.95)
    assert grace > ECHO_GRACE_S


def test_grace_is_capped_regardless_of_how_bad_the_reading_is() -> None:
    # A single very bad reading must not suppress listening indefinitely.
    grace = grace_s_for_last_response(attenuation_db=0.0, ceiling_db=100.0)
    assert grace == _MAX_GRACE_S


def test_grace_respects_a_custom_base() -> None:
    assert grace_s_for_last_response(None, None, base_grace_s=0.5) == 0.5
    grace = grace_s_for_last_response(attenuation_db=2.0, ceiling_db=15.0, base_grace_s=0.5)
    assert grace == pytest.approx(min(0.5 + 13.0 * 0.05, _MAX_GRACE_S))


# --- stage-2 signal-level echo handling: EchoTailGuard ---


def test_tail_guard_opens_nothing_for_silent_endpoint() -> None:
    # Headset / dead output: ceiling below the measurable threshold.
    g = EchoTailGuard()
    now = 1000.0
    g.set_echo_profile(attenuation_db=8.2, ceiling_db=-0.3, delay_s=0.1, now=now)
    g.observe(playing=False, now=now)
    assert g.is_closed() is False


def test_tail_guard_opens_nothing_when_aec_floor_limited() -> None:
    # Echo reached the mic but AEC cancelled it to the floor (residual <=
    # margin) -> nothing leaking, so no extra tail.
    g = EchoTailGuard()
    now = 1000.0
    g.set_echo_profile(attenuation_db=4.6, ceiling_db=4.6, delay_s=0.1, now=now)
    g.observe(playing=False, now=now)
    assert g.is_closed() is False


def test_tail_guard_opens_tail_when_aec_under_cancels() -> None:
    # Echo reached the mic AND AEC left real headroom -> open a bounded
    # tail so the reverb of our own voice is suppressed.
    g = EchoTailGuard()
    now = 1000.0
    g.set_echo_profile(attenuation_db=2.0, ceiling_db=15.0, delay_s=0.22, now=now)
    assert g.observe(playing=False, now=now) is True
    assert g.is_closed() is True
    # Tail length scales with the acoustic path (delay) but is capped.
    assert 0.3 <= g._tail_len <= EchoTailGuard.CAP_S + 1e-9


def test_tail_guard_reopens_while_still_playing() -> None:
    # During playback the gate stays open and re-anchors the tail clock.
    g = EchoTailGuard()
    now = 1000.0
    g.set_echo_profile(attenuation_db=2.0, ceiling_db=15.0, delay_s=0.22, now=now)
    assert g.observe(playing=True, now=now) is False
    assert g.is_closed() is False


def test_tail_guard_releases_after_window() -> None:
    g = EchoTailGuard()
    now = 1000.0
    g.set_echo_profile(attenuation_db=2.0, ceiling_db=15.0, delay_s=0.22, now=now)
    g.observe(playing=False, now=now)  # open the tail
    # Just past the tail end, the gate reopens.
    assert g.observe(playing=False, now=now + g._tail_len + 0.01) is False
    assert g.is_closed() is False


def test_tail_guard_tail_is_capped() -> None:
    # A huge measured delay must not produce an unbounded tail.
    g = EchoTailGuard()
    now = 1000.0
    g.set_echo_profile(attenuation_db=0.0, ceiling_db=30.0, delay_s=10.0, now=now)
    assert g._tail_len <= EchoTailGuard.CAP_S + 1e-9
