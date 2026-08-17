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

- PDF/CSV rich viewers beyond the simple fallback above. **Partially
  superseded 2026-08-07:** source code (JS/TS, YAML, Java, C/C++/C#,
  JSON, XML, Markdown) now gets a real bespoke viewer -- syntax
  highlighting via a vendored highlight.js (`vendor/highlightjs/`, no
  CDN, no build step, matches this file's own "plain HTML/JS" rendering
  constraint) -- JP asked for this directly, listing the exact language
  set. PDF/CSV specifically remain the simple fallback; not revisited
  this pass.
- codex adapter support (Claude Code shipped; opencode still blocked, see
  "Progress" above).
- Multiple simultaneous artifacts / a history of past artifacts — v1
  shipped as "one pane, latest artifact only." Now being designed as the
  Artifact Chooser below, per JP's request.
- Any artifact *editing* (this pane is view-only; an editable artifact
  pane is a control-plane-shaped feature, same class of decision as the
  stop/resume-listening buttons — not assumed in scope here). **Still
  true as of 2026-08-07** -- the same session that added code
  highlighting also added an "Open in editor" link
  (`GET /api/artifacts/{path}/editor-uri` → a `vscode://file/` URI), but
  that is a DIFFERENT, smaller decision: hand off to an external tool
  the user already trusts and already manages concurrent
  edit/save/conflict semantics in, not build in-pane editing inside
  ConvoBox's own no-auth loopback surface. VS Code-specific by
  construction (the URI scheme); no other editor's scheme is detected or
  supported.

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

### Progress (2026-08-12)

Implemented per the plan above: frontend-only tab strip, live ARTIFACT
events accumulate into it, history-replay seed added (fixed a real gap
found while building this — replayed history rows never carried
`artifact_path` because `event_to_dict()`'s output only reaches the
frontend nested inside the `backend_event_json` column, not flattened
onto the row; the frontend now parses that JSON for `event_type ==
"artifact"` rows during replay, same as the live SSE shape already
provided). Identity-key refresh-in-place behavior implemented as
specified — a repeat path updates the existing tab, never duplicates.

## Working-Directory File Browser (2026-08-12, reverses the 2026-07-29 rejection above)

JP asked for a way to open *any* file in `backend.working_dir`, not
just ones a tool call already produced an `ARTIFACT` event for — the
exact feature explicitly rejected above. Revisited deliberately, with
the objection taken seriously rather than dismissed:

**The original objection was really two separate risks, conflated.**
(1) *Rendering* risk — a given file's content could be actively
malicious (e.g. an HTML artifact attempting something nasty). Already
well-mitigated for the existing single-artifact path: sandboxed
`<iframe sandbox="allow-scripts">` (deliberately no `allow-same-origin`,
so it can't reach ConvoBox's own API or the parent page), `textContent`
never `innerHTML` for code, a fixed extension/MIME allowlist that
refuses to serve arbitrary types at all. (2) *Enumeration* risk — a
live directory-listing endpoint lets anything that can reach
`http://127.0.0.1:<port>` (JS in an unrelated browser tab probing
localhost, another local process) discover and read files a tool call
never touched — `.env`, SSH keys, credentials, anything sitting in
`working_dir` that was never meant to be artifact-served. **This second
risk is the one the original rejection was actually about, and a UI
warning dialog does nothing against it** — a warning only gates a
human's *informed* choice to open something; it's no barrier at all to
an automated/cross-origin read of the same endpoint.

**Decision: build the listing endpoint filtered through the SAME
`ARTIFACT_MEDIA_TYPES` allowlist the single-artifact route already
enforces**, rather than a raw directory scan. This closes the sharpest
edge of the enumeration risk structurally, not with a warning: `.env`,
credentials, dotfiles, and anything off the renderable-extension
allowlist simply never appear in the listing, regardless of what's
actually sitting in the directory — the listing endpoint cannot surface
a file type it would refuse to serve anyway. It does not eliminate
enumeration entirely (an allowlisted file — an image, HTML, source
file — could still contain something sensitive), which is why the
warning banner is still worth adding, as a mitigation for the
*rendering* risk (Risk 1) and an honest disclosure for residual Risk 2,
not as the primary defense.

