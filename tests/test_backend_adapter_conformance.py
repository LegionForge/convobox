"""Shared BackendAdapter contract conformance suite, parametrized across
all three real adapters (claude-code, codex, opencode) against their
existing fakes/loopback server -- see tests/fake_claude_cli.py,
tests/fake_codex_appserver.py, tests/_opencode_loopback.py.

Each backend's own test file (test_claude_code_adapter.py,
test_codex_adapter.py, test_opencode_adapter.py) already covers its own
behavior in depth. This file exists for a narrower, cross-cutting purpose:
assert the properties src/convobox/adapters/base.py's BackendAdapter
class documents as a SHARED contract, identically across all three real
implementations, in one place -- so a future contract change (a new
optional method, a changed default) has one test file to update, and a
regression in one adapter's conformance to that shared contract can't
hide behind "well, its own test file didn't check that."

Deliberately asserts DIVERGENT per-backend behavior where the real
contract is divergent by design (force_kill()'s shape differs
architecturally per backend -- see each test's own comment) rather than
assuming uniformity where none exists. See docs/field-notes and
docs/KNOWN-ISSUES.md for the live incidents that shaped each of these.

Needs no real audio hardware and no live backend credentials -- every
adapter here talks to a local fake subprocess or loopback HTTP server, so
this suite is intended to run in CI on every platform.

NOT exercising tests/test_orchestrator.py's FakeBackendAdapter here: that
double deliberately diverges from real adapter behavior on purpose (e.g.
its force_kill() never clears _busy, to test Orchestrator's OWN
responsibility for that) -- see that file's own class docstring. A
contract change here should prompt a human to check that file too, not be
enforced structurally against it.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from convobox.adapters.base import BackendAdapter
from convobox.adapters.claude_code import ClaudeCodeAdapter
from convobox.adapters.codex import CodexAdapter
from convobox.adapters.opencode import OpenCodeAdapter

from ._opencode_loopback import OpenCodeServer, _frame

_FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_claude_cli.py"))]
_FAKE_CODEX = [sys.executable, str(Path(__file__).with_name("fake_codex_appserver.py"))]

_BACKENDS = ["claude-code", "codex", "opencode"]

_ONE_STEP_OPENCODE_FRAMES: list[dict[str, object]] = [
    _frame(1, "session.next.step.started", {}),
    _frame(2, "session.next.text.ended", {"textID": "text-0", "text": "hello"}),
    _frame(3, "session.next.step.ended", {"finish": "stop"}),
]


@asynccontextmanager
async def _adapter(backend: str) -> AsyncIterator[BackendAdapter]:
    """Construct one real adapter for `backend`, freshly, with no prior
    turn sent -- the "just constructed, nothing has happened yet" state
    the optional no-op contract methods must all handle safely.
    """
    if backend == "claude-code":
        adapter = ClaudeCodeAdapter(_FAKE_CLI)
        try:
            yield adapter
        finally:
            await adapter.aclose()
    elif backend == "codex":
        adapter = CodexAdapter(_FAKE_CODEX)
        try:
            yield adapter
        finally:
            await adapter.aclose()
    elif backend == "opencode":
        server = OpenCodeServer(list(_ONE_STEP_OPENCODE_FRAMES))
        await server.start()
        adapter = OpenCodeAdapter(server.base_url)
        try:
            yield adapter
        finally:
            await adapter.aclose()
            await server.stop()
    else:
        raise ValueError(backend)


async def _send_one_turn(adapter: BackendAdapter) -> None:
    """Puts the adapter into the "has an active/recently-active turn"
    state the kill_phrase path actually encounters in Orchestrator,
    unlike a bare freshly-constructed adapter. send_text() itself only
    dispatches the request and flips busy state for all three real
    adapters (spawns+writes for claude-code, POSTs turn/start for codex,
    POSTs the prompt for opencode) -- none of them need events() actively
    consumed for send_text() to return, so no consumer task is needed
    just to reach this state.
    """
    await adapter.send_text("hello")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _BACKENDS)
async def test_default_noop_contract_methods_never_raise_and_do_no_io(
    backend: str,
) -> None:
    """The seven optional BackendAdapter methods, called on a freshly
    constructed adapter with nothing pending/tracked -- every one of
    them must be safe to call in this state (an operator's kill_phrase/
    quit path may call several of these before anything has ever
    happened), per each method's own "default no-op, override where
    real" docstring contract.
    """
    async with _adapter(backend) as adapter:
        await adapter.wait_listening(timeout=0.05)
        adapter.set_interactive_approvals(True)
        adapter.set_interactive_approvals(False)
        assert await adapter.resolve_pending_approval(True) is False
        assert await adapter.resolve_pending_approval(False) is False
        jobs = adapter.background_jobs()
        assert list(jobs) == []
        assert not inspect.iscoroutinefunction(adapter.background_jobs), (
            "background_jobs() must be synchronous and do no I/O, per its "
            "own docstring -- a caller on the quit/eject path relies on "
            "this never blocking."
        )
        assert await adapter.stop_background_job("nonexistent-job-id") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _BACKENDS)
async def test_force_kill_then_later_aclose_is_idempotent_and_never_raises(
    backend: str,
) -> None:
    """The EXACT sequence Orchestrator's kill_phrase escalation path runs:
    force_kill() first (must not go through the backend's own possibly-
    wedged channel), then a normal aclose() moments later (runs whatever
    teardown force_kill() deliberately skipped). Asserted identically
    across all three backends -- today no single test file asserts this
    ordered pair for any of them, only force_kill() alone or aclose()
    alone.
    """
    async with _adapter(backend) as adapter:
        await _send_one_turn(adapter)
        await adapter.force_kill()
        await adapter.force_kill()  # force_kill() itself must be idempotent
        await adapter.aclose()
        await adapter.aclose()  # and the aclose() that follows it, too


@pytest.mark.asyncio
async def test_opencode_force_kill_is_local_disconnect_only() -> None:
    """opencode has no owned OS process to escalate against (HTTP+SSE) --
    force_kill() has no override at all in opencode.py and must delegate
    straight to aclose() (close the SSE stream / HTTP client). This is a
    LOCAL connection-close assertion only -- it must NOT assert anything
    about the real `opencode serve` process's fate. docs/KNOWN-ISSUES.md
    records a real 30-run live harness where that remote process died
    anyway in 7/30 runs, unpredictably, root cause not established --
    asserting "it stays alive" or "it dies" here would both be lies about
    something this adapter architecturally cannot control or observe.
    """
    async with _adapter("opencode") as adapter:
        assert isinstance(adapter, OpenCodeAdapter)
        assert type(adapter).force_kill is BackendAdapter.force_kill, (
            "OpenCodeAdapter must not define its own force_kill() override -- "
            "if this ever changes, this test (and its own comment above) "
            "needs a human to re-decide what to assert, not just be deleted."
        )
        await adapter.force_kill()
        assert adapter._client.is_closed


@pytest.mark.asyncio
async def test_claude_code_force_kill_skips_aclose_teardown_until_aclose_runs() -> None:
    """claude-code's force_kill() deliberately terminates only the
    subprocess and returns -- it must NOT also run aclose()'s other
    teardown (pending-approval denial, approval-server shutdown,
    settings-file cleanup) inline, per claude_code.py's own comment: that
    teardown is left for the SEPARATE aclose() call Orchestrator's
    kill_phrase path makes moments later. Asserted here by confirming the
    process is gone after force_kill() alone, while aclose() has not yet
    been called (so any teardown it would run hasn't run either) --
    the actual teardown methods are private/version-fragile to hook
    directly, so this asserts the documented BEHAVIORAL split (process
    death happens at force_kill time, not later) rather than mocking
    claude_code.py's own internals.
    """
    async with _adapter("claude-code") as adapter:
        assert isinstance(adapter, ClaudeCodeAdapter)
        await adapter.send_text("hello")
        proc = adapter._proc
        assert proc is not None and proc.returncode is None
        await adapter.force_kill()
        assert adapter._proc is None
        assert proc.returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [True, False])
async def test_codex_kill_by_command_text_fallback_gated_on_busy(
    busy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex.py's own force_kill() reads self._last_command_text and
    self._busy BEFORE terminating the app-server, and only invokes the
    `ps`-substring-match SIGKILL fallback (_kill_by_command_text) when
    was_busy AND a command string is known AND the platform is POSIX --
    see codex.py's force_kill() docstring for why (reduce, not eliminate,
    the chance of acting on a stale command from an already-finished
    turn). This is pure logic -- no real SIGKILL, no `ps` call, no
    subprocess tree -- so it runs on every platform including Windows CI,
    unlike the real-process integration tests in
    test_real_process_tree_kill.py.
    """
    calls: list[str] = []

    def _spy(command: str) -> list[int]:
        calls.append(command)
        return []

    monkeypatch.setattr("convobox.adapters.codex._kill_by_command_text", _spy)
    monkeypatch.setattr(sys, "platform", "linux", raising=False)

    async with _adapter("codex") as adapter:
        assert isinstance(adapter, CodexAdapter)
        adapter._last_command_text = "sleep 90"
        adapter._busy = busy
        await adapter.force_kill()

    if busy:
        assert calls == ["sleep 90"]
    else:
        assert calls == []
