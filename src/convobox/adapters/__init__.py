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


def create_backend_adapter(
    config: BackendConfig, *, interactive_approval: bool = False
) -> BackendAdapter:
    # interactive_approval is only honored by claude-code today (see
    # ClaudeCodeAdapter's module docstring) -- opencode/codex silently
    # ignore it rather than erroring, same "not every adapter can do
    # everything" stance as wait_listening's default no-op.
    if config.name == "opencode":
        # working_dir deliberately not passed: opencode is a pre-launched
        # HTTP server, not a subprocess ConvoBox spawns, so its directory
        # is fixed by wherever `opencode serve` was started. Enforcing a
        # workspace for opencode means launching the server from it.
        return OpenCodeAdapter(config.url, model=config.model)
    if config.name == "claude-code":
        return ClaudeCodeAdapter(
            config.command,
            working_dir=config.working_dir,
            interactive_approval=interactive_approval,
        )
    if config.name == "codex":
        return CodexAdapter(config.command, working_dir=config.working_dir)
    raise ValueError(
        f"unknown backend.name {config.name!r} "
        "(implemented: 'opencode', 'claude-code', 'codex')"
    )
