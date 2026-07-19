from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from convobox.adapters import CodexAdapter, create_backend_adapter
from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.config import BackendConfig

_FAKE_CODEX = [sys.executable, str(Path(__file__).with_name("fake_codex_appserver.py"))]


def _adapter() -> CodexAdapter:
    return CodexAdapter(_FAKE_CODEX)


async def _collect(
    adapter: CodexAdapter, count: int, timeout: float = 10.0
) -> list[BackendEvent]:
    events: list[BackendEvent] = []

    async def take() -> None:
        async for event in adapter.events():
            events.append(event)
            if len(events) >= count:
                return

    await asyncio.wait_for(take(), timeout=timeout)
    return events


async def _shutdown(adapter: CodexAdapter) -> None:
    # aclose() IS the shutdown path now (terminate the app-server + cancel
    # the reader within the loop); using it as teardown exercises it too.
    await adapter.aclose()


@pytest.mark.asyncio
async def test_aclose_terminates_the_appserver() -> None:
    adapter = _adapter()
    await adapter.send_text("hi")
    proc = adapter._proc
    assert proc is not None and proc.returncode is None
    await adapter.aclose()
    assert adapter._proc is None
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_aclose_without_a_process_is_a_safe_noop() -> None:
    adapter = _adapter()
    await adapter.aclose()
    await adapter.aclose()


@pytest.mark.asyncio
async def test_send_text_yields_text_then_done_and_busy_lifecycle() -> None:
    adapter = _adapter()
    try:
        assert adapter.is_busy() is False
        await adapter.send_text("hello there")
        # No busy assertion here: the fake completes the whole turn
        # instantly, so the reader task may legitimately have already
        # processed turn/completed by the time send_text returns. The
        # busy-True-while-in-flight half of the lifecycle is covered by
        # the hanging-turn tests (steer/hard-stop), where in-flight is a
        # controlled state rather than a race against the fake.

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
        assert events[0].tool == "commandExecution"
        assert events[0].tool_input is not None and "ls" in events[0].tool_input
        assert events[1].tool_output is not None and "file1" in events[1].tool_output
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_interject_steers_the_active_turn() -> None:
    # Codex has REAL steering (turn/steer), unlike Claude Code's queueing.
    adapter = _adapter()
    try:
        await adapter.send_text("hang in there")
        assert adapter.is_busy() is True

        collected: list[BackendEvent] = []

        async def consume() -> None:
            async for event in adapter.events():
                collected.append(event)

        consumer = asyncio.ensure_future(consume())
        await asyncio.sleep(0.2)  # let turn/started land so the turn id is known

        await adapter.send_interject("change course")
        await asyncio.sleep(0.5)
        assert any(
            e.type == BackendEventType.TEXT and e.content == "steered: change course"
            for e in collected
        )
        assert adapter.is_busy() is False  # fake completes the turn after steering

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_interject_with_no_active_turn_falls_back_to_fresh_turn() -> None:
    adapter = _adapter()
    try:
        # Nothing in flight at all: interject must deliver the utterance as
        # a new turn instead of erroring or dropping it.
        await adapter.send_interject("nothing was running")
        events = await _collect(adapter, 2)
        assert events[0].content == "echo: nothing was running"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_interject_falls_back_when_steer_misses_its_turn() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("hang around")
        collected: list[BackendEvent] = []

        async def consume() -> None:
            async for event in adapter.events():
                collected.append(event)

        consumer = asyncio.ensure_future(consume())
        await asyncio.sleep(0.2)

        # Force the steer to reference a turn the server no longer accepts:
        # the schema-documented failure ("Required active turn id
        # precondition") -- adapter must fall back to a fresh turn.
        adapter._active_turn_id = "turn_gone"
        await adapter.send_interject("do not lose me")
        await asyncio.sleep(0.5)
        assert any(
            e.type == BackendEventType.TEXT and e.content == "echo: do not lose me"
            for e in collected
        )

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_hard_stop_interrupts_and_thread_stays_usable() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("hang forever")
        assert adapter.is_busy() is True

        collected: list[BackendEvent] = []

        async def consume() -> None:
            async for event in adapter.events():
                collected.append(event)

        consumer = asyncio.ensure_future(consume())
        await asyncio.sleep(0.2)

        await adapter.send_hard_stop()
        assert adapter.is_busy() is False  # immediately

        await asyncio.sleep(0.3)
        # Interrupted turn's turn/completed is DONE, not ERROR: the user
        # asked for the stop.
        assert any(e.type == BackendEventType.DONE for e in collected)
        assert not any(e.type == BackendEventType.ERROR for e in collected)

        # Same thread serves the next turn (confirmed live behavior).
        await adapter.send_text("still alive?")
        await asyncio.sleep(0.5)
        assert any(
            e.type == BackendEventType.TEXT and e.content == "echo: still alive?"
            for e in collected
        )

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_hard_stop_survives_the_appserver_dying_mid_interrupt() -> None:
    # turn/interrupt's own request can fail (here: the app-server exits
    # before responding) -- send_hard_stop must not raise. The read loop's
    # death fails every PENDING future (including this one) with
    # ConnectionError, which send_hard_stop's except clause must catch.
    # Untested before this: the existing "process dies" test (die now)
    # dies AFTER its one in-flight request already resolved, so no
    # future was ever pending at the moment of death.
    adapter = _adapter()
    try:
        await adapter.send_text("hang and vanish on interrupt")
        assert adapter.is_busy() is True
        # Let the reader task process the turn/started notification so
        # _active_turn_id is set -- otherwise send_hard_stop takes its
        # "nothing in flight" early-return path instead of actually
        # issuing (and failing on) a turn/interrupt request.
        await asyncio.sleep(0.2)

        await adapter.send_hard_stop()  # must not raise
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_malformed_stdout_line_is_skipped_not_crashed() -> None:
    # A genuinely malformed (non-JSON) line on the app-server's stdout
    # must not crash the read loop or drop the real messages around it.
    # Untested before this: every existing test's fake-server output is
    # always valid JSON-RPC.
    adapter = _adapter()
    try:
        await adapter.send_text("emit garbage first")
        events = await _collect(adapter, 1)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "echo: emit garbage first"
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_thread_start_with_no_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # thread/start responding with no usable thread id is a protocol-level
    # failure this adapter can't recover from -- must raise RuntimeError
    # rather than silently proceeding with self._thread_id = None (which
    # every later request would then send as a literal null threadId).
    # Untested before this: nothing in the existing fixture can produce
    # this response, since it happens before any turn text exists to
    # script the fake server by -- FAKE_CODEX_NO_THREAD_ID exists for
    # exactly this test.
    monkeypatch.setenv("FAKE_CODEX_NO_THREAD_ID", "1")
    adapter = _adapter()
    try:
        with pytest.raises(RuntimeError, match="no thread id"):
            await adapter.send_text("hi")
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_hard_stop_before_any_send_is_a_noop() -> None:
    adapter = _adapter()
    await adapter.send_hard_stop()
    assert adapter.is_busy() is False
    assert adapter._proc is None  # must not spawn a server just to stop it


