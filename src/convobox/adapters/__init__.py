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
        # permission_mode not passed: opencode's permissions are fixed by
        # wherever `opencode serve` was launched, not something ConvoBox can
        # set per-session. run_convobox.py warns if it's non-default here.
        return OpenCodeAdapter(config.url, model=config.model)
    if config.name == "claude-code":
        return ClaudeCodeAdapter(config.command, permission_mode=config.permission_mode)
    if config.name == "codex":
        return CodexAdapter(config.command, permission_mode=config.permission_mode)
    raise ValueError(
        f"unknown backend.name {config.name!r} "
        "(implemented: 'opencode', 'claude-code', 'codex')"
    )
