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

Also serves GET /api/artifacts (2026-08-12, docs/ARTIFACT-PANE-SCOPE.md's
"Working-Directory File Browser" section) -- a filtered listing of files
in working_dir, not just ones a tool call already named. Reverses that
doc's earlier 2026-07-29 rejection of a general browser: the listing is
filtered through the SAME ARTIFACT_MEDIA_TYPES allowlist this file's
single-artifact route already enforces (a file type this server would
refuse to serve can never appear in the listing either), plus dotfile
and symlink exclusion on top -- see the doc for the full reasoning on
why a plain directory scan was rejected and what changed.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from convobox.adapters.base import ARTIFACT_MEDIA_TYPES

# Working-directory file browser (docs/ARTIFACT-PANE-SCOPE.md's
# "Working-Directory File Browser" section) -- a hard cap on how many
# matching files a single listing request will return. Not a pagination
# limit (no offset/cursor) -- v1 scope is "flat, filtered list", not a
# browsable-at-any-scale directory tree. The walk stops entirely the
# moment this many matches are found, so a pathological deeply-nested
# working_dir can't turn one request into unbounded filesystem work.
_MAX_BROWSE_RESULTS = 500


def _resolve_working_dir(working_dir: Path | None) -> Path:
    """Shared by every route below -- raises the same 503 either the
    single-file route or the listing route needs when backend.working_dir
    isn't configured. Resolving here (once) rather than at each call site
    keeps that check from drifting between routes."""
    if working_dir is None:
        raise HTTPException(
            503,
            "no backend.working_dir configured -- artifacts can only be "
            "served from within it, and none is set",
        )
    return working_dir.resolve()


def _resolve_artifact(working_dir: Path | None, artifact_path: str) -> Path:
    """Shared by both single-file routes below -- the path-traversal fence
    must have exactly one implementation, not two copies that could drift
    apart. Raises the same HTTPExceptions either route needs; callers
    don't catch anything themselves."""
    base = _resolve_working_dir(working_dir)
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


def _list_browsable_files(base: Path) -> tuple[list[str], bool]:
    """Every file under base whose extension is already in
    ARTIFACT_MEDIA_TYPES (the same allowlist the single-file route
    enforces -- a listing can never surface a file type this server would
    refuse to serve anyway), sorted, relative-POSIX-path. Two exclusions
    on top of the extension allowlist, both defense-in-depth against the
    listing surfacing something it shouldn't (see the scope doc's
    reasoning): any path component starting with "." (dotfiles/dot-dirs
    like .git/.env/.ssh -- excluded even if an entry inside happened to
    have an allowlisted extension), and symlinks (never followed --
    os.walk's own followlinks=False keeps directory symlinks from being
    descended into at all; the per-file is_symlink() check below catches
    a symlinked FILE sitting directly in an otherwise-real directory,
    which followlinks alone wouldn't).

    Returns (paths, truncated) -- truncated is True if _MAX_BROWSE_RESULTS
    was hit before the walk finished (there were more matches than shown,
    not that these are necessarily the "first" N in any meaningful order).
    """
    results: list[str] = []
    truncated = False
    for root, dirnames, filenames in os.walk(base, followlinks=False):
        root_path = Path(root)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".") and not (root_path / d).is_symlink()
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            file_path = root_path / filename
            if file_path.is_symlink():
                continue
            if file_path.suffix.lower() not in ARTIFACT_MEDIA_TYPES:
                continue
            if len(results) >= _MAX_BROWSE_RESULTS:
                truncated = True
                break
            results.append(file_path.relative_to(base).as_posix())
        if truncated:
            break
    return sorted(results), truncated


class _BrowseResponse(BaseModel):
    files: list[str]
    truncated: bool


class _SetActiveArtifactRequest(BaseModel):
    # None clears it -- the pane closed, or nothing has ever been shown.
    path: str | None = None
    # Client-side artifactLoadCounter at the time this report was fired --
    # a monotonic per-page-load sequence number, not a timestamp. None
    # (any caller that doesn't send one) always applies, for robustness
    # against any other client; this repo's own frontend always sends one.
    # See the route handler below for why this exists.
    sequence: int | None = None


