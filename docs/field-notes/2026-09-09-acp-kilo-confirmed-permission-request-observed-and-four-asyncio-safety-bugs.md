---
title: Closing the ACP adapter's three open unknowns (Kilo live-verified, a real permission request finally observed, a real tool failure triggered) surfaced four separate asyncio safety bugs, found via a fake-subprocess conformance suite and two independent LLM reviews
status: validated-live
date: 2026-09-09
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ PR #400 (src/convobox/adapters/acp.py); opencode 1.18.26 (`opencode acp`); kilo (`@kilocode/cli`) 7.5.6 (`kilo acp`, first live round-trip against this adapter -- prior passes only reached handshake/session-creation)
evidence:
  - PR #396 (2026-09-08T15:09:04Z) -- send_text() non-blocking fix, generalized model selection, permission_mode -> session/set_mode wiring, tool-failure text extraction fix, settings-UI wiring
  - PR #398 (2026-09-09T01:57:24Z) -- Kilo confirmed working end to end through the real adapter
  - PR #399 (2026-09-09T04:43:25Z) -- ACP added to the shared cross-adapter conformance suite; caught a real aclose() idempotency crash on first run
  - PR #400 (2026-09-09T05:58:51Z) -- hardening from an independent Opus review, then a second Opus review verifying that fix, which itself found one more real bug in the first review's own remedy
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (asked "are there corner cases we haven't considered?" and "have a more complex model investigate", which is what actually surfaced most of the findings below)
    - Claude Code (Anthropic claude-sonnet-5) -- implementation, live verification, writing
    - Claude Code subagent (Anthropic claude-opus-5, spawned twice) -- independent code review and independent verification-of-the-fix review
  org: https://legionforge.org
  created: 2026-09-09T06:10:00Z
  revised: 2026-09-09T06:10:00Z
license: CC BY 4.0 (intent; repo code MIT)
---

# Closing ACP's three open unknowns surfaced four asyncio safety bugs

