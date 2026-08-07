# Design: opencode artifact-pane wiring (file.edited)

Status: DESIGN RECORDED 2026-08-07, not implemented. Origin: closing
opencode's half of the artifact-pane gap `docs/KNOWN-ISSUES.md` tracks
("Web UI: artifact pane gaps") -- codex's half shipped schema-verified in
PR #219; this is the harder of the two remaining backends (Claude Code
already ships this).

## Why this needs a design note and codex's fix didn't

Codex's `fileChange` items arrive on the same JSON-RPC stream
`CodexAdapter` already parses -- wiring it was one new `if` branch. opencode
is structurally different, confirmed against opencode 1.18.13's live
OpenAPI spec (`GET /doc`, fetched via a local `opencode serve`, no prompt
sent, no LLM call -- see
`docs/field-notes/2026-08-07-opencode-artifact-pane-file-edited-is-a-global-event.md`
for the full evidence):

- `OpenCodeAdapter.events()` subscribes to `GET
  /api/session/{sessionID}/event`, whose SSE payload is `SessionDurableEvent`
  -- a 28-member union of `SessionNext*` lifecycle events. `file.edited` is
  not a member.
- `file.edited` only exists on the **global** `/event` (`Event`, 89
  members) and `/api/event` (`V2Event`, 88 members) streams -- not scoped
  to any one session.
- The payload itself is trivial: `{data: {file: <path string>}}`, no
  status field, no session ID.

So this is a two-part problem: (1) run a second, concurrent SSE
subscription alongside the existing one, and (2) decide how to attribute a
global file-edit to "this adapter's own session" when the payload carries
no session ID at all.

## Proposed shape

### Second subscription -- revised 2026-08-07, later same-day re-check

**Correction to the original version of this note:** `/api/event`
(`V2Event`) is NOT actually the better endpoint here, despite matching
this adapter's usual `/api/` preference. Re-checked the OpenAPI spec's
`parameters` for all three candidate endpoints:

| Endpoint | Query params |
|---|---|
| `/api/event` (`V2Event`) | none |
| `/global/event` (`GlobalEvent`) | none |
| `/event` (`Event`, the OLD unversioned surface) | **`directory` (optional), `workspace` (optional)** |

Only the unversioned `/event` endpoint accepts a `directory`/`workspace`
query parameter -- neither current-gen endpoint does. If `directory`
actually scopes the stream server-side, that's a categorically better
correlation mechanism than anything client-side (see below) -- but the
spec gives no parameter description ("Subscribe to events" is the whole
docstring), so **whether it actually filters, or is accepted and ignored,
is unconfirmed from the schema alone.** This needs a real live check
(subscribe with and without `?directory=...` against a real `opencode
serve` instance with two different working directories, confirm which
events each connection receives) before it can be trusted -- not
something a schema fetch can answer. Do this check FIRST, before writing
the subscription code -- if `directory` genuinely filters, it replaces
the working-dir-fencing plan below entirely (a real architectural
simplification, not just a nice-to-have), and the choice of endpoint
changes from `/api/event` to `/event`.

Add a second `async for` loop over whichever endpoint the live check
above confirms (`/event?directory=...` if it filters; `/api/event`
unfiltered + client-side fencing otherwise). Run it as a second task
alongside the existing session-event consumer, merging both into the
single `BackendEvent` stream `events()` yields -- the same shape
`Orchestrator` already expects from every adapter, so no caller-side
change needed.

