"""Real OS process-tree kill verification -- NOT log-inference, not a
mocked `ps`/`os.kill`, an actual spawned process tree checked for real
after force_kill() runs. Gated the same way tests/test_windows_job_object.py
already does it (per-test @pytest.mark.skipif, flat tests/ layout, no new
marker infrastructure) -- these need no real audio hardware, only a real
OS, so they're intended to run in a CI platform matrix.

This project has a real, documented history of process-kill bugs that
only ever showed up against REAL process trees, never against mocked
`ps`/`os.kill` (see docs/KNOWN-ISSUES.md, and the several 2026-08-15/18/23
field notes) -- tests/test_codex_adapter.py's existing
test_kill_by_command_text_matches_* tests cover the STRING-matching logic
thoroughly with synthetic `ps` output, but never spawn a real process or
call a real os.kill. These tests close that specific gap.

Encodes today's ACTUAL state, not aspiration:
- claude-code: force_kill() reliably kills its own directly-owned
  subprocess (verified here with a real OS-level liveness check, not just
  proc.returncode). Whether a child THAT subprocess spawns also dies is a
  property of the real `claude` CLI's own internal process management
  (previously live-validated 10/10 on Windows and macOS) -- not something
  a bare test fake can meaningfully validate, since a fake script doesn't
  replicate the real CLI's internal signal handling. Not asserted here;
  see docs/UAT-claude-code-smoke.md for the live-hardware check.
- codex: `_kill_by_command_text`'s real descendant-kill fallback, proven
  here against real spawned process trees on Linux/macOS (POSIX-only,
  skipped on Windows -- the function requires `ps`/`signal.SIGKILL`,
  neither of which exists there).
- opencode: no process-tree assertion at all -- see
  tests/test_backend_adapter_conformance.py's
  test_opencode_force_kill_is_local_disconnect_only for why.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess  # nosec B404 -- spawns real, short-lived test processes
import sys
import time
import uuid
from pathlib import Path

import pytest

from convobox.adapters.claude_code import ClaudeCodeAdapter
from convobox.adapters.codex import CodexAdapter, _kill_by_command_text

_FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_claude_cli.py"))]
_FAKE_CODEX = [sys.executable, str(Path(__file__).with_name("fake_codex_appserver.py"))]

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="_kill_by_command_text uses `ps`/signal.SIGKILL, neither of "
    "which exists on Windows -- gated the same way at the force_kill() "
    "call site in codex.py itself.",
)


def _pid_alive(pid: int) -> bool:
    """Real OS-level liveness check -- deliberately NOT the same
    implementation style as _kill_by_command_text's own `ps` scan, so a
    bug shared between both wouldn't cancel out in these tests.
    """
    if sys.platform == "win32":
        out = subprocess.run(  # nosec B603 B607
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, check=False,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)  # signal 0: existence check only, sends nothing real
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not signalable by us
    return True


def _wait_until(predicate: object, timeout_s: float = 5.0, interval_s: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(interval_s)
    return predicate()  # type: ignore[operator]


async def _await_until(predicate: object, timeout_s: float = 5.0, interval_s: float = 0.1) -> bool:
    """Same polling loop as _wait_until, but with `await asyncio.sleep()`
    instead of a blocking `time.sleep()` -- REQUIRED inside an async test
    that also owns a live adapter with its own background reader task
    (CodexAdapter._read_loop): a blocking time.sleep() here would stall
    the whole event loop for its duration, starving that reader task of
    any chance to run and process the very message
    (item/started/commandExecution) the test is waiting to see land.
    Found live while writing this test: the real spawned OS process
    genuinely existed (an independent OS-level fact, not gated on our
    event loop running at all), so a _wait_until-style blocking poll
    here still "succeeded" at finding it -- while silently starving the
    adapter's own _last_command_text tracking of the chance to update,
    making the later force_kill() call fire with a stale/empty command
    and fail for a completely different, confusing reason.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        await asyncio.sleep(interval_s)
    return predicate()  # type: ignore[operator]


