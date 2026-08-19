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
    config: BackendConfig,
    *,
    mcp_url: str | None = None,
    mcp_token: str | None = None,
) -> BackendAdapter:
    # permission_mode is only honored by claude-code and codex today (see
    # each adapter's module docstring) -- opencode silently ignores it
    # rather than erroring, same "not every adapter can do everything"
    # stance as wait_listening's default no-op. ClaudeCodeAdapter derives
    # its own hook-wiring decision from permission_mode internally (see
    # its __init__) -- no separate flag needed here.
    #
    # mcp_url/mcp_token (the "show_document" tool, web/mcp_server.py) are
    # claude-code-only so far -- see docs/ARTIFACT-PANE-SCOPE.md's "Agent-
    # Initiated Artifacts" section for why: opencode isn't a subprocess
    # ConvoBox spawns at all (no per-session config surface to inject a
    # server into), and codex's own MCP-server-registration mechanism is
    # unverified against the real CLI. Both silently ignored here rather
    # than erroring, same stance as permission_mode above.
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
            mcp_url=mcp_url,
            mcp_token=mcp_token,
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
