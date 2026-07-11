from __future__ import annotations

import pytest

from convobox.adapters import OpenCodeAdapter, create_backend_adapter
from convobox.config import BackendConfig


def test_create_backend_adapter_opencode() -> None:
    adapter = create_backend_adapter(BackendConfig(name="opencode", url="http://localhost:9999"))
    assert isinstance(adapter, OpenCodeAdapter)
    assert adapter._base_url == "http://localhost:9999"


def test_create_backend_adapter_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="claude-code"):
        create_backend_adapter(BackendConfig(name="claude-code"))
