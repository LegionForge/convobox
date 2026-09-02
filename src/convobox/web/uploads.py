"""Accepts drag-and-drop file uploads from the web UI, fenced to
backend.working_dir -- the SAME boundary that already bounds artifacts.py's
reads and everything the sandboxed coding agent itself can touch (config.py's
own security note on BackendConfig.working_dir: leave it unset and a voice
session could modify ConvoBox's own source). No fallback to ConvoBox's own
directory if working_dir is unset -- an unconfigured working_dir means
uploads are unavailable, same stance artifacts.py takes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

# Executable/script extensions rejected outright. The working_dir fence
# below is the real security boundary here -- an uploaded file can only
# ever land inside the same directory the backend agent already has full
# read/write/execute access to -- but there's no reason to make it
# convenient to drop a payload the agent might later be asked to run.
_BLOCKED_EXTENSIONS = frozenset(
    {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi", ".sh"}
)

# Generous but bounded -- this is a loopback-only, no-auth surface (same
# trust model as every other mutating route in app.py), so a cap exists
# purely to stop a runaway or malicious upload from filling the disk, not
# because a legitimate reference image/PDF is expected to be small.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_UPLOAD_PATH = "/api/upload"


def _safe_destination(base: Path, raw_filename: str) -> Path:
    """A write path guaranteed to stay inside base, never overwriting an
    existing file.

    Path(raw_filename).name strips every directory component (../, an
    absolute path, a drive letter) -- the only thing that can reach the
    filesystem is a bare filename, so this can't escape base by
    construction. Collision-safe: appends " (2)", " (3)", ... rather than
    silently overwriting whatever's already there, the same convention a
    file manager's own "copy" dialog uses.
    """
    name = Path(raw_filename).name
    if not name or name in (".", ".."):
        raise HTTPException(400, "invalid filename")
    suffix = Path(name).suffix
    if suffix.lower() in _BLOCKED_EXTENSIONS:
        raise HTTPException(415, f"{suffix!r} files can't be uploaded")
    stem = Path(name).stem
    candidate = base / name
    n = 1
    while candidate.exists():
        n += 1
        candidate = base / f"{stem} ({n}){suffix}"
    return candidate


def add_upload_routes(
    app: FastAPI,
    working_dir: Path | None,
    on_uploaded: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Registers POST /api/upload. on_uploaded, when given, is called with
    the final (post-collision-handling) filename after a successful write
    -- run_convobox.py wires this to WebTextInputBridge.submit() so the
    backend is actually told a new file exists, the same way a spoken
    reference to it would reach the backend."""

    @app.middleware("http")
    async def reject_oversized_uploads(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Cross-session security review, 2026-09-02 ("SOL"): the
        upload_file() handler's own per-chunk size check below runs too
        late to be the real bound -- by the time `file: UploadFile =
        File(...)` is populated, Starlette has already parsed the WHOLE
        multipart body (its default UploadFile spools to a temp file
        during that parse), so an oversized body already hit disk before
        our 50MB check ever got a chance to reject it.

        This middleware runs before routing/dependency resolution, so it
        can reject on Content-Length alone -- before Starlette touches
        the body at all -- for the one route that accepts a file. Not
        airtight: Content-Length reflects the whole multipart body
        (boundaries/field overhead included, slightly more than the raw
        file), and a client using chunked transfer-encoding with no
        Content-Length header bypasses this check entirely, falling back
        to the same too-late per-chunk check below. Accepted as
        proportionate to the actual threat model (loopback-only, and
        POST already requires the CSRF header plus web_ui_token when
        configured -- an attacker who can reach this route already has
        much bigger levers than filling disk), not claimed as a hard
        guarantee.
        """
        if request.url.path == _UPLOAD_PATH and request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    too_large = int(content_length) > _MAX_UPLOAD_BYTES
                except ValueError:
                    too_large = False
                if too_large:
                    return Response(
                        status_code=413,
                        content=f"file exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB "
                        "upload limit",
                    )
        return await call_next(request)

    @app.post("/api/upload")
    async def upload_file(file: UploadFile = File(...)) -> dict[str, str]:  # noqa: B008 -- FastAPI's own required parameter-declaration idiom, not a real mutable-default bug
        if working_dir is None:
            raise HTTPException(
                503,
                "no backend.working_dir configured -- uploads can only be "
                "written into it, and none is set",
            )
        if not file.filename:
            raise HTTPException(400, "no filename given")
        base = working_dir.resolve()
        base.mkdir(parents=True, exist_ok=True)
        destination = _safe_destination(base, file.filename)
        # Defense in depth, same check artifacts.py's serving route makes
        # on read -- _safe_destination's basename-only construction should
        # already guarantee this, but verify rather than only assume it.
        resolved = destination.resolve()
        if resolved != base and base not in resolved.parents:
            raise HTTPException(403, "resolved path escapes the configured working_dir")

        size = 0
        try:
            with destination.open("wb") as f:
                while chunk := await file.read(_READ_CHUNK_BYTES):
                    size += len(chunk)
                    if size > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            413,
                            f"file exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB "
                            "upload limit",
                        )
                    f.write(chunk)
        except HTTPException:
            destination.unlink(missing_ok=True)
            raise

        if on_uploaded is not None:
            await on_uploaded(destination.name)
        return {"filename": destination.name}
