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
   **Done** — see "Progress" below.
2. Wire ONE adapter first (opencode — the default backend, most complete
   adapter today). Do not attempt claude-code/codex in the same pass.
3. `GET /api/artifacts/{path}` with the working_dir fence above,
   including a real test asserting path-traversal is rejected. **Done**
   — see "Progress" below.
4. Frontend: a collapsible right-hand pane, closed by default, opens on
   the first ARTIFACT event, image + HTML rendering only.
5. Live-verify: a real tool call that writes a real image or HTML file
   in a real (scratch) working_dir actually renders in the pane, AND a
   crafted `../../` path attempt against `/api/artifacts/` is actually
   rejected — both need to be checked against the real running app, not
   assumed from the code.

### Progress (2026-07-28)

Steps 1 and 3 above shipped (`78df8aa`) with real path-traversal and
`Path`-join-gotcha regression tests, plus live verification via BrowserOS
against a real running server (a real PNG and a real HTML file both
served and rendered correctly).

**Step 2's own sub-investigation, not yet finished:** originally assumed
detecting a "Write/Edit-shaped tool call" meant parsing
`session.next.tool.called`'s generic `{tool: string, input: object}`
payload. Checked a real local `opencode serve` instance's own live
OpenAPI spec (`GET /doc`, zero LLM cost — this is a static schema
endpoint, no completion involved) and found `input` is untyped per-tool
(opencode's tool system is dynamically pluggable, not statically
described in the spec), so that approach would mean guessing at a tool
name string and an input key with no verified source for either.

