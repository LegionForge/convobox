from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.web.history import HistoryDB, new_session_id


def _db(tmp_path: Path) -> HistoryDB:
    return HistoryDB(tmp_path / "history" / "events.db")


def test_new_session_id_is_unique_across_calls() -> None:
    assert new_session_id() != new_session_id()


def test_creates_the_db_file_and_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "events.db"
    assert not db_path.parent.exists()
    db = HistoryDB(db_path)
    db.close()
    assert db_path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file-mode semantics only")
def test_new_db_file_is_owner_readable_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        mode = stat.S_IMODE(db.path.stat().st_mode)
        assert mode == 0o600
    finally:
        db.close()


def test_append_and_get_session_events_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        db.append_event(session_id, "transcript", user_transcript="what time is it")
        db.append_event(
            session_id,
            "response",
            backend_event=BackendEvent(type=BackendEventType.TEXT, content="it's 3pm"),
        )

        events = db.get_session_events(session_id)

        assert len(events) == 2
        assert events[0]["event_type"] == "transcript"
        assert events[0]["user_transcript"] == "what time is it"
        assert events[1]["event_type"] == "response"
        assert events[1]["backend_response"] == "it's 3pm"
    finally:
        db.close()


def test_events_are_returned_oldest_first(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        for i in range(3):
            db.append_event(session_id, "transcript", user_transcript=f"turn {i}")

        events = db.get_session_events(session_id)

        assert [e["user_transcript"] for e in events] == ["turn 0", "turn 1", "turn 2"]
    finally:
        db.close()


def test_get_session_events_respects_limit_and_offset(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        for i in range(5):
            db.append_event(session_id, "transcript", user_transcript=f"turn {i}")

        page = db.get_session_events(session_id, limit=2, offset=2)

        assert [e["user_transcript"] for e in page] == ["turn 2", "turn 3"]
    finally:
        db.close()


def test_get_session_events_only_returns_the_requested_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_a, session_b = new_session_id(), new_session_id()
        db.append_event(session_a, "transcript", user_transcript="from a")
        db.append_event(session_b, "transcript", user_transcript="from b")

        events = db.get_session_events(session_a)

        assert len(events) == 1
        assert events[0]["user_transcript"] == "from a"
    finally:
        db.close()


def test_backend_event_is_stored_as_full_json_for_replay(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        event = BackendEvent(
            type=BackendEventType.TOOL_CALL, tool="Bash", tool_input='{"command": "ls"}'
        )
        db.append_event(session_id, "tool_call", backend_event=event)

        stored = db.get_session_events(session_id)[0]

        assert stored["tool_name"] == "Bash"
        assert stored["tool_input"] == '{"command": "ls"}'
        # TOOL_CALL isn't a "response" type -- backend_response stays unset,
        # the full event is only in backend_event_json.
        assert stored["backend_response"] is None
        parsed = json.loads(stored["backend_event_json"])
        assert parsed == {
            "type": "tool_call",
            "content": None,
            "tool": "Bash",
            "tool_input": '{"command": "ls"}',
            "tool_output": None,
            "artifact_path": None,
        }
    finally:
        db.close()


def test_tool_result_content_falls_back_to_tool_output(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        event = BackendEvent(type=BackendEventType.TOOL_RESULT, tool_output="file1\nfile2")
        db.append_event(session_id, "tool_result", backend_event=event)

        stored = db.get_session_events(session_id)[0]

        assert stored["backend_response"] == "file1\nfile2"
    finally:
        db.close()


def test_approval_explanation_and_decision_are_stored(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        db.append_event(
            session_id,
            "approval",
            approval_explanation="about to delete a file",
            user_decision="deny",
        )

        stored = db.get_session_events(session_id)[0]

        assert stored["approval_explanation"] == "about to delete a file"
        assert stored["user_decision"] == "deny"
    finally:
        db.close()


def test_get_active_session_returns_the_most_recently_appended(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        older, newer = new_session_id(), new_session_id()
        db.append_event(older, "transcript", user_transcript="first")
        db.append_event(newer, "transcript", user_transcript="second")

        assert db.get_active_session() == newer
    finally:
        db.close()


def test_get_active_session_returns_none_when_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert db.get_active_session() is None
    finally:
        db.close()


def test_list_sessions_returns_every_session_most_recent_first(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        older, newer = new_session_id(), new_session_id()
        db.append_event(older, "transcript", user_transcript="first")
        db.append_event(newer, "transcript", user_transcript="second")

        sessions = db.list_sessions()

        assert [s[0] for s in sessions] == [newer, older]
    finally:
        db.close()


def test_export_session_json_round_trips_through_json(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        session_id = new_session_id()
        db.append_event(session_id, "transcript", user_transcript="export me")

        exported = json.loads(db.export_session_json(session_id))

        assert exported["session_id"] == session_id
        assert len(exported["events"]) == 1
        assert exported["events"][0]["user_transcript"] == "export me"
    finally:
        db.close()


def test_clear_session_deletes_only_that_sessions_events(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        keep, clear = new_session_id(), new_session_id()
        db.append_event(keep, "transcript", user_transcript="keep me")
        db.append_event(clear, "transcript", user_transcript="clear me")

        db.clear_session(clear)

        assert db.get_session_events(clear) == []
        assert len(db.get_session_events(keep)) == 1
    finally:
        db.close()


def test_reopening_an_existing_db_preserves_its_events(tmp_path: Path) -> None:
    db_path = tmp_path / "events.db"
    session_id = new_session_id()
    first = HistoryDB(db_path)
    first.append_event(session_id, "transcript", user_transcript="persisted")
    first.close()

    second = HistoryDB(db_path)
    try:
        events = second.get_session_events(session_id)
        assert len(events) == 1
        assert events[0]["user_transcript"] == "persisted"
    finally:
        second.close()
