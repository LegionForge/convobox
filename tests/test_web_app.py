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

from fastapi.testclient import TestClient

from convobox.config import DisplayConfig
from convobox.web.app import create_app, sse_lines
from convobox.web.bridge import WebEventForwarder
from convobox.web.history import HistoryDB, new_session_id
from convobox.web.stream import EventBroadcaster

# Required by app.py's require_csrf_header middleware on every mutating
# request (see its own docstring, GitHub issue #235 finding A3) -- set as
# this client's default headers so every test call carries it without
# repeating it at each call site.
_CSRF_HEADERS = {"X-ConvoBox-Client": "1"}


@pytest.fixture
def db(tmp_path: Path) -> HistoryDB:
    history = HistoryDB(tmp_path / "events.db")
    yield history
    history.close()


@pytest.fixture
def client(db: HistoryDB) -> TestClient:
    app = create_app(db=db)
    return TestClient(app, headers=_CSRF_HEADERS)


def test_health_check() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- require_csrf_header middleware (GitHub issue #235, finding A3): a
# body-less mutating route (/api/quit, /api/stop, /api/sessions/{id}/
# clear) is a CORS "simple request" without this -- no preflight, so
# CORSMiddleware's loopback-only origin check never gets a chance to
# reject it. These tests use a client WITHOUT the default header the
# `client` fixture normally carries, to prove the rejection is real.


def test_post_without_csrf_header_is_rejected() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app) as client:  # deliberately no default headers
        response = client.post("/api/stop")
    assert response.status_code == 403


def test_post_with_csrf_header_is_not_rejected_by_the_middleware() -> None:
    # "Not rejected by the middleware" specifically -- /api/stop with no
    # safeword_bridge configured still 503s, a different check entirely;
    # the point here is proving it's not a 403 anymore.
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/stop")
    assert response.status_code != 403


def test_get_requests_never_need_the_csrf_header() -> None:
    # Safe methods are read-only by definition -- the middleware must
    # only gate POST/PUT/PATCH/DELETE, never GET/HEAD/OPTIONS.
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app) as client:  # deliberately no default headers
        response = client.get("/health")
    assert response.status_code == 200


# --- require_web_ui_token (2026-09-01, GitHub security review): a real
# per-session bearer token gating /api/* -- the CSRF header above forces
# a preflight but checks a constant, public string, not a secret. Off
# entirely (web_ui_token=None, every test above) unless a real token is
# passed, matching how mcp_token already works for the MCP mount. ---

_WEB_UI_TOKEN = "test-token-do-not-use-in-real-life"


def test_api_request_without_token_is_rejected_when_configured() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), web_ui_token=_WEB_UI_TOKEN)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/config")
    assert response.status_code == 401


def test_api_request_with_wrong_bearer_token_is_rejected() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), web_ui_token=_WEB_UI_TOKEN)
    headers = {**_CSRF_HEADERS, "Authorization": "Bearer not-the-real-token"}
    with TestClient(app, headers=headers) as client:
        response = client.get("/api/config")
    assert response.status_code == 401


def test_api_request_with_correct_bearer_token_is_accepted() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), web_ui_token=_WEB_UI_TOKEN)
    headers = {**_CSRF_HEADERS, "Authorization": f"Bearer {_WEB_UI_TOKEN}"}
    with TestClient(app, headers=headers) as client:
        response = client.get("/api/config")
    assert response.status_code == 200


def test_api_request_with_correct_query_param_token_is_accepted() -> None:
    # The EventSource/img-src/iframe-src/download-link path -- a browser
    # can't attach a custom header to these, so the query param is the
    # only option for them.
    app = create_app(db=HistoryDB(Path(":memory:")), web_ui_token=_WEB_UI_TOKEN)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get(f"/api/config?token={_WEB_UI_TOKEN}")
    assert response.status_code == 200


