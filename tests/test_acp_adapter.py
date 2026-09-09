"""Tests for ACPAdapter transport and request/response routing."""

import asyncio
import sys
from pathlib import Path

import pytest

from convobox.adapters.acp import ACPAdapter, ACPBackendDied
from convobox.adapters.base import BackendEvent, BackendEventType

_FAKE_ACP = [sys.executable, str(Path(__file__).with_name("fake_acp_server.py"))]


def _real_adapter(**kwargs: object) -> ACPAdapter:
    """A real subprocess fake, not a monkeypatched double -- exercises the
    genuine wire framing (tests/fake_acp_server.py), unlike every other
    test in this file, which stubs out _request/_ensure_session or feeds
    _read_loop a hand-built fake stdout. Same discipline as
    test_codex_adapter.py's own _adapter() helper against
    fake_codex_appserver.py.
    """
    return ACPAdapter(_FAKE_ACP, backend="opencode", **kwargs)  # type: ignore[arg-type]


async def _collect(adapter: ACPAdapter, count: int, timeout: float = 10.0) -> list[BackendEvent]:
    events: list[BackendEvent] = []

    async def take() -> None:
        async for event in adapter.events():
            events.append(event)
            if len(events) >= count:
                return

    await asyncio.wait_for(take(), timeout=timeout)
    return events


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

    await adapter._read_loop(fake_proc)

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


# --- Real-subprocess integration tests, against tests/fake_acp_server.py.
# Everything above this point stubs out _request/_ensure_session or feeds
# _read_loop a hand-built fake stdout -- nothing before this exercises
# self._pending, _request, _write, or genuine wire framing at all. Found
# by an independent second-opinion review (2026-09-09) after this file's
# own fake server was built with seven scripted scenarios and none of
# them were actually exercised by a test.


@pytest.mark.asyncio
async def test_process_death_mid_turn_fails_fast_and_clears_busy() -> None:
    """Regression test for the most severe finding of that review:
    _read_loop's own finally block used to only push _EOF -- it never
    rejected whatever was still in self._pending, nor cleared self._busy.
    A backend dying mid-turn left the caller's own session/prompt request
    waiting the full _RESPONSE_TIMEOUT_S (30s) to time out on its own, and
    left is_busy() reporting True forever afterward (routing the next
    utterance to send_interject() instead of send_text() while believing
    a dead turn was still live). Must resolve in well under 30s -- 5s is
    generous slack for process spawn/exit on a loaded CI box -- and
    aclose() afterward must not raise (a second bug in the same area:
    _read_loop used to catch only CancelledError, so any other exception
    from a dying process escaped as the task's own exception and
    aclose()'s `await self._reader_task` re-raised it right back out).
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("please die now")
        await asyncio.wait_for(adapter._prompt_task, timeout=5.0)
        assert adapter.is_busy() is False
        event = await asyncio.wait_for(adapter._events.get(), timeout=1.0)
        assert event.type == BackendEventType.ERROR
        assert event.content == (
            "ACPBackendDied: ACP backend process exited before a response arrived"
        )
    finally:
        await adapter.aclose()  # must not raise


@pytest.mark.asyncio
async def test_send_hard_stop_cancels_a_hanging_turn() -> None:
    """send_hard_stop() was completely untested for ACP before this --
    the only end-to-end proof that session/cancel goes out as a
    NOTIFICATION (no "id"), not a request. Sending it as a request was
    the actual shipped bug fixed 2026-09-06 (see acp.py's own
    send_hard_stop docstring); nothing before this test would catch a
    regression back to the request form, since opencode replies
    -32601 Method not found to that shape rather than raising locally.
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("please hang forever")
        assert adapter.is_busy() is True
        # send_text() only CREATES _prompt_task (asyncio.create_task does
        # not run it) -- a real safeword always arrives well after the
        # backend has actually seen the prompt (it takes real audio/STT
        # time to recognize a separate utterance), but calling
        # send_hard_stop() with no yield at all here would race
        # session/cancel onto the wire BEFORE session/prompt itself (the
        # fake's own `stdin.drain()` doesn't suspend for a write this
        # small, so nothing here would otherwise force that ordering).
        # This sleep stands in for that always-present real-world gap.
        await asyncio.sleep(0.2)
        await adapter.send_hard_stop()
        assert adapter.is_busy() is False
        await asyncio.wait_for(adapter._prompt_task, timeout=5.0)
        event = await asyncio.wait_for(adapter._events.get(), timeout=1.0)
        assert event.type == BackendEventType.DONE
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_send_hard_stop_before_any_send_is_a_noop() -> None:
    adapter = _real_adapter()
    await adapter.send_hard_stop()
    assert adapter.is_busy() is False
    assert adapter._proc is None  # must not spawn a process just to stop it