**Additional exclusions, on top of the extension allowlist:**
- Any path component starting with `.` (dotfiles, `.git/`, `.env`,
  `.ssh/`) — excluded even if an entry inside happened to have an
  allowlisted extension, since a dotDIRECTORY often signals "not meant
  to be browsed" regardless of what's inside it.
- Symlinks — never followed. A symlink inside `working_dir` pointing
  outside it is a realistic way to defeat the fence entirely; simplest
  safe behavior is to skip them, not to resolve-and-fence each one
  individually.
- A result cap (see implementation) with a `truncated` flag rather than
  an unbounded walk — protects against a pathological huge/deeply-nested
  `working_dir` turning a listing request into a resource problem.

**Explicitly still NOT in scope**: a hierarchical tree explorer (the
2026-07-29 rejection's other objection — VSCode-style browsing — still
holds; this ships a flat, filtered, sorted file list, not a directory
tree with expand/collapse). Opening a browsed file reuses the exact
same rendering path as an ARTIFACT-triggered one (same pane, same
Chooser tab-strip mechanism above) — from the frontend's perspective, a
browsed file and a tool-produced artifact are the same kind of thing
once opened, just discovered a different way. Each listed file also
gets a plain `target="_blank"` link to open it in a new browser tab
directly (JP's own "(or new tab)" ask) — no new backend route needed
for that, the existing `/api/artifacts/{path}` GET route already serves
it.

## Agent-Initiated Artifacts (2026-08-16)

JP asked directly: "the llm should be given a tool to refocus a document
or show a document from the cwd." Everything above only ever fires an
ARTIFACT event as a SIDE EFFECT of a detected Write/Edit tool call --
there was no way for the agent to say "show me this file I only read" or
bring an already-shown one back into focus.

### Design

A new MCP server ConvoBox itself hosts (`src/convobox/web/mcp_server.py`),
mounted on the SAME FastAPI app the web UI already runs, exposing one
tool: `show_document(path)`. Deliberately reuses everything already
built rather than inventing a parallel mechanism:

- **Fencing**: the tool calls `artifacts.py`'s own `_resolve_artifact()`
  and checks the same `ARTIFACT_MEDIA_TYPES` allowlist the browser-facing
  `GET /api/artifacts/{path}` route already enforces -- one fence, not
  two copies that could drift apart, same reasoning `_resolve_artifact`'s
  own docstring already gives for sharing it between routes.
- **Delivery**: the tool calls the SAME `WebEventForwarder` the adapter's
  own Write/Edit detection already uses, emitting a plain
  `BackendEvent(type=ARTIFACT, artifact_path=...)` -- from the frontend's
  perspective, an agent-initiated artifact and a tool-produced one are
  identical events over the same SSE stream; zero frontend changes were
  needed.
- **Auth**: this is a NEW kind of exposure the rest of this doc's
  "Security" section doesn't cover -- a plain loopback HTTP endpoint any
  local process could POST to, not gated by the existing CSRF-header
  middleware (that header is a same-origin-browser signal only
  ConvoBox's own frontend JS knows to send; the MCP client here is the
  claude/codex CLI subprocess, not a browser, and can't send it). Fixed
  with a random per-session bearer token, generated once in
  `run_convobox.py` and handed to the CLI via `--mcp-config`'s own
  `headers` field -- the same "random token over a loopback channel"
  shape `adapters/claude_code.py`'s approval-hook TCP server already
  uses, not a new pattern.
- **Granted regardless of `permission_mode`**, unlike every other MCP
  server this adapter grants (which only happens under `permissive`):
  `show_document` is safe-by-construction (same fence as a route the
  browser can already hit), so refusing it under the "plan" default
  would defeat the point -- an agent that can only PLAN writes should
  still be able to show a file it already read. Live-verified this
  actually works under `plan`, the most restrictive default.

### Real bugs live verification caught (spec-reading alone would have missed all three)

This project's own standing rule (`TESTING.md`, `AGENTS.md`) is to verify
against the real thing, not trust the code reading itself as done. Three
real, independent bugs surfaced only by actually driving a real `claude`
CLI subprocess against a real running server -- each would have shipped
silently broken on spec-reading alone:

1. **`--mcp-config`'s JSON schema and CLI flag usage were right, but the
   registered server still failed to connect (`HTTP 405: ... Method Not
   Allowed`).** Root cause: FastAPI/Starlette does NOT automatically run
   a MOUNTED sub-app's own lifespan -- the MCP SDK's streamable-HTTP
   session manager needs its `run()` context manager entered to
   initialize an internal task group, and a request into a session
   manager whose task group never started raises exactly that class of
   error. Fixed by wrapping `app.router.lifespan_context` to also enter
   the mounted sub-app's own lifespan (see `mcp_server.py`'s
   `combined_lifespan`).
2. **Mount path / trailing slash.** A bare `/mcp` (no trailing slash)
   POST either 404s or 405s depending on exactly how the sub-app is
   wrapped -- Starlette's redirect-to-add-a-slash behavior only fires for
   GET/HEAD (redirecting a POST would silently drop its body), so it
   never rescues a POST the way it would a browser GET. Fixed by always
   using the trailing-slash URL (`.../mcp/`) end to end, sidestepping the
   redirect question entirely rather than relying on it.
3. **`show_document` as a plain `def` (not `async def`) crashed every
   real call**: `"There is no current event loop in thread 'AnyIO worker
   thread'"`. The SDK runs sync tool functions in an anyio worker thread
   (no event loop of its own); `WebEventForwarder`'s own broadcast path
   calls `asyncio.ensure_future()` internally, which needs one. `async
   def` keeps the tool on the main loop (the SDK awaits async tool
   functions directly, confirmed by reading `func_metadata.py`'s own
   `call_fn_with_arg_validation`).

Full path live-verified end to end (2026-08-16): a real `claude --print`
process, given the exact `--mcp-config`/`--settings` flags
`ClaudeCodeAdapter._ensure_extra_cli_flags()` builds, discovered and
called `show_document`, and a real subscriber on `/api/events/stream`
received the resulting `{"type": "artifact", ..., "artifact_path":
"hello.txt"}` SSE event -- the identical shape the frontend's existing
ARTIFACT handling already consumes.

### codex and opencode: not done this pass

JP asked for all three backends; only claude-code shipped and was
live-verified this pass, deliberately -- see this project's own
Change-Scope Discipline (`AGENTS.md`): ship a coherent, verified slice,
name the rest rather than guess at it unverified.

- **codex**: plausible via its own `-c mcp_servers.<name>.command=...`-
  style dotted config overrides (the SAME mechanism
  `adapters/codex.py`'s `_permission_config_args` already uses for other
  settings, confirmed live 2026-07-20 that spawn-time `-c` wins over
  `config.toml`) -- but registering an MCP server this way is unverified
  against the real CLI. Given how many claude-code assumptions THIS pass
  turned out wrong only once actually tested (all three bugs above),
  codex needs the same live-verification treatment, not a port of this
  code assumed correct by analogy.
- **opencode**: structurally harder, not just unverified. ConvoBox
  doesn't spawn opencode at all -- `OpenCodeAdapter` connects via
  HTTP/SSE to an already-running `opencode serve` instance
  (`adapters/__init__.py`'s own comment: "opencode is a pre-launched HTTP
  server... both its permissions and its directory are fixed by wherever
  `opencode serve` was started"). There is no per-session config surface
  this adapter controls to inject an ad-hoc MCP server into the way
  `--mcp-config` does for claude-code; registering one would mean
  either the user's own persistent opencode config (outside ConvoBox's
  reach) or a deeper investigation into opencode's own plugin/tool
  system that hasn't happened yet.

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
