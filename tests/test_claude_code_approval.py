"""Voice-gated tool approval for the claude-code backend (Phase 3,
docs/DESIGN-0.3.0-interaction-and-safety.md) -- see claude_code.py's
module docstring for the live-verified mechanism this exercises.

Most tests here talk to the adapter's internals directly (_ensure_approval_
server, _events, _approval_token) rather than through a real spawned
subprocess: fake_claude_cli.py doesn't itself run a PreToolUse hook (it
just echoes prompts), so there is no meaningful "real subprocess" version
of "a hook connects" to test against -- the adapter's own TCP server and
event-queue plumbing is the actual unit under test, same as
test_claude_code_adapter.py tests _resolve_flags/_safe_json_loads as bare
functions rather than only through a live process.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from unittest.mock import AsyncMock, MagicMock

from convobox.adapters.base import BackendEventType
from convobox.adapters.claude_code import (
    ClaudeCodeAdapter,
    _APPROVAL_DECISION_TIMEOUT_S,
    _APPROVAL_HOST,
    _parse_mcp_list_output,
    _resolve_flags,
)

_FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_claude_cli.py"))]


def _adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(_FAKE_CLI, permission_mode="approve")


async def _connect(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", port)


# --- _resolve_flags: permission_mode="approve" changes the CLI flag
# (plan -> acceptEdits, see module docstring finding 2), never overrides
# an explicit user choice ---


def test_resolve_flags_approve_defaults_to_accept_edits() -> None:
    flags = _resolve_flags(["claude"], "approve")
    assert flags[flags.index("--permission-mode") + 1] == "acceptEdits"


def test_resolve_flags_approve_respects_explicit_user_mode() -> None:
    flags = _resolve_flags(["claude", "--permission-mode", "bypassPermissions"], "approve")
    assert flags.count("--permission-mode") == 0
    assert "acceptEdits" not in flags


def test_resolve_flags_default_plan_when_unset() -> None:
    flags = _resolve_flags(["claude"])
    assert flags[flags.index("--permission-mode") + 1] == "plan"


def test_resolve_flags_permissive_also_maps_to_accept_edits() -> None:
    # "permissive" and "approve" share the same underlying CLI flag --
    # only whether the hook gets wired up differs (see
    # _PERMISSION_CLAUDE_MODE's own comment).
    flags = _resolve_flags(["claude"], "permissive")
    assert flags[flags.index("--permission-mode") + 1] == "acceptEdits"


# --- the --settings file: valid JSON wiring the hook via `-m`, no
# "matcher" (confirmed live: omitting it gates ALL tools) ---


def test_ensure_settings_file_wires_the_hook_via_module_invocation() -> None:
    adapter = _adapter()
    path = adapter._ensure_settings_file()
    try:
        data = json.loads(path.read_text())
        pre_tool_use = data["hooks"]["PreToolUse"][0]
        hook = pre_tool_use["hooks"][0]
        assert hook["type"] == "command"
        assert "-m convobox.approval.hook_script" in hook["command"]
        assert sys.executable in hook["command"]
        assert "matcher" not in pre_tool_use
    finally:
        path.unlink()


def test_ensure_settings_file_is_written_once() -> None:
    adapter = _adapter()
    first = adapter._ensure_settings_file()
    try:
        second = adapter._ensure_settings_file()
        assert first == second
    finally:
        first.unlink()


@pytest.mark.asyncio
async def test_ensure_proc_wires_approval_env_and_settings_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every test above calls _ensure_approval_server/_ensure_settings_file
    # directly -- none of them go through _ensure_proc() itself, so a bug
    # in how IT assembles the env vars/--settings flag before the real
    # spawn (e.g. a typo'd env key, or forgetting to add the flag) would
    # slip past every one of them. This drives the real subprocess spawn
    # (same real fake_claude_cli.py every other adapter test uses) and
    # inspects exactly what reached it.
    adapter = _adapter()  # permission_mode="approve" -> _interactive_approval=True
    captured: dict[str, object] = {}
    real_spawn = asyncio.create_subprocess_exec

    async def capturing_spawn(*args: object, **kwargs: object):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return await real_spawn(*args, **kwargs)  # type: ignore[arg-type]

    import convobox.adapters.claude_code as mod

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", capturing_spawn)

    try:
        await adapter.send_text("hello")
        await asyncio.wait_for(adapter._events.get(), timeout=10.0)

        env = captured["env"]
        assert env is not None
        assert env["CONVOBOX_APPROVAL_HOST"] == _APPROVAL_HOST
        assert env["CONVOBOX_APPROVAL_TOKEN"] == adapter._approval_token
        assert env["CONVOBOX_APPROVAL_TIMEOUT_S"] == str(_APPROVAL_DECISION_TIMEOUT_S)
        assert int(env["CONVOBOX_APPROVAL_PORT"]) == adapter._approval_port

        args = captured["args"]
        assert "--settings" in args
        settings_arg = args[args.index("--settings") + 1]
        assert Path(settings_arg) == adapter._settings_path
    finally:
        await adapter.aclose()
        if adapter._settings_path is not None:
            adapter._settings_path.unlink(missing_ok=True)


# --- the approval TCP server + event queue ---


@pytest.mark.asyncio
async def test_approval_connection_emits_an_approval_request_event() -> None:
    adapter = _adapter()
    try:
        port = await adapter._ensure_approval_server()
        _, writer = await _connect(port)
        writer.write(
            json.dumps(
                {
                    "token": adapter._approval_token,
                    "tool_name": "Bash",
                    "tool_input": {"command": "rm -rf /tmp/x"},
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()

        event = await asyncio.wait_for(adapter._events.get(), timeout=5.0)
        assert event.type == BackendEventType.APPROVAL_REQUEST
        assert event.tool == "Bash"
        assert event.tool_input is not None and "rm -rf" in event.tool_input
        writer.close()
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_resolve_pending_approval_true_sends_allow_decision() -> None:
    adapter = _adapter()
    try:
        port = await adapter._ensure_approval_server()
        reader, writer = await _connect(port)
        writer.write(
            json.dumps(
                {"token": adapter._approval_token, "tool_name": "Bash", "tool_input": {}}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        await asyncio.wait_for(adapter._events.get(), timeout=5.0)  # the APPROVAL_REQUEST

        resolved = await adapter.resolve_pending_approval(True)
        assert resolved is True

        reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5.0))
        assert reply == {"decision": "allow"}
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_resolve_pending_approval_false_sends_deny_decision() -> None:
    adapter = _adapter()
    try:
        port = await adapter._ensure_approval_server()
        reader, writer = await _connect(port)
        writer.write(
            json.dumps(
                {"token": adapter._approval_token, "tool_name": "Write", "tool_input": {}}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        await asyncio.wait_for(adapter._events.get(), timeout=5.0)

        resolved = await adapter.resolve_pending_approval(False)
        assert resolved is True

        reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5.0))
        assert reply["decision"] == "deny"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_resolve_pending_approval_with_nothing_pending_returns_false() -> None:
    # A stale gate (e.g. a race with a timeout that already fired) must
    # not raise -- it just has nothing real to answer.
    adapter = _adapter()
    try:
        assert await adapter.resolve_pending_approval(True) is False
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_wrong_token_is_rejected_with_a_deny_and_no_event() -> None:
    adapter = _adapter()
    try:
        port = await adapter._ensure_approval_server()
        reader, writer = await _connect(port)
        writer.write(json.dumps({"token": "wrong-token", "tool_name": "Bash"}).encode() + b"\n")
        await writer.drain()

        reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5.0))
        assert reply["decision"] == "deny"
        # A spoofed/garbled connection must never surface a spoken
        # approval prompt for something that isn't a real gated tool call.
        assert adapter._events.qsize() == 0
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_second_concurrent_connection_is_denied_while_one_is_pending() -> None:
    # This adapter's turn model only has one tool call in flight at a
    # time (see module docstring) -- a second connection while one is
    # pending is a defensive case, answered immediately rather than
    # queued or silently dropped.
    adapter = _adapter()
    try:
        port = await adapter._ensure_approval_server()
        _, writer1 = await _connect(port)
        writer1.write(
            json.dumps({"token": adapter._approval_token, "tool_name": "Bash"}).encode() + b"\n"
        )
        await writer1.drain()
        await asyncio.wait_for(adapter._events.get(), timeout=5.0)

        reader2, writer2 = await _connect(port)
        writer2.write(
            json.dumps({"token": adapter._approval_token, "tool_name": "Write"}).encode() + b"\n"
        )
        await writer2.drain()
        reply = json.loads(await asyncio.wait_for(reader2.readline(), timeout=5.0))
        assert reply["decision"] == "deny"
        writer1.close()
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_aclose_denies_a_pending_approval_rather_than_hanging_it() -> None:
    adapter = _adapter()
    port = await adapter._ensure_approval_server()
    reader, writer = await _connect(port)
    writer.write(
        json.dumps({"token": adapter._approval_token, "tool_name": "Bash"}).encode() + b"\n"
    )
    await writer.drain()
    await asyncio.wait_for(adapter._events.get(), timeout=5.0)

    await adapter.aclose()

    reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5.0))
    assert reply["decision"] == "deny"


# --- MCP tool calls hit a SEPARATE permission gate from --permission-mode
# (live-confirmed 2026-07-22: even acceptEdits still rejects an MCP tool
# call with "you haven't granted it yet"). permissive mode grants every
# configured MCP server via --settings permissions.allow instead. ---


def test_parse_mcp_list_output_extracts_server_names() -> None:
    # Modeled on real captured output, 2026-07-22 (one name deliberately
    # includes periods, hyphens, and spaces -- confirmed splitting on the
    # FIRST ": " still isolates it correctly; a real connector name from
    # a live account had exactly this shape).
    text = (
        "Checking MCP server health...\n"
        "\n"
        "claude.ai Some Connector - Demo - 2026.01.07: "
        "https://example.com/mcp - ! Needs authentication\n"
        "claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - OK Connected\n"
        "obsidian: http://localhost:22360/sse (SSE) - OK Connected\n"
        "browseros: http://127.0.0.1:9000/mcp (HTTP) - FAILED Failed to connect\n"
    )
    assert _parse_mcp_list_output(text) == [
        "claude.ai Some Connector - Demo - 2026.01.07",
        "claude.ai Gmail",
        "obsidian",
        "browseros",
    ]


def test_parse_mcp_list_output_skips_the_header_and_blank_lines() -> None:
    assert _parse_mcp_list_output("Checking MCP server health...\n\n") == []


def test_parse_mcp_list_output_handles_no_servers_configured() -> None:
    assert _parse_mcp_list_output("") == []


@pytest.mark.asyncio
async def test_enumerate_mcp_server_names_returns_empty_on_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter(_FAKE_CLI, permission_mode="permissive")

    async def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("claude not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

    assert await adapter._enumerate_mcp_server_names() == []


@pytest.mark.asyncio
async def test_enumerate_mcp_server_names_parses_real_subprocess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter(_FAKE_CLI, permission_mode="permissive")

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(
        return_value=(b"obsidian: http://localhost:22360/sse (SSE) - OK Connected\n", b"")
    )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=fake_proc))

    assert await adapter._enumerate_mcp_server_names() == ["obsidian"]


@pytest.mark.asyncio
async def test_permissive_mode_writes_an_mcp_permissions_settings_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter(_FAKE_CLI, permission_mode="permissive")
    monkeypatch.setattr(
        adapter, "_enumerate_mcp_server_names", AsyncMock(return_value=["obsidian", "browseros"])
    )

    try:
        path = await adapter._ensure_mcp_permissions_settings_file()
        data = json.loads(path.read_text())
        assert data == {"permissions": {"allow": ["mcp__obsidian", "mcp__browseros"]}}
    finally:
        adapter._settings_path = None
        path.unlink()


@pytest.mark.asyncio
async def test_ensure_proc_wires_mcp_settings_flag_when_permissive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same gap as the interactive-approval test above, for the OTHER
    # branch of _ensure_proc()'s if/elif: test_permissive_mode_writes_an_
    # mcp_permissions_settings_file calls _ensure_mcp_permissions_settings_
    # file() directly, never through _ensure_proc() itself, so a bug in
    # how _ensure_proc wires the resulting path into the real spawn's
    # --settings flag would go uncaught.
    adapter = ClaudeCodeAdapter(_FAKE_CLI, permission_mode="permissive")
    monkeypatch.setattr(
        adapter, "_enumerate_mcp_server_names", AsyncMock(return_value=["obsidian"])
    )
    captured: dict[str, object] = {}
    real_spawn = asyncio.create_subprocess_exec

    async def capturing_spawn(*args: object, **kwargs: object):
        captured["args"] = args
        return await real_spawn(*args, **kwargs)  # type: ignore[arg-type]

    import convobox.adapters.claude_code as mod

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", capturing_spawn)

    try:
        await adapter.send_text("hello")
        await asyncio.wait_for(adapter._events.get(), timeout=10.0)

        args = captured["args"]
        assert "--settings" in args
        settings_arg = args[args.index("--settings") + 1]
        assert Path(settings_arg) == adapter._settings_path
        data = json.loads(Path(settings_arg).read_text())
        assert data == {"permissions": {"allow": ["mcp__obsidian"]}}
    finally:
        await adapter.aclose()
        if adapter._settings_path is not None:
            adapter._settings_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_plan_mode_never_calls_mcp_enumeration(monkeypatch: pytest.MonkeyPatch) -> None:
    # plan mode (the default) must not pay the ~3s claude-mcp-list cost
    # for a session that's read-only anyway.
    adapter = ClaudeCodeAdapter(_FAKE_CLI, permission_mode="plan")
    enumerate_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(adapter, "_enumerate_mcp_server_names", enumerate_mock)

    try:
        await adapter.send_text("hi")  # triggers _ensure_proc()
    finally:
        await adapter.aclose()

    enumerate_mock.assert_not_called()
