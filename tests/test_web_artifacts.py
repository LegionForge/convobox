from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)

from fastapi.testclient import TestClient  # noqa: E402

from convobox.web.app import create_app  # noqa: E402
from convobox.web.history import HistoryDB  # noqa: E402

# Required by app.py's require_csrf_header middleware on every mutating
# request (see its own docstring, GitHub issue #235 finding A3) -- set as
# this client's default headers so every test call carries it without
# repeating it at each call site.
_CSRF_HEADERS = {"X-ConvoBox-Client": "1"}


@pytest.fixture
def working_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


def test_get_artifact_with_no_working_dir_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/chart.png")
    assert response.status_code == 503


def test_get_artifact_serves_an_allowed_file_type(working_dir: Path) -> None:
    (working_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/chart.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\nfakepngbytes"


def test_get_artifact_serves_a_nested_path(working_dir: Path) -> None:
    nested = working_dir / "plots"
    nested.mkdir()
    (nested / "report.html").write_text("<html>hi</html>")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/plots/report.html")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"


def test_get_artifact_missing_file_returns_404(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/does-not-exist.png")
    assert response.status_code == 404


def test_get_artifact_rejects_a_disallowed_extension(working_dir: Path) -> None:
    (working_dir / "script.py").write_text("print('hi')")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/script.py")
    assert response.status_code == 415


def test_get_artifact_rejects_path_traversal_out_of_working_dir(
    working_dir: Path, tmp_path: Path
) -> None:
    # A real secret sitting just outside working_dir -- must never be
    # reachable through this route. A literal "../" in the URL gets
    # collapsed by the HTTP client itself before the request is even
    # sent (confirmed: it never reaches this route at all, so a naive
    # version of this test would pass for the wrong reason regardless of
    # whether the fence check below is correct) -- %2e%2e is the same
    # traversal attempt in a form that actually reaches the handler.
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"do-not-serve-me")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/%2e%2e/secret.png")
    assert response.status_code == 403
    assert response.content != b"do-not-serve-me"


def test_get_artifact_rejects_an_absolute_path_join_gotcha(
    working_dir: Path, tmp_path: Path
) -> None:
    # Python's own pathlib gotcha: Path("/base") / "/etc/passwd" silently
    # DISCARDS the base and returns "/etc/passwd" outright -- if a client
    # can get a leading "/" into artifact_path (a double slash in the URL
    # does this), a naive version of this route would join straight past
    # working_dir entirely. The fence must still catch it because it
    # checks the FINAL resolved candidate against base, not how it was
    # constructed.
    secret = tmp_path / "outside.txt"
    secret.write_text("do-not-serve-me")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get(f"/api/artifacts//{tmp_path.name}/outside.txt")
    assert response.status_code == 403
    assert response.content != b"do-not-serve-me"


def test_get_artifact_rejects_a_string_prefix_sibling_directory(
    working_dir: Path,
) -> None:
    # Regression guard for the naive "startswith" version of this check:
    # working_dir-evil/ shares a string prefix with working_dir/ but is
    # NOT nested inside it, and must not be servable through this route.
    sibling = working_dir.parent / (working_dir.name + "-evil")
    sibling.mkdir()
    (sibling / "leak.png").write_bytes(b"leaked")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get(f"/api/artifacts/%2e%2e/{sibling.name}/leak.png")
    assert response.status_code == 403
    assert response.content != b"leaked"


# --- Code files (2026-08-07, JP asked directly for syntax-highlighted
# rendering): served as text/plain regardless of language -- the frontend
# fetches the raw text and highlights it client-side, browsers have no
# native rendering for these the way they do images/HTML/PDF. ---


def test_get_artifact_serves_a_code_file_as_text_plain(working_dir: Path) -> None:
    (working_dir / "main.js").write_text("console.log('hi');")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts/main.js")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == b"console.log('hi');"


# --- "Open in editor" (.../editor-uri): resolves the same working_dir
# fence as the main route, returns a vscode://file/ URI for the browser
# to navigate to. ---


def test_get_artifact_editor_uri_with_no_working_dir_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app) as client:
        response = client.get("/api/artifacts/chart.png/editor-uri")
    assert response.status_code == 503


def test_get_artifact_editor_uri_returns_a_vscode_uri_for_the_resolved_path(
    working_dir: Path,
) -> None:
    (working_dir / "notes.md").write_text("# hi")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts/notes.md/editor-uri")
    assert response.status_code == 200
    data = response.json()
    assert data["uri"] == f"vscode://file/{(working_dir / 'notes.md').resolve().as_posix()}"


def test_get_artifact_editor_uri_missing_file_returns_404(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts/does-not-exist.js/editor-uri")
    assert response.status_code == 404


def test_get_artifact_editor_uri_rejects_path_traversal(
    working_dir: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.js"
    secret.write_text("do-not-serve-me")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts/%2e%2e/secret.js/editor-uri")
    assert response.status_code == 403


def test_editor_uri_route_does_not_get_swallowed_by_the_catch_all_artifact_route(
    working_dir: Path,
) -> None:
    # Regression guard for the route-registration-order gotcha:
    # {artifact_path:path} in get_artifact() greedily matches an entire
    # remaining path including slashes, so if that route were registered
    # BEFORE .../editor-uri, this exact request would resolve as
    # artifact_path="deep/nested/main.js/editor-uri" (a literal, real
    # file lookup that 404s) instead of reaching get_artifact_editor_uri
    # at all. A real nested path is used here specifically so a passing
    # test can't be an accident of a flat, one-segment path.
    nested = working_dir / "deep" / "nested"
    nested.mkdir(parents=True)
    (nested / "main.js").write_text("console.log('hi');")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts/deep/nested/main.js/editor-uri")
    assert response.status_code == 200
    assert response.json()["uri"] == f"vscode://file/{(nested / 'main.js').resolve().as_posix()}"
