# OpenCode API: what `OpenCodeAdapter` assumed vs. what's real

## Status: implemented and re-verified live (2026-07-11)

`src/convobox/adapters/opencode.py` has been rewritten against the `/api/`
(versioned) surface described below. Everything under "If/when this gets
implemented" was done: session creation, `prompt`'s `delivery` field
(`steer`=interject, `queue`=fresh command), `POST .../interrupt` for a real
hard stop, and the real SSE event shape. The "two API surfaces — undecided
which to target" question is now decided: **`/api/`**, because empirically
it's the *only* surface with an event stream at all (`/session` has none).

**Corrections to this document's own original findings**, found re-running
the same live-verification methodology against `opencode` v1.17.18 (this
doc's original pass used v1.17.15) on 2026-07-11:

- **`PromptInput` is simpler than documented below.** A live `POST
  /api/session/:id/prompt` body is `{"prompt": {"text": "..."}, "delivery":
  "steer"|"queue"}` — not the `parts`-array-of-typed-content shape the
  original table describes. Whether that's a real API change between
  patch versions or this doc's original research conflated it with a
  different endpoint wasn't determined; what's in the adapter now is what a
  real request against v1.17.18 was confirmed to accept and be admitted
  (`SessionInputAdmitted` response, `admittedSeq` present).
- **A `session.idle`-equivalent event does not reliably fire.** This
  doc's "real event taxonomy" section lists `SessionIdle` as part of the
  schema, and it likely exists — but two full live runs (a single-step
  text reply, and a multi-step tool-calling one) never produced it or
  anything equivalent, even well after the response was visibly complete.
  `is_busy()` clearing does **not** depend on it; see "How is_busy() is
  actually tracked" below.
- **`POST /api/session/:id/wait` was tried, then abandoned — dead end,
  kept here so it isn't re-discovered the hard way.** "Wait for a session
  agent loop to become idle" sounds like exactly the right mechanism, and
  an early version of this adapter used it (background task, cleared
  `is_busy()` on resolution). Two real, live-confirmed problems killed it:
  1. Called immediately after posting a prompt, it reliably 503s with
     `{"_tag": "ServiceUnavailableError", "message": "Session wait is not
     available yet", "service": "session.wait"}`. A short retry helps for
     slower responses, but for a *fast* one (a single word, done in well
     under a second) the session can go idle again before the retry
     window elapses, so the SAME 503 can mean either "not ready yet" or
     "already finished" — genuinely ambiguous from the response alone.
  2. **Far more serious, and the actual reason it's not used at all:** a
     concurrent `POST .../wait` while this adapter's own SSE
     `GET .../event` connection is open silently kills that connection's
     event delivery. Confirmed repeatedly: with `/wait` active, 0 events
     ever arrive over several real runs (despite the server genuinely
     emitting them — confirmed via a raw, wait-free SSE connection
     receiving them fine); with `/wait` fully disabled, events arrive
     reliably every time. Since `Orchestrator` always keeps `events()`
     running continuously (`start_event_loop()` runs before any `send_*`,
     see `orchestrator.py`), `/wait` would ALWAYS be racing an active SSE
     subscription in real usage — this isn't an edge case to route around,
     it's incompatible with how the adapter is actually used. Whatever the
     exact server-side mechanism is (single event-subscriber slot per
     session, internal lock shared between the wait and broadcast paths,
     something else) wasn't determined and doesn't need to be to know not
     to use it here.

## How is_busy() is actually tracked

Confirmed empirically, not assumed: `step.ended` only means one step of
the agent loop finished, not that the loop is done — a real multi-step
tool-calling response (see the trace below) fires `step.started` →
`tool.input.started` → `tool.input.ended` → `tool.called` → `tool.success`
→ `step.ended`, and the session was **not** actually idle at that point
(the assistant still owed the user a text summary of the tool result).
Relying on any single `step.ended` alone would clear `is_busy()` too early.

The fix that actually works, found by reading what `step.ended` carries
rather than reaching for another endpoint: **`step.ended`'s own `finish`
field tells you whether more is coming.** Confirmed on the same live
multi-step trace: the tool-calling step's `step.ended` carries
`"finish": "tool-calls"` (more work queued), while a genuinely final
step's carries `"finish": "stop"`. `OpenCodeAdapter._track_busy` treats
`"tool-calls"` as the one confirmed "definitely continuing" value and
everything else (including `"stop"` and any future/unrecognized value) as
terminal — deliberately an allowlist of the continuing case, not a
denylist of terminal ones, since the OpenAPI spec types `finish` as a bare
string with no enum and the failure modes aren't symmetric: an unknown
value wrongly treated as terminal just means a later utterance gets
queued instead of steered (harmless — `delivery="queue"` waits behind
current work); the opposite mistake (an unknown value wrongly treated as
"still going") blocks the user outright if that step turns out to be the
last one. `events()` also keeps a last-resort fallback (clear `_busy` when
the SSE stream ends for any reason, e.g. a dropped connection) as a safety
net under this, carried over from the original adapter's own regression
test for the same failure shape.

This requires **no extra HTTP call at all** — it's read straight off the
event stream `events()` is already consuming — which is also exactly why
it doesn't hit the `/wait` interference problem above.

## A second real bug, found verifying the fix above: httpx's default read timeout

`aconnect_sse` inherits `self._client`'s default `httpx.Timeout(5.0)`
unless overridden. That's fine for the short request/response calls
elsewhere in this class, but wrong for the SSE connection specifically:
confirmed live, a real multi-step tool-calling response's total time
varied 8.6s–13.5s across three separate runs against the same prompt (tool
execution + model latency, not anything wrong) — comfortably past 5s of
gap between some frames. Without a fix, `httpx.ReadTimeout` killed the
connection mid-response on a real, unremarkable request, not an edge
case. Fixed by passing `timeout=httpx.Timeout(5.0, read=None)` to the
`aconnect_sse(...)` call specifically (kwargs forward through to the
underlying `client.stream()`) — connect/write/pool keep the default (a
genuinely unreachable server should still fail fast), only the read side,
which has no legitimate upper bound for a long-lived stream, is disabled.

## Live trace: single-step text reply (v1.17.18)

Prompt: `"Say the word banana and nothing else."`, `delivery: "queue"`.
Real SSE event sequence, in order:
`prompt.admitted` → `prompted` → `step.started` → `text.started` →
`text.ended` (full text `"banana"` inline, no `text.delta` events for a
reply this short) → `step.ended` (`finish: "stop"`). Stream then went
silent — no idle-equivalent event within the observation window.

## Live trace: multi-step tool-calling reply (v1.17.18)

Prompt: `"List the files in the current directory (read-only, do not
modify anything), then tell me how many there are."`, `delivery: "queue"`.
Real SSE event sequence captured (25s window, cut off before the session
actually finished — itself evidence step.ended isn't a completion signal):
`prompt.admitted` → `prompted` → `step.started` → `tool.input.started`
(`name: "read"`) → `tool.input.ended` (`text: '{"path": "..."}'`) →
`tool.called` (`tool: "read"`, `input: {"path": "..."}`) → `tool.success`
(`structured: {"entries": [...], "truncated": false}`) → `step.ended`.
This confirms the real `tool.called`/`tool.success` payload shapes used in
`_parse_event`: `data.tool`, `data.input` (called), `data.structured` /
`data.content` (success) — none of which matched a guess, they're read
directly off this trace.

---

This document is kept as the detailed record of the investigation; the
adapter's own docstring and inline comments cite back to it for anyone
verifying the fix or re-investigating after a future OpenCode API change.

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

## What this meant for `OpenCodeAdapter` (fixed 2026-07-11)

The adapter as originally shipped **did not work** against a real
`opencode serve` instance — every request would 404 or fail schema
validation except session creation. The old tests
(`tests/test_opencode_adapter.py`) only validated against an in-repo fake
server that mirrored the *assumed* (wrong) API shape, so they passed
without catching this — a known limitation of testing against a hand-built
fake rather than the real service, not a bug in the tests themselves. The
rewritten adapter and its rewritten fake-server tests both target the real
`/api/` shape documented above; see "How is_busy() is actually tracked"
and the two live traces for exactly what changed and why.

**Places that asserted the false "no cancel endpoint" claim — corrected,
not silently left wrong:**
- `src/convobox/adapters/opencode.py` — `send_hard_stop` now calls the
  real `POST /api/session/:id/interrupt`
- `src/convobox/adapters/base.py` — the `BackendAdapter` class docstring's
  honesty clause, updated to note the fix landed
- `.tours/03-extension-points-modularity.tour` — three steps still
  reference this as the cautionary example (see `base.py`'s docstring for
  why that's worth keeping even after the fix)

## What was actually implemented

1. Versioned (`/api/`) surface — see "decided" note at the top.
2. `_post_prompt` uses the real `{"prompt": {"text": ...}, "delivery":
   "steer"|"queue"}` body (simpler than this doc's original `parts`-array
   guess — see "Corrections" above).
3. `_to_backend_event` maps the four real event types that have a slot in
   our `BackendEventType` model (`text.ended`, `tool.called`,
   `tool.success`, `tool.failed`); everything else is intentionally
   dropped, not missed.
4. `is_busy()` is cleared by `_track_busy` reading `step.ended`'s own
   `finish` field (terminal unless `"tool-calls"`), plus a last-resort
   fallback if the SSE stream ends for any other reason — see "How
   is_busy() is actually tracked" above. `POST .../wait` was tried first
   and abandoned; see why above, kept so it isn't re-discovered the hard
   way.
5. `send_hard_stop` calls `POST /api/session/:id/interrupt` for real.
6. `aconnect_sse(...)` gets an explicit `timeout=httpx.Timeout(5.0,
   read=None)` — see "A second real bug" above; without it a real,
   unremarkable multi-step response gets killed by httpx's default 5s
   read timeout.
7. Fake-server tests rewritten to the real shape, including a scripted
   `finish="tool-calls"` frame to prove `is_busy()` stays `True` through
   it; re-run against a real local `opencode serve` (v1.17.18, `scoop
   install opencode` on this Windows machine) as the final check,
   including the multi-step tool-calling case specifically, the same
   discipline this investigation used throughout.

## Three more real bugs, found on the first full Orchestrator-level live run (2026-07-11)

Everything above was verified with the adapter driven directly (bare
`send_text` + a manually-iterated `events()`). The first time the whole
`Orchestrator.handle_transcript()` path ran against a real server —
transcript in, TTS audio out — three new failures appeared that the
adapter-level runs could not have shown:

1. **`_ensure_session` raced itself and created two sessions.**
   `handle_transcript` spawns the event-consumer task and then immediately
   awaits the send; both reach `_ensure_session` while `_session_id` is
   still `None`, so each `POST /api/session` created its own session. The
   prompt landed in one, the SSE subscription in the other: **zero events
   ever delivered, `is_busy()` latched `True` forever** (observed as a
   90s idle-wait timeout with an empty event list). Fixed with an
   `asyncio.Lock` around session creation.

2. **`send_hard_stop` closed the SSE context from the wrong task.**
   The stream is owned by the task suspended inside `events()`'s
   `aiter_sse()`; driving its `__aexit__` from the hard-stop caller's task
   raised `RuntimeError: anext(): asynchronous generator is already
   running` — a crash *during the safety-critical abort path*. Fixed by
   not touching the SSE subscription at all in `send_hard_stop`: the
   session survives an interrupt, so the same subscription must keep
   serving whatever the user says next anyway.

3. **OpenCode holds SSE response headers back until the first event.**
   A `curl` to an idle session's `/event` endpoint received zero header
   bytes in 5s. So "response headers received" can never be the
   subscribe-before-send readiness signal against this server — a gate on
   it would burn its full timeout on every first send. The subscriber is
   registered when the GET *arrives*, long before headers flush, so
   `wait_listening()` (new, called by the Orchestrator between starting
   the event loop and posting the prompt) signals on "subscription request
   dispatched" instead, with a bounded timeout as fallback.

The pattern for the third time running: **each integration layer you run
for real for the first time finds bugs the layer below's tests were
structurally incapable of showing.** Adapter-level live runs couldn't
catch these because a single task drove everything; the Orchestrator's
concurrency is what exposed them.