@pytest.mark.asyncio
async def test_approval_requests_are_auto_declined() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("this needs approval")
        events = await _collect(adapter, 2)
        # The fake echoes the client's decision back: proves the adapter
        # answered the server->client request, and answered it "decline".
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "approval decision was: decline"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_interactive_command_approval_waits_for_operator_and_can_approve() -> None:
    adapter = _adapter()
    try:
        adapter.set_interactive_approvals(True)
        await adapter.send_text("this needs approval")
        event = (await _collect(adapter, 1))[0]
        assert event.type == BackendEventType.APPROVAL_REQUEST
        assert event.content is not None
        assert "COMMAND EXECUTION" in event.content
        assert "rm -rf /" in event.content
        # No result was written until the caller explicitly approves.
        assert adapter.is_busy() is True
        assert await adapter.resolve_pending_approval(True) is True
        events = await _collect(adapter, 2)
        assert events[0].content == "approval decision was: approve"
        assert events[1].type == BackendEventType.DONE
        assert await adapter.resolve_pending_approval(False) is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_interactive_file_change_approval_can_be_declined() -> None:
    adapter = _adapter()
    try:
        adapter.set_interactive_approvals(True)
        await adapter.send_text("this needs file edit approval")
        event = (await _collect(adapter, 1))[0]
        assert event.type == BackendEventType.APPROVAL_REQUEST
        assert event.content is not None and "FILE CHANGE" in event.content
        assert await adapter.resolve_pending_approval(False) is True
        events = await _collect(adapter, 2)
        assert events[0].content == "approval decision was: decline"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_filechange_approval_uses_decline() -> None:
    # item/fileChange/requestApproval -- live-confirmed 2026-07-14 against
    # a real codex app-server (see codex.py's module docstring): a file
    # write triggers this method specifically (not commandExecution), and
    # {"decision": "decline"} correctly blocks it.
    adapter = _adapter()
    try:
        await adapter.send_text("this needs file edit approval")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "approval decision was: decline"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_legacy_exec_approval_uses_denied_not_decline() -> None:
    # "decline" is not a valid ReviewDecision value for the legacy
    # execCommandApproval method (confirmed against codex-cli 0.144.1's
    # own schema) -- the adapter must answer "denied" here specifically,
    # not the "decline" that item/commandExecution/requestApproval uses.
    adapter = _adapter()
    try:
        await adapter.send_text("this needs legacy exec approval")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "approval decision was: denied"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_legacy_patch_approval_uses_denied_not_decline() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("this needs legacy patch approval")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "approval decision was: denied"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_permissions_approval_grants_nothing() -> None:
    # item/permissions/requestApproval has no "decision" field at all --
    # a required "permissions" object naming what's granted. {} grants
    # nothing, the schema-correct equivalent of declining.
    adapter = _adapter()
    try:
        await adapter.send_text("this needs permissions approval")
        events = await _collect(adapter, 2)
        assert events[0].type == BackendEventType.TEXT
        assert events[0].content == "approval decision was: {}"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_failed_turn_yields_error_event() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("fail please")
        events = await _collect(adapter, 1)
        assert events[0].type == BackendEventType.ERROR
        assert events[0].content is not None and "model exploded" in events[0].content
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_process_death_ends_events_and_clears_busy() -> None:
    adapter = _adapter()
    try:
        await adapter.send_text("die now")
        events: list[BackendEvent] = []

        async def drain() -> None:
            async for event in adapter.events():
                events.append(event)

        await asyncio.wait_for(drain(), timeout=10)
        assert adapter.is_busy() is False
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_concurrent_consume_and_send_spawn_exactly_one_process() -> None:
    # Same orchestrator-shaped race as the other two adapters' locks guard.
    adapter = _adapter()
    spawns = 0
    real_spawn = asyncio.create_subprocess_exec

    async def counting_spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal spawns
        spawns += 1
        return await real_spawn(*args, **kwargs)

    import convobox.adapters.codex as mod

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
        assert events[0].content == "echo: hello race"
    finally:
        mod.asyncio.create_subprocess_exec = original  # type: ignore[assignment]
        await _shutdown(adapter)


