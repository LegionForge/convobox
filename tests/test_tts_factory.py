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


def test_resolve_voice_paths_downloads_a_missing_voice_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("piper", reason="piper-tts is GPL-3.0, opt-in only (uv sync --extra piper)")
    calls: list[tuple[str, Path]] = []

    def _fake_download_voice(key: str, voices_dir: Path) -> None:
        calls.append((key, voices_dir))
        _touch(voices_dir / f"{key}.onnx")
        _touch(voices_dir / f"{key}.onnx.json")

    monkeypatch.setattr("piper.download_voices.download_voice", _fake_download_voice)

    model, config = resolve_voice_paths("en_US-lessac-medium", tmp_path)

    assert calls == [("en_US-lessac-medium", tmp_path)]
    assert model == tmp_path / "en_US-lessac-medium.onnx"
    assert config == tmp_path / "en_US-lessac-medium.onnx.json"


def test_resolve_voice_paths_does_not_redownload_an_existing_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("piper", reason="piper-tts is GPL-3.0, opt-in only (uv sync --extra piper)")
    _touch(tmp_path / "en_US-lessac-medium.onnx")
    _touch(tmp_path / "en_US-lessac-medium.onnx.json")
    calls: list[str] = []
    monkeypatch.setattr(
        "piper.download_voices.download_voice",
        lambda key, voices_dir: calls.append(key),
    )

    resolve_voice_paths("en_US-lessac-medium", tmp_path)

    assert calls == []


def test_resolve_voice_paths_download_failure_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("piper", reason="piper-tts is GPL-3.0, opt-in only (uv sync --extra piper)")

    def _fail(key: str, voices_dir: Path) -> None:
        raise RuntimeError("404 not found")

    monkeypatch.setattr("piper.download_voices.download_voice", _fail)

    with pytest.raises(FileNotFoundError, match=r"could not be downloaded automatically"):
        resolve_voice_paths("nonexistent-voice", tmp_path)


def test_resolve_voice_paths_incomplete_download_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("piper", reason="piper-tts is GPL-3.0, opt-in only (uv sync --extra piper)")
    # download_voice "succeeds" (no exception) but doesn't actually produce
    # both expected files -- must not be silently treated as success.
    monkeypatch.setattr(
        "piper.download_voices.download_voice", lambda key, voices_dir: None
    )

    with pytest.raises(FileNotFoundError, match="did not produce the expected files"):
        resolve_voice_paths("en_US-lessac-medium", tmp_path)


def test_create_tts_engine_rejects_unset_voice(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tts.voice is not set"):
        create_tts_engine(TTSConfig(voice=None), voices_dir=tmp_path)


def test_create_tts_engine_rejects_unknown_engine(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="bogus"):
        create_tts_engine(TTSConfig(engine="bogus", voice="x"), voices_dir=tmp_path)


def test_create_tts_engine_constructs_kokoro_with_config_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model.onnx"
    voices_path = tmp_path / "voices.bin"
    _touch(model_path)
    _touch(voices_path)

    calls: list[dict[str, object]] = []

    class _FakeKokoroTTSEngine:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(factory_module, "KokoroTTSEngine", _FakeKokoroTTSEngine)

    engine = create_tts_engine(
        TTSConfig(
            engine="kokoro",
            voice="af_sarah",
            rate=1.2,
            model_path=str(model_path),
            voices_path=str(voices_path),
            language="en-us",
        ),
        voices_dir=tmp_path,
    )

    assert isinstance(engine, _FakeKokoroTTSEngine)
    assert calls == [
        {
            "model_path": str(model_path),
            "voices_path": str(voices_path),
            "voice": "af_sarah",
            "speed": 1.2,
            "lang": "en-us",
        }
    ]


def test_create_tts_engine_kokoro_requires_a_voice(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="voice"):
        create_tts_engine(TTSConfig(engine="kokoro", voice=None), voices_dir=tmp_path)


# --- resolve_kokoro_model_paths: the shared model/voices files auto-download
# on first use, same convention as resolve_voice_paths for Piper (see
# docs/ROADMAP.md's "carry the same convention to Kokoro's engine factory
# once it lands"). Unlike Piper's per-voice catalog, there's no voice name
# to look up -- just these two fixed files. ---


def test_resolve_kokoro_model_paths_returns_existing_files_without_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model.onnx"
    voices_path = tmp_path / "voices.bin"
    _touch(model_path)
    _touch(voices_path)

    def _fail_if_called(filename: str, dest: Path) -> None:
        raise AssertionError(f"should not download {filename!r}, files already exist")

    monkeypatch.setattr(factory_module, "_download_kokoro_asset", _fail_if_called)

    model, voices = factory_module.resolve_kokoro_model_paths(
        str(model_path), str(voices_path)
    )

    assert model == model_path
    assert voices == voices_path


def test_resolve_kokoro_model_paths_downloads_missing_files_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model.onnx"
    voices_path = tmp_path / "voices.bin"

    calls: list[str] = []

    def _fake_download(filename: str, dest: Path) -> None:
        calls.append(filename)
        _touch(dest)

    monkeypatch.setattr(factory_module, "_download_kokoro_asset", _fake_download)

    model, voices = factory_module.resolve_kokoro_model_paths(
        str(model_path), str(voices_path)
    )

    assert calls == [factory_module._KOKORO_MODEL_FILENAME, factory_module._KOKORO_VOICES_FILENAME]
    assert model == model_path
    assert voices == voices_path


def test_resolve_kokoro_model_paths_download_failure_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_download(filename: str, dest: Path) -> None:
        raise FileNotFoundError(f"asset {filename!r} could not be downloaded automatically (boom)")

    monkeypatch.setattr(factory_module, "_download_kokoro_asset", _fake_download)

    with pytest.raises(FileNotFoundError, match="could not be downloaded automatically"):
        factory_module.resolve_kokoro_model_paths(
            str(tmp_path / "model.onnx"), str(tmp_path / "voices.bin")
        )


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
        TTSConfig(engine="piper", voice="en_US-lessac-medium", rate=1.5, volume=0.8),
        voices_dir=tmp_path,
    )

    assert isinstance(engine, _FakePiperTTSEngine)
    assert calls == [
        {
            "model_path": str(tmp_path / "en_US-lessac-medium.onnx"),
            "config_path": str(tmp_path / "en_US-lessac-medium.onnx.json"),
            "rate": 1.5,
            "volume": 0.8,
            "speaker": None,
        }
    ]


def test_create_tts_engine_passes_speaker_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _touch(tmp_path / "en_GB-semaine-medium.onnx")
    _touch(tmp_path / "en_GB-semaine-medium.onnx.json")

    calls: list[dict[str, object]] = []

    class _FakePiperTTSEngine:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(factory_module, "PiperTTSEngine", _FakePiperTTSEngine)

    create_tts_engine(
        TTSConfig(engine="piper", voice="en_GB-semaine-medium", speaker="prudence"),
        voices_dir=tmp_path,
    )

    assert calls[0]["speaker"] == "prudence"
