from __future__ import annotations

from pathlib import Path

import pytest

from convobox.config import TTSConfig
from convobox.tts import factory as factory_module
from convobox.tts.factory import create_tts_engine, resolve_voice_paths


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


class _FakeStreamResponse:
    """Mimics the httpx.stream(...) context manager's return value --
    just enough surface (raise_for_status, iter_bytes) for
    _download_kokoro_asset's real body to run against, without a real
    network call.
    """

    def __init__(self, chunks: list[bytes], status_error: Exception | None = None) -> None:
        self._chunks = chunks
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


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


def test_create_tts_engine_rejects_unset_piper_voice(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tts.voice is not set"):
        create_tts_engine(TTSConfig(engine="piper", voice=None), voices_dir=tmp_path)


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


def test_resolve_kokoro_model_paths_incomplete_download_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # _download_kokoro_asset "succeeds" (no exception) but doesn't actually
    # produce the file -- must not be silently treated as success, same
    # discipline as resolve_voice_paths' own incomplete-download check.
    monkeypatch.setattr(factory_module, "_download_kokoro_asset", lambda filename, dest: None)

    with pytest.raises(FileNotFoundError, match="did not download successfully"):
        factory_module.resolve_kokoro_model_paths(
            str(tmp_path / "model.onnx"), str(tmp_path / "voices.bin")
        )


# --- _download_kokoro_asset itself: the tests above all monkeypatch this
# function away, so its own body (the real httpx streaming, tmp-file-then-
# rename, and error-cleanup logic) had zero coverage -- these exercise it
# directly against a fake httpx.stream response instead. ---


def test_download_kokoro_asset_streams_to_a_tmp_file_then_renames_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "kokoro-v1.0.onnx"
    captured: dict[str, object] = {}

    def _fake_stream(method: str, url: str, **kwargs: object) -> _FakeStreamResponse:
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeStreamResponse([b"chunk-a-", b"chunk-b"])

    monkeypatch.setattr(factory_module.httpx, "stream", _fake_stream)

    factory_module._download_kokoro_asset("kokoro-v1.0.onnx", dest)

    assert dest.read_bytes() == b"chunk-a-chunk-b"
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    assert captured["method"] == "GET"
    assert captured["url"] == f"{factory_module._KOKORO_RELEASE_BASE}/kokoro-v1.0.onnx"
    assert captured["kwargs"]["follow_redirects"] is True


def test_download_kokoro_asset_cleans_up_tmp_file_on_http_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "kokoro-v1.0.onnx"

    def _fake_stream(method: str, url: str, **kwargs: object) -> _FakeStreamResponse:
        return _FakeStreamResponse([], status_error=RuntimeError("404 not found"))

    monkeypatch.setattr(factory_module.httpx, "stream", _fake_stream)

    with pytest.raises(FileNotFoundError, match="could not be downloaded automatically"):
        factory_module._download_kokoro_asset("kokoro-v1.0.onnx", dest)

    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


# --- list_kokoro_voices: reads voice names directly out of the downloaded
# voices archive without constructing a full KokoroTTSEngine (which would
# load the ~326MB ONNX model just to call Kokoro.get_voices()). Uses a
# real np.savez archive as the fixture -- same file format kokoro-onnx's
# own np.load(voices_path) reads, verified live against the actual
# downloaded voices-v1.0.bin (54 real voice names) during development,
# not guessed. ---


def test_list_kokoro_voices_reads_names_from_a_real_npz_archive(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    voices_path = tmp_path / "voices.bin"
    np.savez(
        voices_path,
        af_sarah=np.zeros((1, 1), dtype=np.float32),
        am_adam=np.zeros((1, 1), dtype=np.float32),
    )
    # np.savez always appends .npz; rename to match the real asset's
    # actual (extensionless-of-npz) filename convention.
    voices_path.with_suffix(".bin.npz").rename(voices_path)

    assert factory_module.list_kokoro_voices(str(voices_path)) == ["af_sarah", "am_adam"]


def test_list_kokoro_voices_returns_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert factory_module.list_kokoro_voices(str(tmp_path / "does-not-exist.bin")) == []


def test_list_kokoro_voices_returns_empty_for_a_corrupt_file(tmp_path: Path) -> None:
    voices_path = tmp_path / "voices.bin"
    voices_path.write_bytes(b"not a real npz archive")

    assert factory_module.list_kokoro_voices(str(voices_path)) == []


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
