"""Tests for convobox.adapters._windows_job_object.

Two tiers, matching this repo's own stated bar for this specific piece
(docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md's Open Questions: "any
acceptance test for this work must include a real live run reproducing
the original Windows incident, not just a scripted harness"):

1. Fast, cross-platform unit tests of the non-Windows short-circuit and
   the ctypes call shape, mocking ``ctypes.windll`` the same way
   test_transcriber.py's _memory_diagnostic tests already do.
2. One real, live, Windows-only integration test that reproduces the
   actual disclosed scenario end to end: a tracked process launches a
   DETACHED child via Start-Process, the tracked process exits (the
   moment force_kill() has nothing left to reach today), and the
   detached child must still be visible via enumerate_job_pids() --
   then close_job() must NOT have killed it. This is the same shape
   independently live-verified once already (2026-08-23, not committed)
   before this test was written; this formalizes that run as a
   permanent regression check rather than a one-off manual probe.
"""

from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from convobox.adapters import _windows_job_object as jo


# --- non-Windows short-circuit: every function must degrade to a safe,
# non-raising no-op without touching ctypes.windll at all (which doesn't
# exist off Windows) ---


def test_create_job_returns_none_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert jo.create_job() is None


def test_assign_to_job_returns_false_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert jo.assign_to_job(1, 2) is False


def test_enumerate_job_pids_returns_empty_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert jo.enumerate_job_pids(1) == []


def test_close_job_is_a_silent_no_op_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    jo.close_job(1)  # must not raise


# --- Windows-shaped ctypes mocking, same pattern as test_transcriber.py's
# _memory_diagnostic tests: fake kernel32 functions that write into the
# REAL structure/pointer objects the module builds internally, via
# byref()._obj (a stable CPython implementation detail already relied on
# elsewhere in this suite). ---


def _fake_windll(**kernel32_funcs: object) -> SimpleNamespace:
    # ctypes.windll.kernel32.X -- windll itself needs a .kernel32
    # sub-object carrying the actual functions, same double-nesting as
    # test_transcriber.py's _fake_windll for _memory_diagnostic.
    return SimpleNamespace(kernel32=SimpleNamespace(**kernel32_funcs))


def test_create_job_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_create(_attrs, _name):  # type: ignore[no-untyped-def]
        return 0  # NULL handle -- failure

    monkeypatch.setattr(
        "ctypes.windll", _fake_windll(CreateJobObjectW=fake_create), raising=False
    )
    monkeypatch.setattr("ctypes.get_last_error", lambda: 5, raising=False)
    assert jo.create_job() is None


def test_create_job_returns_a_handle_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_create(_attrs, _name):  # type: ignore[no-untyped-def]
        return 4242

    monkeypatch.setattr(
        "ctypes.windll", _fake_windll(CreateJobObjectW=fake_create), raising=False
    )
    assert jo.create_job() == 4242


def test_assign_to_job_fails_closed_when_openprocess_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_open_process(_access, _inherit, _pid):  # type: ignore[no-untyped-def]
        return 0  # NULL -- OpenProcess failed

    monkeypatch.setattr(
        "ctypes.windll",
        _fake_windll(
            OpenProcess=fake_open_process,
            AssignProcessToJobObject=lambda *_: 1,
            CloseHandle=lambda *_: 1,
        ),
        raising=False,
    )
    monkeypatch.setattr("ctypes.get_last_error", lambda: 5, raising=False)
    assert jo.assign_to_job(1, 999) is False


def test_assign_to_job_closes_the_process_handle_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A leaked process handle on every failed assignment would be a real
    # resource leak over a long-running voice session -- must close
    # regardless of AssignProcessToJobObject's own result.
    monkeypatch.setattr(sys, "platform", "win32")
    closed: list[int] = []

    monkeypatch.setattr(
        "ctypes.windll",
        _fake_windll(
            OpenProcess=lambda *_: 777,
            AssignProcessToJobObject=lambda *_: 0,  # fails
            CloseHandle=lambda h: closed.append(h) or 1,  # type: ignore[func-returns-value]
        ),
        raising=False,
    )
    monkeypatch.setattr("ctypes.get_last_error", lambda: 5, raising=False)
    assert jo.assign_to_job(1, 999) is False
    assert len(closed) == 1


