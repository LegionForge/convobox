from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)

from fastapi.testclient import TestClient  # noqa: E402

from convobox.config import DisplayConfig  # noqa: E402
from convobox.web.app import create_app, sse_lines  # noqa: E402
from convobox.web.history import HistoryDB, new_session_id  # noqa: E402
from convobox.web.stream import EventBroadcaster  # noqa: E402


@pytest.fixture
def db(tmp_path: Path) -> HistoryDB:
    history = HistoryDB(tmp_path / "events.db")
    yield history
    history.close()


@pytest.fixture
def client(db: HistoryDB) -> TestClient:
    app = create_app(db=db)
    return TestClient(app)


def test_health_check() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_display_config_defaults_to_no_overrides(client: TestClient) -> None:
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "user_color": None,
        "assistant_color": None,
        "user_name": None,
        "assistant_name": None,
    }


def test_get_display_config_returns_configured_colors_and_names() -> None:
    app = create_app(
        db=HistoryDB(Path(":memory:")),
        display=DisplayConfig(
            user_color="#2e7dfb",
            assistant_color="#f0f0f2",
            user_name="JP",
            assistant_name="Athena",
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "user_color": "#2e7dfb",
        "assistant_color": "#f0f0f2",
        "user_name": "JP",
        "assistant_name": "Athena",
    }


def test_list_sessions_empty(client: TestClient) -> None:
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_list_sessions_returns_recorded_sessions(client: TestClient, db: HistoryDB) -> None:
    session_id = new_session_id()
    db.append_event(session_id, "transcript", user_transcript="hi")

    response = client.get("/api/sessions")

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id


def test_get_session_events_returns_recorded_events(client: TestClient, db: HistoryDB) -> None:
    session_id = new_session_id()
    db.append_event(session_id, "transcript", user_transcript="what time is it")

    response = client.get(f"/api/sessions/{session_id}/events")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["user_transcript"] == "what time is it"


def test_get_session_events_for_unknown_session_is_empty(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist/events")
    assert response.status_code == 200
    assert response.json() == {"events": []}


def test_get_session_events_honors_limit_and_offset(
    client: TestClient, db: HistoryDB
) -> None:
    session_id = new_session_id()
    for i in range(5):
        db.append_event(session_id, "transcript", user_transcript=f"turn {i}")

    response = client.get(f"/api/sessions/{session_id}/events?limit=2&offset=2")

    events = response.json()["events"]
    assert [e["user_transcript"] for e in events] == ["turn 2", "turn 3"]


def test_clear_session_deletes_its_events(client: TestClient, db: HistoryDB) -> None:
    session_id = new_session_id()
    db.append_event(session_id, "transcript", user_transcript="clear me")

    response = client.post(f"/api/sessions/{session_id}/clear")

    assert response.status_code == 200
    assert response.json() == {"status": "cleared"}
    assert db.get_session_events(session_id) == []


def test_export_session_returns_a_downloadable_json_attachment(
    client: TestClient, db: HistoryDB
) -> None:
    session_id = new_session_id()
    db.append_event(session_id, "transcript", user_transcript="export me")

    response = client.get(f"/api/sessions/{session_id}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert f'filename="{session_id}.json"' in response.headers["content-disposition"]
    body = json.loads(response.content)
    assert body["session_id"] == session_id
    assert body["events"][0]["user_transcript"] == "export me"


def test_cors_allows_a_loopback_origin_on_any_port(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:54321"})
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:54321"


def test_cors_rejects_a_non_loopback_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


# --- Static frontend: mounted at "/" LAST, after every /api/* route, so it
# must never shadow them -- these tests are exactly the "does route
# registration order actually protect the API" check. ---


def test_root_serves_the_frontend_index_html(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "ConvoBox" in response.text


def test_static_mount_does_not_shadow_api_routes(client: TestClient) -> None:
    # The exact regression this ordering exists to prevent: if the static
    # mount were registered before the API routes (or matched greedily),
    # this would 404 or return index.html instead of the real JSON route.
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


# --- sse_lines: the SSE wire-format generator itself, tested as a plain
# async generator over a queue -- NOT through the /api/events/stream route.
# httpx's ASGITransport fully drains the ASGI call before returning anything
# (confirmed live while writing this: even the response headers never
# arrived in a test), so a body that only ends on client disconnect hangs
# forever under it. The route itself is exercised end-to-end below instead,
# over a real uvicorn server + real socket. ---


@pytest.mark.asyncio
async def test_sse_lines_yields_a_data_line_for_a_queued_event() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    await queue.put({"type": "response", "content": "hello"})

    gen = sse_lines(queue)
    line = await gen.__anext__()

    assert line.startswith("data: ")
    assert json.loads(line[len("data: ") :])["content"] == "hello"


@pytest.mark.asyncio
async def test_sse_lines_yields_a_heartbeat_comment_on_idle_timeout() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    gen = sse_lines(queue, heartbeat_interval=0.01)
    line = await gen.__anext__()

    assert line == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_sse_lines_ends_cleanly_on_a_queued_none() -> None:
    # EventBroadcaster.close_all()'s sentinel -- lets an open SSE
    # connection end itself with a plain `return` on shutdown instead of
    # needing uvicorn to force-cancel it.
    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
    await queue.put(None)

    gen = sse_lines(queue)

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_sse_lines_still_yields_queued_events_before_a_later_none() -> None:
    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
    await queue.put({"type": "response", "content": "before close"})
    await queue.put(None)

    gen = sse_lines(queue)
    line = await gen.__anext__()
    assert json.loads(line[len("data: ") :])["content"] == "before close"

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


# --- EventBroadcaster.close_all(): the shutdown-quieting half of the same
# fix -- puts the sse_lines() None sentinel into every subscriber's queue,
# nothing more. Doesn't touch subscribe/unsubscribe/broadcast. ---


@pytest.mark.asyncio
async def test_close_all_puts_none_in_every_subscriber_queue() -> None:
    broadcaster = EventBroadcaster()
    q1 = broadcaster.subscribe()
    q2 = broadcaster.subscribe()

    await broadcaster.close_all()

    assert await q1.get() is None
    assert await q2.get() is None


@pytest.mark.asyncio
async def test_close_all_is_a_no_op_with_no_subscribers() -> None:
    broadcaster = EventBroadcaster()
    await broadcaster.close_all()  # must not raise


@pytest.mark.asyncio
async def test_broadcast_still_delivers_real_events_unaffected_by_close_all() -> None:
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()

    await broadcaster.broadcast({"type": "response", "content": "still works"})

    event = await queue.get()
    assert event == {"type": "response", "content": "still works"}


# --- /api/events/stream: a real uvicorn server + a real socket, since
# ASGITransport can't drive an infinite streaming response (see above). ---


@pytest.fixture
async def running_server(db: HistoryDB):
    import uvicorn

    broadcaster = EventBroadcaster()
    app = create_app(db=db, broadcaster=broadcaster)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", broadcaster
    finally:
        server.should_exit = True
        await serve_task


@pytest.mark.asyncio
async def test_stream_events_broadcasts_a_live_event_over_a_real_socket(
    running_server: tuple[str, EventBroadcaster],
) -> None:
    base_url, broadcaster = running_server

    async def broadcast_once_a_subscriber_appears() -> None:
        for _ in range(500):
            if broadcaster._subscribers:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("no subscriber appeared for the SSE stream")
        await broadcaster.broadcast({"type": "response", "content": "live event"})

    async with httpx.AsyncClient(base_url=base_url) as client, client.stream(
        "GET", "/api/events/stream"
    ) as response:
        broadcast_task = asyncio.ensure_future(broadcast_once_a_subscriber_appears())
        try:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    assert payload["content"] == "live event"
                    break
            else:
                pytest.fail("stream closed before a data event arrived")
        finally:
            await broadcast_task


# --- POST /api/sessions/{session_id}/approval: the web UI's approve/deny/
# explain buttons, a non-voice equivalent of the same
# ApprovalPromptGate/Orchestrator.resolve_pending_approval path a spoken
# phrase answers. A fake bridge stands in for the real
# convobox.web.bridge.WebApprovalBridge (whose own decision-forwarding
# logic is covered by tests/test_web_bridge.py) -- this only tests that
# the route wires actions/status codes to the bridge correctly. ---


class _FakeApprovalBridge:
    def __init__(self, pending: bool = True, explanation: str | None = None) -> None:
        self.pending = pending
        self.explanation = explanation
        self.decisions: list[bool] = []
        self.decide_result = True
        self.extended = False

    @property
    def is_pending(self) -> bool:
        return self.pending

    async def decide(self, approved: bool) -> bool:
        self.decisions.append(approved)
        if self.decide_result:
            self.pending = False
        return self.decide_result

    def extend(self) -> str | None:
        self.extended = True
        return self.explanation


def test_resolve_approval_with_no_bridge_returns_409(client: TestClient) -> None:
    response = client.post("/api/sessions/some-session/approval", json={"action": "approve"})
    assert response.status_code == 409


def test_resolve_approval_with_nothing_pending_returns_409() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=_FakeApprovalBridge(pending=False))
    with TestClient(app) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 409


def test_resolve_approval_approve_calls_the_bridge_and_returns_approved() -> None:
    bridge = _FakeApprovalBridge(pending=True)
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 200
    assert response.json() == {"status": "approved", "explanation": None}
    assert bridge.decisions == [True]


def test_resolve_approval_deny_calls_the_bridge_and_returns_denied() -> None:
    bridge = _FakeApprovalBridge(pending=True)
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "deny"})
    assert response.status_code == 200
    assert response.json() == {"status": "denied", "explanation": None}
    assert bridge.decisions == [False]


def test_resolve_approval_explain_extends_and_returns_the_explanation() -> None:
    bridge = _FakeApprovalBridge(pending=True, explanation="rm -rf .incident-captures/*.wav")
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "explain"})
    assert response.status_code == 200
    assert response.json() == {"status": "pending", "explanation": "rm -rf .incident-captures/*.wav"}
    assert bridge.extended is True
    assert bridge.decisions == []  # explain never decides anything


