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
async def test_aclose_force_kills_a_process_that_ignores_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import convobox.adapters.codex as mod

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
        # exercise the force-kill path (mirrors the same fixture in
        # test_claude_code_adapter.py for ClaudeCodeAdapter's identical
        # terminate-then-kill-on-timeout shutdown path).
        coro.close()  # type: ignore[attr-defined]
        raise TimeoutError

    monkeypatch.setattr(mod.asyncio, "wait_for", _fake_wait_for)

    adapter = _adapter()
    proc = _StubbornProcess()
    adapter._proc = proc  # type: ignore[assignment]

    await adapter.aclose()

    assert proc.terminate_called is True
    assert proc.kill_called is True
    assert adapter._proc is None


# --- force_kill(): "option 2 (escalating force-kill)" -- shares
# _terminate_and_kill_process() with aclose() (same real terminate()/kill()
# sequence), so these mirror the aclose() tests above. The one thing that
# actually matters and can't be shown by a plain call-count assertion: this
# path never touches _request()/the RPC channel at all -- see the "doesn't
# wait on send_hard_stop" test below. ---


@pytest.mark.asyncio
async def test_force_kill_terminates_the_appserver() -> None:
    adapter = _adapter()
    await adapter.send_text("hi")
    proc = adapter._proc
    assert proc is not None and proc.returncode is None
    await adapter.force_kill()
    assert adapter._proc is None
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_force_kill_without_a_process_is_a_safe_noop() -> None:
    adapter = _adapter()
    await adapter.force_kill()
    await adapter.force_kill()


@pytest.mark.asyncio
async def test_force_kill_kills_a_process_that_ignores_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import convobox.adapters.codex as mod

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
        coro.close()  # type: ignore[attr-defined]
        raise TimeoutError

    monkeypatch.setattr(mod.asyncio, "wait_for", _fake_wait_for)

    adapter = _adapter()
    proc = _StubbornProcess()
    adapter._proc = proc  # type: ignore[assignment]

    await adapter.force_kill()

    assert proc.terminate_called is True
    assert proc.kill_called is True
    assert adapter._proc is None


@pytest.mark.asyncio
async def test_force_kill_does_not_send_a_polite_interrupt_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole reason force_kill() exists: send_hard_stop()'s
    # "turn/interrupt" RPC rides the same pipe a wedged backend has
    # already stopped reading -- waiting on it (even with its own 30s
    # _RESPONSE_TIMEOUT_S) defeats the purpose. Assert force_kill() never
    # calls _request() at all, not just that it eventually succeeds.
    adapter = _adapter()
    await adapter.send_text("hi")

    called = False

    async def _fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("force_kill() must not call _request()")

    monkeypatch.setattr(adapter, "_request", _fail_if_called)
    await adapter.force_kill()
    assert called is False


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


# --- BackendEventType.ARTIFACT: a completed fileChange item's renderable,
# in-working_dir paths get a matching ARTIFACT event each -- parity with
# ClaudeCodeAdapter's Write/Edit wiring, closing the gap docs/KNOWN-ISSUES.md
# flagged ("codex hasn't been looked at"). See codex.py's
# _resolve_artifact_writes docstring for the schema this is grounded in. ---


@pytest.mark.asyncio
async def test_successful_file_change_yields_artifact_events_for_renderable_paths_only(
    tmp_path: Path,
) -> None:
    # The fake server's "write a file" scenario reports three changes:
    # notes.md (renderable, inside working_dir), binary.exe (not a
    # renderable extension), and ../outside.md (renderable extension but
    # outside working_dir) -- only notes.md should produce an ARTIFACT.
    adapter = CodexAdapter(_FAKE_CODEX, working_dir=str(tmp_path))
    try:
        await adapter.send_text("write a file please")
        events = await _collect(adapter, 5)

        assert [e.type for e in events] == [
            BackendEventType.TOOL_CALL,
            BackendEventType.TOOL_RESULT,
            BackendEventType.ARTIFACT,
            BackendEventType.TEXT,
            BackendEventType.DONE,
        ]
        assert events[2].artifact_path == "notes.md"
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_failed_file_change_yields_no_artifact_event(tmp_path: Path) -> None:
    adapter = CodexAdapter(_FAKE_CODEX, working_dir=str(tmp_path))
    try:
        await adapter.send_text("write a broken file please")
        events = await _collect(adapter, 4)

        assert [e.type for e in events] == [
            BackendEventType.TOOL_CALL,
            BackendEventType.TOOL_RESULT,
            BackendEventType.TEXT,
            BackendEventType.DONE,
        ]
        assert not any(e.type == BackendEventType.ARTIFACT for e in events)
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_successful_file_change_with_no_working_dir_configured_yields_no_artifact() -> None:
    adapter = CodexAdapter(_FAKE_CODEX)  # working_dir defaults to None
    try:
        await adapter.send_text("write a file please")
        events = await _collect(adapter, 4)

        assert [e.type for e in events] == [
            BackendEventType.TOOL_CALL,
            BackendEventType.TOOL_RESULT,
            BackendEventType.TEXT,
            BackendEventType.DONE,
        ]
    finally:
        await _shutdown(adapter)


