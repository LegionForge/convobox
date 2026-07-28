"""Serves files a tool call produced (docs/ARTIFACT-PANE-SCOPE.md) --
fenced to backend.working_dir, the SAME boundary that already bounds what
the sandboxed coding agent itself can touch (config.py's own security note
on BackendConfig.working_dir: leave it unset and a voice session could
modify ConvoBox's own source). Serving arbitrary local file content to a
no-auth loopback browser is a materially different kind of exposure than
any mutation this web UI ships elsewhere (approve/deny/quit/settings-save
are all bounded API calls; this is an open-ended file read unless fenced)
-- see the scope doc's own "Security" section for the reasoning.

No adapter emits BackendEventType.ARTIFACT yet (that's each adapter's own
later, separate opt-in) -- this route exists ahead of any adapter using
it, same as the event type itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Deliberately narrow (docs/ARTIFACT-PANE-SCOPE.md's "Rendering" section)
# -- never serve arbitrary file types as application/octet-stream just
# because a tool happened to write one.
_ALLOWED_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".txt": "text/plain",
}


def add_artifact_routes(app: FastAPI, working_dir: Path | None) -> None:
    """Registers GET /api/artifacts/{path}. working_dir is None unless
    backend.working_dir is explicitly configured -- unlike settings/
    listening, there is deliberately no fallback to ConvoBox's own
    directory here; an unconfigured working_dir means artifacts are
    unavailable, not "serve from somewhere unexpected"."""

    @app.get("/api/artifacts/{artifact_path:path}")
    async def get_artifact(artifact_path: str) -> FileResponse:
        if working_dir is None:
            raise HTTPException(
                503,
                "no backend.working_dir configured -- artifacts can only be "
                "served from within it, and none is set",
            )
        base = working_dir.resolve()
        candidate = (base / artifact_path).resolve()
        # Path-traversal fence: candidate must resolve to somewhere INSIDE
        # base. Checking base in candidate.parents (not a string prefix
        # check) so a sibling directory that merely SHARES a string
        # prefix (base-evil/ vs base/) can't false-positive as "inside".
        if candidate != base and base not in candidate.parents:
            raise HTTPException(403, "path escapes the configured working_dir")
        if not candidate.is_file():
            raise HTTPException(404, "no such artifact")
        media_type = _ALLOWED_MEDIA_TYPES.get(candidate.suffix.lower())
        if media_type is None:
            raise HTTPException(
                415, f"{candidate.suffix!r} is not a servable artifact type"
            )
        return FileResponse(candidate, media_type=media_type)