def test_resolve_approval_when_bridge_reports_it_could_not_deliver_returns_409() -> None:
    bridge = _FakeApprovalBridge(pending=True)
    bridge.decide_result = False  # e.g. resolved by voice in the same instant
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 409


def test_resolve_approval_rejects_an_unknown_action(client: TestClient) -> None:
    response = client.post("/api/sessions/s/approval", json={"action": "yolo"})
    assert response.status_code == 422


# --- POST /api/quit: the web UI's Quit button. quit_handler is a plain
# callable (run_convobox.py passes a closure around its own
# _cancel_main_task -- see tests/test_run_convobox_quit.py) -- unlike
# approval_bridge there's no per-request state to inspect, so a bare
# MagicMock is enough to prove the route calls it. ---


def test_quit_with_no_handler_returns_503(client: TestClient) -> None:
    response = client.post("/api/quit")
    assert response.status_code == 503


def test_quit_calls_the_handler_and_returns_quitting() -> None:
    handler = MagicMock()
    app = create_app(db=HistoryDB(Path(":memory:")), quit_handler=handler)
    with TestClient(app) as client:
        response = client.post("/api/quit")
    assert response.status_code == 200
    assert response.json() == {"status": "quitting"}
    handler.assert_called_once_with()


# --- GET/POST /api/listening: the web UI's Stop/Resume listening button.
# listening_bridge's own pause/resume side-effect logic is covered by
# tests/test_web_bridge.py -- this only tests that the route wires
# actions/status codes to the bridge correctly, same shape as the approval
# route tests above. ---


