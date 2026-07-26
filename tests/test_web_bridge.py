from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.safeword.detector import SafewordDetector
from convobox.web.bridge import WebEventForwarder
from convobox.web.history import HistoryDB, new_session_id
from convobox.web.stream import EventBroadcaster


@pytest.fixture
def db(tmp_path: Path) -> HistoryDB:
    history = HistoryDB(tmp_path / "events.db")
    yield history
    history.close()


def test_both_none_is_a_harmless_noop(db: HistoryDB) -> None:
    # Matches web.enabled=False (the default) making zero difference.
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder(BackendEvent(type=BackendEventType.TEXT, content="hi"))  # must not raise


def test_forwards_to_history_when_given(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="it works"))

    events = db.get_session_events(session_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "response"
    assert events[0]["backend_response"] == "it works"


def test_tool_call_event_type_is_not_folded_into_response(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.TOOL_CALL, tool="Bash", tool_input="ls"))

    stored = db.get_session_events(session_id)[0]
    assert stored["event_type"] == "tool_call"
    assert stored["tool_name"] == "Bash"


def test_approval_request_gets_its_own_event_type(db: HistoryDB) -> None:
    session_id = new_session_id()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=None)

    forwarder(BackendEvent(type=BackendEventType.APPROVAL_REQUEST, tool="Bash"))

    stored = db.get_session_events(session_id)[0]
    assert stored["event_type"] == "approval_request"


def test_history_none_skips_persistence_but_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder(BackendEvent(type=BackendEventType.DONE))  # must not raise


@pytest.mark.asyncio
async def test_broadcasts_to_a_subscriber_when_given() -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="live"))
    # The forwarder schedules the broadcast (asyncio.ensure_future) rather
    # than awaiting it, matching its sync on_event-hook contract -- give
    # the scheduled task a turn to actually run before checking the queue.
    await asyncio.sleep(0)

    event = queue.get_nowait()
    assert event.content == "live"


def test_broadcaster_none_skips_broadcast_but_does_not_raise() -> None:
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=None)
    forwarder(BackendEvent(type=BackendEventType.TEXT, content="hi"))  # must not raise


@pytest.mark.asyncio
async def test_forwards_to_both_history_and_broadcaster_together(db: HistoryDB) -> None:
    session_id = new_session_id()
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=broadcaster)

    forwarder(BackendEvent(type=BackendEventType.TEXT, content="both"))
    await asyncio.sleep(0)

    assert db.get_session_events(session_id)[0]["backend_response"] == "both"
    assert queue.get_nowait().content == "both"


# --- Integration: a real Orchestrator wired with WebEventForwarder as its
# on_event hook (the actual run_convobox.py wiring shape), not just the
# forwarder in isolation -- proves the two really compose, since
# Orchestrator's on_event contract is what WebEventForwarder.__call__ has
# to match. ---


class _MinimalBackendAdapter(BackendAdapter):
    """Just enough of BackendAdapter to drive Orchestrator._consume_events()
    with a scripted event list -- see FakeBackendAdapter in
    test_orchestrator.py for the fuller version used elsewhere."""

    def __init__(self, events_to_yield: list[BackendEvent]) -> None:
        self._events_to_yield = events_to_yield

    async def send_text(self, text: str) -> None: ...
    async def send_interject(self, text: str) -> None: ...
    async def send_hard_stop(self) -> None: ...
    def is_busy(self) -> bool:
        return False

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        for event in self._events_to_yield:
            await asyncio.sleep(0)
            yield event


@pytest.mark.asyncio
async def test_orchestrator_wired_with_web_forwarder_persists_and_broadcasts(
    db: HistoryDB,
) -> None:
    session_id = new_session_id()
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(session_id, history=db, broadcaster=broadcaster)
    adapter = _MinimalBackendAdapter(
        [BackendEvent(type=BackendEventType.TEXT, content="wired end to end")]
    )
    orch = Orchestrator(
        adapter=adapter, safeword=SafewordDetector(["stop stop stop"]), on_event=forwarder
    )

    await orch._consume_events()
    await asyncio.sleep(0)  # let the forwarder's scheduled broadcast task run

    stored = db.get_session_events(session_id)
    assert len(stored) == 1
    assert stored[0]["backend_response"] == "wired end to end"
    assert queue.get_nowait().content == "wired end to end"