@pytest.mark.asyncio
async def test_send_hard_stop_guards_on_proc_liveness_not_just_busy() -> None:
    """Regression test for the exact conditions that used to reach
    _notify -> _write's own `assert self._proc is not None`, raising
    AssertionError out of Orchestrator.hard_stop() and skipping
    stop_event_loop()/approval_gate.cancel_wait() entirely: a stray
    safeword arriving while self._busy is (for whatever reason) still
    True but self._proc is already None/dead -- e.g. force_kill() ran,
    which has no override for ACP and delegates straight to aclose().
    Manipulates state directly rather than going through a real
    force_kill(), so this pins send_hard_stop()'s OWN guard specifically,
    independent of whichever other fix might also happen to clear busy
    by the time a real force_kill() finishes.
    """
    adapter = ACPAdapter()
    adapter._session_id = "sess_whatever"
    adapter._busy = True
    adapter._proc = None
    await adapter.send_hard_stop()  # must not raise
    assert adapter.is_busy() is False


@pytest.mark.asyncio
async def test_tool_call_event_ordering_through_real_pipe() -> None:
    """Proves _read_loop unwraps params.update off a genuine newline-
    delimited stream, that ordering survives the queue, and that DONE
    (which comes from session/prompt's own RESPONSE, a different
    transport path than the notifications) lands after them -- none of
    which the existing mocked-_process_notification unit tests above can
    prove on their own.
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("please use a tool")
        events = await _collect(adapter, 4)
        assert events[0].type == BackendEventType.TOOL_CALL
        assert events[0].tool == "bash"
        assert events[1].type == BackendEventType.TOOL_RESULT
        assert events[1].tool_output == "hi\n"
        assert events[2].type == BackendEventType.TEXT
        assert events[2].content == "the tool ran"
        assert events[3].type == BackendEventType.DONE
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_request_level_failure_yields_error_event_through_real_pipe() -> None:
    """The existing mocked unit test for this path
    (test_acp_adapter_send_text_error_clears_busy_and_emits_error) raises
    a Python RuntimeError from a stubbed _request -- it never touches
    _read_loop's own "id"+"error" branch or future.set_exception.
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("please fail this turn")
        await asyncio.wait_for(adapter._prompt_task, timeout=5.0)
        assert adapter.is_busy() is False
        event = await asyncio.wait_for(adapter._events.get(), timeout=1.0)
        assert event.type == BackendEventType.ERROR
        assert "model exploded" in event.content
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_tool_failure_yields_error_then_done_through_real_pipe() -> None:
    """A failed TOOL is not a failed TURN -- ACP has no equivalent of
    codex's turn/completed(status="failed"). Confirms both the
    human-readable text extraction (already unit-tested against a
    hand-built dict) AND that the turn still completes with DONE
    afterward, over a genuine pipe.
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("please demonstrate a tool fails case")
        events = await _collect(adapter, 3)
        assert events[0].type == BackendEventType.TOOL_CALL
        assert events[1].type == BackendEventType.ERROR
        assert events[1].content == "File not found: nope.txt"
        assert events[2].type == BackendEventType.DONE
        assert adapter.is_busy() is False
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_needs_approval_auto_decline_reaches_the_real_process() -> None:
    """Proves the real adapter's auto-decline ({"outcome": {"outcome":
    "cancelled"}}) actually gets serialized to the child's stdin and read
    back by it -- the existing unit test
    (test_acp_adapter_read_loop_routes_notification_vs_request)
    monkeypatches _write, so it proves _read_loop's OWN dispatch/routing
    logic but not that the reply crosses a genuine pipe, nor that
    `await self._write(...)` from INSIDE _read_loop's own server-request
    branch doesn't stall the reader.
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("this needs approval first")
        events = await _collect(adapter, 3)
        assert events[0].type == BackendEventType.TOOL_CALL
        assert events[1].type == BackendEventType.ERROR
        assert events[1].content == "permission outcome was: cancelled"
        assert events[2].type == BackendEventType.DONE
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_garbage_line_is_skipped_and_real_response_still_routes() -> None:
    """Nothing before this fed _read_loop an undecodable line over a real
    pipe -- the malformed line must be skipped, not kill the loop or
    desync framing for the real response right behind it.
    """
    adapter = _real_adapter()
    try:
        await adapter.send_text("please emit garbage first then respond")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content.startswith("echo:")
        assert events[1].type == BackendEventType.DONE
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_full_handshake_with_model_and_plan_mode_completes_a_turn() -> None:
    """The only test that runs _ensure_session's full sequence
    (initialize -> session/new -> session/set_config_option ->
    session/set_mode) against a real process -- every _ensure_session
    test above monkeypatches _request entirely, so none of them can
    catch set_config_option/set_mode's real {} response being mishandled
    or an initialize-before-session/new ordering regression.
    """
    adapter = _real_adapter(permission_mode="plan", model="some/model")
    try:
        await adapter.send_text("hello there")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[1].type == BackendEventType.DONE
        assert adapter.is_busy() is False
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_read_loop_ignores_a_response_for_an_already_done_future() -> None:
    """Regression test: asyncio.wait_for's own timeout path cancels the
    future FIRST and only removes it from self._pending afterward, on the
    timed-out coroutine's own turn -- a response arriving in that exact
    gap used to hit set_result on an already-cancelled future and raise
    InvalidStateError, killing the whole read loop (and, per the same
    finally-block bug this file's other new tests pin, everything else
    still pending with it).
    """
    adapter = ACPAdapter()

    future: asyncio.Future = asyncio.Future()
    future.cancel()
    adapter._pending[42] = future

    class FakeStdout:
        def __init__(self, lines):
            self._lines = lines

        async def readline(self):
            return self._lines.pop(0) if self._lines else b""

    class FakeProc:
        returncode = None
        stdout = None

    fake_proc = FakeProc()
    fake_proc.stdout = FakeStdout([
        b'{"jsonrpc": "2.0", "id": 42, "result": {"ok": true}}\n',
        b"",
    ])

    await adapter._read_loop(fake_proc)  # must not raise InvalidStateError

    assert 42 not in adapter._pending


