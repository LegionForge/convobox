# OpenCode API: what `OpenCodeAdapter` assumes vs. what's real

## Status: documented, not yet acted on

`src/convobox/adapters/opencode.py` has **not been changed** based on this
document. This is a precise record of a real discrepancy found by testing
against an actual `opencode` server, kept separate from the adapter so the
finding isn't lost — implementing the fix is a deliberately parked,
separate piece of work.

## How this was found

`OpenCodeAdapter` was built by porting the HTTP+SSE API shape documented in
an *earlier, related* TypeScript project (`voice-opencode`) — see
`README.md` → "Lessons from an earlier attempt." That project's own
documentation was the only source available at the time; no real OpenCode
server had been run against it.

On 2026-07-10, `opencode` (v1.17.15) was installed via `brew install
opencode` and run locally (`opencode serve --port 4096`). It exposes a
live OpenAPI 3.1 spec at `GET /doc`, which was fetched and compared
directly against `OpenCodeAdapter`'s assumptions, plus a few endpoints were
called for real (`POST /session`) to confirm response shapes empirically,
not just from the spec.

## Summary: assumed vs. real

| | `OpenCodeAdapter` assumes | Real API (v1.17.15) |
|---|---|---|
| Create session | `POST /api/sessions` | `POST /session` (unversioned) **or** `POST /api/session` (versioned v2 surface — both exist, see below) |
| Post message | `POST /api/sessions/:id/messages`<br>body: `{"messages": [{"role": "user", "content": "..."}]}` | `POST /session/:id/message`<br>body: `{"parts": [{"type": "text", "text": "..."}]}` (a `parts` array of typed content — text/file/agent/subtask parts), plus optional `model: {providerID, modelID}`, `agent`, `system`, `tools`, `format`, `variant` |
| Event stream | `GET /api/sessions/:id/events`<br>raw `{type, content, tool, toolInput, toolOutput}` JSON per SSE frame | `GET /api/session/:id/event` (note: versioned `/api/` prefix here specifically)<br>SSE frames have `{id, event, data}`, where `data` is a JSON-encoded `SessionDurableEvent` — a **discriminated union of ~28 real event types** (see below), not a flat 5-value enum |
| Hard stop / cancel | **Claimed not to exist** — `send_hard_stop`'s docstring says "OpenCode's documented HTTP API exposes no cancel endpoint" | **`POST /session/:id/abort` exists.** Documented summary: "Abort an active session and stop any ongoing AI processing or command execution." This directly contradicts the shipped docstring. |

The create-session response shape we already assumed (`{"id": "..."}`)
happens to be compatible — a real `POST /session` returned
`{"id": "ses_...", "slug": "...", "projectID": "...", "directory": "...", "cost": 0, "tokens": {...}, "title": "...", "version": "...", "time": {...}}`,
and `resp.json()["id"]` (what `OpenCodeAdapter._ensure_session` actually
reads) works fine against it. That's the *only* part of the current
implementation that would work unmodified against the real server.

## The real event taxonomy

Our `BackendEventType` enum has 5 values: `text | tool_call | tool_result |
error | done`. The real `SessionDurableEvent` schema is a discriminated
union of these (grouped by what they roughly correspond to):

- **Session-level:** `SessionIdle` (this is almost certainly the real
  analog of our `done` — the busy→idle transition our `is_busy()` tracking
  needs), `SessionNextAgentSwitched`, `SessionNextModelSwitched`,
  `SessionNextMoved`, `SessionNextPrompted`, `SessionNextPromptAdmitted`,
  `SessionNextContextUpdated`, `SessionNextSynthetic`, `SessionNextRetried`
- **Text (streaming):** `SessionNextTextStarted`, `SessionNextTextDelta`
  (incremental chunks — real streaming within one logical message, not
  just one-shot), `SessionNextTextEnded`
- **Reasoning (streaming):** `SessionNextReasoningStarted`,
  `SessionNextReasoningDelta`, `SessionNextReasoningEnded`
- **Tool calls (streaming input too):** `SessionNextToolInputStarted`,
  `SessionNextToolInputDelta`, `SessionNextToolInputEnded`,
  `SessionNextToolCalled`, `SessionNextToolProgress`,
  `SessionNextToolSuccess`, `SessionNextToolFailed`
- **Steps / shell / compaction / revert:** `SessionNextStepStarted/Ended/Failed`,
  `SessionNextShellStarted/Ended`, `SessionNextCompactionStarted/Delta/Ended`,
  `SessionNextRevertStaged/Cleared/Committed`

This is a materially richer model than ours — notably, text and tool input
both stream incrementally (`*Delta` events), which our flat `content: str
| None` field on `BackendEvent` can't represent without changes.

## Two API surfaces — undecided which to target

The real server exposes **both** an unversioned surface (`/session`,
`/session/:id/message`, `/session/:id/abort` — what was probed above) and
a versioned one (`/api/session/:id/event` was explicitly tagged
`v2.session.events` in the spec; there's a parallel `/api/session` GET/POST
too). Which one is the intended long-term integration surface for external
tools wasn't determined — that needs investigation (read OpenCode's own
docs/changelog, check which surface their own TUI/web client uses) before
implementing against either, so the adapter doesn't end up pinned to
something OpenCode considers legacy.

## What this means for `OpenCodeAdapter` today

The adapter as shipped **will not work** against a real `opencode serve`
instance — every request would 404 or fail schema validation except
session creation. The existing tests (`tests/test_opencode_adapter.py`)
only validate against an in-repo fake server that mirrors the *assumed*
(wrong) API shape, so they pass without catching this — that's a known
limitation of testing against a hand-built fake rather than the real
service, not a bug in the tests themselves.

**Places that asserted the false "no cancel endpoint" claim — all now
carry a correction pointing back to this file, not silently left wrong:**
- `src/convobox/adapters/opencode.py` — `send_hard_stop`'s docstring
- `src/convobox/adapters/base.py` — the `BackendAdapter` class docstring's
  honesty clause, written referencing this exact case
- `.tours/03-extension-points-modularity.tour` — three steps: the
  `BackendAdapter` extension-point step, the `OpenCodeAdapter`
  reference-implementation step (this one also had the wrong endpoint
  paths presented as fact — corrected to say so explicitly), and the
  closing "adding a new adapter" checklist

## If/when this gets implemented

Rough shape of the work, for whenever it's picked up:
1. Decide unversioned vs. versioned API surface (see above).
2. Rewrite `_post_message` for the `parts`-array body shape.
3. Rewrite `_parse_event`/`BackendEventType` for the real discriminated
   union — likely needs `TextDelta`-style incremental content, not just
   one-shot `content: str`.
4. Map `SessionIdle` (or whatever the chosen surface's real equivalent is)
   to clearing `is_busy()`, replacing the current `DONE`/`ERROR` check.
5. Implement `send_hard_stop` for real via `POST /session/:id/abort`,
   removing the false docstring claim.
6. Either replace or add to the existing fake-server tests
   (`tests/test_opencode_adapter.py`) so they validate against the *real*
   shape — and ideally, re-run against a real local `opencode serve`
   instance (`brew install opencode`) as a final check, the way this
   investigation did.
