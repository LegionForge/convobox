from __future__ import annotations

from pathlib import Path

import pytest

from convobox.config import TTSConfig
from convobox.tts import factory as factory_module
from convobox.tts.factory import create_tts_engine, resolve_voice_paths


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_resolve_voice_paths_returns_existing_files(tmp_path: Path) -> None:
    _touch(tmp_path / "en_US-lessac-medium.onnx")
    _touch(tmp_path / "en_US-lessac-medium.onnx.json")

    model, config = resolve_voice_paths("en_US-lessac-medium", tmp_path)

    assert model == tmp_path / "en_US-lessac-medium.onnx"
    assert config == tmp_path / "en_US-lessac-medium.onnx.json"


def test_resolve_voice_paths_missing_model_raises_with_download_hint(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"voice_picker\.py --download en_US-lessac-medium"):
        resolve_voice_paths("en_US-lessac-medium", tmp_path)


def test_resolve_voice_paths_missing_config_raises(tmp_path: Path) -> None:
    _touch(tmp_path / "en_US-lessac-medium.onnx")
    # config .json intentionally not created
    with pytest.raises(FileNotFoundError):
        resolve_voice_paths("en_US-lessac-medium", tmp_path)


def test_create_tts_engine_rejects_unset_voice(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tts.voice is not set"):
        create_tts_engine(TTSConfig(voice=None), voices_dir=tmp_path)


def test_create_tts_engine_rejects_unknown_engine(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="kokoro"):
        create_tts_engine(TTSConfig(engine="kokoro", voice="x"), voices_dir=tmp_path)


def test_create_tts_engine_constructs_piper_with_resolved_paths_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _touch(tmp_path / "en_US-lessac-medium.onnx")
    _touch(tmp_path / "en_US-lessac-medium.onnx.json")

    calls: list[dict[str, object]] = []

    class _FakePiperTTSEngine:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(factory_module, "PiperTTSEngine", _FakePiperTTSEngine)

    engine = create_tts_engine(
        TTSConfig(voice="en_US-lessac-medium", rate=1.5, volume=0.8),
        voices_dir=tmp_path,
    )

    assert isinstance(engine, _FakePiperTTSEngine)
    assert calls == [
        {
            "model_path": str(tmp_path / "en_US-lessac-medium.onnx"),
            "config_path": str(tmp_path / "en_US-lessac-medium.onnx.json"),
            "rate": 1.5,
            "volume": 0.8,
        }
    ]