@pytest.mark.asyncio
async def test_request_removes_pending_entry_even_when_cancelled(monkeypatch) -> None:
    """Regression test: _request used to remove its own self._pending
    entry only in the TimeoutError branch -- the coroutine awaiting it
    being cancelled (aclose() cancelling _prompt_task mid session/prompt)
    used to leak the Future in self._pending forever.
    """
    adapter = ACPAdapter()

    async def fake_write(payload):
        return None  # never resolves the future -- lets us cancel from outside

    monkeypatch.setattr(adapter, "_write", fake_write)

    task = asyncio.create_task(adapter._request("session/prompt", {}))
    await asyncio.sleep(0)  # let it register in _pending and start awaiting
    assert len(adapter._pending) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter._pending == {}


@pytest.mark.asyncio
async def test_ensure_session_reraises_acp_backend_died_for_model(monkeypatch) -> None:
    """ACPBackendDied must propagate out of _ensure_session, not be
    swallowed by the same `except RuntimeError` that deliberately
    log-and-continues on an ordinary per-call failure (a bad model name,
    an unsupported mode) -- trying the NEXT call against a process that
    has already exited is pointless.
    """
    adapter = ACPAdapter(permission_mode="permissive", model="some/model")
    adapter._proc = _FakeAliveProc()

    async def fake_request(method, params):
        if method == "session/new":
            return {"sessionId": "s1"}
        raise ACPBackendDied("gone")

    monkeypatch.setattr(adapter, "_request", fake_request)

    with pytest.raises(ACPBackendDied):
        await adapter._ensure_session()


@pytest.mark.asyncio
async def test_ensure_session_reraises_acp_backend_died_for_plan_mode(monkeypatch) -> None:
    adapter = ACPAdapter(permission_mode="plan")
    adapter._proc = _FakeAliveProc()

    async def fake_request(method, params):
        if method == "session/new":
            return {"sessionId": "s1"}
        raise ACPBackendDied("gone")

    monkeypatch.setattr(adapter, "_request", fake_request)

    with pytest.raises(ACPBackendDied):
        await adapter._ensure_session()
