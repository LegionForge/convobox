"""Covers add_mcp_routes (src/convobox/web/mcp_server.py) at the HTTP
level: auth enforcement and the no-op-unless-configured behavior.

Does NOT drive a full MCP protocol handshake through a real MCP client --
that path (the streamable-HTTP transport, the lifespan-wiring fix, the
async-tool-function fix) was live-verified end-to-end against a real
`claude` CLI subprocess instead, a stronger guarantee than a mocked
client here could give, and is the one thing this module's own docstring
says needs live verification, not just spec-reading. These tests cover
the two things that ARE meaningfully unit-testable and cheap to keep
green: the route only exists when it's supposed to, and it can't be
reached without the bearer token.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)
pytest.importorskip(
    "mcp",
    reason="the 'mcp' SDK ships with the web extra (pyproject.toml) but is its "
    "own optional import -- skip rather than fail if it's somehow absent",
)

from fastapi.testclient import TestClient

from convobox.web.app import create_app
from convobox.web.history import HistoryDB

_CSRF_HEADERS = {"X-ConvoBox-Client": "1"}


@pytest.fixture
def working_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


def test_mcp_route_absent_without_working_dir() -> None:
    # token set, working_dir not -- add_mcp_routes' own docstring says
    # this must mount nothing (an always-erroring tool wastes a real
    # agent turn discovering that, worse than not existing). With no /mcp
    # mount at all, the request falls through to the static-file catch-
    # all mounted at "/" (index.html's own `html=True` StaticFiles),
    # which 405s a POST rather than 404ing -- it still matches "/mcp/" as
    # a candidate path, it just doesn't support this method.
    app = create_app(db=HistoryDB(Path(":memory:")), mcp_token="t")
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/mcp/", json={})
    assert response.status_code == 405


def test_mcp_route_absent_without_token(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/mcp/", json={})
    assert response.status_code == 405


def test_mcp_route_rejects_missing_bearer_token(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir, mcp_token="secret")
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert response.status_code == 401


def test_mcp_route_rejects_wrong_bearer_token(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir, mcp_token="secret")
    with TestClient(app, headers={**_CSRF_HEADERS, "Authorization": "Bearer wrong"}) as client:
        response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert response.status_code == 401


def test_mcp_route_is_exempt_from_the_csrf_header_check(working_dir: Path) -> None:
    # The claude/codex CLI's MCP client has no way to send ConvoBox's own
    # x-convobox-client CSRF header (app.py's require_csrf_header) -- a
    # 403 from THAT middleware, rather than the expected 401 from this
    # route's own bearer-token check, would mean the exemption regressed.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir, mcp_token="secret")
    with TestClient(app) as client:  # deliberately no CSRF header at all
        response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert response.status_code == 401


def test_mcp_route_forwards_to_the_wrapped_app_with_a_correct_bearer_token(
    working_dir: Path,
) -> None:
    # Every other test here supplies a missing/wrong token and checks for
    # 401 -- none of them ever exercises _require_bearer_token()'s actual
    # happy path (`await app(scope, receive, send)`), so a regression that
    # accidentally dropped or no-op'd that call (e.g. returning without
    # forwarding) would pass every existing test in this file. A CORRECT
    # token must not be rejected by OUR auth check -- whatever happens
    # next is the wrapped mcp SDK app's own business (here, its DNS-
    # rebinding transport-security check rejects TestClient's synthetic
    # Host header with 421, a completely different failure from -- and
    # proof of getting past -- our 401).
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir, mcp_token="secret")
    with TestClient(app, headers={**_CSRF_HEADERS, "Authorization": "Bearer secret"}) as client:
        response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert response.status_code != 401
