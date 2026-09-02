from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)

from fastapi.testclient import TestClient

from convobox.web.app import create_app
from convobox.web.history import HistoryDB

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


@functools.lru_cache(maxsize=1)
def _symlinks_supported() -> bool:
    """Probe (not assume) whether this process can create symlinks.

    Windows raises OSError [WinError 1314] on Path.symlink_to() unless the
    process is elevated or Developer Mode is on (SeCreateSymbolicLinkPrivilege)
    -- POSIX has no equivalent restriction. Probing directly, rather than
    branching on sys.platform, means this stays correct if that ever changes
    (elevated/Developer Mode Windows, a future POSIX restriction, etc.)
    without needing another edit here.
    """
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "target"
        target.write_bytes(b"")
        link = Path(d) / "link"
        try:
            link.symlink_to(target)
        except OSError:
            return False
    return True


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


# --- CSP sandbox on script-capable artifact types (2026-09-01, GitHub
# security review): the in-pane <iframe sandbox="allow-scripts"> path was
# already safe, but the "Open in new tab" link opens the same URL as a
# plain top-level navigation with no sandbox at all -- full same-origin
# standing for an attacker-influenced HTML/SVG artifact's own script.
# The response-level CSP sandbox header achieves the same isolation a
# top-level navigation can't get from an iframe attribute. ---


def test_get_artifact_sets_csp_sandbox_for_html(working_dir: Path) -> None:
    (working_dir / "page.html").write_text("<html><body>hi</body></html>")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/page.html")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "sandbox allow-scripts"


def test_get_artifact_sets_csp_sandbox_for_svg(working_dir: Path) -> None:
    (working_dir / "chart.svg").write_text("<svg></svg>")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/chart.svg")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "sandbox allow-scripts"


