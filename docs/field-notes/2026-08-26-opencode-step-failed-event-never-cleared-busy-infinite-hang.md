---
title: OpenCode's session.next.step.failed event was invisible to ConvoBox -- a provider-rejected model produced a real, indefinite hang instead of a spoken error
status: validated-live (bug reproduced live twice on real hardware; fix applied and the exact same live scenario re-run to confirm recovery)
date: 2026-08-26
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ f0752c3 + this fix; opencode serve 1.18.19; OpenCode Zen gateway (real network, real free-tier routing); openSUSE Tumbleweed, Clevo P17SM-A (Sager) laptop
hardware: Clevo P17SM-A barebone (Sager-branded, 2014), Intel Core i7-4810MQ (Haswell) -- not acoustically relevant, this is a pure protocol/logic bug
evidence:
  - src/convobox/adapters/opencode.py (_STEP_FAILED constant, _track_busy, _to_backend_event)
  - tests/test_opencode_adapter.py (test_step_failed_event_maps_to_error_event_and_clears_busy; also fixed test_tool_failed_event_maps_to_error_event's content= assertion)
  - Raw SSE capture (/tmp scratch, this session) showing the real session.next.step.failed event shape
  - Live re-run of the identical scenario after the fix, confirming recovery in ~1s instead of an indefinite hang
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for real live-voice mic-loop testing of the OpenCode backend on Linux -- a previously undone gap -- while the machine was up and awake for testing anyway)
    - Claude Code (Anthropic claude-sonnet-5) -- built the real speaker-to-mic test harness, found the bug live, root-caused it against the raw SSE stream, implemented and verified the fix
  org: https://legionforge.org
  created: 2026-08-26T22:48:50-05:00
  revised: 2026-08-26T22:48:50-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# OpenCode's step.failed event was invisible to ConvoBox -- a real infinite hang, found live and fixed

