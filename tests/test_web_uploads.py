from __future__ import annotations

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
from convobox.web.uploads import _MAX_UPLOAD_BYTES

# app.py's CSRF middleware (require_csrf_header) 403s any mutating request
# missing this header -- added after this test file was first written, same
# fix already applied to every other TestClient(app) in test_web_app.py.
_CSRF_HEADERS = {"X-ConvoBox-Client": "1"}


@pytest.fixture
def working_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspace"
    d.mkdir()
    return d


def test_upload_with_no_working_dir_returns_503() -> None:
    app = create_app(db=HistoryDB(Path(":memory:")))
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("photo.png", b"fake-bytes")})
    assert response.status_code == 503


def test_upload_writes_the_file_into_working_dir(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post(
            "/api/upload", files={"file": ("report.pdf", b"%PDF-fake-content")}
        )
    assert response.status_code == 200
    assert response.json() == {"filename": "report.pdf"}
    assert (working_dir / "report.pdf").read_bytes() == b"%PDF-fake-content"


def test_upload_creates_working_dir_if_it_does_not_exist_yet(tmp_path: Path) -> None:
    # backend.working_dir can be configured but not yet exist (a fresh
    # project directory) -- the upload is what creates it, same as a real
    # coding agent's own first write into it would.
    not_yet_created = tmp_path / "new-workspace"
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=not_yet_created)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("note.txt", b"hi")})
    assert response.status_code == 200
    assert (not_yet_created / "note.txt").read_text() == "hi"


def test_upload_rejects_no_filename(working_dir: Path) -> None:
    # FastAPI's own multipart validation rejects an empty filename before
    # this ever reaches the route handler (422, not the 400 our own
    # `if not file.filename` guard would raise) -- still confirms the
    # security property (nothing gets written), just at a layer above ours.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("", b"stuff")})
    assert response.status_code == 422
    assert list(working_dir.iterdir()) == []


def test_upload_rejects_a_blocked_extension(working_dir: Path) -> None:
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post(
            "/api/upload", files={"file": ("payload.exe", b"MZfake")}
        )
    assert response.status_code == 415
    assert list(working_dir.iterdir()) == []


def test_upload_neutralizes_path_traversal_in_the_filename(working_dir: Path) -> None:
    # A raw filename containing directory components must never let the
    # upload escape working_dir -- Path(name).name strips everything but
    # the bare filename, so this can only ever land INSIDE working_dir,
    # never at the traversed location.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post(
            "/api/upload", files={"file": ("../../evil.txt", b"gotcha")}
        )
    assert response.status_code == 200
    assert response.json() == {"filename": "evil.txt"}
    assert (working_dir / "evil.txt").read_bytes() == b"gotcha"
    assert not (working_dir.parent.parent / "evil.txt").exists()


def test_upload_never_overwrites_an_existing_file(working_dir: Path) -> None:
    (working_dir / "photo.png").write_bytes(b"original")
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("photo.png", b"new-upload")})
    assert response.status_code == 200
    assert response.json() == {"filename": "photo (2).png"}
    assert (working_dir / "photo.png").read_bytes() == b"original"
    assert (working_dir / "photo (2).png").read_bytes() == b"new-upload"


def test_upload_rejects_a_file_over_the_size_limit(working_dir: Path) -> None:
    # A real oversized body -- httpx computes an accurate Content-Length for
    # it, so this is now caught by reject_oversized_uploads (the early
    # middleware below) before Starlette ever parses/spools the multipart
    # body, not by upload_file()'s own too-late per-chunk check.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    oversized = b"x" * (_MAX_UPLOAD_BYTES + 1)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("huge.bin", oversized)})
    assert response.status_code == 413
    # The partial write must not be left behind on disk.
    assert list(working_dir.iterdir()) == []


# --- reject_oversized_uploads (2026-09-02, cross-session security review,
# "SOL"): upload_file()'s own per-chunk check runs too late to be the real
# bound -- by the time it sees any bytes, Starlette has already spooled the
# WHOLE multipart body to a temp file while resolving the `UploadFile`
# parameter. This middleware rejects on Content-Length alone, before
# Starlette touches the body at all. ---


def test_upload_rejects_solely_on_a_claimed_content_length_before_parsing(
    working_dir: Path,
) -> None:
    # The actual body sent here is tiny and not even valid multipart data --
    # if this 413s anyway, the rejection can only have come from the
    # Content-Length header check, proven independent of what (if anything)
    # Starlette's multipart parser would have made of the body itself.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post(
            "/api/upload",
            content=b"not-actually-this-big",
            headers={
                "content-length": str(_MAX_UPLOAD_BYTES + 1),
                "content-type": "multipart/form-data; boundary=x",
            },
        )
    assert response.status_code == 413
    assert list(working_dir.iterdir()) == []


def test_upload_with_a_valid_content_length_is_not_rejected_by_the_middleware(
    working_dir: Path,
) -> None:
    # A truthful, under-the-limit Content-Length must sail through the new
    # early check exactly as before -- proves it isn't just rejecting every
    # upload outright.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("note.txt", b"hi")})
    assert response.status_code == 200


def test_upload_with_no_text_bridge_still_succeeds(working_dir: Path) -> None:
    # create_app's own on_uploaded wiring is best-effort (see app.py's
    # _notify_backend_of_upload) -- no live session must never make the
    # upload itself fail. The "backend actually gets told" half of this
    # is covered in test_web_app.py, alongside the other bridge fakes.
    app = create_app(db=HistoryDB(Path(":memory:")), working_dir=working_dir)
    with TestClient(app, headers=_CSRF_HEADERS) as client:
        response = client.post("/api/upload", files={"file": ("note.txt", b"hi")})
    assert response.status_code == 200