def test_non_api_paths_are_not_gated_by_the_token() -> None:
    # The static shell (index.html/JS/CSS) isn't sensitive on its own --
    # only /api/* (where the real data/actions live) needs the token.
    app = create_app(db=HistoryDB(Path(":memory:")), web_ui_token=_WEB_UI_TOKEN)
    with TestClient(app) as client:  # deliberately no token anywhere
        response = client.get("/health")
    assert response.status_code == 200


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
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "user_color": "#2e7dfb",
        "assistant_color": "#f0f0f2",
        "user_name": "JP",
        "assistant_name": "Athena",
    }


# --- /api/config re-reads config_path fresh on every call (2026-08-07,
# fixed live after JP hit the restart-to-see-a-color-change friction
# firsthand) -- unlike the two tests above (no config_path at all, or a
# display= override with none), this proves the actual production
# behavior: a color/name change on disk shows up on the NEXT request,
# no backend restart, matching every other section's genuine need for
# one (scripts/settings_tui.py's SectionSpec.restart_required). ---


def test_get_display_config_re_reads_a_changed_file_without_restart(
    tmp_path: Path,
) -> None:
    import yaml

    config_path = tmp_path / "convobox.yaml"
    config_path.write_text(yaml.safe_dump({"display": {"assistant_color": "#00ff00"}}))
    app = create_app(db=HistoryDB(Path(":memory:")), config_path=config_path)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["assistant_color"] == "#00ff00"

        # Simulates a real Settings-modal save landing on disk while this
        # same app instance keeps running -- no restart, no new create_app().
        config_path.write_text(yaml.safe_dump({"display": {"assistant_color": "#448844"}}))
        second = client.get("/api/config").json()
        assert second["assistant_color"] == "#448844"


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


# --- limit/offset bounds (GitHub issue #235, finding A6): limit used to
# go straight into the SQL LIMIT clause unbounded. ---