def test_resolve_artifact_writes_ignores_a_non_renderable_extension(tmp_path: Path) -> None:
    adapter = CodexAdapter(_FAKE_CODEX, working_dir=str(tmp_path))
    result = adapter._resolve_artifact_writes(
        {"status": "completed", "changes": [{"path": "binary.exe"}]}
    )
    assert result == []


def test_resolve_artifact_writes_ignores_an_incomplete_status(tmp_path: Path) -> None:
    adapter = CodexAdapter(_FAKE_CODEX, working_dir=str(tmp_path))
    result = adapter._resolve_artifact_writes(
        {"status": "inProgress", "changes": [{"path": "notes.md"}]}
    )
    assert result == []


def test_resolve_artifact_path_rejects_a_path_outside_working_dir(tmp_path: Path) -> None:
    adapter = CodexAdapter(_FAKE_CODEX, working_dir=str(tmp_path / "workspace"))
    outside = str(tmp_path / "outside.png")
    assert adapter._resolve_artifact_path(outside) is None


def test_resolve_artifact_path_accepts_an_absolute_path_inside_working_dir(
    tmp_path: Path,
) -> None:
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    absolute = str(working_dir / "plots" / "chart.png")
    adapter = CodexAdapter(_FAKE_CODEX, working_dir=str(working_dir))
    assert adapter._resolve_artifact_path(absolute) == str(Path("plots") / "chart.png")


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
async def test_send_text_clears_busy_when_the_turn_start_request_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # send_text sets _busy = True before awaiting turn/start and relies on
    # its own except/re-raise to unset it if the request fails -- nothing
    # else clears busy on this path (the reader task never sees a
    # turn/started notification for a request that never got a response).
    # Untested before this: every existing failure-flavored scenario
    # (thread/start with no id, process death) fails BEFORE or entirely
    # outside send_text's own try block.
    adapter = _adapter()
    try:
        await adapter._ensure_thread()

        async def _boom(method: str, params: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("app-server rejected turn/start")

        monkeypatch.setattr(adapter, "_request", _boom)

        with pytest.raises(RuntimeError, match="rejected turn/start"):
            await adapter.send_text("hi")
        assert adapter.is_busy() is False
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
        # "accept", not "approve" -- confirmed against codex-cli 0.144.6's
        # own generated schema (CommandExecutionApprovalDecision has no
        # "approve" enum member at all); a stale "approve" here silently
        # made every voice-approved write get rejected by Codex anyway
        # (live-found 2026-07-20 UAT session).
        assert events[0].content == "approval decision was: accept"
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
async def test_hard_stop_declines_a_pending_approval_first() -> None:
    # send_hard_stop()'s own comment: "Never leave an operator-held request
    # dangling when the safeword aborts the turn." Real coverage gap found
    # 2026-07-21: no existing test exercised this interaction at all --
    # every hard-stop test used a plain "hang forever" turn with no
    # approval in flight, and every approval test resolved (or left
    # untouched) the approval without ever calling send_hard_stop().
    adapter = _adapter()
    try:
        adapter.set_interactive_approvals(True)
        await adapter.send_text("this needs approval")

        collected: list[BackendEvent] = []

        async def consume() -> None:
            async for event in adapter.events():
                collected.append(event)

        consumer = asyncio.ensure_future(consume())
        await asyncio.sleep(0.2)
        assert any(e.type == BackendEventType.APPROVAL_REQUEST for e in collected)
        assert adapter.is_busy() is True  # turn stays blocked on the approval

        await adapter.send_hard_stop()
        assert adapter.is_busy() is False  # immediately

        await asyncio.sleep(0.3)
        # The pending approval was declined (not left dangling) as part of
        # the hard stop -- a second, explicit resolve_pending_approval must
        # now be a no-op (nothing left pending).
        assert await adapter.resolve_pending_approval(True) is False
        assert any(
            e.type == BackendEventType.TEXT and e.content == "approval decision was: decline"
            for e in collected
        )
        assert any(e.type == BackendEventType.DONE for e in collected)

        # Same thread serves the next turn (matches the plain hard-stop
        # test's own "thread stays usable" guarantee).
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
async def test_non_integer_approval_id_is_declined_not_operator_facing() -> None:
    # _answer_server_request's defensive guard: an approval request whose
    # id isn't an int can't be answered later (resolve_pending_approval
    # keys off it), so it must be declined immediately rather than surfaced
    # as an operator-facing prompt nothing could ever resolve.
    adapter = _adapter()
    try:
        adapter.set_interactive_approvals(True)
        await adapter.send_text("this needs approval with a bad id")
        events = await _collect(adapter, 2)
        assert not any(e.type == BackendEventType.APPROVAL_REQUEST for e in events)
        assert events[0].content == "approval decision was: decline"
        assert events[1].type == BackendEventType.DONE
    finally:
        await _shutdown(adapter)


@pytest.mark.asyncio
async def test_second_pending_approval_is_auto_declined_not_swapped_in() -> None:
    # _answer_server_request's other defensive guard: if a second approval
    # request arrives before the first is answered, it must never replace
    # the decision the operator is currently looking at -- auto-decline the
    # second, leave the first's prompt (and the operator's chance to answer
    # it) untouched.
    adapter = _adapter()
    try:
        adapter.set_interactive_approvals(True)
        await adapter.send_text("this needs two approvals")
        events = await _collect(adapter, 2)
        # Exactly one operator-facing prompt -- the second request never
        # became a second APPROVAL_REQUEST event.
        assert sum(1 for e in events if e.type == BackendEventType.APPROVAL_REQUEST) == 1
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
async def test_resolve_response_ignores_unknown_and_already_done_futures() -> None:
    # _resolve_response is the reader task's only path back to a waiting
    # _request() call; a response for an id nobody's waiting on (already
    # popped, or resolved twice by a malformed server) must be a silent
    # no-op, not a KeyError or an InvalidStateError from calling
    # set_result/set_exception on an already-done future.
    adapter = _adapter()

    adapter._resolve_response({"id": 999, "result": {}})  # nothing pending at all

    future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    future.set_result({"already": "done"})
    adapter._pending[1] = future
    adapter._resolve_response({"id": 1, "result": {"new": "value"}})
    assert future.result() == {"already": "done"}


@pytest.mark.asyncio
async def test_answer_server_request_declines_unknown_methods_generically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A server->client request whose method isn't in _APPROVAL_DENY_PAYLOADS
    # (some future app-server protocol addition) falls back to a generic
    # decline rather than hanging the turn forever or raising a KeyError.
    adapter = _adapter()
    written: list[dict[str, object]] = []

    async def _capture(payload: dict[str, object]) -> None:
        written.append(payload)

    monkeypatch.setattr(adapter, "_write", _capture)
    await adapter._answer_server_request({"id": 42, "method": "some/unknown/method"})
    assert written == [{"jsonrpc": "2.0", "id": 42, "result": {"decision": "decline"}}]


def test_handle_notification_bare_error_method_yields_error_event() -> None:
    # Distinct from a failed turn/completed: a bare top-level "error"
    # notification (protocol-level, not turn-scoped) must still surface
    # as an ERROR event rather than being one of the deliberately-ignored
    # notification types.
    adapter = _adapter()
    adapter._handle_notification({"method": "error", "params": {"message": "boom"}})
    event = adapter._events.get_nowait()
    assert event.type == BackendEventType.ERROR
    assert event.content is not None and "boom" in event.content


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


def test_resolve_command_leaves_non_codex_binary_names_untouched_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The .cmd/.exe shim guessing loop only exists to resolve a bare
    # "codex" on PATH; a caller who already named a specific wrapper
    # binary (e.g. a custom launcher script) must pass through unchanged,
    # without even consulting which() -- untested before this, since both
    # existing Windows-path tests use "codex" as the head token.
    import convobox.adapters.codex as mod

    monkeypatch.setattr(mod.os, "name", "nt", raising=False)

    def _unexpected_which(name: str) -> str | None:
        raise AssertionError(f"shutil.which({name!r}) should not be called for a non-codex binary")

    monkeypatch.setattr(mod.shutil, "which", _unexpected_which)

    adapter = CodexAdapter(["my-custom-codex-wrapper", "--flag"])
    assert adapter._command == ["my-custom-codex-wrapper", "--flag"]


def test_permission_config_args_unknown_mode_passes_no_overrides() -> None:
    # permission_mode is a plain string with no validation between
    # BackendConfig and CodexAdapter's constructor -- an unrecognized
    # value (typo, future mode not yet wired here) must degrade to no
    # -c overrides rather than raising or silently picking a posture.
    import convobox.adapters.codex as mod

    assert mod._permission_config_args("nonexistent-mode") == []


def test_describe_approval_request_file_change_uses_the_changes_field() -> None:
    import convobox.adapters.codex as mod

    text = mod._describe_approval_request(
        "item/fileChange/requestApproval",
        {"changes": "diff --git a/x b/x\n+added line"},
    )
    assert "FILE CHANGE" in text
    assert "Requested change:" in text
    assert "diff --git a/x b/x" in text


def test_describe_approval_request_includes_cwd_and_reason_when_present() -> None:
    import convobox.adapters.codex as mod

    text = mod._describe_approval_request(
        "item/commandExecution/requestApproval",
        {"command": "rm -rf /tmp/x", "cwd": "/home/user/project", "reason": "cleanup"},
    )
    assert "Working directory: /home/user/project" in text
    assert "Reason: cleanup" in text


@pytest.mark.parametrize(
    "command_line",
    ["zsh", "-zsh", "sh", "bash", "/bin/zsh", "/usr/bin/sh", "fish"],
)
def test_is_bare_generic_shell_true_for_a_bare_shell_name(command_line: str) -> None:
    import convobox.adapters.codex as mod

    assert mod._is_bare_generic_shell(command_line) is True


@pytest.mark.parametrize(
    "command_line",
    [
        "sleep 90",
        "ls -la",
        "pwd",
        "/bin/zsh -lc 'echo hi'",
        "zsh-completion-helper",
    ],
)
def test_is_bare_generic_shell_false_for_a_real_command(command_line: str) -> None:
    import convobox.adapters.codex as mod

    assert mod._is_bare_generic_shell(command_line) is False


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="_kill_by_command_text uses signal.SIGKILL, which does not exist "
    "on Windows; the pgrep/ps fallback it belongs to is itself gated to "
    "non-Windows platforms at the force_kill() call site (codex.py).",
)
def test_kill_by_command_text_matches_a_short_legitimate_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test for the 2026-08-18 live-voice finding: a bare,
    # unwrapped short command like "sleep 90" (8 chars) must still be
    # matched and killed, while an unrelated bare shell process that
    # only coincidentally shares a substring ("zsh") with the reported
    # invocation text must NOT be -- see docs/field-notes/2026-08-18-
    # kill-phrase-live-voice-test-finds-two-real-gaps.md.
    import convobox.adapters.codex as mod

    ps_output = (
        "  PID  PPID COMMAND\n"
        "49230     1 codex app-server\n"
        "50564 49230 /bin/zsh -lc sleep 90\n"
        "50565 50564 sleep 90\n"
        "60001     1 zsh\n"  # unrelated bare shell -- must NOT be killed
    )

    class _FakeCompleted:
        stdout = ps_output

    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: _FakeCompleted()  # noqa: ARG005
    )
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)  # noqa: ARG005

    result = mod._kill_by_command_text("/bin/zsh -lc 'sleep 90'")

    assert 50564 in result  # the shell wrapper, matched directly
    assert 50565 in result  # its child, killed via descendant expansion
    assert 60001 not in result
