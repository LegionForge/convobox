"""Covers readline_with_stall_diagnostic's `busy` parameter
(src/convobox/adapters/base.py) -- added 2026-08-15 after a live capture
showed the stall warning firing routinely during ordinary IDLE gaps
between turns, indistinguishable in the log from a real stuck-mid-turn
hang unless busy state is shown alongside it. The diagnostic mechanism
itself (fires on a real delay, recovers once data arrives) is already
covered end-to-end via the adapters that call it (test_opencode_adapter.py's
own SSE-stall test); this file is scoped to the one thing that's new:
what `busy` does to the log line.
"""

from __future__ import annotations

import asyncio

import pytest

from convobox.adapters.base import readline_with_stall_diagnostic


class _FakeProcess:
    returncode: int | None = None


async def _make_pending_reader() -> asyncio.StreamReader:
    """A StreamReader with no data and no EOF fed yet -- readline() blocks
    on it until the test feeds something, the same "genuinely pending"
    shape a real subprocess pipe has mid-stall."""
    return asyncio.StreamReader()


@pytest.mark.asyncio
async def test_busy_true_is_reported_in_the_stall_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = await _make_pending_reader()
    proc = _FakeProcess()

    async def resolve_after_delay() -> None:
        await asyncio.sleep(0.7)  # past _READLINE_STALL_FIRST_WARNING_S (0.5s)
        stream.feed_data(b"line\n")

    with caplog.at_level("WARNING"):
        resolver = asyncio.ensure_future(resolve_after_delay())
        result = await asyncio.wait_for(
            readline_with_stall_diagnostic(stream, proc, "test", busy=lambda: True),
            timeout=5,
        )
        await resolver
    assert result == b"line\n"

    stall_warnings = [r for r in caplog.records if "still pending" in r.message]
    assert stall_warnings, "expected at least one stall warning during the 0.7s delay"
    assert all("busy=True" in r.message for r in stall_warnings)


@pytest.mark.asyncio
async def test_busy_false_is_reported_in_the_stall_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The exact case this parameter exists for: a long gap with busy=False
    # is ordinary idle time between turns, not a hang -- the log line must
    # say so rather than reading identically to the busy=True case.
    stream = await _make_pending_reader()
    proc = _FakeProcess()

    async def resolve_after_delay() -> None:
        await asyncio.sleep(0.7)
        stream.feed_data(b"line\n")

    with caplog.at_level("WARNING"):
        resolver = asyncio.ensure_future(resolve_after_delay())
        await asyncio.wait_for(
            readline_with_stall_diagnostic(stream, proc, "test", busy=lambda: False),
            timeout=5,
        )
        await resolver

    stall_warnings = [r for r in caplog.records if "still pending" in r.message]
    assert stall_warnings
    assert all("busy=False" in r.message for r in stall_warnings)


@pytest.mark.asyncio
async def test_busy_omitted_reports_unknown_not_a_crash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Every pre-existing call site before 2026-08-15 (and any future one
    # that doesn't have a meaningful busy signal to offer) must keep
    # working exactly as before -- busy is optional, not a new
    # requirement on every caller.
    stream = await _make_pending_reader()
    proc = _FakeProcess()

    async def resolve_after_delay() -> None:
        await asyncio.sleep(0.7)
        stream.feed_data(b"line\n")

    with caplog.at_level("WARNING"):
        resolver = asyncio.ensure_future(resolve_after_delay())
        await asyncio.wait_for(
            readline_with_stall_diagnostic(stream, proc, "test"),
            timeout=5,
        )
        await resolver

    stall_warnings = [r for r in caplog.records if "still pending" in r.message]
    assert stall_warnings
    assert all("busy=unknown" in r.message for r in stall_warnings)