class _FakeListeningBridge:
    def __init__(self, ready: bool = True, paused: bool = False) -> None:
        self.ready = ready
        self.paused = paused
        self.pause_calls = 0
        self.resume_calls = 0

    @property
    def is_ready(self) -> bool:
        return self.ready

    @property
    def is_paused(self) -> bool:
        return self.paused

    async def pause(self) -> bool:
        self.pause_calls += 1
        if not self.paused:
            self.paused = True
            return True
        return False

    def resume(self) -> bool:
        self.resume_calls += 1
        if self.paused:
            self.paused = False
            return True
        return False


def test_get_listening_with_no_bridge_reports_not_paused(client: TestClient) -> None:
    response = client.get("/api/listening")
    assert response.status_code == 200
    assert response.json() == {"is_paused": False}


def test_get_listening_reflects_the_bridge_state() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=_FakeListeningBridge(paused=True))
    with TestClient(app) as client:
        response = client.get("/api/listening")
    assert response.json() == {"is_paused": True}


def test_set_listening_with_no_bridge_returns_503(client: TestClient) -> None:
    response = client.post("/api/listening", json={"action": "pause"})
    assert response.status_code == 503


def test_set_listening_with_a_not_ready_bridge_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=_FakeListeningBridge(ready=False))
    with TestClient(app) as client:
        response = client.post("/api/listening", json={"action": "pause"})
    assert response.status_code == 503


def test_set_listening_pause_calls_the_bridge_and_returns_paused() -> None:
    bridge = _FakeListeningBridge(paused=False)
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/listening", json={"action": "pause"})
    assert response.status_code == 200
    assert response.json() == {"is_paused": True}
    assert bridge.pause_calls == 1


def test_set_listening_resume_calls_the_bridge_and_returns_listening() -> None:
    bridge = _FakeListeningBridge(paused=True)
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/listening", json={"action": "resume"})
    assert response.status_code == 200
    assert response.json() == {"is_paused": False}
    assert bridge.resume_calls == 1


def test_set_listening_rejects_an_unknown_action(client: TestClient) -> None:
    response = client.post("/api/listening", json={"action": "yolo"})
    assert response.status_code == 422


# --- POST /api/stop: the web UI's Stop button, distinct from
# /api/listening's pause action -- does exactly what saying the safeword
# does (abort the current turn, keep listening normally afterward), not
# pause-until-resume-word. safeword_bridge's own trigger() logic is covered
# by tests/test_web_bridge.py -- this only tests that the route wires the
# action/status codes to the bridge correctly, same shape as the listening
# route tests above. ---


class _FakeSafewordBridge:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.trigger_calls = 0

    @property
    def is_ready(self) -> bool:
        return self.ready

    async def trigger(self) -> bool:
        self.trigger_calls += 1
        return True


def test_stop_with_no_bridge_returns_503(client: TestClient) -> None:
    response = client.post("/api/stop")
    assert response.status_code == 503


def test_stop_with_a_not_ready_bridge_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), safeword_bridge=_FakeSafewordBridge(ready=False))
    with TestClient(app) as client:
        response = client.post("/api/stop")
    assert response.status_code == 503


def test_stop_calls_the_bridge_and_returns_stopped() -> None:
    bridge = _FakeSafewordBridge()
    app = create_app(db=HistoryDB(Path(":memory:")), safeword_bridge=bridge)
    with TestClient(app) as client:
        response = client.post("/api/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": True}
    assert bridge.trigger_calls == 1
