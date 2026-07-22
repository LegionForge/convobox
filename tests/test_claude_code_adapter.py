from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from convobox.adapters import ClaudeCodeAdapter, create_backend_adapter
from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.adapters.claude_code import _resolve_flags, _safe_json_loads
from convobox.config import BackendConfig

_FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_claude_cli.py"))]


def _adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(_FAKE_CLI)


async def _collect(
    adapter: ClaudeCodeAdapter, count: int, timeout: float = 10.0
) -> list[BackendEvent]:
    events: list[BackendEvent] = []

    async def take() -> None:
        async for event in adapter.events():
            events.append(event)
            if len(events) >= count:
                return

    await asyncio.wait_for(take(), timeout=timeout)
    return events


async def _shutdown(adapter: ClaudeCodeAdapter) -> None:
    # aclose() IS the shutdown path now (terminate + await the subprocess so
    # its pipe transports close within the loop); using it here means every
    # test also exercises it as teardown.
    await adapter.aclose()


@pytest.mark.asyncio
async def test_aclose_terminates_the_subprocess() -> None:
    adapter = _adapter()
    await adapter.send_text("hi")
    proc = adapter._proc
    assert proc is not None and proc.returncode is None  # spawned + alive
    await adapter.aclose()
    assert adapter._proc is None
    assert proc.returncode is not None  # actually terminated, not leaked


@pytest.mark.asyncio
async def test_aclose_without_a_process_is_a_safe_noop() -> None:
    adapter = _adapter()
    await adapter.aclose()  # never spawned -> must not raise
    await adapter.aclose()  # idempotent


@pytest.mark.asyncio
async def test_send_text_yields_text_then_done_and_busy_lifecycle() -> None:
    adapter = _adapter()
    try:
        assert adapter.is_busy() is False
        await adapter.send_text("hello there")
        assert adapter.is_busy() is True

        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "echo: hello there"
        assert events[1].type == BackendEventType.DONE
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_tool_turn_yields_tool_call_and_tool_result() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("please use a tool")
        events = await _collect(adapter, 4)

        assert [e.type for e in events] == [
            BackendEventType.TOOL_CALL,
            BackendEventType.TOOL_RESULT,
            BackendEventType.TEXT,
            BackendEventType.DONE,
        ]
        assert events[0].tool == "Bash"
        assert events[0].tool_input is not None and "ls" in events[0].tool_input
        assert events[1].tool_output is not None and "file1" in events[1].tool_output
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_error_result_yields_error_event_and_clears_busy() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("fail on purpose")
        events = await _collect(adapter, 1)
        assert events[0].type == BackendEventType.ERROR
        assert events[0].content == "boom"
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_interject_queues_and_busy_holds_until_last_result() -> None:
    # User messages queue as separate turns on this backend (confirmed
    # against the real CLI) -- two sends means two results, and busy must
    # not clear until both have actually arrived.
    #
    # NOT asserted here anymore: is_busy() sampled interleaved with each
    # yielded event (the old version of this test did, expecting
    # [True, True, True, False]). The events()/_read_loop queue refactor
    # (added for the voice-approval feature -- see claude_code.py's module
    # docstring) decouples PARSING stdout (which decrements _pending the
    # instant a "result" line is read) from CONSUMING the queue (which
    # this test's async-for does one event at a time) -- against this
    # fake CLI's zero real latency, the reader task races ahead and parses
    # both DONE lines before the consumer pulls even the first event off
    # the queue, so busy_after_each was actually [False, False, False,
    # False], not a real regression: no live session has sub-millisecond
    # turnaround between two turns for this ordering to matter, and
    # codex.py's adapter already has this exact same "busy reflects the
    # wire, not consumer drain speed" contract.
    adapter = _adapter()
    try:
        await adapter.send_text("first")
        await adapter.send_interject("second")
        assert adapter.is_busy() is True

        events = await _collect(adapter, 4)
        assert [e.type for e in events] == [
            BackendEventType.TEXT,
            BackendEventType.DONE,
            BackendEventType.TEXT,
            BackendEventType.DONE,
        ]
        # Idle only once both turns' results have actually arrived.
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_hard_stop_interrupts_and_process_stays_usable() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("hang forever")
        events = await _collect(adapter, 1)  # the turn's opening text
        assert events[0].content == "starting the long task"
        assert adapter.is_busy() is True

        # Consume in the background exactly the way Orchestrator does, so
        # the hard stop happens from a different task than the consumer --
        # the arrangement that crashed OpenCodeAdapter's first version.
        seen: list[BackendEvent] = []

        async def consume() -> None:
            async for event in adapter.events():
                seen.append(event)

        consumer = asyncio.ensure_future(consume())
        await asyncio.sleep(0.1)

        await adapter.send_hard_stop()
        assert adapter.is_busy() is False  # immediately, not eventually

        # The interrupted turn's own error result arrives afterwards and
        # must not push the pending counter negative or flip busy back on.
        await asyncio.sleep(0.5)
        assert adapter.is_busy() is False
        assert any(e.type == BackendEventType.ERROR for e in seen)

        # Same process must serve the next turn (confirmed live behavior).
        await adapter.send_text("still alive?")
        await asyncio.sleep(1.0)
        assert any(
            e.type == BackendEventType.TEXT and e.content == "echo: still alive?"
            for e in seen
        )
        assert adapter.is_busy() is False

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_hard_stop_before_any_send_is_a_noop() -> None:
    adapter = _adapter()
    await adapter.send_hard_stop()
    assert adapter.is_busy() is False
    assert adapter._proc is None  # must not spawn a process just to stop it


