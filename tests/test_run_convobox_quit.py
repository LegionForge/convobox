from __future__ import annotations

import asyncio

import pytest

from scripts.run_convobox import _cancel_main_on_web_server_exit, _cancel_main_task


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
