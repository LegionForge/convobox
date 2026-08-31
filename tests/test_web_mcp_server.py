"""Covers add_mcp_routes (src/convobox/web/mcp_server.py) at the HTTP
level: auth enforcement and the no-op-unless-configured behavior. Also
covers the show_document/get_shown_artifact tool FUNCTIONS' own logic
directly via MCPServer.call_tool(), bypassing the wire protocol.

Does NOT drive a full MCP protocol handshake through a real MCP client --
that path (the streamable-HTTP transport, the lifespan-wiring fix, the
async-tool-function fix) was live-verified end-to-end against a real
`claude` CLI subprocess instead, a stronger guarantee than a mocked
client here could give, and is the one thing this module's own docstring
says needs live verification, not just spec-reading. These tests cover
the things that ARE meaningfully unit-testable and cheap to keep green:
the route only exists when it's supposed to, it can't be reached without
the bearer token, and the two tools' own path-resolution/media-type/
forwarder logic behaves correctly given a valid call -- independent of
whatever the wire protocol on top of them does.
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

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from convobox.adapters.base import BackendEventType
from convobox.web.app import create_app
from convobox.web.history import HistoryDB
from convobox.web.mcp_server import add_mcp_routes

_CSRF_HEADERS = {"X-ConvoBox-Client": "1"}


class _FakeArtifactForwarder:
    def __init__(self) -> None:
        self.events: list[object] = []

    def __call__(self, event: object) -> None:
        self.events.append(event)


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


# --- show_document / get_shown_artifact tool logic, via call_tool() directly ---
#
# Bypasses the wire protocol entirely (see module docstring) -- these
# exercise the tools' own path-resolution/media-type/forwarder logic,
# not the MCP transport.


async def test_show_document_tool_shows_a_valid_file_and_forwards_artifact_event(
    working_dir: Path,
) -> None:
    (working_dir / "notes.md").write_text("# hi")
    forwarder = _FakeArtifactForwarder()
    server = add_mcp_routes(FastAPI(), working_dir, "secret", forwarder)
    assert server is not None

    result = await server.call_tool("show_document", {"path": "notes.md"})

    assert result.is_error is False
    assert "notes.md" in result.content[0].text
    assert len(forwarder.events) == 1
    assert forwarder.events[0].type == BackendEventType.ARTIFACT
    assert forwarder.events[0].artifact_path == "notes.md"


async def test_show_document_tool_works_with_no_forwarder_configured(working_dir: Path) -> None:
    (working_dir / "notes.md").write_text("# hi")
    server = add_mcp_routes(FastAPI(), working_dir, "secret", None)
    assert server is not None

    result = await server.call_tool("show_document", {"path": "notes.md"})

    assert result.is_error is False


async def test_show_document_tool_rejects_unsupported_extension(working_dir: Path) -> None:
    (working_dir / "data.bin").write_bytes(b"\x00\x01")
    server = add_mcp_routes(FastAPI(), working_dir, "secret", None)
    assert server is not None

    with pytest.raises(ToolError, match="not a servable artifact type"):
        await server.call_tool("show_document", {"path": "data.bin"})


async def test_show_document_tool_blocks_path_traversal(working_dir: Path) -> None:
    server = add_mcp_routes(FastAPI(), working_dir, "secret", None)
    assert server is not None

    with pytest.raises(ToolError, match="escapes the configured working_dir"):
        await server.call_tool("show_document", {"path": "../outside.txt"})


async def test_show_document_tool_rejects_missing_file(working_dir: Path) -> None:
    server = add_mcp_routes(FastAPI(), working_dir, "secret", None)
    assert server is not None

    with pytest.raises(ToolError, match="no such artifact"):
        await server.call_tool("show_document", {"path": "nope.md"})


async def test_get_shown_artifact_tool_reports_none_when_nothing_shown(working_dir: Path) -> None:
    app = FastAPI()
    server = add_mcp_routes(app, working_dir, "secret", None)
    assert server is not None

    result = await server.call_tool("get_shown_artifact", {})

    assert result.is_error is False
    assert "No artifact is currently shown" in result.content[0].text


async def test_get_shown_artifact_tool_reports_the_browser_reported_path(working_dir: Path) -> None:
    app = FastAPI()
    server = add_mcp_routes(app, working_dir, "secret", None)
    assert server is not None
    app.state.active_artifact_path = "notes.md"

    result = await server.call_tool("get_shown_artifact", {})

    assert result.is_error is False
    assert "notes.md" in result.content[0].text