**Context for outsiders:** ConvoBox is a voice-driven coding-agent frontend.
`src/convobox/adapters/acp.py` speaks the Agent Client Protocol (JSON-RPC
over stdio) to drive OpenCode or Kilo Code as a subprocess. An earlier
field note (`2026-09-06-acp-adapter-live-fixes-wrong-method-names-and-
notification-routing-bug.md`) fixed five wrong-method-name/routing bugs in
the adapter's first commit but left three things explicitly unverified:
Kilo specifically (only OpenCode had been live-tested), the real
`session/request_permission` response shape (it had never actually fired
in any live pass), and a real tool failure (the `status: "failed"` handling
was inferred from the "completed" case's sibling fields, never observed).
This note closes all three -- and documents four separate, more severe
bugs that surfaced along the way, none of which were suspected going in.

## Problem

Closing the three open items required deliberately triggering states the
adapter had never actually seen: a dead backend process, a backend that
asks permission instead of trusting by default, and a tool that fails.
Each of those states turned out to exercise error-handling and
cleanup code paths that had never been exercised at all -- and in an
`asyncio` JSON-RPC-over-stdio adapter, the failure mode for "this cleanup
path is wrong" is rarely a crash. It's usually a silent hang, a stuck flag,
or a lost event -- exactly the failure class hardest to catch with normal
testing and easiest to miss in code review.

## Evidence

**1. Kilo confirmed working end to end.** `kilo` 7.5.6 was already
installed but not authenticated with "Kilo Gateway" (its own hosted
model service); `kilo auth list` showed Inception + OpenRouter credentials
were configured instead. Running the exact same battery already used for
OpenCode -- through `create_backend_adapter()`, not raw probes --
confirmed: non-blocking `send_text()` (0.00s once a session exists),
`permission_mode: permissive` allows a real write, a real tool failure
produces the identical human-readable-text shape already confirmed for
OpenCode, and a fresh unconfigured session still defaults to
`kilo/google/gemini-3-pro-image` (an unauthenticated Kilo-Gateway model) --
confirming the model-selection bug fixed in this same pass (see finding 5)
is a live, current issue for Kilo, not a stale one from an old install.

**2. `session/request_permission`'s real response shape, finally
observed.** Neither backend's default posture asks for permission (both
are full-trust by default, confirmed again). It DOES fire when OpenCode's
own project config (`opencode.json` in the session's `cwd`) sets
`{"permission": {"edit": "ask", "bash": "ask"}}` -- a posture set entirely
on the agent's own side, invisible to and not reachable through this
adapter's own config surface at all. Triggering it live: the adapter's
existing auto-decline (`{"outcome": {"outcome": "cancelled"}}`, previously
a best-effort spec-shape guess) was accepted with no protocol error, and
the tool call it was for then correctly reported `status: "failed"` with
`"The user rejected permission to use this specific tool call."`

**3. A real tool failure, and a bug it exposed.** Prompting a `read` tool
against a nonexistent path produced the first genuine (not hand-built)
`tool_call_update` `status: "failed"` payload either backend had ever
produced in this project's testing:
```json
{"sessionUpdate": "tool_call_update", "status": "failed",
 "content": [{"type": "content", "content": {"type": "text",
              "text": "File not found: D:\\...\\this_file_does_not_exist.txt"}}],
 "rawOutput": {"error": "File not found: D:\\...\\this_file_does_not_exist.txt"}}
```
The adapter's inferred handling only read `rawOutput` (the raw JSON blob)
instead of `content` (the same human-readable text array the `"completed"`
case already preferred) -- a real bug, now fixed to share the same
extraction. A tool failure shouldn't read worse than its own success path.

**4. `send_text()` blocked the mic loop for the entire turn.** Unrelated
to the three open items above, found while live-verifying Kilo: ACP's
`session/prompt` response only resolves once the WHOLE turn completes
(unlike Codex's `turn/start`, which just acks). The adapter awaited it
inline, so `is_busy()` reported `False` for the entire turn duration --
measured live at 30+ seconds for a real prompt, vs. 0.00s after the fix
(dispatch as a background task, mirroring `codex.py`'s own `send_text`
structure). A mid-turn utterance during that window would have been routed
to a second concurrent `send_text()` instead of `send_interject()`.

**5. `permission_mode` was a complete no-op for ACP.** Stored in
`__init__`, never referenced anywhere else in the file --
`backend.permission_mode: plan` provided zero actual protection. Fixed:
`session/set_mode(sessionId, "plan")` (note `modeId`, not `mode` -- found
from a live `-32602 Invalid params` error's own field name) is now called
when `permission_mode == "plan"`, live-verified to genuinely block a real
write for OpenCode. **Kilo's own plan-mode enforcement works differently**:
OpenCode's model self-declines outright (a `TEXT` reply, zero `tool_call`
attempted); Kilo instead lets the model attempt the tool call, then
REJECTS it via an explicit rule-enforcement layer -- the `ERROR` event's
own text is a literal dump of Kilo's deny-rule list. Same practical
outcome, but this partially answers a question this project's own earlier
research had left open ("is plan mode a hard-enforced sandbox or just
model compliance") -- for Kilo specifically, it looks like a real enforced
permission engine, not prompt-level compliance.

**6. A real `aclose()` idempotency crash, caught by a fake-subprocess
conformance suite on its very first run.** `aclose()` never checked
`self._proc.returncode` before calling `.terminate()` again. Since
`force_kill()` has no override for this adapter and delegates straight to
`aclose()`, the shared cross-adapter test
`test_force_kill_then_later_aclose_is_idempotent_and_never_raises`
(`force_kill(); force_kill(); aclose(); aclose()` -- the exact sequence
`Orchestrator`'s kill-phrase/safeword escalation runs) crashed on Windows
with `ProcessLookupError` from asyncio's own `_check_proc()` the SECOND
time `.terminate()` was called on an already-dead process transport.
Fixed by clearing `self._proc` and checking `returncode` first, mirroring
`codex.py`'s own `_terminate_and_kill_process()`.

**7. A dead backend process left `is_busy()` stuck `True` forever, and
its own error event usually lost entirely.** `_read_loop`'s `finally`
block only pushed an EOF sentinel -- it never rejected whatever requests
were still pending, nor cleared the busy flag. A backend dying mid-turn
left the caller's own request waiting the full 30-second response timeout
to fail on its own, and if that timeout's resulting error event DID get
built, it usually never reached anyone anyway: `events()`'s consumer
generator returns the instant it sees the EOF sentinel, and that sentinel
was being pushed onto the queue microseconds BEFORE the error event, since
rejecting a pending future only *schedules* the awaiting coroutine's
reaction rather than running it. Fixed by rejecting every pending request
and clearing busy unconditionally, then explicitly waiting (bounded,
best-effort) for that reaction to actually run before pushing the EOF
sentinel behind it.

**8. That exact fix (finding 7) introduced a new bug, caught by a second,
independent review verifying the first review's own remedy.** The
bounded wait added a `contextlib.suppress(Exception)` around itself --
which does NOT catch `asyncio.CancelledError` (a `BaseException` since
Python 3.8). If the pending request's own task was cancelled elsewhere
(the normal shutdown path does exactly this) while that bounded wait was
in flight, its cancellation propagated straight through the suppress,
skipping the EOF-sentinel push entirely and permanently hanging the event
stream for the rest of the session -- reproduced in a scratch harness, not
theoretical. Fixed by catching the specific exception types that can
actually occur there instead of guessing broadly.

## Mechanism

Every one of findings 6 through 8 shares the same shape: an asyncio
background task's cleanup path (a `finally` block, an idempotency guard,
an exception-suppression scope) that had literally never been exercised,
because exercising it requires deliberately killing/cancelling/racing a
real subprocess -- not something ordinary "does the happy path work" testing
does. A fake subprocess speaking the real protocol (`tests/fake_acp_server.py`,
newline-delimited JSON-RPC over real stdio, not a mock) was necessary but
not sufficient on its own: it caught finding 6 immediately, but finding 7
needed an actual regression test that killed the backend mid-turn to
surface, and finding 8 needed a second reviewer specifically told to
verify the first reviewer's own fix rather than re-audit from scratch.
Neither finding 7 nor 8 was hypothesized in advance -- both were found by
deliberately probing for "what happens in the case nobody's tested yet",
the same instinct that motivated closing the three original open items.

## What transfers

- **validated-live**: for any ACP-speaking adapter (or, more broadly, any
  JSON-RPC-over-stdio adapter using an `asyncio.Future`-per-request-id
  pattern), the background reader task's own `finally`/error-handling path
  needs the SAME rigor as the happy path -- it is exactly as reachable in
  production (a subprocess dying is not exotic) and is far less likely to
  be exercised by ordinary testing.
- **validated-live**: `contextlib.suppress(Exception)` is not a safe
  "swallow anything that might go wrong here" idiom around an `await` in
  asyncio code -- `CancelledError` is a `BaseException`, not an
  `Exception`, specifically since Python 3.8's cancellation-semantics
  changes, and it is exactly the exception most likely to actually occur
  at an arbitrary await point in a task that might get cancelled from
  elsewhere.
- **validated-live**: two independently-reasoned LLM reviews of the same
  code, one asked to find bugs and a second asked specifically to verify
  the first review's own fix rather than re-derive it, caught different,
  non-overlapping real bugs. Neither review alone would have caught both
  finding 7 and finding 8.
- **diagnosed**: Kilo's plan-mode enforcement being a real rule-engine
  rejection (finding 5) rather than model self-restraint is a single
  observation on one version of one product; whether it holds across Kilo
  versions or under adversarial prompting was not tested.
