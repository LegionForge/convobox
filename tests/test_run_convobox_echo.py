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
