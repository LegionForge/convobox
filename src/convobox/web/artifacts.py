"""Serves files a tool call produced (docs/ARTIFACT-PANE-SCOPE.md) --
fenced to backend.working_dir, the SAME boundary that already bounds what
the sandboxed coding agent itself can touch (config.py's own security note
on BackendConfig.working_dir: leave it unset and a voice session could
modify ConvoBox's own source). Serving arbitrary local file content to a
no-auth loopback browser is a materially different kind of exposure than
any mutation this web UI ships elsewhere (approve/deny/quit/settings-save
are all bounded API calls; this is an open-ended file read unless fenced)
-- see the scope doc's own "Security" section for the reasoning.

ClaudeCodeAdapter is the first adapter to emit BackendEventType.ARTIFACT
(see claude_code.py); opencode/codex remain unwired -- each adapter's
detection is its own separate opt-in, per the scope doc's slice-by-slice
plan.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from convobox.adapters.base import ARTIFACT_MEDIA_TYPES


def _resolve_artifact(working_dir: Path | None, artifact_path: str) -> Path:
    """Shared by both routes below -- the path-traversal fence must have
    exactly one implementation, not two copies that could drift apart.
    Raises the same HTTPExceptions either route needs; callers don't
    catch anything themselves."""
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
    return candidate


def add_artifact_routes(app: FastAPI, working_dir: Path | None) -> None:
    """Registers GET /api/artifacts/{path} and .../editor-uri. working_dir
    is None unless backend.working_dir is explicitly configured --
    unlike settings/listening, there is deliberately no fallback to
    ConvoBox's own directory here; an unconfigured working_dir means
    artifacts are unavailable, not "serve from somewhere unexpected"."""

    # Registered BEFORE the greedy catch-all route below on purpose:
    # {artifact_path:path} matches an entire remaining path including
    # slashes, so "/api/artifacts/foo/bar/editor-uri" would otherwise be
    # swallowed whole by get_artifact() (artifact_path="foo/bar/editor-uri")
    # before this more specific route ever got a chance -- Starlette tries
    # routes in registration order, first match wins, not "most specific
    # wins" automatically.
    @app.get("/api/artifacts/{artifact_path:path}/editor-uri")
    async def get_artifact_editor_uri(artifact_path: str) -> dict[str, str]:
        # "Open in editor" (JP, 2026-08-07): hand off to VS Code via its
        # own registered vscode://file/ URI scheme rather than building
        # in-pane editing -- docs/ARTIFACT-PANE-SCOPE.md's own "Deferred
        # For Later" already ruled out an editable pane as a
        # control-plane-shaped decision needing its own scoping pass;
        # this is a different, smaller thing (open externally, in a
        # tool the user already trusts and already manages concurrent
        # edits/saves in) -- explicitly not that.
        #
        # This DOES hand the browser the resolved absolute path (which
        # necessarily contains the working_dir prefix `/api/config`
        # deliberately never exposes) -- accepted as a narrow, specific
        # disclosure: only for a path this session's own ARTIFACT event
        # already named and the browser has already fetched the content
        # of, not a new way to discover arbitrary filesystem structure.
        # VS Code-specific by construction (the URI scheme); no attempt
        # to detect or support other editors in this pass.
        candidate = _resolve_artifact(working_dir, artifact_path)
        return {"uri": f"vscode://file/{candidate}"}

    @app.get("/api/artifacts/{artifact_path:path}")
    async def get_artifact(artifact_path: str) -> FileResponse:
        candidate = _resolve_artifact(working_dir, artifact_path)
        media_type = ARTIFACT_MEDIA_TYPES.get(candidate.suffix.lower())
        if media_type is None:
            raise HTTPException(
                415, f"{candidate.suffix!r} is not a servable artifact type"
            )
        return FileResponse(candidate, media_type=media_type)