def test_create_backend_adapter_codex() -> None:
    adapter = create_backend_adapter(BackendConfig(name="codex", command=["my-codex"]))
    assert isinstance(adapter, CodexAdapter)
    assert adapter._command == ["my-codex"]


def test_create_backend_adapter_codex_defaults() -> None:
    adapter = create_backend_adapter(BackendConfig(name="codex"))
    assert isinstance(adapter, CodexAdapter)
    if sys.platform == "win32":
        assert adapter._command[0].endswith("codex.cmd")
    else:
        assert adapter._command == ["codex"]


def test_codex_adapter_resolves_windows_cmd_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    import convobox.adapters.codex as mod

    monkeypatch.setattr(mod.os, "name", "nt", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"C:/bin/{name}" if name == "codex.cmd" else None)

    adapter = CodexAdapter(["codex"])
    assert adapter._command == ["C:/bin/codex.cmd"]


def test_resolve_command_never_consults_which_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PATHEXT/.cmd-shim resolution is a Windows-only concern; on other
    # platforms `codex` on PATH is directly executable, no guessing
    # needed. Asserts which() is never even CALLED, not just that the
    # final result happens to be unchanged -- a stronger check than
    # comparing output alone would give.
    import convobox.adapters.codex as mod

    monkeypatch.setattr(mod.os, "name", "posix", raising=False)

    def _unexpected_which(name: str) -> str | None:
        raise AssertionError(f"shutil.which({name!r}) should not be called on non-Windows")

    monkeypatch.setattr(mod.shutil, "which", _unexpected_which)

    adapter = CodexAdapter(["codex"])
    assert adapter._command == ["codex"]


def test_resolve_command_falls_back_to_bare_name_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # codex isn't found anywhere on PATH under any of the tried names --
    # passes the original command through unchanged (including any extra
    # args) rather than raising here; the real FileNotFoundError surfaces
    # naturally when asyncio.create_subprocess_exec actually tries to
    # spawn it, matching how a missing claude-code/opencode command is
    # already handled elsewhere (Settings TUI's own shutil.which warning).
    import convobox.adapters.codex as mod

    monkeypatch.setattr(mod.os, "name", "nt", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)

    adapter = CodexAdapter(["codex", "--flag"])
    assert adapter._command == ["codex", "--flag"]
