"""Tests for ACPAdapter transport and request/response routing."""

import asyncio

import pytest

from convobox.adapters.acp import ACPAdapter
from convobox.adapters.base import BackendEvent, BackendEventType


@pytest.mark.asyncio
async def test_acp_adapter_imports():
    """Verify ACPAdapter can be imported."""
    assert ACPAdapter is not None


@pytest.mark.asyncio
async def test_acp_adapter_init():
    """Test ACPAdapter initialization with various commands."""
    # Test with default opencode command
    adapter = ACPAdapter()
    assert adapter._backend == "opencode"
    # Command can be either a bare name or a resolved path (with .exe on Windows)
    assert "opencode" in adapter._command[0].lower()
    assert "acp" in adapter._command

    # Test with explicit command
    adapter_explicit = ACPAdapter(
        command=["custom-acp-server"],
        backend="custom",
    )
    assert adapter_explicit._command == ["custom-acp-server"]
    assert adapter_explicit._backend == "custom"

    # Test with working_dir
    adapter_wd = ACPAdapter(working_dir="/tmp/test")
    assert adapter_wd._working_dir == "/tmp/test"


@pytest.mark.asyncio
async def test_acp_adapter_request_response_routing():
    """Test that responses are correctly routed to their requests."""
    adapter = ACPAdapter()

    # Simulate a mock backend by creating a simple echo subprocess
    # For now, just test the data structures
    assert adapter._pending == {}
    assert adapter._request_seq == 0

    # Manually test the request correlation logic
    request_id = adapter._request_seq
    adapter._request_seq += 1

    future = asyncio.Future()
    adapter._pending[request_id] = future

    # Simulate a response
    response_payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"status": "ok"},
    }

    # Process the response (this would normally happen in _read_loop)
    if "id" in response_payload and "result" in response_payload:
        request_id_from_response = response_payload["id"]
        if request_id_from_response in adapter._pending:
            adapter._pending[request_id_from_response].set_result(
                response_payload.get("result", {})
            )

    result = await future
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_acp_adapter_notification_processing():
    """_process_notification takes the already-unwrapped update object
    (params.update, not the outer session/update envelope) -- live-verified
    2026-09-06 against a real opencode acp process (see acp.py's own module
    docstring): the discriminator is update.sessionUpdate, and text lives at
    update.content.text, not a bare top-level "type"/"text".
    """
    adapter = ACPAdapter()

    update = {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Hello, world!"},
    }

    await adapter._process_notification(update)

    event = adapter._events.get_nowait()
    assert isinstance(event, BackendEvent)
    assert event.type == BackendEventType.TEXT
    assert event.content == "Hello, world!"


@pytest.mark.asyncio
async def test_acp_adapter_notification_processing_tool_call():
    adapter = ACPAdapter()
    update = {
        "sessionUpdate": "tool_call",
        "toolCallId": "call_1",
        "title": "bash",
        "kind": "execute",
        "status": "pending",
        "rawInput": {"command": "echo hi"},
    }

    await adapter._process_notification(update)

    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.TOOL_CALL
    assert event.tool == "bash"
    assert event.tool_input == '{"command": "echo hi"}'


@pytest.mark.asyncio
async def test_acp_adapter_notification_processing_tool_call_completed():
    adapter = ACPAdapter()
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "call_1",
        "status": "completed",
        "content": [{"type": "content", "content": {"type": "text", "text": "hi\n"}}],
    }

    await adapter._process_notification(update)

    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.TOOL_RESULT
    assert event.tool_output == "hi\n"


@pytest.mark.asyncio
async def test_acp_adapter_notification_processing_ignores_unmapped_kinds():
    # usage_update/agent_thought_chunk/available_commands_update are real,
    # observed sessionUpdate kinds this adapter has no use for -- must be a
    # silent no-op, not an exception or a spurious event.
    adapter = ACPAdapter()
    await adapter._process_notification({"sessionUpdate": "usage_update", "used": 1})
    assert adapter._events.empty()


@pytest.mark.asyncio
async def test_acp_adapter_read_loop_routes_notification_vs_request(monkeypatch):
    """The dispatcher in _read_loop must tell a real notification (method,
    no id) apart from a server request (method AND id) -- live-verified
    2026-09-06: the first version of this code treated every "method"
    payload as a request, so real session/update notifications (no id)
    silently vanished instead of reaching _process_notification.
    """
    adapter = ACPAdapter()

    written: list[dict] = []

    async def fake_write(payload):
        written.append(payload)

    monkeypatch.setattr(adapter, "_write", fake_write)

    class FakeStdout:
        def __init__(self, lines):
            self._lines = lines

        async def readline(self):
            if not self._lines:
                return b""
            return self._lines.pop(0)

    class FakeProc:
        returncode = None
        stdout = None

    notification_line = (
        b'{"jsonrpc": "2.0", "method": "session/update", '
        b'"params": {"sessionId": "s1", "update": '
        b'{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}}}\n'
    )
    request_line = (
        b'{"jsonrpc": "2.0", "id": 99, "method": "session/request_permission", "params": {}}\n'
    )
    fake_proc = FakeProc()
    fake_proc.stdout = FakeStdout([notification_line, request_line, b""])
    adapter._proc = fake_proc

    await adapter._read_loop()

    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.TEXT
    assert event.content == "hi"

    assert written == [{"jsonrpc": "2.0", "id": 99, "result": {"outcome": {"outcome": "cancelled"}}}]


@pytest.mark.asyncio
async def test_acp_adapter_is_busy():
    """Test the is_busy flag."""
    adapter = ACPAdapter()
    assert not adapter.is_busy()

    adapter._busy = True
    assert adapter.is_busy()

    adapter._busy = False
    assert not adapter.is_busy()