def test_get_session_events_rejects_a_limit_above_the_bound(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist/events?limit=1001")
    assert response.status_code == 422


def test_get_session_events_rejects_a_zero_or_negative_limit(client: TestClient) -> None:
    assert client.get("/api/sessions/does-not-exist/events?limit=0").status_code == 422
    assert client.get("/api/sessions/does-not-exist/events?limit=-1").status_code == 422


def test_get_session_events_rejects_a_negative_offset(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist/events?offset=-1")
    assert response.status_code == 422


def test_get_session_events_accepts_the_upper_bound(
    client: TestClient, db: HistoryDB
) -> None:
    session_id = new_session_id()
    db.append_event(session_id, "transcript", user_transcript="ok")
    response = client.get(f"/api/sessions/{session_id}/events?limit=1000")
    assert response.status_code == 200


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


def test_export_session_streams_events_in_order_for_multiple_events(
    client: TestClient, db: HistoryDB
) -> None:
    # The export route was rewritten to stream (GitHub issue #235, finding
    # A6) -- confirms the generator still produces valid, complete,
    # correctly-ordered JSON across more than one event, not just the
    # single-event happy path above.
    session_id = new_session_id()
    for i in range(5):
        db.append_event(session_id, "transcript", user_transcript=f"turn {i}")

    response = client.get(f"/api/sessions/{session_id}/export")

    assert response.status_code == 200
    body = json.loads(response.content)
    assert [e["user_transcript"] for e in body["events"]] == [
        f"turn {i}" for i in range(5)
    ]


def test_export_session_for_an_empty_session_is_valid_json(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist/export")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {"session_id": "does-not-exist", "events": []}


def test_cors_allows_a_loopback_origin_on_any_port(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:54321"})
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:54321"


def test_cors_rejects_a_non_loopback_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_scoped_to_a_specific_port_rejects_other_loopback_ports() -> None:
    # 2026-09-01 (GitHub security review): "any loopback origin, any
    # port" trusts every other local process/page that happens to bind
    # a port, not just this app's own frontend. When the real bound port
    # is known (run_convobox.py's own real startup path always knows
    # it), only THAT exact port is trusted.
    app = create_app(db=HistoryDB(Path(":memory:")), port=5173)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        same_port = client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
        other_port = client.get("/health", headers={"Origin": "http://127.0.0.1:54321"})
    assert same_port.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in other_port.headers


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

    # session_ended (see close_all()'s own docstring) precedes the None
    # sentinel -- both queues have plenty of room here, so neither put
    # evicts the other.
    assert await q1.get() == {"type": "session_ended"}
    assert await q1.get() is None
    assert await q2.get() == {"type": "session_ended"}
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


# --- EventBroadcaster: bounded queues + oldest-drop (B5, 2026-08-08 review):
# an unbounded per-subscriber queue meant a stalled/backgrounded browser tab
# (still connected, no longer draining) grew its queue for the rest of the
# session. subscribe() now caps it; a full queue evicts its oldest item
# instead of blocking broadcast()'s delivery to every OTHER subscriber. ---


@pytest.mark.asyncio
async def test_subscribe_returns_a_bounded_queue() -> None:
    broadcaster = EventBroadcaster(max_queue_size=3)
    queue = broadcaster.subscribe()
    assert queue.maxsize == 3


@pytest.mark.asyncio
async def test_broadcast_to_a_full_queue_evicts_the_oldest_event() -> None:
    broadcaster = EventBroadcaster(max_queue_size=2)
    queue = broadcaster.subscribe()

    await broadcaster.broadcast({"type": "response", "content": "1"})
    await broadcaster.broadcast({"type": "response", "content": "2"})
    await broadcaster.broadcast({"type": "response", "content": "3"})

    # "1" was evicted to make room -- queue never exceeds its cap, and the
    # newest events (not the oldest) are what a live UI most needs to see.
    assert queue.qsize() == 2
    assert await queue.get() == {"type": "response", "content": "2"}
    assert await queue.get() == {"type": "response", "content": "3"}


@pytest.mark.asyncio
async def test_broadcast_still_delivers_normally_below_capacity() -> None:
    broadcaster = EventBroadcaster(max_queue_size=200)
    queue = broadcaster.subscribe()

    await broadcaster.broadcast({"type": "response", "content": "a"})
    await broadcaster.broadcast({"type": "response", "content": "b"})

    assert await queue.get() == {"type": "response", "content": "a"}
    assert await queue.get() == {"type": "response", "content": "b"}


@pytest.mark.asyncio
async def test_dropped_events_surface_as_a_marker_on_the_next_broadcast() -> None:
    broadcaster = EventBroadcaster(max_queue_size=1)
    queue = broadcaster.subscribe()

    await broadcaster.broadcast({"type": "response", "content": "1"})
    # Evicts "1" (dropped count -> 1), queue now holds "2".
    await broadcaster.broadcast({"type": "response", "content": "2"})
    # Delivers the pending marker first, which itself evicts "2" to fit
    # (maxsize=1 has no spare room) -- then the "3" payload evicts the
    # marker in turn to fit. maxsize=1 is the pathological extreme where
    # even the marker can't coexist with a real event in the same queue;
    # broadcast() still ends this call holding the newest real payload,
    # "3", with the evicted marker's own count rolled into whatever the
    # NEXT marker eventually reports (see test_dropped_marker_reports_the_
    # correct_count_with_room_to_spare below for the non-pathological case).
    await broadcaster.broadcast({"type": "response", "content": "3"})

    assert await queue.get() == {"type": "response", "content": "3"}


@pytest.mark.asyncio
async def test_dropped_marker_reports_the_correct_count_with_room_to_spare() -> None:
    broadcaster = EventBroadcaster(max_queue_size=2)
    queue = broadcaster.subscribe()

    await broadcaster.broadcast({"type": "response", "content": "1"})
    await broadcaster.broadcast({"type": "response", "content": "2"})
    # Queue is now full (["1", "2"]). This evicts "1" (dropped -> 1).
    await broadcaster.broadcast({"type": "response", "content": "3"})
    # Delivers the pending marker first (evicting "2" to fit it, since the
    # queue is still full: ["2", "3"]), then "4" (evicting "3").
    await broadcaster.broadcast({"type": "response", "content": "4"})

    assert await queue.get() == {"type": "dropped", "count": 1}
    assert await queue.get() == {"type": "response", "content": "4"}


@pytest.mark.asyncio
async def test_unsubscribe_clears_pending_drop_count() -> None:
    broadcaster = EventBroadcaster(max_queue_size=1)
    queue = broadcaster.subscribe()
    await broadcaster.broadcast({"type": "response", "content": "1"})
    await broadcaster.broadcast({"type": "response", "content": "2"})
    assert broadcaster._dropped.get(queue)

    broadcaster.unsubscribe(queue)

    assert queue not in broadcaster._dropped


@pytest.mark.asyncio
async def test_close_all_delivers_the_none_sentinel_even_when_the_queue_is_full() -> None:
    # The original bug this whole method exists for: close_all() must
    # actually reach every subscriber. A raw blocking `await queue.put(None)`
    # against a full, undrained queue would hang this call forever.
    broadcaster = EventBroadcaster(max_queue_size=1)
    queue = broadcaster.subscribe()
    await broadcaster.broadcast({"type": "response", "content": "still queued"})

    await broadcaster.close_all()

    assert await queue.get() is None


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
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 409


def test_resolve_approval_approve_calls_the_bridge_and_returns_approved() -> None:
    bridge = _FakeApprovalBridge(pending=True)
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 200
    assert response.json() == {"status": "approved", "explanation": None}
    assert bridge.decisions == [True]


def test_resolve_approval_deny_calls_the_bridge_and_returns_denied() -> None:
    bridge = _FakeApprovalBridge(pending=True)
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "deny"})
    assert response.status_code == 200
    assert response.json() == {"status": "denied", "explanation": None}
    assert bridge.decisions == [False]


def test_resolve_approval_explain_extends_and_returns_the_explanation() -> None:
    bridge = _FakeApprovalBridge(pending=True, explanation="rm -rf .incident-captures/*.wav")
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "explain"})
    assert response.status_code == 200
    assert response.json() == {"status": "pending", "explanation": "rm -rf .incident-captures/*.wav"}
    assert bridge.extended is True
    assert bridge.decisions == []  # explain never decides anything