Concretely: `events()` currently is a single `async for sse in
sse_source.aiter_sse()` loop. The cleanest fix is two independent
`asyncio.Task`s each feeding a shared `asyncio.Queue[BackendEvent]`, with
`events()` becoming `while True: yield await queue.get()`. Both tasks need
independent lifecycle management mirroring what `_close_sse`/`aclose`
already do for the session stream -- the global subscription needs the
exact same "never teardown from inside the consumer's own task" care the
existing docstring already documents for hard-stop (`send_hard_stop`'s
comment on why it doesn't touch `_sse_context` directly).

### Correlation: working_dir fencing (fallback), or the `directory` query param if it actually filters

`file.edited`'s payload has no session ID -- there is no clean way to say
"this edit came from MY session" from the event alone, other than the
`/event?directory=...` possibility above. Fallback proposed mitigation if
that doesn't pan out: **apply the exact same `working_dir` fencing
`ClaudeCodeAdapter` and `CodexAdapter` already use for their own artifact
resolution** -- if the edited path doesn't resolve inside
`backend.working_dir`, drop the event. This doesn't perfectly solve
multi-session attribution (a second, unrelated opencode session editing
files in the SAME working_dir would still leak through), but:

- It's the same trust boundary already relied on everywhere else in this
  feature (`ARTIFACT_MEDIA_TYPES` + working_dir fencing is the existing
  security model for the whole artifact pane, not new-risk surface). Fails
  SAFE, not open: worst case is a real artifact silently not showing, never
  showing something from the wrong session.
- ConvoBox's own usage shape today is one `opencode serve` process per
  adapter instance, one working_dir -- the multi-session leak scenario is
  a real edge case, not the common path.
- It requires zero new plumbing (the fencing helper pattern already
  exists twice; a third copy is consistent, not novel).

**A real, separate wrinkle found re-reading `create_backend_adapter`
(`src/convobox/adapters/__init__.py`), not previously accounted for in
this note's first draft:** `OpenCodeAdapter` is constructed WITHOUT
`config.working_dir` today, on purpose --

```python
if config.name == "opencode":
    # Neither permission_mode nor working_dir is passed: opencode is a
    # pre-launched HTTP server, not a subprocess ConvoBox spawns, so
    # both its permissions and its directory are fixed by wherever
    # `opencode serve` was started.
    return OpenCodeAdapter(config.url, model=config.model)
```

So even after adding a `working_dir` param to `OpenCodeAdapter.__init__`
for fencing, `config.working_dir` (ConvoBox's own config value) is not
guaranteed to match wherever the user's already-running `opencode serve`
process actually is -- ConvoBox has no way to know or enforce that
directory for a pre-launched server the way it does for Claude
Code/codex, which it spawns itself. Passing `config.working_dir` through
anyway and fencing against it is still SAFE (fails closed: a mismatch
just means artifacts silently don't appear, not a leak), but it's worth
being explicit that this is a real, known limitation, not an oversight --
name it in the PR/commit when this gets built, don't let it look like a
solved problem.

**Open question for JP, not decided here:** if the live check above shows
`/event`'s `directory` param does NOT actually filter (accepted but
ignored), is working_dir fencing alone good enough to ship, given the
known pre-launched-server limitation just described -- or does that
combination (imperfect fence + a config value not guaranteed to match
the real server directory) push this from "worth shipping with caveats"
to "wait for opencode to expose something better"? Real judgment call,
not a technical question this note can resolve alone.

### Payload parsing

Trivial once the event reaches the parser -- simpler than codex's version
since there's no status field to check:

```python
if event_type == "file.edited":
    file_path = payload.get("file")
    if isinstance(file_path, str) and Path(file_path).suffix.lower() in ARTIFACT_MEDIA_TYPES:
        artifact_path = self._resolve_artifact_path(file_path)  # same helper shape as codex/claude_code
        if artifact_path is not None:
            yield BackendEvent(type=BackendEventType.ARTIFACT, artifact_path=artifact_path)
```

## Slicing

- **Slice 0 (do this first, before writing any adapter code):** live-check
  whether `/event?directory=<path>` actually filters the stream, by
  opening two connections (one with a `directory` matching a test file's
  location, one without / with a different one) against a real `opencode
  serve` and comparing which `file.edited` events each receives -- a real
  file-write is still needed to generate the event, so this can piggyback
  on Slice 2's own live-verification step below rather than being fully
  separate work, but the QUESTION needs answering before Slice 1's fencing
  approach is even decided, not after.
- **Slice 1 (safe, read-only):** add the second subscription (endpoint
  chosen per Slice 0's finding), log every `file.edited` event that
  survives the correlation check (server-side `directory` filter if
  confirmed working, `working_dir` fencing otherwise) at INFO level, do
  NOT yield an `ARTIFACT` event yet. Confirms the subscription mechanics
  and the correlation decision live before it can affect the UI.
- **Slice 2:** yield the actual `ARTIFACT` event, same as codex's PR #219 --
  schema-verified via a fake-server unit test first (extend
  `tests/test_opencode_adapter.py`'s `OpenCodeServer` harness with the
  chosen endpoint's route), live-verified against a real `opencode serve`
  + a real file-write prompt before calling this done (unlike PR #219,
  which shipped schema-only -- this one has a cheap live-verification path
  since no paid LLM call is strictly required to test the SSE wiring
  itself, only to trigger a real `file.edited`; worth doing before
  merging, not deferring like codex's did).

## What this note deliberately does not do

No code was written. `tests/test_opencode_adapter.py`'s `OpenCodeServer`
fake-server harness would need a second route
(`GET /api/event`) added before Slice 1 is even unit-testable -- a
reasonably small addition given the harness already exists, but real work,
not attempted here. The two-task/shared-queue restructuring of `events()`
touches a method every existing opencode test already exercises -- worth
doing carefully with the full existing suite green throughout, not as a
rushed single-session change.