def test_enumerate_job_pids_reads_back_the_real_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_query(_job, _class, byref_info, _size, byref_returned):  # type: ignore[no-untyped-def]
        info = byref_info._obj
        info.NumberOfAssignedProcesses = 2
        info.NumberOfProcessIdsInList = 2
        info.ProcessIdList[0] = 111
        info.ProcessIdList[1] = 222
        return 1

    monkeypatch.setattr(
        "ctypes.windll", _fake_windll(QueryInformationJobObject=fake_query), raising=False
    )
    assert jo.enumerate_job_pids(1) == [111, 222]


def test_enumerate_job_pids_returns_empty_on_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "ctypes.windll",
        _fake_windll(QueryInformationJobObject=lambda *_: 0),
        raising=False,
    )
    monkeypatch.setattr("ctypes.get_last_error", lambda: 5, raising=False)
    assert jo.enumerate_job_pids(1) == []


def test_close_job_never_raises_even_if_closehandle_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "ctypes.windll", _fake_windll(CloseHandle=lambda *_: 0), raising=False
    )
    jo.close_job(1)  # must not raise


# --- The real thing: live on actual Windows, reproducing the disclosed
# scenario end to end. Skipped everywhere else, same shape as
# test_codex_adapter.py's SIGKILL-only skip. ---


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Exercises the real Win32 Job Object API against real spawned "
    "processes -- the whole point of this module. No cross-platform "
    "equivalent to fall back to; the unit tests above already cover the "
    "ctypes call shape and the non-Windows no-op path.",
)
def test_detached_descendant_stays_visible_after_tracked_process_exits() -> None:
    # Reproduces docs/KNOWN-ISSUES.md's disclosed Windows kill_phrase gap
    # mechanistically: a tracked process launches a DETACHED child via
    # Start-Process, then exits -- the exact moment force_kill() has
    # nothing left to reach today. The detached child must still be
    # visible via enumerate_job_pids(), and close_job() must not kill it.
    job = jo.create_job()
    assert job is not None

    # ONE PowerShell -Command argument value, assembled as its own named
    # string first rather than left as adjacent-literal concatenation
    # inline in the list -- a static-analysis pass flagged the inline
    # form as "implicit string concatenation in a list, maybe missing a
    # comma?" (a real, common bug elsewhere; a false positive here, since
    # a comma here would instead pass this text as several BROKEN
    # trailing argv entries to powershell.exe rather than one coherent
    # command -- but not worth leaving ambiguous for the next reader).
    detach_command = (
        "Start-Process powershell -ArgumentList "
        "'-NoProfile -Command \"Start-Sleep -Seconds 20\"' "
        "-WindowStyle Hidden; exit 0"
    )
    tracked = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", detach_command],
    )
    try:
        assert jo.assign_to_job(job, tracked.pid) is True
        tracked.wait(timeout=10)

        # Give Start-Process's own child a moment to fully spawn.
        time.sleep(1.5)

        pids = jo.enumerate_job_pids(job)
        assert len(pids) >= 1, (
            "detached child not visible in job -- the exact gap this "
            "module exists to close"
        )

        # Closing the job must not have killed anything -- the whole
        # "observation, not termination" contract.
        jo.close_job(job)
        time.sleep(0.5)
        def _still_running(pid: int) -> bool:
            check_command = (
                f"Get-Process -Id {pid} -ErrorAction SilentlyContinue "
                "| Select-Object -ExpandProperty Id"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", check_command],
                capture_output=True, text=True,
            )
            return bool(result.stdout.strip())

        still_alive = [pid for pid in pids if _still_running(pid)]
        assert still_alive, "close_job() killed the detached process -- it must not"
    finally:
        # Test cleanup only -- not exercising kill_phrase, just not
        # leaving stray sleep processes behind after the test run.
        for pid in jo.enumerate_job_pids(job):
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True,
            )
