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

### Second subscription

Add a second `async for` loop over `GET /api/event` (the versioned `V2Event`
union, matching this adapter's existing preference for the `/api/` surface
over the unversioned one -- see `OpenCodeAdapter`'s own class docstring).
Run it as a second task alongside the existing session-event consumer,
merging both into the single `BackendEvent` stream `events()` yields --
the same shape `Orchestrator` already expects from every adapter, so no
caller-side change needed.

Concretely: `events()` currently is a single `async for sse in
sse_source.aiter_sse()` loop. The cleanest fix is two independent
`asyncio.Task`s each feeding a shared `asyncio.Queue[BackendEvent]`, with
`events()` becoming `while True: yield await queue.get()`. Both tasks need
independent lifecycle management mirroring what `_close_sse`/`aclose`
already do for the session stream -- the global subscription needs the
exact same "never teardown from inside the consumer's own task" care the
existing docstring already documents for hard-stop (`send_hard_stop`'s
comment on why it doesn't touch `_sse_context` directly).

### Correlation: working_dir fencing, not session-ID matching

`file.edited`'s payload has no session ID -- there is no clean way to say
"this edit came from MY session" from the event alone. Proposed mitigation:
**apply the exact same `working_dir` fencing `ClaudeCodeAdapter` and
`CodexAdapter` already use for their own artifact resolution** -- if the
edited path doesn't resolve inside `backend.working_dir`, drop the event.
This doesn't perfectly solve multi-session attribution (a second, unrelated
opencode session editing files in the SAME working_dir would still leak
through), but:

- It's the same trust boundary already relied on everywhere else in this
  feature (`ARTIFACT_MEDIA_TYPES` + working_dir fencing is the existing
  security model for the whole artifact pane, not new-risk surface).
- ConvoBox's own usage shape today is one `opencode serve` process per
  adapter instance, one working_dir -- the multi-session leak scenario is
  a real edge case, not the common path.
- It requires zero new plumbing (the fencing helper pattern already
  exists twice; a third copy is consistent, not novel).

**Open question for JP, not decided here:** is working_dir fencing good
enough, or does this need to wait for something better (e.g. does opencode
expose a way to scope `/api/event` to one session/project -- unconfirmed,
would need re-checking the OpenAPI spec's query parameters for that
endpoint specifically, not done in this pass)?

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

- **Slice 1 (safe, read-only):** add the second subscription, log every
  `file.edited` event that survives the `working_dir` fence at INFO level,
  do NOT yield an `ARTIFACT` event yet. Confirms the subscription mechanics
  and the fencing decision live before it can affect the UI.
- **Slice 2:** yield the actual `ARTIFACT` event, same as codex's PR #219 --
  schema-verified via a fake-server unit test first (extend
  `tests/test_opencode_adapter.py`'s `OpenCodeServer` harness with a
  `GET /api/event` route), live-verified against a real `opencode serve`
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
