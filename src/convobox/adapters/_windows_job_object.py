"""Windows Job Object wrapper -- OBSERVATION ONLY, never used to kill.

docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md's central pivot, encoded here
structurally rather than just documented: KNOWN-ISSUES.md's original
recommended fix for the Windows kill_phrase gap was a Job Object with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, so closing the job handle kills
everything the backend ever spawned -- including a dev server the user
deliberately backgrounded and wanted kept alive. This module never calls
SetInformationJobObject at all, so the job we create has NO limits set.
CloseHandle-ing it (close_job below) releases our reference and does
nothing to the member processes -- confirmed by omission, not by a flag
we could accidentally flip back on.

What this buys: a Windows Job Object automatically tracks every process a
member process spawns, including ones that detach via Start-Process --
Start-Process does not break out of a job unless CREATE_BREAKAWAY_FROM_JOB
is used and the job explicitly permits it (a limit we never set, so
breakaway is impossible here). QueryInformationJobObject then enumerates
every live PID in the job at any moment, which is exactly the visibility
the disclosed Windows kill_phrase gap (docs/KNOWN-ISSUES.md) needs: the
detached descendant that survives force_kill() today is INVISIBLE to it
specifically because nothing currently tracks it at all. This makes it
visible. It does not make it killable -- that's a deliberate, separate
decision left to a later, explicit action, never automatic.

Known, honest limitation: this assigns the process to the job
immediately after spawn, not via CREATE_SUSPENDED + assign + resume (the
airtight approach, which would require replacing
asyncio.create_subprocess_exec with a raw CreateProcess call -- real
added complexity for closing an already-narrow window). There is a real
but small race: a process could theoretically spawn and detach a child of
its own in the few milliseconds between process creation and this
module's assign_to_job() call. Not yet measured; flagged rather than
silently assumed away.

Same house style as transcriber.py's _memory_diagnostic(): ctypes only
(no new dependency), everything local to each function, degrades to a
logged warning and a no-op return rather than ever raising into a caller
that has no reason to expect a platform API to fail.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_MAX_TRACKED_PIDS = 1024  # generous headroom; see enumerate_job_pids()

_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3  # JobObjectBasicProcessIdList info class


def create_job() -> int | None:
    """CreateJobObjectW(None, None) -- an unnamed job with NO limits set
    (see module docstring for why that omission is load-bearing). Returns
    a raw HANDLE as an int, or None on any failure/non-Windows platform.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            logger.warning(
                "CreateJobObjectW failed (error %d) -- background-job "
                "observation via Windows Job Object unavailable this "
                "session; force_kill()'s existing behavior is unaffected",
                ctypes.get_last_error(),
            )
            return None
        return int(handle)
    except OSError:
        logger.warning("CreateJobObjectW raised", exc_info=True)
        return None


def assign_to_job(job_handle: int, pid: int) -> bool:
    """Assign the process ``pid`` to the job at ``job_handle``. Opens its
    own handle to the target process (PROCESS_SET_QUOTA | PROCESS_TERMINATE
    -- the exact access AssignProcessToJobObject requires) and closes it
    again regardless of outcome. Returns whether the assignment succeeded;
    never raises.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        proc_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
        )
        if not proc_handle:
            logger.warning(
                "OpenProcess(pid=%d) failed (error %d) -- can't assign it "
                "to the observation Job Object",
                pid, ctypes.get_last_error(),
            )
            return False
        try:
            ok = bool(
                kernel32.AssignProcessToJobObject(
                    ctypes.c_void_p(job_handle), proc_handle
                )
            )
            if not ok:
                logger.warning(
                    "AssignProcessToJobObject(pid=%d) failed (error %d)",
                    pid, ctypes.get_last_error(),
                )
            return ok
        finally:
            kernel32.CloseHandle(proc_handle)
    except OSError:
        logger.warning("assign_to_job(pid=%d) raised", pid, exc_info=True)
        return False


def enumerate_job_pids(job_handle: int) -> list[int]:
    """QueryInformationJobObject(JobObjectBasicProcessIdList) -- every PID
    currently in the job, including ones the tracked process spawned and
    then detached (the whole reason this module exists). Returns an empty
    list on any failure; never raises.

    JOBOBJECT_BASIC_PROCESS_ID_LIST is variable-length (a DWORD count
    followed by that many ULONG_PTR pids) -- over-allocated at
    _MAX_TRACKED_PIDS rather than sized exactly, since the real count
    isn't known until after the call. A job with more members than that
    is not expected in practice (this is a voice-frontend-driven coding
    agent, not a build farm); silently truncating a job that large is an
    acceptable degrade, not a claimed guarantee.
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes

        class _PidList(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", ctypes.c_ulong),
                ("NumberOfProcessIdsInList", ctypes.c_ulong),
                ("ProcessIdList", ctypes.c_size_t * _MAX_TRACKED_PIDS),
            ]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.QueryInformationJobObject.restype = ctypes.c_int
        kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]

        info = _PidList()
        returned = ctypes.c_ulong(0)
        ok = kernel32.QueryInformationJobObject(
            ctypes.c_void_p(job_handle),
            _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
        )
        if not ok:
            logger.warning(
                "QueryInformationJobObject failed (error %d)",
                ctypes.get_last_error(),
            )
            return []
        count = min(info.NumberOfProcessIdsInList, _MAX_TRACKED_PIDS)
        return [int(info.ProcessIdList[i]) for i in range(count)]
    except OSError:
        logger.warning("enumerate_job_pids raised", exc_info=True)
        return []


def close_job(job_handle: int) -> None:
    """CloseHandle on our reference to the job. Does NOT terminate member
    processes -- we never set JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (see
    module docstring). Must not raise; a failure here just leaks a handle
    for the life of this process, not a correctness issue worth crashing
    over.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle(ctypes.c_void_p(job_handle))
    except OSError:
        logger.warning("close_job raised", exc_info=True)
