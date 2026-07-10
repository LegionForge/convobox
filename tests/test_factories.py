from __future__ import annotations

import pytest

from convobox.adapters import OpenCodeAdapter, create_backend_adapter
from convobox.config import BackendConfig, TTSConfig
from convobox.tts import create_tts_engine
from convobox.tts.piper import PiperTTSEngine


def test_create_tts_engine_piper_passes_through_paths() -> None:
    config = TTSConfig(engine="piper", model_path="unused.onnx", config_path="unused.json")
    called: dict[str, object] = {}

    def fake_load(self: PiperTTSEngine) -> object:
        called["model_path"] = self._model_path
        called["config_path"] = self._config_path
        return type("FakeVoice", (), {"config": type("C", (), {"sample_rate": 16000})()})()

    PiperTTSEngine._load_voice = fake_load  # type: ignore[method-assign]
    try:
        result = create_tts_engine(config)
    finally:
        del PiperTTSEngine._load_voice

    assert isinstance(result, PiperTTSEngine)
    assert called["model_path"] == "unused.onnx"
    assert called["config_path"] == "unused.json"


def test_create_tts_engine_unknown_engine_raises() -> None:
    with pytest.raises(ValueError, match="kokoro"):
        create_tts_engine(TTSConfig(engine="kokoro"))


def test_create_backend_adapter_opencode() -> None:
    adapter = create_backend_adapter(BackendConfig(name="opencode", url="http://localhost:9999"))
    assert isinstance(adapter, OpenCodeAdapter)
    assert adapter._base_url == "http://localhost:9999"


def test_create_backend_adapter_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="claude-code"):
        create_backend_adapter(BackendConfig(name="claude-code"))
