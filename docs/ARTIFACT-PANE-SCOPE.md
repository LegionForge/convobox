# Artifact Pane Scope

This doc defines the first implementation target for the web UI's
right-hand artifact pane (queued in `convobox-quickref.md` since web UI v2
was scoped, not yet built). Written as a design pass before any code, same
reasoning as `docs/SETTINGS-TUI-SCOPE.md`: this is bigger and more
security-sensitive than it first looks, and deserves real decisions before
an implementation starts, not an improvised first cut.

## Product Goal

Claude/ChatGPT-desktop-style: when a tool call produces something worth
*looking at* (an image, a plot, a rendered HTML page), a pane opens next
to the chat and shows it — interactive where that makes sense, and it
stays open/updates in place if the same artifact changes again, rather
than spawning a new pane per event.

## Why This Needs a New BackendEventType, Not a Heuristic

Confirmed by reading `src/convobox/adapters/base.py`
(`BackendEventType`/`BackendEvent`) before writing this doc: today's event
shape is `text | tool_call | tool_result | error | done |
approval_request`, and `tool_output` is an untyped string — there is no
structured signal that a given tool result *is* a file path, let alone a
renderable one.

Two ways to detect "this tool result is an artifact":

1. **Heuristic**: regex-match `tool_output` for something that looks like
   a file path with a known extension (`.png`, `.svg`, `.html`, ...).
   Rejected — fragile (a tool that prints "wrote to plot.png inside a
   longer sentence" vs. one that prints a bare path are indistinguishable
   from text alone), and every backend (opencode/claude-code/codex) shapes
   tool output differently, so the heuristic would need per-backend tuning
   anyway.
2. **A new explicit `BackendEventType.ARTIFACT`**, emitted by an adapter
   only when it can confidently identify a renderable output (e.g. a
   Write/Edit tool call whose target path has a known-renderable
   extension). Adapter-specific detection logic, but explicit and
   reliable rather than guessed.

**Decision: (2).** Costs one enum value and one `BackendEvent` field
(`artifact_path: str | None`); each adapter opts in independently, so
shipping this for one backend first (see "First Implementation Slice")
doesn't require touching the others.

## Security: What's Safe To Serve

This is the part that makes this feature bigger than it looks. The web
UI's whole trust model (`docs/WEB-UI-USAGE.md`) is "no auth, but it can't
do much either, loopback-only is enough." An artifact pane means the
browser can now ask the server to **read and return arbitrary local file
content** — a materially different kind of exposure than anything shipped
so far (approve/deny and settings-save are bounded *mutations* through a
fixed API surface; serving files is open-ended *reads* of whatever path a
client names, unless deliberately fenced in).

**Decision: only serve files that resolve inside `backend.working_dir`**
(already the documented security boundary for what the sandboxed coding
agent itself can touch — `config.py`'s own comment: "a voice session
could then modify ConvoBox's source [without it]"). A new
`GET /api/artifacts/{path}` route must:
- Resolve `path` against `working_dir`, reject anything that escapes it
  (`..`, symlinks, absolute paths) — same class of check as any
  path-traversal guard, needs an actual test asserting the rejection, not
  just an assumption.
- Reject if `working_dir` itself is unset (defaults to ConvoBox's own
  directory — see the security note already in `config.py`'s
  `working_dir` field docs) or serve from ConvoBox's own tree, whichever
  is stricter.
- Only serve a fixed allowlist of extensions/MIME types (images, HTML,
  maybe PDF/CSV/plain text) — never serve arbitrary file types as
  `application/octet-stream` just because a tool happened to write one.

## Rendering

No build step (matches this file's own established constraint — plain
HTML/JS, no React/Vite). Per MIME type:
- Images (`png`/`jpg`/`svg`/`webp`) → `<img>`.
- HTML → `<iframe sandbox="allow-scripts">` at minimum — an artifact is
  agent-generated content, not ConvoBox's own trusted UI; do not remove
  `sandbox` without a real reason.
- Everything else in the allowlist (PDF/CSV/plain text) → punt to a
  simple `<embed>`/`<pre>` fallback or a download link; not worth
  building bespoke viewers for in a first slice.

## Refresh, Not Duplicate

"If a later ARTIFACT event references the SAME file path, treat it as a
refresh of the open pane rather than a new one" (already the intent noted
in prior session scoping). Identity key: the resolved absolute path.
Implementation: the frontend keeps the pane's `src` pointing at
`/api/artifacts/{path}?t=<event timestamp>` — a fresh event for the same
path changes the query string, forcing a reload without changing which
pane is open. No new backend statefulness needed.

## Initial Screens / Slice

Start with the minimum useful thing, same philosophy as
`SETTINGS-TUI-SCOPE.md`'s own "first slice" section:

1. `BackendEventType.ARTIFACT` + `BackendEvent.artifact_path` (base.py).
2. Wire ONE adapter first (opencode — the default backend, most complete
   adapter today) to emit it when a Write/Edit-shaped tool call's target
   path has a renderable extension. Do not attempt claude-code/codex in
   the same pass.
3. `GET /api/artifacts/{path}` with the working_dir fence above,
   including a real test asserting path-traversal is rejected.
4. Frontend: a collapsible right-hand pane, closed by default, opens on
   the first ARTIFACT event, image + HTML rendering only.
5. Live-verify: a real tool call that writes a real image or HTML file
   in a real (scratch) working_dir actually renders in the pane, AND a
   crafted `../../` path attempt against `/api/artifacts/` is actually
   rejected — both need to be checked against the real running app, not
   assumed from the code.

## Deferred For Later

- PDF/CSV/rich viewers beyond the simple fallback above.
- claude-code/codex adapter support.
- Multiple simultaneous artifacts / a history of past artifacts (v1: one
  pane, latest artifact only, matching the "no artifact/image concept
  today" starting point — don't build a gallery before the single-pane
  case is even shipped).
- Any artifact *editing* (this pane is view-only; an editable artifact
  pane is a control-plane-shaped feature, same class of decision as the
  stop/resume-listening buttons — not assumed in scope here).

## Open Questions (for JP, not decided here)

- Is opencode-first the right adapter to start with, or does JP want
  claude-code first since that's what's actually in daily use per recent
  session notes?
- Exact renderable-extension allowlist — this doc proposes images + HTML
  as the v1 set; confirm before building.
- Should `GET /api/artifacts/*` require `web.history_tracking_enabled` or
  any other existing opt-in gate, or is it unconditionally available
  whenever `web.enabled` is (matching every other route so far)?