def test_resolve_approval_when_bridge_reports_it_could_not_deliver_returns_409() -> None:
    bridge = _FakeApprovalBridge(pending=True)
    bridge.decide_result = False  # e.g. resolved by voice in the same instant
    app = create_app(db=HistoryDB(Path(":memory:")), approval_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 409


def test_resolve_approval_broadcasts_approval_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live UAT gap, 2026-08-17: a voice-approved request left the web
    # UI's own row clickable indefinitely -- this is what an approve/deny
    # from the web buttons themselves must also broadcast, so every OTHER
    # open tab (not just this one, which already updates its row locally)
    # catches up too.
    bridge = _FakeApprovalBridge(pending=True)
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)
    app = create_app(
        db=HistoryDB(Path(":memory:")), broadcaster=broadcaster,
        approval_bridge=bridge, web_forwarder=forwarder,
    )
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "approve"})
    assert response.status_code == 200
    assert queue.get_nowait() == {"type": "approval_resolved", "approved": True}


def test_resolve_approval_explain_does_not_broadcast_approval_resolved() -> None:
    # "explain" keeps the request open -- the web UI's own row must stay
    # active, so nothing should tell any tab a decision was made.
    bridge = _FakeApprovalBridge(pending=True, explanation="rm -rf .incident-captures/*.wav")
    broadcaster = EventBroadcaster()
    queue = broadcaster.subscribe()
    forwarder = WebEventForwarder(new_session_id(), history=None, broadcaster=broadcaster)
    app = create_app(
        db=HistoryDB(Path(":memory:")), broadcaster=broadcaster,
        approval_bridge=bridge, web_forwarder=forwarder,
    )
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/sessions/s/approval", json={"action": "explain"})
    assert response.status_code == 200
    assert queue.empty()


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
    with TestClient(app, headers=_CSRF_HEADERS) as client:
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
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/listening")
    assert response.json() == {"is_paused": True}


