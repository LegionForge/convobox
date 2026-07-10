from convobox.adapters.base import BackendAdapter
from convobox.adapters.opencode import OpenCodeAdapter
from convobox.config import BackendConfig

__all__ = ["BackendAdapter", "OpenCodeAdapter", "create_backend_adapter"]


def create_backend_adapter(config: BackendConfig) -> BackendAdapter:
    if config.name == "opencode":
        return OpenCodeAdapter(config.url)
    raise ValueError(f"unknown backend.name {config.name!r} (only 'opencode' is implemented)")
