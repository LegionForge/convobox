from __future__ import annotations

import pytest

from convobox.adapters import OpenCodeAdapter, create_backend_adapter
from convobox.config import BackendConfig


def test_create_backend_adapter_opencode() -> None:
    adapter = create_backend_adapter(BackendConfig(name="opencode", url="http://localhost:9999"))
    assert isinstance(adapter, OpenCodeAdapter)
    assert adapter._base_url == "http://localhost:9999"
    assert adapter._model_ref is None


def test_create_backend_adapter_opencode_passes_model_through() -> None:
    adapter = create_backend_adapter(
        BackendConfig(name="opencode", url="http://localhost:9999", model="openai/gpt-5.6-sol")
    )
    assert isinstance(adapter, OpenCodeAdapter)
    assert adapter._model_ref == {"providerID": "openai", "id": "gpt-5.6-sol"}


def test_create_backend_adapter_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="gemini-cli"):
        create_backend_adapter(BackendConfig(name="gemini-cli"))


def test_backend_config_model_without_a_slash_rejected_at_load() -> None:
    with pytest.raises(ValueError, match="provider/model-id"):
        BackendConfig(model="gpt-5.6-sol")


def test_backend_config_model_none_is_the_default() -> None:
    assert BackendConfig().model is None