def test_set_listening_with_no_bridge_returns_503(client: TestClient) -> None:
    response = client.post("/api/listening", json={"action": "pause"})
    assert response.status_code == 503


def test_set_listening_with_a_not_ready_bridge_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=_FakeListeningBridge(ready=False))
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/listening", json={"action": "pause"})
    assert response.status_code == 503


def test_set_listening_pause_calls_the_bridge_and_returns_paused() -> None:
    bridge = _FakeListeningBridge(paused=False)
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/listening", json={"action": "pause"})
    assert response.status_code == 200
    assert response.json() == {"is_paused": True}
    assert bridge.pause_calls == 1


def test_set_listening_resume_calls_the_bridge_and_returns_listening() -> None:
    bridge = _FakeListeningBridge(paused=True)
    app = create_app(db=HistoryDB(Path(":memory:")), listening_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
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
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/stop")
    assert response.status_code == 503


def test_stop_calls_the_bridge_and_returns_stopped() -> None:
    bridge = _FakeSafewordBridge()
    app = create_app(db=HistoryDB(Path(":memory:")), safeword_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": True}
    assert bridge.trigger_calls == 1


class _FakeTextBridge:
    def __init__(self, ready: bool = True, accepts: bool = True) -> None:
        self.ready = ready
        self.accepts = accepts
        self.submitted: list[str] = []

    @property
    def is_ready(self) -> bool:
        return self.ready

    async def submit(self, text: str) -> bool:
        self.submitted.append(text)
        return self.accepts


def test_submit_text_with_no_bridge_returns_503(client: TestClient) -> None:
    response = client.post("/api/text", json={"text": "hello"})
    assert response.status_code == 503


def test_submit_text_with_a_not_ready_bridge_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), text_bridge=_FakeTextBridge(ready=False))
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/text", json={"text": "hello"})
    assert response.status_code == 503


def test_submit_text_forwards_to_the_bridge_and_returns_accepted() -> None:
    bridge = _FakeTextBridge()
    app = create_app(db=HistoryDB(Path(":memory:")), text_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/text", json={"text": "what should I work on next"})
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert bridge.submitted == ["what should I work on next"]


def test_submit_text_rejected_by_the_bridge_returns_400() -> None:
    # Matches WebTextInputBridge.submit()'s own contract: False means
    # "nothing sent" (e.g. blank after stripping), not a server error.
    bridge = _FakeTextBridge(accepts=False)
    app = create_app(db=HistoryDB(Path(":memory:")), text_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/text", json={"text": "   "})
    assert response.status_code == 400


def test_upload_notifies_a_ready_text_bridge(tmp_path: Path) -> None:
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    bridge = _FakeTextBridge()
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir, text_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("photo.png", b"fake-bytes")})
    assert response.status_code == 200
    assert bridge.submitted == ["[Uploaded file: photo.png]"]


def test_upload_with_a_not_ready_text_bridge_still_succeeds(tmp_path: Path) -> None:
    # Best-effort notification (app.py's _notify_backend_of_upload) -- no
    # live session must never make the upload itself fail.
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    bridge = _FakeTextBridge(ready=False)
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir, text_bridge=bridge)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("photo.png", b"fake-bytes")})
    assert response.status_code == 200
    assert bridge.submitted == []
