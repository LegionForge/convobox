from convobox.adapters.base import BackendAdapter
from convobox.adapters.claude_code import ClaudeCodeAdapter
from convobox.adapters.codex import CodexAdapter
from convobox.adapters.opencode import OpenCodeAdapter
from convobox.config import BackendConfig

__all__ = [
    "BackendAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "OpenCodeAdapter",
    "create_backend_adapter",
]


def create_backend_adapter(config: BackendConfig) -> BackendAdapter:
    if config.name == "opencode":
        return OpenCodeAdapter(config.url, model=config.model)
    if config.name == "claude-code":
        return ClaudeCodeAdapter(config.command)
    if config.name == "codex":
        return CodexAdapter(config.command)
    raise ValueError(
        f"unknown backend.name {config.name!r} "
        "(implemented: 'opencode', 'claude-code', 'codex')"
    )