def _ps_pids_matching(marker: str) -> list[int]:
    """Test-only process scan -- deliberately simple and independently
    written from _kill_by_command_text's own ps parsing, for the same
    "don't let a shared bug cancel out" reason as _pid_alive above.

    `COLUMNS=10000` is load-bearing, not decorative: found live while
    writing this test -- `ps`'s COMMAND column truncates to terminal
    width whenever stdout isn't a wide/real tty (confirmed directly: run
    under pytest, a real spawned process's own `ps` line was cut off
    mid-word, e.g. "...python -c import tim", losing everything after --
    the marker this function searches for lives later in the command
    text and was silently never there to find, making a real, live
    process look like it never existed). `_kill_by_command_text` itself
    isn't affected by this specific issue (its own callers run outside
    pytest's capture), but this test helper needed the same wide-output
    guarantee to verify it correctly.
    """
    env = {**os.environ, "COLUMNS": "10000"}
    out = subprocess.run(  # nosec B603 B607
        ["ps", "-eo", "pid,command"], capture_output=True, text=True, check=False, env=env,
    ).stdout
    pids = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        pid_str, _, cmd = line.partition(" ")
        if marker in cmd:
            try:
                pids.append(int(pid_str))
            except ValueError:
                continue
    return pids


@_POSIX_ONLY
def test_kill_by_command_text_kills_a_real_multi_statement_shell_tree() -> None:
    """Reproduces the 2026-08-15 finding directly: a real `sh -c 'echo
    marker; sleep N'` process -- the wrapper reports the matched command
    text, but `sleep` runs as a SEPARATE forked child (only a script's
    tail command can be exec'd in-place; anything before a `;` forks).
    Both the wrapper and its forked child must be dead after
    _kill_by_command_text, not just the wrapper.
    """
    marker = f"convobox-test-{uuid.uuid4().hex}"
    # "; echo done" at the end is load-bearing, not decorative: bash (this
    # box's /bin/sh) execs the LAST command of a `;`-script in place
    # (replacing its own process image, losing the marker from `ps`
    # entirely) whenever nothing follows it -- confirmed live while
    # writing this test, `echo X; sleep N` alone left NO separate child
    # and no wrapper process either. A trailing no-op command makes
    # `sleep` genuinely non-tail, so it forks as its own process --
    # matching the real bug shape this test exists to catch.
    real_argv_command = f"echo {marker}; sleep 45; echo done"
    reported_command = f"sh -c {real_argv_command}"
    proc = subprocess.Popen(["sh", "-c", real_argv_command])  # nosec B603 B607
    try:
        found = _wait_until(lambda: len(_ps_pids_matching(marker)) >= 1, timeout_s=5.0)
        assert found, "the real child process never appeared in `ps` -- test setup itself failed"
        # Give the wrapper a moment to settle past the (near-instant)
        # `echo` into just the long-lived `sleep` child, so the assertion
        # below is about the real steady-state tree, not a startup race.
        time.sleep(0.3)
        pids_before = _ps_pids_matching(marker)
        assert pids_before, "no matching real processes found before the kill"

        killed = _kill_by_command_text(reported_command)
        assert killed, "_kill_by_command_text found nothing to kill against a real process tree"

        all_dead = _wait_until(lambda: not _ps_pids_matching(marker), timeout_s=5.0)
        assert all_dead, f"still alive after kill: {_ps_pids_matching(marker)}"
    finally:
        # Best-effort reap -- already dead if the kill worked, so a
        # timeout/any other failure here is not this test's concern.
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


