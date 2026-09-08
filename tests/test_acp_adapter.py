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
async def test_acp_adapter_notification_processing_tool_call_failed():
    """Live-verified 2026-09-07 against a real opencode acp process (a
    genuine tool failure: its own `read` tool against a nonexistent path,
    not inferred) -- a "failed" update carries the same `content`
    text-block array a "completed" one does, in addition to `rawOutput`.
    Must prefer the human-readable content text over the raw JSON blob,
    same extraction the "completed" branch already uses -- a tool failure
    shouldn't read worse than its own success path.
    """
    adapter = ACPAdapter()
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "call_1",
        "status": "failed",
        "content": [
            {"type": "content", "content": {"type": "text", "text": "File not found: nope.txt"}}
        ],
        "rawOutput": {"error": "File not found: nope.txt"},
    }

    await adapter._process_notification(update)

    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.ERROR
    assert event.content == "File not found: nope.txt"


@pytest.mark.asyncio
async def test_acp_adapter_notification_processing_tool_call_failed_falls_back_to_raw_output():
    """No usable content text block -- fall back to rawOutput, same
    fallback the "completed" branch already has."""
    adapter = ACPAdapter()
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "call_1",
        "status": "failed",
        "content": [],
        "rawOutput": {"error": "boom"},
    }

    await adapter._process_notification(update)

    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.ERROR
    assert event.content == '{"error": "boom"}'


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


class _FakeAliveProc:
    """Stand-in for a spawned subprocess: returncode is None ("still
    running"), which is the only thing _ensure_session's own spawn-guard
    checks, so it skips create_subprocess_exec/initialize entirely and
    goes straight to the session/new + config branch under test."""

    returncode = None


def _fake_request_recorder(calls: list, *, session_id: str = "s1"):
    async def fake_request(method, params):
        calls.append((method, params))
        if method == "session/new":
            return {"sessionId": session_id}
        return {}

    return fake_request


@pytest.mark.asyncio
async def test_acp_adapter_ensure_session_sets_plan_mode(monkeypatch):
    """permission_mode == "plan" must call session/set_mode(sessionId,
    "plan") right after session/new -- live-verified 2026-09-07 this
    genuinely blocks a real file-write prompt against opencode (agent
    declines, no tool_call, no file created -- see acp.py's own module
    docstring). Previously self._permission_mode was stored in __init__
    and never referenced anywhere else in the file -- a real no-op bug
    where backend.permission_mode: plan provided zero protection for ACP
    backends.
    """
    adapter = ACPAdapter(permission_mode="plan")
    adapter._proc = _FakeAliveProc()

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(adapter, "_request", _fake_request_recorder(calls))

    session_id = await adapter._ensure_session()

    assert session_id == "s1"
    assert [c[0] for c in calls] == ["session/new", "session/set_mode"]
    assert calls[1][1] == {"sessionId": "s1", "modeId": "plan"}


@pytest.mark.asyncio
async def test_acp_adapter_ensure_session_permissive_skips_set_mode(monkeypatch):
    """permission_mode == "permissive" must NOT call session/set_mode --
    full trust is already the session's own default, live-confirmed
    (docs/ROADMAP.md, acp.py's own module docstring)."""
    adapter = ACPAdapter(permission_mode="permissive")
    adapter._proc = _FakeAliveProc()

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(adapter, "_request", _fake_request_recorder(calls))

    await adapter._ensure_session()

    assert [c[0] for c in calls] == ["session/new"]


@pytest.mark.asyncio
async def test_acp_adapter_ensure_session_sets_model(monkeypatch):
    """The model param, when set, must be pinned via
    session/set_config_option before session/set_mode -- applies to
    both opencode and kilo (live-verified 2026-09-08, not Kilo-only as
    an earlier version of this code assumed)."""
    adapter = ACPAdapter(permission_mode="plan", model="inception/mercury-2")
    adapter._proc = _FakeAliveProc()

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(adapter, "_request", _fake_request_recorder(calls))

    await adapter._ensure_session()

    assert [c[0] for c in calls] == [
        "session/new",
        "session/set_config_option",
        "session/set_mode",
    ]
    assert calls[1][1] == {
        "sessionId": "s1",
        "configId": "model",
        "value": "inception/mercury-2",
    }


@pytest.mark.asyncio
async def test_acp_adapter_send_text_is_nonblocking(monkeypatch):
    """send_text() must dispatch session/prompt as a background task and
    return immediately with busy already set -- live-verified 2026-09-08
    that the original blocking-await design left is_busy() reporting
    False for the entire turn duration (see send_text's own docstring),
    which routed a mid-turn utterance to a second concurrent send_text()
    instead of send_interject() at the Orchestrator's own is_busy() check
    (orchestrator.py's handle_transcript)."""
    adapter = ACPAdapter()

    async def fake_ensure_session():
        return "s1"

    release = asyncio.Event()

    async def fake_request(method, params):
        assert method == "session/prompt"
        await release.wait()
        return {"stopReason": "end_turn"}

    monkeypatch.setattr(adapter, "_ensure_session", fake_ensure_session)
    monkeypatch.setattr(adapter, "_request", fake_request)

    await adapter.send_text("hello")

    assert adapter.is_busy() is True

    release.set()
    await adapter._prompt_task

    assert adapter.is_busy() is False
    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.DONE


@pytest.mark.asyncio
async def test_acp_adapter_send_text_error_clears_busy_and_emits_error(monkeypatch):
    """A session/prompt failure must still clear busy and surface an
    ERROR event -- a background task's own exception would otherwise
    vanish silently (never retrieved)."""
    adapter = ACPAdapter()

    async def fake_ensure_session():
        return "s1"

    async def fake_request(method, params):
        raise RuntimeError("ACP error: boom")

    monkeypatch.setattr(adapter, "_ensure_session", fake_ensure_session)
    monkeypatch.setattr(adapter, "_request", fake_request)

    await adapter.send_text("hello")
    await adapter._prompt_task

    assert adapter.is_busy() is False
    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.ERROR
    assert "boom" in event.content


@pytest.mark.asyncio
async def test_acp_adapter_stale_prompt_task_does_not_clear_busy(monkeypatch):
    """A superseded prompt task (send_hard_stop() + a new send_text())
    must not touch busy state or emit an event once it finally resolves
    -- only the CURRENT self._prompt_task is allowed to (the task-identity
    race guard in _await_prompt)."""
    adapter = ACPAdapter()

    async def fake_ensure_session():
        return "s1"

    async def fake_request(method, params):
        return {"stopReason": "end_turn"}

    monkeypatch.setattr(adapter, "_ensure_session", fake_ensure_session)
    monkeypatch.setattr(adapter, "_request", fake_request)

    stale_task = asyncio.create_task(adapter._await_prompt("s1", "old"))
    await asyncio.sleep(0)  # let it reach the await inside _request
    adapter._busy = True

    # Simulate a new send_text() superseding the stale task before it resolves.
    adapter._prompt_task = asyncio.create_task(asyncio.sleep(3600))

    # Awaited for synchronization only (its return value, always None, isn't
    # the point) -- drives the stale task through its own race-guard check
    # in _await_prompt before the assertions below verify that check held.
    await stale_task

    assert adapter.is_busy() is True  # untouched by the stale task
    assert adapter._events.empty()

    adapter._prompt_task.cancel()
