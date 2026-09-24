---
title: A 30s flat ACP request timeout can outrace a backend's own error, turning a precise upstream failure into a generic "timed out"
status: validated-live
date: 2026-09-22
project: ConvoBox (github.com/LegionForge/convobox)
versions: kilo 7.7.7 (up from 7.5.6 on 2026-09-08/09); Inception mercury-2/mercury-2.5; convobox ACPAdapter (src/convobox/adapters/acp.py, _RESPONSE_TIMEOUT_S=30.0)
evidence:
  - PR #417 (tests/test_kilo_live.py) -- the live battery that surfaced this
  - Raw ACP wire probe transcripts (see this note's own Evidence section)
  - kilo's own per-run logs at ~/.local/share/kilo/log/*.log
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, asleep during the live runs; reviewed after)
    - Claude Sonnet 5 (Anthropic claude-sonnet-5) — investigation, live probing, writing
  org: https://legionforge.org
  created: 2026-09-22T23:19:28-05:00
  revised: 2026-09-22T23:19:28-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A 30s flat ACP request timeout can outrace a backend's own error, turning a precise upstream failure into a generic "timed out"

**Context for outsiders**: ConvoBox is a voice assistant that drives coding
agents (Claude Code, Codex, OpenCode, Kilo) as its "backend brain" over each
agent's own client protocol. The Agent Client Protocol (ACP) adapter talks
JSON-RPC over stdio to a spawned `kilo acp` (or `opencode acp`) process. This
note is about what happened when the newly added live-Kilo test battery
(PR #417) was run against a real `kilo` install overnight, and only real
findings survived: no hypothesis is reported here without a wire-level
transcript backing it.

## Problem

Three of five tests in `tests/test_kilo_live.py` (a battery re-verifying the
2026-09-09 field note's findings against a real `kilo acp` process) failed:

- `test_send_text_is_nonblocking_against_real_kilo`
- `test_permissive_mode_allows_a_real_write_against_real_kilo`
- `test_real_tool_failure_yields_friendly_text_against_real_kilo`

All three failed the same way: no `TEXT`/`DONE` event ever arrived, and the
`ERROR` event any of them did surface read
`RuntimeError: ACP request session/prompt timed out after 30.0s` -- convobox's
own client-side timeout, not anything from kilo itself.

## Evidence

A standalone raw-wire probe (bypassing convobox's adapter entirely, sending
the exact same `initialize` -> `session/new` -> `session/set_config_option`
-> `session/prompt` sequence `acp.py` does) reproduced the hang three times
against `inception/mercury-2` and once against `inception/mercury-2.5`,
after first ruling out a red herring: an early retry run saw even
`initialize` (a purely local handshake, no model call) stall past 15s, which
briefly looked like general resource contention from launching several
`kilo acp` processes back to back. A `tasklist` check found no lingering
kilo/node processes, and a single clean probe run after a cooldown showed
`initialize`/`session/new` completing in ~2s each (matching healthy control
runs) while `session/prompt` still produced nothing -- ruling out resource
contention as the cause and isolating the hang specifically to the
`session/prompt` call against Inception's models.

A control run against a non-Inception model
(`openrouter/~deepseek/deepseek-flash-latest`, same exact sequence, same
session) completed cleanly in 3.2s with full streaming:

```
[  4.61s] -> session/prompt id=4 ("Say exactly: hello from the live kilo test")
[  4.62s] <- session/update (available_commands_update)
[  7.48s] <- session/update (agent_message_chunk: "hello from the live kilo")
[  7.56s] <- session/update (agent_message_chunk: " test")
[  7.86s] <- session/update (usage_update, cost $0.0018)
[  7.86s] <- id=4 result: {"stopReason": "end_turn", ...}
```

The decisive run let `inception/mercury-2` run to completion instead of
giving up at convobox's own 30s mark. It took **74.47s**, and kilo's own
stderr and ACP response both carried the real cause:

```
[ 74.47s] STDERR: Error handling request {jsonrpc: "2.0", method: "session/prompt", ...} {
  code: -32603,
  message: "Internal error: Internal server error",
  data: { service: "session", errorName: "APIError" },
}
[ 74.47s] <- {"jsonrpc": "2.0", "id": 4, "error": {"code": -32603, "message": "Internal error: Internal server error", "data": {"service": "session", "errorName": "APIError"}}}
```

kilo's own per-run log (`~/.local/share/kilo/log/2026-09-23T034102.log`)
independently confirms the turn's real duration:
`session.turn.open publishing` at `03:41:06`, `session.turn.close publishing`
at `03:41:46` -- `+39966ms` logged directly on that line. A separate run
against `mercury-2.5` showed the same shape at `+24936ms`. Durations varied
(25s / 40s / 74s across three separate calls) but the shape did not: Inception
never streamed a single token, and never returned in under 24s, across every
attempt made tonight.

`kilo auth list` shows valid Inception + OpenRouter credentials throughout;
`kilo models inception` still lists `mercury-2`/`mercury-2.5`/`mercury-edit-2`
as available. This is not a missing/expired credential -- Inception's own API
is returning a server-side error (`errorName: "APIError"`) for this account's
`session/prompt` calls right now, and kilo is not fast to report that.

## Mechanism

Two independent problems stacked to produce the observed test failures:

1. **External**: Inception's API is currently erroring
   (`-32603`/`APIError: Internal server error`) for `session/prompt` calls
   through kilo 7.7.7, for both `mercury-2` and `mercury-2.5`. The
   2026-09-08/09 field note verified these same models responding in
   well under 2 seconds through kilo 7.5.6 -- whether the regression is in
   Inception's service, in kilo's own Inception-provider bridge changing
   between 7.5.6 and 7.7.7, or an account-specific issue, was not
   determined tonight and needs re-testing once (or if) the error clears.
2. **Internal, convobox-side**: `_RESPONSE_TIMEOUT_S = 30.0`
   (`src/convobox/adapters/acp.py`) is a single flat timeout applied to
   *every* ACP JSON-RPC request -- the same value covers a quick local
   handshake (`initialize`, `session/new`, `session/set_config_option`,
   each normally ~2s) and `session/prompt`, which `send_text()`'s own
   docstring already documents as resolving "only once the WHOLE turn
   completes," i.e. a duration that inherently scales with model/tool
   speed and has no reason to share a budget with the quick control
   calls. Because kilo took 74s to surface its own error tonight, and
   convobox gives up on ANY request at 30s, the caller never sees kilo's
   own precise, actionable `APIError` message -- it sees a generic
   "timed out," which is what actually reached three of five tests as an
   `ERROR` event. The hang wasn't silent to kilo -- kilo eventually told
   the truth. It just told it more than 40 seconds too late for
   convobox's own client-side patience.

Ruled out along the way: general resource exhaustion from launching several
`kilo acp` processes in a short window (a real symptom on one intermediate
retry -- `initialize` itself stalled past 15s -- but not reproduced after a
cooldown, and no lingering processes were found via `tasklist`); a
model-specific bug in `mercury-2` alone (`mercury-2.5` hung identically); and
a convobox-side protocol bug (the raw probe bypasses `acp.py` entirely and
reproduced the exact same hang, so the adapter code itself is not the
source of the delay).

## What transfers

- **validated-live**: kilo 7.7.7's `session/prompt` can take 25-74+ seconds
  to report an upstream provider's own server-side error, rather than
  failing fast. Any ACP client with a client-side request timeout shorter
  than that window will see its own generic timeout instead of the
  backend's real error message.
- **validated-live**: a single flat per-request timeout is the wrong shape
  for a protocol where one request type (`session/prompt`) is documented to
  block for a full model turn while the others are quick control calls --
  this is a real, currently-shared risk surface, not specific to tonight's
  Inception incident. **Not yet decided**: what the right fix is. Splitting
  `session/prompt` onto its own, longer timeout is the obvious shape, but
  choosing that number is a live, real-time-voice UX tradeoff (how long
  should a user wait mid-conversation before ConvoBox gives up on a stuck
  backend) that deserves a decision, not a value picked unilaterally
  overnight -- left open for review rather than shipped as a code change in
  this pass.
- **diagnosed, not validated**: whether tonight's Inception `APIError` is a
  transient outage, an account-specific issue, or a persistent regression
  in kilo 7.7.7's Inception bridge is unresolved. The three failing
  `tests/test_kilo_live.py` tests are failing correctly (a real upstream
  problem, not a test bug or a convobox regression) -- re-run
  `pytest tests/test_kilo_live.py` once Inception's status is confirmed
  clear before treating a continued failure as still-open.
- **update, same night**: a fourth attempt against `mercury-2`, roughly an
  hour after the first, reproduced the identical `-32603`/`APIError:
  Internal server error` at 76.97s -- same error, same shape, comparable
  duration. Four for four across ~an hour rules out a brief blip; this
  looks like a sustained condition on Inception's side (or kilo's bridge
  to it), not one bad request. Still unconfirmed whether it is
  account-specific or affects every kilo+Inception user right now.