def test_get_artifact_does_not_set_csp_sandbox_for_non_script_types(working_dir: Path) -> None:
    # Image/PDF/text artifacts can't execute script -- the header would be
    # inert but shouldn't be there confusing a future reader into thinking
    # it means something for these types.
    (working_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/chart.png")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


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
    (working_dir / "notes.exe").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.get("/api/artifacts/notes.exe")
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


# Live UAT gap-check, 2026-08-17 (same class as the .py fix above): these
# 19 extensions were entirely absent from ARTIFACT_MEDIA_TYPES. One
# parametrized test rather than 19 near-duplicate functions -- the
# mechanism (a dict lookup) is identical for every extension; what's
# worth guarding is that each specific one is actually IN the dict, not
# 19 copies of the same assertion shape.
@pytest.mark.parametrize(
    "ext",
    [
        "css", "sh", "bash", "ps1", "toml", "sql", "go", "rs", "rb", "php",
        "kt", "kts", "swift", "scala", "lua", "dart", "vue", "graphql", "gql",
    ],
)
def test_get_artifact_serves_newly_added_extensions(working_dir: Path, ext: str) -> None:
    (working_dir / f"sample.{ext}").write_text("content")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get(f"/api/artifacts/sample.{ext}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


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


# --- GET /api/artifacts (2026-08-12): the working-directory file browser
# -- filtered through the SAME ARTIFACT_MEDIA_TYPES allowlist the
# single-file route enforces, plus dotfile/symlink exclusion. See
# docs/ARTIFACT-PANE-SCOPE.md's "Working-Directory File Browser" section
# for why a plain directory scan was rejected and what this replaces it
# with. ---


def test_list_artifacts_with_no_working_dir_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.status_code == 503


def test_list_artifacts_returns_only_allowlisted_extensions(working_dir: Path) -> None:
    (working_dir / "chart.png").write_bytes(b"fake")
    (working_dir / "report.html").write_text("<html></html>")
    (working_dir / "script.py").write_text("print('hi')")  # in the allowlist
    (working_dir / "notes.exe").write_bytes(b"fake")  # not in the allowlist
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.status_code == 200
    data = response.json()
    assert sorted(data["files"]) == ["chart.png", "report.html", "script.py"]
    assert data["truncated"] is False


def test_list_artifacts_includes_nested_files_with_relative_posix_paths(
    working_dir: Path,
) -> None:
    nested = working_dir / "plots" / "sub"
    nested.mkdir(parents=True)
    (nested / "a.png").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.status_code == 200
    assert response.json()["files"] == ["plots/sub/a.png"]


def test_list_artifacts_excludes_dotfiles(working_dir: Path) -> None:
    (working_dir / ".env").write_text("SECRET=1")
    (working_dir / "chart.png").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.json()["files"] == ["chart.png"]


def test_list_artifacts_excludes_dot_directories_entirely(working_dir: Path) -> None:
    # A file with an allowlisted extension sitting INSIDE a dot-directory
    # (e.g. .git/) must never appear, regardless of its own extension --
    # the directory itself signals "not meant to be browsed".
    dot_dir = working_dir / ".git"
    dot_dir.mkdir()
    (dot_dir / "config.txt").write_text("not a real git config, just allowlisted-looking")
    (working_dir / "chart.png").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.json()["files"] == ["chart.png"]


@pytest.mark.skipif(
    not _symlinks_supported(),
    reason="symlink creation requires elevation or Developer Mode on this platform",
)
def test_list_artifacts_excludes_symlinked_files(working_dir: Path, tmp_path: Path) -> None:
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"do-not-list-me")
    link = working_dir / "link.png"
    link.symlink_to(secret)
    (working_dir / "real.png").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.json()["files"] == ["real.png"]


@pytest.mark.skipif(
    not _symlinks_supported(),
    reason="symlink creation requires elevation or Developer Mode on this platform",
)
def test_list_artifacts_excludes_symlinked_directories(
    working_dir: Path, tmp_path: Path
) -> None:
    secret_dir = tmp_path / "secret-dir"
    secret_dir.mkdir()
    (secret_dir / "leak.png").write_bytes(b"do-not-list-me")
    link_dir = working_dir / "linked"
    link_dir.symlink_to(secret_dir)
    (working_dir / "real.png").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.json()["files"] == ["real.png"]


def test_list_artifacts_returns_empty_list_for_an_empty_working_dir(
    working_dir: Path,
) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    assert response.status_code == 200
    assert response.json() == {"files": [], "truncated": False}


def test_list_artifacts_truncates_and_flags_it(working_dir: Path) -> None:
    from convobox.web.artifacts import _MAX_BROWSE_RESULTS

    for i in range(_MAX_BROWSE_RESULTS + 5):
        (working_dir / f"file{i:04d}.png").write_bytes(b"fake")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:
        response = client.get("/api/artifacts")
    data = response.json()
    assert len(data["files"]) == _MAX_BROWSE_RESULTS
    assert data["truncated"] is True


# --- POST /api/artifacts/active (GitHub issue #280): the frontend's own
# report of what the pane is really showing -- get_shown_artifact
# (web/mcp_server.py) reads app.state.active_artifact_path, this route
# is the only thing that ever writes it. ---


def test_active_artifact_starts_unset() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    assert app.state.active_artifact_path is None


def test_set_active_artifact_records_the_relative_path(working_dir: Path) -> None:
    (working_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/artifacts/active", json={"path": "chart.png"})
    assert response.status_code == 200
    assert app.state.active_artifact_path == "chart.png"


def test_set_active_artifact_with_null_clears_it(working_dir: Path) -> None:
    (working_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        client.post("/api/artifacts/active", json={"path": "chart.png"})
        response = client.post("/api/artifacts/active", json={"path": None})
    assert response.status_code == 200
    assert app.state.active_artifact_path is None


def test_set_active_artifact_with_a_traversal_path_clears_rather_than_errors(
    working_dir: Path,
) -> None:
    # A stale/racy or malformed report only feeds an informational MCP
    # tool, never a security boundary (GET /api/artifacts/{path} already
    # enforces that fence on the content this report describes) -- see
    # add_artifact_routes()'s own comment on why this degrades to
    # "nothing confidently known" instead of a 4xx the frontend has no
    # useful way to act on.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/artifacts/active", json={"path": "../../etc/passwd"})
    assert response.status_code == 200
    assert app.state.active_artifact_path is None


def test_set_active_artifact_requires_the_csrf_header(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app) as client:  # deliberately no CSRF header
        response = client.post("/api/artifacts/active", json={"path": None})
    assert response.status_code == 403


# --- sequence guard (live UAT finding, 2026-08-18): two of these POSTs
# fired close together (a fast tab switch) have no guarantee of being
# APPLIED in send order -- a monotonic sequence number lets the server
# ignore anything not newer than what it's already applied, regardless
# of arrival order. ---


def test_set_active_artifact_ignores_an_older_sequence(working_dir: Path) -> None:
    (working_dir / "chart.png").write_bytes(b"fake")
    (working_dir / "notes.md").write_text("# hi")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        # The newer report (sequence=2) arrives and is applied FIRST --
        # simulating the out-of-order completion this guard exists for.
        client.post("/api/artifacts/active", json={"path": "notes.md", "sequence": 2})
        response = client.post(
            "/api/artifacts/active", json={"path": "chart.png", "sequence": 1}
        )
    assert response.status_code == 200
    assert app.state.active_artifact_path == "notes.md"


def test_set_active_artifact_ignores_an_equal_sequence(working_dir: Path) -> None:
    # Strictly greater, not >=: a duplicate/retried report for the same
    # render must not re-apply (harmless here, but keeps the semantics
    # exact -- "newer," not "not older").
    (working_dir / "chart.png").write_bytes(b"fake")
    (working_dir / "notes.md").write_text("# hi")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        client.post("/api/artifacts/active", json={"path": "notes.md", "sequence": 1})
        response = client.post(
            "/api/artifacts/active", json={"path": "chart.png", "sequence": 1}
        )
    assert response.status_code == 200
    assert app.state.active_artifact_path == "notes.md"


def test_set_active_artifact_applies_a_newer_sequence(working_dir: Path) -> None:
    (working_dir / "chart.png").write_bytes(b"fake")
    (working_dir / "notes.md").write_text("# hi")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        client.post("/api/artifacts/active", json={"path": "chart.png", "sequence": 1})
        response = client.post(
            "/api/artifacts/active", json={"path": "notes.md", "sequence": 2}
        )
    assert response.status_code == 200
    assert app.state.active_artifact_path == "notes.md"


def test_set_active_artifact_without_a_sequence_always_applies(working_dir: Path) -> None:
    # Backward-compatible fallback for any caller that doesn't send one
    # (this repo's own frontend always does) -- must not require it.
    (working_dir / "chart.png").write_bytes(b"fake")
    (working_dir / "notes.md").write_text("# hi")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        client.post("/api/artifacts/active", json={"path": "notes.md", "sequence": 5})
        response = client.post("/api/artifacts/active", json={"path": "chart.png"})
    assert response.status_code == 200
    assert app.state.active_artifact_path == "chart.png"
