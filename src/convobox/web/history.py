"""SQLite-backed event history for the web UI (docs/WEB-UI-ARCHITECTURE.md).

Deliberately dumb: HistoryDB knows nothing about the orchestrator or which
backend produced an event, only BackendEvent's shape. Callers (the future
run_convobox.py integration) decide what an `event_type` string means --
this class just persists and queries rows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from convobox.adapters.base import BackendEvent, BackendEventType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    user_transcript TEXT,
    backend_response TEXT,
    tool_name TEXT,
    tool_input TEXT,
    approval_explanation TEXT,
    user_decision TEXT,
    backend_event_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_timestamp ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type);
"""

# BackendEvent types whose .content/.tool_output is the thing a transcript
# view would show as "what the assistant said" -- TOOL_CALL and
# APPROVAL_REQUEST describe pending actions, not a response to show inline.
_RESPONSE_EVENT_TYPES = (
    BackendEventType.TEXT,
    BackendEventType.TOOL_RESULT,
    BackendEventType.ERROR,
)


def new_session_id() -> str:
    """A sortable, collision-safe session id: a local-time-ish timestamp plus
    a short random suffix, so two sessions started within the same second
    (e.g. a fast restart) never collide the way a bare timestamp could."""
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


class HistoryDB:
    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not db_path.exists()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()
        if is_new:
            # Owner-readable only -- this file can contain transcripts, tool
            # calls, and approval decisions (docs/WEB-UI-ARCHITECTURE.md's
            # "Data at Rest" section). Best-effort: some filesystems (e.g.
            # certain network mounts) don't support chmod, and a failure
            # here must never block the DB from working.
            try:
                os.chmod(db_path, 0o600)
            except OSError:
                pass

    def append_event(
        self,
        session_id: str,
        event_type: str,
        *,
        user_transcript: str | None = None,
        backend_event: BackendEvent | None = None,
        approval_explanation: str | None = None,
        user_decision: str | None = None,
    ) -> int:
        """Persist one history row and return its id.

        backend_event, when given, is stored twice: its full JSON (for
        replay/export -- see export_session_json) and, for TEXT/TOOL_RESULT/
        ERROR events, its content pulled into the indexed backend_response
        column so the web UI can render a transcript without parsing JSON
        per row.
        """
        tool_name = backend_event.tool if backend_event else None
        tool_input = backend_event.tool_input if backend_event else None
        backend_response = None
        if backend_event is not None and backend_event.type in _RESPONSE_EVENT_TYPES:
            backend_response = backend_event.content or backend_event.tool_output
        backend_event_json = (
            json.dumps(event_to_dict(backend_event)) if backend_event is not None else None
        )
        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO events (session_id, timestamp, event_type, user_transcript, "
            "backend_response, tool_name, tool_input, approval_explanation, "
            "user_decision, backend_event_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                now,
                event_type,
                user_transcript,
                backend_response,
                tool_name,
                tool_input,
                approval_explanation,
                user_decision,
                backend_event_json,
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None  # nosec B101 -- AUTOINCREMENT guarantees this
        return cursor.lastrowid

    def get_session_events(
        self, session_id: str, limit: int = 1000, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Events for one session, OLDEST first (transcript reading order) --
        `limit`/`offset` page forward through history, not backward from the
        most recent event."""
        rows = self._conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC "
            "LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_active_session(self) -> str | None:
        row = self._conn.execute(
            "SELECT session_id FROM events ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row["session_id"] if row else None

    def list_sessions(self) -> list[tuple[str, str]]:
        """[(session_id, last_activity_iso), ...], most recently active first.

        Ordered by MAX(timestamp) (the sub-second REAL clock), not
        MAX(created_at) (second-resolution text) -- two sessions last
        touched within the same second would otherwise tie and sort in an
        unspecified order.
        """
        rows = self._conn.execute(
            "SELECT session_id, MAX(created_at) AS last_activity, "
            "MAX(timestamp) AS last_ts FROM events "
            "GROUP BY session_id ORDER BY last_ts DESC"
        ).fetchall()
        return [(row["session_id"], row["last_activity"]) for row in rows]

    def export_session_json(self, session_id: str) -> str:
        events = self.get_session_events(session_id, limit=1_000_000, offset=0)
        return json.dumps({"session_id": session_id, "events": events}, indent=2)

    def clear_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def event_to_dict(event: BackendEvent) -> dict[str, Any]:
    """JSON-able shape shared by the history row's backend_event_json column
    and the live SSE stream (see convobox.web.app) -- one place defining
    what a BackendEvent looks like over the wire."""
    return {
        "type": event.type.value,
        "content": event.content,
        "tool": event.tool,
        "tool_input": event.tool_input,
        "tool_output": event.tool_output,
        "artifact_path": event.artifact_path,
    }
