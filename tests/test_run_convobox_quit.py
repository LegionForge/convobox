from __future__ import annotations

import asyncio
import signal

import pytest

from scripts.run_convobox import (
    _cancel_main_on_web_server_exit,
    _cancel_main_task,
    _install_web_sigint_override,
    _print_clean_exit_note,
)


# --- _cancel_main_task: the web UI's Quit button, wired as run()'s
# quit_handler. Deliberately doesn't touch OS signals at all (see its own
# docstring) -- both this and the route calling it run on the same event
# loop, so a direct asyncio cancellation is enough. ---


@pytest.mark.asyncio
async def test_cancel_main_task_cancels_a_running_task() -> None:
    async def sleeper() -> None:
        await asyncio.sleep(10)

    task = asyncio.ensure_future(sleeper())
    await asyncio.sleep(0)  # let it actually start
    _cancel_main_task(task)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_main_task_is_a_no_op_on_an_already_done_task() -> None:
    async def noop() -> None:
        return None

    task = asyncio.ensure_future(noop())
    await task
    _cancel_main_task(task)  # must not raise even though task.done() is True
    assert task.done()
    assert not task.cancelled()


# --- _cancel_main_on_web_server_exit: covers the OTHER way the same bug
# shows up -- a real terminal Ctrl+C while --web is active, which uvicorn.
# Server.serve()'s own signal handler intercepts before it ever reaches the
# main task (confirmed live, 2026-07-28: neither a real Ctrl+C nor the web
# UI's Quit button did anything while a --web session was running). This
# watcher notices the web server task ending on its own and cancels the
# main task in response. ---


@pytest.mark.asyncio
async def test_watchdog_cancels_main_task_when_web_server_task_ends() -> None:
    async def web_server_stub() -> None:
        await asyncio.sleep(0.05)  # simulates uvicorn winding down after should_exit=True

    async def main_body() -> None:
        await asyncio.sleep(10)

    web_server_task = asyncio.ensure_future(web_server_stub())
    main_task = asyncio.ensure_future(main_body())
    await asyncio.ensure_future(
        _cancel_main_on_web_server_exit(web_server_task, main_task)
    )
    assert main_task.cancelled()


@pytest.mark.asyncio
async def test_watchdog_is_a_no_op_if_main_task_already_finished() -> None:
    async def web_server_stub() -> None:
        await asyncio.sleep(0.05)

    async def main_body() -> None:
        return None

    web_server_task = asyncio.ensure_future(web_server_stub())
    main_task = asyncio.ensure_future(main_body())
    await main_task  # finishes on its own before the web server task does
    await asyncio.ensure_future(
        _cancel_main_on_web_server_exit(web_server_task, main_task)
    )
    assert not main_task.cancelled()


# --- _install_web_sigint_override: the real fix for terminal Ctrl+C while
# --web is active. The watchdog above assumes uvicorn.Server.serve()'s
# should_exit reliably propagates to web_server_task ending -- confirmed
# live, 2026-07-29, that this alone was NOT enough (a real terminal Ctrl+C
# during a --tui --web session still did nothing). This installs ConvoBox's
# own handler directly, so it owns the signal instead of depending on
# uvicorn's internal shutdown timing. signal.signal always dispatches to
# whichever handler was registered MOST RECENTLY -- these tests exercise
# that real dispatch via signal.raise_signal, not a mock. ---


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """Every test in this file mutates process-wide SIGINT/SIGTERM state --
    restore whatever was registered before, so this file can't leak a
    handler into unrelated tests or pytest's own Ctrl+C handling."""
    saved = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None))
        if sig is not None
    }
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.mark.asyncio
async def test_install_web_sigint_override_wins_over_a_prior_handler() -> None:
    def someone_elses_handler(signum: object, frame: object) -> None:
        raise AssertionError("uvicorn's handler fired instead of ConvoBox's override")

    signal.signal(signal.SIGINT, someone_elses_handler)

    async def main_body() -> None:
        await asyncio.sleep(10)

    main_task = asyncio.ensure_future(main_body())
    await asyncio.sleep(0)
    _install_web_sigint_override(main_task)

    assert signal.getsignal(signal.SIGINT) is not someone_elses_handler

    # Exercises the REAL signal-dispatch machinery (not a direct function
    # call) -- this is what a real terminal Ctrl+C ultimately triggers.
    signal.raise_signal(signal.SIGINT)
    with pytest.raises(asyncio.CancelledError):
        await main_task
    assert main_task.cancelled()


@pytest.mark.asyncio
async def test_install_web_sigint_override_also_handles_sigterm() -> None:
    async def main_body() -> None:
        await asyncio.sleep(10)

    main_task = asyncio.ensure_future(main_body())
    await asyncio.sleep(0)
    _install_web_sigint_override(main_task)

    signal.raise_signal(signal.SIGTERM)
    with pytest.raises(asyncio.CancelledError):
        await main_task
    assert main_task.cancelled()


# --- _print_clean_exit_note: console-only reassurance (not log.info --
# --tui redirects that to a file, exactly where this wouldn't help) that
# a quit/Ctrl+C genuinely succeeded, printed only when --web was active
# (the only case uvicorn's own lifespan-task noise can appear). ---


def test_print_clean_exit_note_prints_when_web_was_active(capsys) -> None:
    _print_clean_exit_note(web_active=True)
    captured = capsys.readouterr()
    assert "ConvoBox exited cleanly" in captured.err
    assert captured.out == ""


def test_print_clean_exit_note_is_silent_when_web_was_not_active(capsys) -> None:
    _print_clean_exit_note(web_active=False)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
