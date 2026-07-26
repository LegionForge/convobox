from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

import convobox.audio.incident_capture as incident_capture_module
from convobox.audio.incident_capture import IncidentCapture


def _frames(path: Path) -> int:
    with wave.open(str(path), "rb") as reader:
        assert reader.getframerate() == 16000
        return reader.getnframes()


def test_incident_capture_writes_bounded_preroll_postroll_and_manifest(tmp_path: Path) -> None:
    capture = IncidentCapture(tmp_path, before_s=0.001, after_s=10)
    audio = np.linspace(-0.5, 0.5, 20, dtype=np.float32)
    capture.observe_mic(audio, audio / 2)
    capture.observe_reference(audio, 16000)

    directory = capture.trigger("barge-in", {"vad_threshold": 0.5})
    assert directory is not None
    capture.observe_mic(audio, audio / 2)
    capture.close()

    assert _frames(directory / "mic-raw.wav") == 36  # 16-sample pre-roll + 20 post-roll
    assert _frames(directory / "mic-processed.wav") == 36
    assert _frames(directory / "reference.wav") == 16
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["events"] == [{"event": "barge-in", "metadata": {"vad_threshold": 0.5}}]


def test_related_events_share_one_incident(tmp_path: Path) -> None:
    capture = IncidentCapture(tmp_path, before_s=1, after_s=1)
    first = capture.trigger("barge-in")
    assert first is not None
    assert capture.trigger("self-barge-in", {"gate": "echo"}) is None
    capture.close()

    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert [event["event"] for event in manifest["events"]] == ["barge-in", "self-barge-in"]


@pytest.mark.parametrize("before_s,after_s", [(0, 1), (1, 0)])
def test_incident_capture_rejects_nonpositive_windows(tmp_path: Path, before_s: float, after_s: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        IncidentCapture(tmp_path, before_s=before_s, after_s=after_s)


def test_active_reflects_pre_and_post_trigger_state(tmp_path: Path) -> None:
    capture = IncidentCapture(tmp_path, before_s=1, after_s=1)
    assert capture.active is False
    capture.trigger("barge-in")
    assert capture.active is True
    capture.close()
    assert capture.active is False


def test_close_before_any_trigger_is_a_safe_noop(tmp_path: Path) -> None:
    capture = IncidentCapture(tmp_path, before_s=1, after_s=1)
    capture.close()  # never triggered -- must not raise
    assert capture.active is False


def test_close_is_idempotent(tmp_path: Path) -> None:
    capture = IncidentCapture(tmp_path, before_s=1, after_s=1)
    capture.trigger("barge-in")
    capture.close()
    capture.close()  # already closed -- must not raise
    assert capture.active is False


def test_observe_reference_resamples_to_the_internal_16k_rate(tmp_path: Path) -> None:
    # A far-end reference captured at a device rate other than 16kHz (e.g. a
    # 48kHz WASAPI output) must land in the wav file at 16kHz, matching
    # mic-raw/mic-processed -- observe_reference's job is exactly this
    # resample, not just a passthrough.
    capture = IncidentCapture(tmp_path, before_s=1, after_s=10)
    audio_48k = np.linspace(-0.5, 0.5, 480, dtype=np.float32)  # 10ms @ 48kHz

    capture.observe_reference(audio_48k, sample_rate=48000)
    directory = capture.trigger("barge-in")
    capture.close()

    assert directory is not None
    # 10ms of audio resampled to 16kHz is 160 frames, not the original 480.
    assert _frames(directory / "reference.wav") == 160


def test_history_evicts_a_whole_stale_chunk_when_it_fits_within_the_excess(
    tmp_path: Path,
) -> None:
    # Two separate observe_mic calls -- a small early chunk, then a much
    # bigger one that pushes the retained pre-roll over budget by more than
    # the small chunk's own length. The eviction loop must drop that whole
    # first chunk in one step (not just trim it), then partially trim the
    # second chunk down to the remaining budget.
    limit_samples = 10
    capture = IncidentCapture(tmp_path, before_s=limit_samples / 16000, after_s=10)
    small_chunk = np.linspace(-0.1, 0.1, 5, dtype=np.float32)
    big_chunk = np.linspace(-0.5, 0.5, 20, dtype=np.float32)

    capture.observe_mic(small_chunk, small_chunk)
    capture.observe_mic(big_chunk, big_chunk)
    directory = capture.trigger("barge-in")
    capture.close()

    assert directory is not None
    # Retained pre-roll settles at exactly the budget -- the whole 5-sample
    # chunk was dropped, then the 20-sample chunk trimmed down to 10.
    assert _frames(directory / "mic-raw.wav") == limit_samples


def test_auto_closes_once_the_post_roll_window_elapses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = IncidentCapture(tmp_path, before_s=1, after_s=10)
    fake_now = 1000.0
    monkeypatch.setattr(incident_capture_module.time, "monotonic", lambda: fake_now)
    capture.trigger("barge-in")
    assert capture.active is True

    fake_now = 1000.0 + 10.0  # exactly at (and past) _ends_at
    audio = np.linspace(-0.1, 0.1, 4, dtype=np.float32)
    capture.observe_mic(audio, audio)

    assert capture.active is False