def add_artifact_routes(app: FastAPI, working_dir: Path | None) -> None:
    """Registers GET /api/artifacts/{path}, .../editor-uri, GET
    /api/artifacts (the working-directory file browser -- see
    _list_browsable_files), and POST /api/artifacts/active (see below).
    working_dir is None unless backend.working_dir is explicitly
    configured -- unlike settings/listening, there is deliberately no
    fallback to ConvoBox's own directory here; an unconfigured
    working_dir means artifacts are unavailable, not "serve from
    somewhere unexpected"."""

    # app.state.active_artifact_path (GitHub issue #280): "what artifact
    # is currently showing" has no single, obvious source of truth --
    # the pane's own tab-switch/close state lives entirely in the
    # browser's JS, not anywhere the backend agent (or this server) can
    # see. Rather than have the server guess from its own broadcast
    # history (wrong the moment a user manually clicks an OLDER tab, or
    # closes the pane, without a fresh ARTIFACT event), the FRONTEND
    # reports its own real state here -- renderArtifact() (index.html)
    # is already the single chokepoint every path funnels through (live
    # SSE events, tab clicks, Browse-files opens), so one fetch() call
    # there keeps this in sync with the truth, not a guess reconstructed
    # server-side. web/mcp_server.py's get_shown_artifact tool reads
    # this same field.
    app.state.active_artifact_path = None
    # Highest `sequence` applied so far -- see set_active_artifact below.
    # -1 (not 0) so a legitimate first report with sequence=0 still applies.
    app.state.active_artifact_sequence = -1

    # No route-ordering hazard here (unlike editor-uri below): this is an
    # exact path with no {artifact_path:path} segment, so it can never be
    # swallowed by (or swallow) the catch-all route -- registration order
    # relative to it doesn't matter, placed first simply for readability.
    @app.get("/api/artifacts", response_model=_BrowseResponse)
    async def list_artifacts() -> _BrowseResponse:
        base = _resolve_working_dir(working_dir)
        files, truncated = _list_browsable_files(base)
        return _BrowseResponse(files=files, truncated=truncated)

    # Also registered before the catch-all, same reasoning as above --
    # "active" as a literal path segment can't collide with it (a POST,
    # while the catch-all is GET-only, so this specific pair couldn't
    # actually collide either way; kept adjacent to list_artifacts for
    # readability, not because ordering matters here).
    @app.post("/api/artifacts/active")
    async def set_active_artifact(body: _SetActiveArtifactRequest) -> dict[str, bool]:
        # Staleness guard, same shape as the frontend's own editor-uri
        # fetch (index.html) but server-side: two of these POSTs fired
        # close together (a fast tab switch) have no guarantee of being
        # APPLIED in the order they were sent -- Starlette awaits
        # request-body parsing before this function's own code runs, and
        # that's enough of a scheduling gap for two concurrent requests
        # to complete out of order. Live UAT finding, 2026-08-18: this
        # showed up as get_shown_artifact intermittently reporting the
        # PREVIOUS tab right after switching ("works on the 2nd/3rd
        # try"). Ignoring anything not newer than the highest sequence
        # already applied removes the race regardless of arrival order.
        if (
            body.sequence is not None
            and body.sequence <= app.state.active_artifact_sequence
        ):
            return {"ok": True}
        if body.sequence is not None:
            app.state.active_artifact_sequence = body.sequence
        if body.path is None:
            app.state.active_artifact_path = None
            return {"ok": True}
        try:
            candidate = _resolve_artifact(working_dir, body.path)
        except HTTPException:
            # Defense in depth, not a hard failure: this report only
            # feeds an informational MCP tool, never a security
            # boundary (that's GET /api/artifacts/{path}'s own fence,
            # already enforced when the browser fetched the content
            # this report describes). A stale/racy report shouldn't
            # break the UI -- treat an unresolvable path as "nothing
            # confidently known" rather than surfacing an error the
            # frontend has no useful way to act on.
            app.state.active_artifact_path = None
            return {"ok": True}
        base = _resolve_working_dir(working_dir)
        app.state.active_artifact_path = candidate.relative_to(base).as_posix()
        return {"ok": True}

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
        # as_posix(), not str() -- on Windows, Path's own str() uses native
        # backslashes ("D:\foo\bar"), but a URI's path component only ever
        # uses forward slashes (RFC 3986; vscode://file/ follows the same
        # convention file:// URIs do). A raw backslash isn't a valid URI
        # path separator at all -- VS Code's URI parser silently fails to
        # navigate to it, so the window just comes to the foreground
        # showing whatever was already open, not the intended file. Live-
        # found 2026-08-09 UAT: "Open in editor" opened VS Code but left an
        # unrelated file on screen, not the clicked artifact.
        return {"uri": f"vscode://file/{candidate.as_posix()}"}

    @app.get("/api/artifacts/{artifact_path:path}")
    async def get_artifact(artifact_path: str) -> FileResponse:
        candidate = _resolve_artifact(working_dir, artifact_path)
        media_type = ARTIFACT_MEDIA_TYPES.get(candidate.suffix.lower())
        if media_type is None:
            raise HTTPException(
                415, f"{candidate.suffix!r} is not a servable artifact type"
            )
        return FileResponse(candidate, media_type=media_type)
