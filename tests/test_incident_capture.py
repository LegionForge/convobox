from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

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