**Context for outsiders.** ConvoBox is a voice frontend for CLI coding
agents. Its OpenCode adapter had only ever been tested at the isolated
adapter level (fake server, one trivial prompt) -- never through the real
orchestrator with a real mic and a real `opencode serve` instance. This
was the first attempt at that, using a real synthesized voice played
through real speakers and captured by the real microphone (the same
speaker-to-mic acoustic loop this project's other live tests use), not
scripted text injection.

## Problem

Asked a real, benign spoken question ("What files are in this
directory?") through the full voice loop with `backend: opencode`. STT
transcribed it correctly (99% confidence). ConvoBox sent it to a real
`opencode serve` instance, which forwarded it to the real OpenCode Zen
gateway. Then: nothing. The log showed `backend still working... THINKING
-- say the safeword to abort` every ~12 seconds, indefinitely -- past
275 seconds before a hard stop ("stop stop stop") was spoken to abort it.
A second attempt with a much shorter prompt ("Say hello in one word.")
did the exact same thing, past 150 seconds before being investigated
directly instead of waited out further.

## Evidence

Querying the opencode server's own REST API directly (bypassing
ConvoBox) showed the real story: both prompts had already failed --
terminally, within 4 milliseconds of being sent:

```json
{
  "type": "assistant",
  "model": {"id": "x-preview-f-free", "providerID": "opencode"},
  "content": [],
  "finish": "error",
  "error": {
    "type": "unknown",
    "message": "Provider request failed with HTTP 401: {\"type\":\"error\",\"error\":{\"type\":\"ModelError\",\"message\":\"Model x-preview-f-free is not supported\"}}"
  }
}
```

The session's own default model resolved to `x-preview-f-free` -- not
even one of the six models `opencode models` itself lists as available --
and the gateway rejected it outright. Capturing the raw SSE event stream
directly confirmed the real event opencode emits for this:

```
data: {"type":"session.next.step.failed","data":{"assistantMessageID":"...","error":{"type":"unknown","message":"Provider request failed with HTTP 401: ...Model x-preview-f-free is not supported"}}}
```

`session.next.step.failed` -- not `session.next.step.ended`. ConvoBox's
adapter (`src/convobox/adapters/opencode.py`) only ever recognized five
event names, and `_track_busy` only cleared `is_busy()` on
`session.next.step.ended`. A failed step fires `step.failed` INSTEAD of
`step.ended` for that step, so it fell through _track_busy entirely --
`is_busy()` latched `True` forever, with no code path that would ever
clear it short of the process exiting. ConvoBox was not slow here; it was
correctly, faithfully waiting forever for an event that could never come,
because the provider had already given up before ConvoBox's wait even
started.

## Mechanism

Two related gaps, both in the same ~15-line area of `opencode.py`:

1. **`_track_busy` didn't recognize `step.failed` at all.** Fixed by
   checking for it explicitly and clearing `_busy` unconditionally (a
   failed step carries no `finish` field to consult -- confirmed from the
   raw capture above -- and a failure is always terminal, unlike
   `step.ended`'s `finish="tool-calls"` continuing case).
2. **A second, adjacent, independently-found bug**: the existing
   `session.next.tool.failed` handler built its `BackendEvent` with
   `tool_output=json.dumps(payload.get("error"))` instead of `content=`.
   Every other ERROR-event constructor in this codebase
   (`claude_code.py`, `codex.py` x2, `orchestrator.py`) uses `content`,
   and `scripts/run_convobox.py`'s own ERROR handler only ever reads
   `event.content` -- so a real `tool.failed` event would have hit the
   exact same silent-swallow failure shape as `step.failed`, just via a
   different mechanism (a wrong field name instead of a missing event
   case). Not live-triggered this session (no real tool failure
   occurred), but caught by inspection while fixing the live one, and
   fixed the same way (`content=`).

Both are now covered by unit tests against the real captured event
shapes, and the `step.failed` fix was then re-verified against the
identical live scenario: the same broken-model session, same real
`opencode serve`, same real prompt, this time recovering with a logged
`error` event in about 1 second instead of hanging.

## What transfers

- **Validated-live**: the bug (real infinite hang, reproduced twice,
  root-caused against the real raw SSE stream) and the fix (re-run of
  the identical live scenario post-fix, recovering in ~1s).
- **Diagnosed, not live-triggered**: the `tool.failed` `content=` fix --
  correct by code-consistency and by matching every other adapter's
  convention, but no real tool failure occurred to trigger that specific
  code path live this session.
- **Separate, unrelated finding, not fixed here**: `safeword.kill_phrase`
  ("eject eject eject") mistranscribed as Arabic when spoken via
  synthesized TTS during this same test session -- consistent with,
  not a new instance beyond, this project's already-documented Kokoro
  pronunciation unreliability for this specific phrase (see
  `docs/field-notes/2026-08-25-live-acoustic-safety-phrase-sweep-*`).
  Noted here only because it happened during this test, not as a new
  bug.
- **Not investigated**: why the OpenCode Zen gateway's default model
  resolved to `x-preview-f-free`, an apparently-unsupported model not
  listed by `opencode models` -- an upstream/gateway-configuration
  question, out of scope for a ConvoBox-side adapter fix. Setting
  `backend.model` explicitly to one of the six listed models would likely
  avoid triggering this specific error, but the adapter fix here matters
  regardless of root cause: ANY step failure, for any reason, needs to
  clear busy and surface an error instead of hanging forever.

## Not done here

Did not attempt to reproduce this against a different, definitely-valid
model to confirm the OpenCode backend can complete a full successful
turn through the real mic loop on Linux -- this pass ran out of time
after finding and fixing the hang. That remains open: OpenCode's full
orchestrator-wired real-mic-loop path has now been exercised (this is
real progress over the prior adapter-only-level testing), but a clean,
successful end-to-end turn through it has still not been observed live.
