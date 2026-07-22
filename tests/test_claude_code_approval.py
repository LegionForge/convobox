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

from convobox.adapters.base import BackendEventType
from convobox.adapters.claude_code import ClaudeCodeAdapter, _resolve_flags

_FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_claude_cli.py"))]


def _adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(_FAKE_CLI, interactive_approval=True)


async def _connect(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", port)


# --- _resolve_flags: interactive approval changes the DEFAULT permission
# mode (plan -> acceptEdits, see module docstring finding 2), never
# overrides an explicit user choice ---


def test_resolve_flags_interactive_approval_defaults_to_accept_edits() -> None:
    flags = _resolve_flags(["claude"], interactive_approval=True)
    assert flags[flags.index("--permission-mode") + 1] == "acceptEdits"


def test_resolve_flags_interactive_approval_respects_explicit_user_mode() -> None:
    flags = _resolve_flags(
        ["claude", "--permission-mode", "bypassPermissions"], interactive_approval=True
    )
    assert flags.count("--permission-mode") == 0
    assert "acceptEdits" not in flags


def test_resolve_flags_default_still_plan_without_interactive_approval() -> None:
    flags = _resolve_flags(["claude"], interactive_approval=False)
    assert flags[flags.index("--permission-mode") + 1] == "plan"


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
