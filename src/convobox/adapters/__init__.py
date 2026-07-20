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
        # Neither permission_mode nor working_dir is passed: opencode is a
        # pre-launched HTTP server, not a subprocess ConvoBox spawns, so
        # both its permissions and its directory are fixed by wherever
        # `opencode serve` was started. run_convobox.py warns if
        # permission_mode is non-default here; enforcing a workspace for
        # opencode means launching the server from it.
        return OpenCodeAdapter(config.url, model=config.model)
    if config.name == "claude-code":
        return ClaudeCodeAdapter(
            config.command,
            permission_mode=config.permission_mode,
            working_dir=config.working_dir,
        )
    if config.name == "codex":
        return CodexAdapter(
            config.command,
            permission_mode=config.permission_mode,
            working_dir=config.working_dir,
        )
    raise ValueError(
        f"unknown backend.name {config.name!r} "
        "(implemented: 'opencode', 'claude-code', 'codex')"
    )
