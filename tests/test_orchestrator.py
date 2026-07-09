from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from convobox.adapters.base import BackendAdapter, BackendEvent
from convobox.orchestrator.orchestrator import Orchestrator, strip_code_for_speech
from convobox.safeword.detector import SafewordDetector


class FakeBackendAdapter(BackendAdapter):
    def __init__(self, busy: bool = False) -> None:
        self._busy = busy
        self.sent_text: list[str] = []
        self.sent_interject: list[str] = []
        self.hard_stops = 0

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_interject(self, text: str) -> None:
        self.sent_interject.append(text)

    async def send_hard_stop(self) -> None:
        self.hard_stops += 1

    def is_busy(self) -> bool:
        return self._busy

    async def events(self) -> AsyncIterator[BackendEvent]:  # pragma: no cover
        return
        yield


def make_orchestrator(busy: bool) -> tuple[Orchestrator, FakeBackendAdapter]:
    adapter = FakeBackendAdapter(busy=busy)
    safeword = SafewordDetector(["stop stop stop"])
    return Orchestrator(adapter, safeword), adapter


@pytest.mark.asyncio
async def test_idle_normal_transcript_sends_text() -> None:
    orch, adapter = make_orchestrator(busy=False)
    await orch.handle_transcript("add a login button")
    assert adapter.sent_text == ["add a login button"]
    assert adapter.sent_interject == []
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_busy_normal_transcript_sends_interject_not_text() -> None:
    orch, adapter = make_orchestrator(busy=True)
    await orch.handle_transcript("oh also add tests")
    assert adapter.sent_interject == ["oh also add tests"]
    assert adapter.sent_text == []
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_hard_stop_when_idle() -> None:
    orch, adapter = make_orchestrator(busy=False)
    await orch.handle_transcript("stop stop stop")
    assert adapter.hard_stops == 1
    assert adapter.sent_text == []
    assert adapter.sent_interject == []


@pytest.mark.asyncio
async def test_hard_stop_wins_over_busy_interject() -> None:
    orch, adapter = make_orchestrator(busy=True)
    await orch.handle_transcript("please stop stop stop now")
    assert adapter.hard_stops == 1
    assert adapter.sent_interject == []
    assert adapter.sent_text == []


@pytest.mark.asyncio
async def test_handle_transcript_starts_event_loop_automatically() -> None:
    # Regression test: is_busy() only stays fresh while _consume_events() is
    # draining adapter.events(). If a caller forgets to call
    # start_event_loop() separately, is_busy() goes stale after the first
    # send. handle_transcript must not depend on the caller remembering that.
    orch, _ = make_orchestrator(busy=False)
    assert orch._events_task is None

    await orch.handle_transcript("add a login button")

    assert orch._events_task is not None
    await orch.stop_event_loop()


def test_strip_code_for_speech_removes_fenced_block() -> None:
    text = "Here is the fix:\n```python\nprint('hi')\n```\nThat should work."
    result = strip_code_for_speech(text)
    assert "print" not in result
    assert "Here is the fix:" in result
    assert "That should work." in result


def test_strip_code_for_speech_removes_inline_span() -> None:
    result = strip_code_for_speech("Call the `run()` function to start.")
    assert "run()" not in result
    assert "Call the" in result
    assert "function to start." in result