@pytest.mark.asyncio
async def test_process_death_ends_events_clears_busy_and_respawns_on_next_send() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("die now")
        # The fake exits without emitting a result; events() must end (not
        # hang) and clear busy via its safety net.
        events: list[BackendEvent] = []

        async def drain() -> None:
            async for event in adapter.events():
                events.append(event)

        await asyncio.wait_for(drain(), timeout=10)
        assert adapter.is_busy() is False
        first_proc = adapter._proc

        # Next send must transparently respawn.
        await adapter.send_text("back again")
        assert adapter._proc is not first_proc
        events2 = await _collect(adapter, 2)
        assert events2[0].content == "echo: back again"
        assert events2[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_lines_beyond_asyncio_default_limit_are_handled() -> None:
    # A real claude system/init line exceeded asyncio's 64KB default
    # readline limit on first probe; the adapter raises the limit.
    adapter = _adapter()
    try:
        await adapter.send_text("bigline please")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


# --- the permission-gate hang fix: default --permission-mode plan ---


def test_resolve_flags_defaults_to_plan_mode() -> None:
    flags = _resolve_flags(["claude"])
    assert "--permission-mode" in flags
    assert flags[flags.index("--permission-mode") + 1] == "plan"


def test_resolve_flags_respects_an_explicit_user_permission_mode() -> None:
    # A user who configured their own --permission-mode must win; the
    # adapter must not append a second, conflicting one.
    flags = _resolve_flags(["claude", "--permission-mode", "acceptEdits"])
    assert flags.count("--permission-mode") == 0  # none appended by the adapter
    assert "acceptEdits" not in flags  # that's in the user's own command, not here


def test_resolve_flags_leaves_disallowed_tools_in_the_users_command_untouched() -> None:
    # docs/DESIGN-0.3.0-interaction-and-safety.md's Phase 3 --disallowedTools
    # question resolved to "command: already supports it, no new config
    # field" -- this is the regression test for that claim: _resolve_flags
    # must not inspect, reject, or duplicate anything besides
    # --permission-mode. The user's own --disallowedTools survives in
    # `command` itself (this function only returns what gets APPENDED),
    # and the adapter still appends its own safe-default --permission-mode
    # plan since the user didn't set one.
    command = ["claude", "--disallowedTools", "Bash", "Write", "Edit"]
    flags = _resolve_flags(command)
    assert "--disallowedTools" not in flags  # untouched, stays in the user's command
    assert "--permission-mode" in flags
    assert flags[flags.index("--permission-mode") + 1] == "plan"


def test_create_backend_adapter_claude_code() -> None:
    adapter = create_backend_adapter(
        BackendConfig(name="claude-code", command=["my-claude", "--model", "x"])
    )
    assert isinstance(adapter, ClaudeCodeAdapter)
    assert adapter._command == ["my-claude", "--model", "x"]


def test_create_backend_adapter_claude_code_defaults_to_claude() -> None:
    adapter = create_backend_adapter(BackendConfig(name="claude-code"))
    assert isinstance(adapter, ClaudeCodeAdapter)
    assert adapter._command == ["claude"]


@pytest.mark.asyncio
async def test_concurrent_consume_and_send_spawn_exactly_one_process() -> None:
    # The exact shape of Orchestrator.handle_transcript's first call: the
    # event-consumer task and the first send start concurrently, and both
    # reach _ensure_proc while _proc is still None. Without the process
    # lock each spawned its own claude -- events attached to one, the
    # prompt went to the other, and the loser's _pending reset made
    # is_busy() lie (found live; sequential tests can't interleave these).
    adapter = _adapter()
    spawns = 0
    real_spawn = asyncio.create_subprocess_exec

    async def counting_spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal spawns
        spawns += 1
        return await real_spawn(*args, **kwargs)

    import convobox.adapters.claude_code as mod

    original = mod.asyncio.create_subprocess_exec
    mod.asyncio.create_subprocess_exec = counting_spawn  # type: ignore[assignment]
    try:
        events: list[BackendEvent] = []

        async def consume() -> None:
            async for event in adapter.events():
                events.append(event)
                if len(events) >= 2:
                    return

        consumer = asyncio.ensure_future(consume())
        await adapter.send_text("hello race")
        await asyncio.wait_for(consumer, timeout=10)

        assert spawns == 1
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "echo: hello race"
        assert adapter.is_busy() is False
    finally:
        mod.asyncio.create_subprocess_exec = original  # type: ignore[assignment]
        await _shutdown(adapter)


# --- send_hard_stop: the interrupt write itself can fail (a real, distinct
# failure mode from "no process to interrupt") ---


@pytest.mark.asyncio
async def test_hard_stop_write_failure_does_not_raise_and_still_clears_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("hi")
        assert adapter.is_busy() is True

        async def _raise(payload: dict[str, object]) -> None:
            raise OSError("pipe closed")

        monkeypatch.setattr(adapter, "_write_line", _raise)

        await adapter.send_hard_stop()  # must not propagate the OSError

        # Same "no result messages are coming" reasoning as a successful
        # interrupt -- pending is zeroed regardless of whether the
        # interrupt request itself actually reached the process.
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


# --- aclose: a process that ignores terminate() must still be reaped ---


@pytest.mark.asyncio
async def test_aclose_force_kills_a_process_that_ignores_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import convobox.adapters.claude_code as mod

    class _StubbornProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.stdin = None
            self.terminate_called = False
            self.kill_called = False

        def terminate(self) -> None:
            self.terminate_called = True

        def kill(self) -> None:
            self.kill_called = True
            self.returncode = -9

        async def wait(self) -> int | None:
            return self.returncode

    async def _fake_wait_for(coro: object, timeout: float) -> None:
        # Simulates the outer asyncio.wait_for(proc.wait(), timeout=5.0)
        # genuinely timing out -- no real 5s sleep needed for the test to
        # exercise the force-kill path.
        coro.close()  # type: ignore[attr-defined]
        raise TimeoutError

    monkeypatch.setattr(mod.asyncio, "wait_for", _fake_wait_for)

    adapter = ClaudeCodeAdapter(_FAKE_CLI)
    proc = _StubbornProcess()
    adapter._proc = proc  # type: ignore[assignment]

    await adapter.aclose()

    assert proc.terminate_called is True
    assert proc.kill_called is True
    assert adapter._proc is None


# --- _safe_json_loads: malformed lines from the subprocess must not crash
# the reader loop, just be skipped (events()'s "if outer is None: continue") ---


def test_safe_json_loads_returns_none_for_malformed_json() -> None:
    assert _safe_json_loads("not json at all {") is None


def test_safe_json_loads_returns_none_for_valid_json_that_is_not_an_object() -> None:
    # A bare JSON array or scalar parses fine but has no .get() -- the
    # dict-only contract downstream (_content_blocks etc.) requires this.
    assert _safe_json_loads("[1, 2, 3]") is None
    assert _safe_json_loads("42") is None
    assert _safe_json_loads('"just a string"') is None


def test_safe_json_loads_returns_the_dict_for_valid_object_json() -> None:
    assert _safe_json_loads('{"type": "result"}') == {"type": "result"}
