from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from convobox.config import STTConfig
from convobox.stt import STTEngine, TranscriptResult, create_stt_engine
from convobox.stt.base import STTEngine as STTEngineBase


class _FakeTranscriber(STTEngineBase):
    def __init__(self, config: STTConfig) -> None:
        self.config = config

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:  # pragma: no cover
        raise NotImplementedError


def test_stt_config_defaults_to_faster_whisper() -> None:
    assert STTConfig().engine == "faster-whisper"


@pytest.mark.parametrize("name", ["faster-whisper", "whisper", "faster_whisper"])
def test_create_stt_engine_dispatches_faster_whisper(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the lazily-imported class so no real Whisper model is loaded.
    import convobox.stt.transcriber as transcriber_module

    monkeypatch.setattr(transcriber_module, "LocalTranscriber", _FakeTranscriber)
    engine = create_stt_engine(STTConfig(engine=name))
    assert isinstance(engine, _FakeTranscriber)
    assert isinstance(engine, STTEngine)


def test_create_stt_engine_unknown_engine_raises() -> None:
    with pytest.raises(ValueError, match="unknown stt.engine"):
        create_stt_engine(STTConfig(engine="deepspeech"))


def test_local_transcriber_is_an_stt_engine() -> None:
    # Importing the class must NOT load a model (that happens in __init__).
    from convobox.stt import LocalTranscriber

    assert issubclass(LocalTranscriber, STTEngine)


def test_transcript_result_backward_compatible_import() -> None:
    # Moved to convobox.stt.base but must still import from its old home.
    from convobox.stt.base import TranscriptResult as FromBase
    from convobox.stt.transcriber import TranscriptResult as FromTranscriber

    assert FromBase is FromTranscriber


def test_transcript_result_shape_unchanged() -> None:
    r = TranscriptResult(
        text="hi", language="en", language_probability=0.9,
        latency_ms=12.0, duration_s=1.0, avg_logprob=-0.5,
    )
    assert r.text == "hi" and r.segments == []
    fields: dict[str, Any] = r.__dict__ if hasattr(r, "__dict__") else {}
    _ = fields  # frozen dataclass: shape asserted via construction above
