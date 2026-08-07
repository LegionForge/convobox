---
title: opencode's file.edited event exists and is trivial to parse, but it isn't on the session event stream ConvoBox's adapter already subscribes to
status: diagnosed
date: 2026-08-07
project: ConvoBox (github.com/LegionForge/convobox)
versions: opencode 1.18.13 (OpenAPI 3.1 spec fetched live via GET /doc)
evidence:
  - opencode serve --port 4097 (local, this machine) + GET /doc -- OpenAPI spec fetched live, no prompt sent, no LLM call made
  - src/convobox/adapters/opencode.py -- existing SessionDurableEvent-based event parsing this finding scopes a change against
  - docs/KNOWN-ISSUES.md, "Web UI: artifact pane gaps (0.3.0)" -- the gap this narrows (not yet closes)
  - OPENCODE_API_NOTES.md -- prior live investigation this one extends (session-event union, 2026-07-10 pass)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, autonomous-loop session owner)
    - Claude Code (Anthropic claude-sonnet-5) -- investigation and writing, no code changes
  org: https://legionforge.org
  created: 2026-08-07T07:15:00-05:00
  revised: 2026-08-07T07:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# opencode's file.edited event exists and is trivial to parse, but it isn't on the session event stream ConvoBox's adapter already subscribes to

**Context for outsiders:** ConvoBox is a voice interface in front of
coding-agent CLIs. Its web UI has an "artifact pane" that renders files an
agent writes (images, HTML, etc). Only the Claude Code adapter wires this up
today; opencode's half of the gap was previously undiagnosed --
`docs/KNOWN-ISSUES.md` said opencode's `file.edited` event "path format
hasn't been live-verified yet (blocks wiring it up)," with no further
detail.

## Problem

Is opencode's `file.edited` event a straightforward port of the pattern used
for Claude Code (`Write`/`Edit` tool_use) or Codex (`fileChange` items, see
the sibling 2026-08-07 field note), or does it need something different?

## Evidence

Ran `opencode serve --port 4097` locally (v1.18.13, already installed on
this machine) and fetched `GET /doc` -- opencode's own live OpenAPI 3.1
spec. **No prompt was sent and no LLM call was made** -- this is a static
schema fetch, the same evidence tier as codex's
`generate-json-schema`, not a live agent run.

**The event exists and its payload is simple.** `components.schemas` has
`FileEdited` / `EventFileEdited`:

```json
{
  "type": "object",
  "properties": {
    "type": {"enum": ["file.edited"]},
    "data": {
      "type": "object",
      "properties": {"file": {"type": "string"}},
      "required": ["file"],
      "additionalProperties": false
    }
  }
}
```

Just a bare file path. No status/confirmation field (contrast codex's
`fileChange`, which has `inProgress`/`completed`/`failed`/`declined`) -- if
this event reached the adapter, filtering it to `ARTIFACT_MEDIA_TYPES` +
`working_dir`-fencing would be nearly identical to the existing
`_resolve_artifact_path` helpers, likely simpler than codex's version since
there's no status check needed.

**It does not arrive on the stream the adapter already listens to.** The
OpenAPI spec documents four separate event-stream endpoints:

| Endpoint | Event union | Members |
|---|---|---|
| `GET /api/session/{sessionID}/event` (**what `OpenCodeAdapter.events()` uses today**) | `SessionDurableEvent` | 28 -- all `SessionNext*` (prompted/step/tool/text/reasoning/compaction/revert lifecycle) |
| `GET /event` | `Event` | 89, includes `EventFileEdited` |
| `GET /api/event` | `V2Event` | 88, includes `FileEdited` |
| `GET /global/event` | `GlobalEvent` | wraps a payload union with `directory`/`project`/`workspace` fields |

Enumerated `SessionDurableEvent`'s all 28 members directly from the schema
(`anyOf` refs): `SessionNextAgentSwitched`, `SessionNextModelSwitched`,
`SessionNextMoved`, `SessionNextPrompted`, `SessionNextPromptAdmitted`,
`SessionNextContextUpdated`, `SessionNextSynthetic`,
`SessionNextShellStarted`, `SessionNextShellEnded`,
`SessionNextStepStarted`, `SessionNextStepEnded`, `SessionNextStepFailed`,
`SessionNextTextStarted`, `SessionNextTextEnded`,
`SessionNextToolInputStarted`, `SessionNextToolInputEnded`,
`SessionNextToolCalled`, `SessionNextToolProgress`,
`SessionNextToolSuccess`, `SessionNextToolFailed`,
`SessionNextReasoningStarted`, `SessionNextReasoningEnded`,
`SessionNextRetried`, `SessionNextCompactionStarted`,
`SessionNextCompactionEnded`, `SessionNextRevertStaged`,
`SessionNextRevertCleared`, `SessionNextRevertCommitted`. No file-edit
member anywhere in this list -- matches `OpenCodeAdapter`'s own docstring
count ("~28-member discriminated union") exactly, confirming this is the
real, current union, not a stale/cached spec.

`file.edited` only appears in `Event` and `V2Event` -- the **global** event
streams (`/event`, `/api/event`), not session-scoped ones. `V2Event` is the
more likely target given `OpenCodeAdapter` already prefers the versioned
`/api/` surface elsewhere (per its own docstring's reasoning).

## Mechanism

This isn't a payload-format question, which is what the old KNOWN-ISSUES.md
wording implied ("path format hasn't been live-verified") -- it's an
architecture question. `OpenCodeAdapter.events()` runs exactly one SSE
subscription today, scoped to one session ID, and yields `BackendEvent`s
from it. To surface `file.edited`, the adapter would need a SECOND,
concurrent SSE subscription to a global endpoint, merged into the same
output stream `Orchestrator` consumes -- a structurally different shape
from "add a new `if event_type == ...` branch to `_to_backend_event`",
which is all the codex and Claude Code wiring needed.

There's also an open correlation question the schema alone can't answer:
`file.edited`'s own `data` has no session ID, just a bare path. If a
ConvoBox instance's `opencode serve` process ever has more than one
concurrent session, a global-stream file edit can't be attributed to a
specific one from this payload alone. `GlobalEvent`'s envelope (wrapping
`V2Event`/`Event` payloads over `/global/event` specifically, not
confirmed to also wrap `/api/event`'s stream) carries
`directory`/`project`/`workspace` fields that might be sufficient scoping
if ConvoBox's own usage pattern is "one opencode server process per
adapter instance, one project directory" (plausible, not confirmed) --
this needs a design decision, not just a code change.

## What transfers

- **diagnosed**: opencode 1.18.13's `file.edited` payload is
  `{data: {file: <path>}}`, no status field.
- **diagnosed**: `file.edited` is absent from the 28-member
  `SessionDurableEvent` union `OpenCodeAdapter` currently subscribes to; it
  only exists on the global `/event`/`/api/event` streams.
- **hypothesis**: `GlobalEvent`'s `directory`/`project`/`workspace` fields
  are sufficient to correlate a global file-edit to "this adapter's own
  server" without needing a session ID -- plausible given this project's
  likely one-server-per-adapter usage shape, not verified against a real
  multi-session scenario.
- **A schema fetch alone can answer a scoping/architecture question, not
  just a payload-format one -- worth checking the *transport* an event
  arrives over, not just its shape, before estimating how big a wiring
  job actually is.** (diagnosed, this instance: assumed to be a payload
  question going in, turned out to be a stream-topology question)
- **Not done, deliberately**: no code changes. The right next step is a
  short design note (worth its own doc, given the two-subscription
  question) before implementation -- porting the codex pattern blindly
  (a single new `if` branch) would not work here.