**Better find in the same spec:** opencode emits a SEPARATE, dedicated
event — `type: "file.edited"`, `data: {file: string}` — specifically
when a file changes, independent of which tool caused it. This is a much
more reliable hook than parsing tool-call input would ever be, IF it
fires for every real file-write tool the AI uses (needs confirming) and
IF `file` is a path resolvable against `backend.working_dir` (needs
confirming whether it's absolute or relative to the session's project
root — the spec's `type: string` gives no format hint either way).

Tried to confirm the format for free: subscribed to the server's global
`/event` SSE stream and manually wrote a scratch file via the shell
while the server was up. No `file.edited` event fired — meaning this
event is very likely tied to the ACTUAL tool-execution pipeline (a real
AI tool call doing the edit), not a generic filesystem watcher, so a
plain shell write was never going to trigger it. **Confirming the real
shape needs one real prompt through a real opencode session (e.g. "write
'hello' to test.txt") to observe the actual event on the wire** — a
small, genuine LLM completion cost, not free like the spec-reading
above. Not spent autonomously this pass (this project's own convention
for spending real API budget on verification is to do it, but not
without it being the deliberate point of the session) — whoever
continues this should either get an explicit go-ahead to run that one
prompt, or already knows the answer from other live opencode experience,
before wiring `file.edited` → `BackendEventType.ARTIFACT`.

## Deferred For Later

- PDF/CSV/rich viewers beyond the simple fallback above.
- codex adapter support (Claude Code shipped; opencode still blocked, see
  "Progress" above).
- Multiple simultaneous artifacts / a history of past artifacts — v1
  shipped as "one pane, latest artifact only." Now being designed as the
  Artifact Chooser below, per JP's request.
- Any artifact *editing* (this pane is view-only; an editable artifact
  pane is a control-plane-shaped feature, same class of decision as the
  stop/resume-listening buttons — not assumed in scope here).

## Artifact Chooser (2026-07-29, design pass before code)

JP asked, after the single-pane version had been live-UAT'd for a while:
should there be a way to see the latest artifact AND get back to earlier
ones from the same session? A design pass first, same reasoning as the
rest of this doc.

### Explicitly rejected: a VSCode-style file explorer

JP's own follow-up question, considered and rejected for the same reasons
`docs/field-notes/2026-07-28-other-claude-code-web-uis-dont-transfer-
much.md` already gives for file trees/terminals/Git explorers generally:
ConvoBox never edits files itself (the backend agent does, in its own
sandboxed `backend.working_dir`, deliberately kept separate from
ConvoBox's own source — `docs/DESIGN-backend-sandboxing.md`), and a
browsable directory listing would mean serving *any* file in
`working_dir` from a second, no-auth surface, not just ones a tool call
actually referenced — a materially bigger trust-boundary widening than
today's `GET /api/artifacts/{path}`, which only ever serves paths this
session's own ARTIFACT events named. **Decision: the chooser lists only
artifacts the backend actually produced this session, never a live
directory scan.**

### Format: a tab strip, not a carousel or dropdown

Three shapes considered:
- **Carousel** (prev/next arrows) — rejected: hides how many artifacts
  exist, no direct jump to a specific one, awkward once past 3-4 items.
- **Dropdown** — matches the existing session-picker pattern already in
  the ribbon, minimal footprint, but hides the list until opened and is
  less scannable at a glance.
- **Tab strip** (chosen) — a row of small filename-labeled tabs across
  the top of the artifact pane itself, most-recent auto-selected/
  highlighted, click any to switch. Most scannable (see count + jump
  directly), reasonable vertical cost given artifact count per session is
  usually small. If a session accumulates enough artifacts to overflow
  the pane's width, horizontal scroll on the tab strip (not wrapping,
  which would eat vertical space from the actual artifact) — revisit if
  live UAT shows this getting unwieldy.

### Data source: this session's own ARTIFACT events, live + replayed from history

No `HistoryDB` schema change needed — corrected an earlier wrong
assumption (this doc, prior draft) that `artifact_path` wasn't persisted;
it already is, exactly like every other `BackendEvent` field
(`WebEventForwarder.__call__` → `HistoryDB.append_event(backend_event=
event)` → `event_to_dict()` → the `backend_event_json` column), gated
only by the existing `web.history_tracking_enabled` opt-in like
everything else. Plan:

1. Frontend keeps an in-memory ordered list of `{path, label, timestamp}`
   for the current page connection, appended to on every live SSE
   `ARTIFACT` event — same source the single pane already reacts to.
2. On page load/reconnect, seed that list from
   `GET /api/sessions/{id}/events`: filter rows where `event_type ==
   "artifact"`, `JSON.parse(row.backend_event_json).artifact_path` for
   each. Only possible when `web.history_tracking_enabled` is on (same
   as every other history-dependent feature) — with it off, the chooser
   starts empty on a fresh page load and only fills in from live events
   from that point on, matching this app's existing "live view always
   works, history is a separate opt-in" rule rather than a special case.
3. Identity key: the resolved artifact path, same as the existing
   refresh-in-place logic. A later event for a path already in the list
   updates that SAME tab's content/timestamp rather than appending a
   duplicate — the tab strip and the existing "same path = refresh"
   behavior are the same mechanism, not two.

### Initial Slice

1. Frontend-only: accumulate the live-event list (step 1 above), render
   a tab strip, latest auto-selected. No backend changes.
2. Add the history-replay seed (step 2) as a follow-up in the same slice
   once step 1 is visually confirmed via BrowserOS.
3. Live-verify: multiple real tool-call-produced artifacts in one
   session actually populate multiple tabs, clicking an older tab shows
   the right content, a repeated path updates its existing tab instead of
   duplicating, and (if `history_tracking_enabled`) a page reload
   restores the tab list.

### Deferred out of this pass

- Per-tab close/dismiss controls.
- Cross-*session* gallery (artifacts from a DIFFERENT past session, not
  just this one) — the existing session picker already covers switching
  which session's transcript you're viewing; whether artifacts should
  follow that same switch is a separate decision, not assumed here.
- Thumbnails/previews rendered directly in the tab (text label only for
  now).

## Open Questions (for JP, not decided here)

- Is opencode-first the right adapter to start with, or does JP want
  claude-code first since that's what's actually in daily use per recent
  session notes?
- Exact renderable-extension allowlist — this doc proposes images + HTML
  as the v1 set; confirm before building.
- Should `GET /api/artifacts/*` require `web.history_tracking_enabled` or
  any other existing opt-in gate, or is it unconditionally available
  whenever `web.enabled` is (matching every other route so far)?
- Chooser tab labels: bare filename, or full path relative to
  `working_dir` (clearer when two artifacts share a filename in
  different subdirectories, at the cost of a longer label)?