@_POSIX_ONLY
def test_kill_by_command_text_kills_a_real_multiline_process() -> None:
    """Reproduces the 2026-08-25 Linux finding (see
    test_codex_adapter.py's test_kill_by_command_text_matches_a_multiline_
    command_on_linux and codex.py's _normalize_whitespace docstring) with
    a REAL spawned process, not mocked `ps` output: a real multi-line
    `python3 -c "..."` script, confirmed dead after the kill on whichever
    platform this actually runs on (Linux's procps renders the embedded
    newline as a space; macOS's BSD ps octal-escapes it as `\\012` --
    _normalize_whitespace handles both, but only a REAL spawned process
    proves that against this platform's REAL ps, not an assumption about it).
    """
    marker = f"convobox-test-{uuid.uuid4().hex}"
    script = f"import time\nprint({marker!r})\ntime.sleep(45)\n"
    # Report the SAME interpreter invocation actually spawned below, not
    # a hardcoded "python3" -- found live while writing this test: this
    # venv's own interpreter is literally named `python` (no "3"), so a
    # hardcoded "python3" in the reported text never substring-matched
    # the real `ps` line at all. Real codex reports the command it
    # actually ran, never a guessed/generic name -- matching that here is
    # what makes this a real test of the matching logic, not an
    # artificial mismatch of this test's own making.
    reported_command = f'{sys.executable} -c "{script}"'
    proc = subprocess.Popen([sys.executable, "-c", script])  # nosec B603
    try:
        found = _wait_until(lambda: len(_ps_pids_matching(marker)) >= 1, timeout_s=5.0)
        assert found, "the real process never appeared in `ps` -- test setup itself failed"

        killed = _kill_by_command_text(reported_command)
        assert killed, "_kill_by_command_text found nothing to kill against a real multi-line process"

        all_dead = _wait_until(lambda: not _ps_pids_matching(marker), timeout_s=5.0)
        assert all_dead, f"still alive after kill: {_ps_pids_matching(marker)}"
    finally:
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


@_POSIX_ONLY
@pytest.mark.asyncio
async def test_codex_adapter_force_kill_kills_a_real_spawned_process_tree() -> None:
    """End-to-end, through the REAL CodexAdapter (not the bare function
    above): drives one real turn against fake_codex_appserver.py's "spawn
    a real killable process" trigger (which forks a real `sh -c` tree and
    reports it exactly like a real long-running commandExecution would,
    left deliberately incomplete so the turn stays busy), then calls the
    adapter's real force_kill() and confirms the real spawned tree --
    not just the fake app-server subprocess itself -- is actually dead.
    Exercises the full path: busy-gating + _last_command_text capture +
    force_kill()'s own orchestration, not just _kill_by_command_text in
    isolation.
    """
    marker = f"convobox-test-{uuid.uuid4().hex}"
    adapter = CodexAdapter(_FAKE_CODEX)
    try:
        await adapter.send_text(f"spawn a real killable process {marker}")
        found = await _await_until(lambda: len(_ps_pids_matching(marker)) >= 1, timeout_s=5.0)
        assert found, "the real child process never appeared in `ps` -- test setup itself failed"
        # Cooperative sleep, not a blocking one -- see _await_until's own
        # docstring: the adapter's background reader task (which sets
        # _last_command_text from the item/started message this test is
        # about to rely on) only runs when the event loop actually gets
        # to schedule it.
        await asyncio.sleep(0.3)

        assert adapter.is_busy() is True
        await adapter.force_kill()

        all_dead = await _await_until(lambda: not _ps_pids_matching(marker), timeout_s=5.0)
        assert all_dead, f"still alive after adapter.force_kill(): {_ps_pids_matching(marker)}"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_claude_code_adapter_force_kill_kills_the_real_os_process() -> None:
    """A real OS-level liveness check (not just proc.returncode, which
    only confirms Python's own bookkeeping updated) for claude-code's
    directly-owned subprocess -- the one thing this adapter's force_kill()
    actually controls. Whether a CHILD that subprocess spawns also dies is
    a separate question this test deliberately does not attempt (see this
    file's own module docstring) -- it depends on the real `claude`
    binary's own internal process management, which a bare test fake
    cannot meaningfully stand in for.
    """
    adapter = ClaudeCodeAdapter(_FAKE_CLI)
    try:
        await adapter.send_text("hello")
        proc = adapter._proc
        assert proc is not None
        pid = proc.pid
        assert _pid_alive(pid) is True, "test setup itself failed -- the fake CLI never started"

        await adapter.force_kill()

        dead = await _await_until(lambda: not _pid_alive(pid), timeout_s=5.0)
        assert dead, f"real OS process {pid} is still alive after force_kill()"
    finally:
        await adapter.aclose()
