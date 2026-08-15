---
title: The rapid-fire-hotwords-while-paused stress pattern reproduces two more codex readline() freezes (65.5s+, 236.7s), this time with the first-ever direct stall telemetry instead of silence
status: validated-live
date: 2026-08-14
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 4f1c58b (PR #274's readline_with_stall_diagnostic() instrumentation, merged same day, live-tested here for the first time against a real recurrence); backend=codex, permission_mode=permissive; working_dir D:/LegionForge/convobox-UAT
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, --web -v, real codex backend, synthetic pause/resume stress harness (`_vad_freeze_stress_harness.py`, scratch, not committed)
  - convobox-tui.log timestamps quoted verbatim below (background task outputs `b3dwolcaz.output`, `br437gk13.output`)
  - docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-and-a-12-minute-unrecoverable-one.md (predecessor investigation, same mechanism)
  - docs/KNOWN-ISSUES.md's "hard-stop does not guarantee..." entry (names this exact readline() gap as the leading candidate before this session)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the automated harness re-run, watched the freeze live, made the call to stop and consolidate rather than keep pushing into confounded data)
    - Claude Code (Anthropic claude-sonnet-5) -- harness build/fix, live session driving, log correlation, writing
  org: https://legionforge.org
  created: 2026-08-15T00:45:00-05:00
  revised: 2026-08-15T00:45:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Two more codex readline() freezes reproduced live, this time with real stall telemetry instead of silence

**Context for outsiders.** Same investigation as the 2026-08-12 notes: a
freeze where ConvoBox's whole voice pipeline goes unresponsive, previously
only diagnosed via inference (no direct evidence of WHERE the freeze
actually was). Earlier the same day as this note, PR #274 added a
diagnostic that logs exactly when a backend adapter's `readline()` call
is taking unusually long. This note is the first time that instrumentation
actually caught a real recurrence -- twice.

## Problem

JP asked for the original 10-cycle automated pause/resume stress batch
(rapid-fire "resume listening" bursts while the session should be paused)
to be re-run against the newly-merged instrumentation. Two separate runs
each triggered a real freeze.

## Evidence

### First run: pause never registered, 65.5s+ freeze, Quit did not recover it

The harness's own audio clipped the leading "s" off "stop listening" --
STT heard only `'listening'`:

```
21:37:07,400 DEBUG listening gate: pass (not paused): 'listening'
21:37:07,400 INFO transcript='listening' lang=en (0.76) dec=0.42 busy=False
```

Since the pause phrase never matched, every subsequent "resume listening"
burst repeat was routed to codex as an ordinary conversational turn
instead of being gated:

```
21:37:09,934 INFO transcript='resume listening' lang=en (0.95) dec=0.75 busy=True
21:37:11,908 INFO transcript='resume listening' lang=en (0.96) dec=0.78 busy=True
21:37:12,479 INFO response: I'm listening—go ahead.
21:37:13,603 INFO response: Listening again. Go ahead.
```

A barge-in fired shortly after (the burst's continued playback overlapping
a fresh response), and from that point the mic/STT layer kept working
correctly (one more utterance correctly dropped as "no input" at
21:38:09,508) while the backend channel went completely silent except for
the new diagnostic:

```
21:37:19,337 WARNING codex app-server _read_loop: readline() still pending after 5.5s ...
21:37:24,349 WARNING ... still pending after 10.5s ...
21:37:34,376 WARNING ... still pending after 20.5s ...
21:37:54,377 WARNING ... still pending after 40.5s ...
21:38:04,367 WARNING ... still pending after 50.5s ...
21:38:09,368 WARNING ... still pending after 55.5s ...
21:38:14,377 WARNING ... still pending after 60.5s ...
21:38:19,367 WARNING ... still pending after 65.5s ...
```

JP hit Quit at this point ("trying to stop the session" /
"I killed the python instance as it wasn't stopping when I hit the quit
button"). The process required a manual kill (`taskkill`) -- Quit's own
signal-based shutdown path did not recover it, consistent with that path
ultimately depending on the same process eventually becoming responsive.

### Second run: same phrase-clipping bug persisted, freeze reached 236.7s then self-resolved

A silence lead-in was tried as a fix for the clipping (later shown NOT to
be the actual mechanism -- see the companion note on the mic-silence
freeze for the real cause). Same clipping recurred
(`transcript='listening'` again), same downstream pattern: un-gated
"resume listening" bursts routed as ordinary turns, another readline()
stall began climbing:

```
21:43:53,352 WARNING ... still pending after 210.5s ...
21:44:53,350 WARNING ... still pending after 235.5s (implied by the next line's total)
21:46:59,574 WARNING codex app-server _read_loop: readline() finally returned after 236.7s total (proc.returncode=None)
```

Unlike the first freeze, this one returned on its own -- no kill needed.
Two web-API recovery attempts (`POST /api/listening` resume,
`POST /api/stop`) were made while it was stuck; the timing is too close
to the self-resolution to honestly credit either call with causing it
(the log's own "finally returned" timestamp lands within the same few
seconds as those calls, not clearly before or after in a way that
separates cause from coincidence). Immediately after, a fresh stall began
building again (5.5s, 10.5s, 15.5s, 20.5s reported) before the session
was deliberately stopped to consolidate findings rather than keep
generating confounded data.

## Mechanism

Both freezes match the leading hypothesis already named in
`docs/KNOWN-ISSUES.md` before this session: `readline()` on the codex
app-server's own stdout pipe blocking with no timeout. What's new here is
direct, on-the-record confirmation via PR #274's diagnostic -- previous
occurrences of this class of freeze (the 2026-08-12 notes, the original
2026-08-09 finding) were inferred from symptom (total silence, no new
log lines) rather than measured directly at the specific blocking call.

Both freezes were triggered the same way: a burst of rapid "resume
listening" repeats landing as real interjects/new turns on a codex
session, not (as originally designed) while genuinely paused -- the
harness's own audio-clipping bug meant the intended "pause, then stress
while paused" scenario was never actually exercised cleanly in either
run. That the freeze still reproduced under a DIFFERENT actual stress
shape (rapid un-gated turns/interjects, not rapid gated-and-dropped
utterances) is itself informative: it suggests the trigger condition may
be broader than "specifically while paused," though this note does not
claim that as established -- a cleaner-paused repro is still open work.

## What transfers

- **A diagnostic built specifically to catch a hypothesized freeze
  mechanism is only proven once it actually fires against a real
  recurrence** -- PR #274 landed same-day, untested against reality until
  these two incidents; both times it fired cleanly and matched the
  existing hypothesis. (validated-live)
- **"Quit doesn't recover a stuck session" and "the freeze itself" are the
  same root cause, not two separate problems** -- Quit's own shutdown
  path (`_self_signal_interrupt`) still needs the stuck process to
  eventually respond in some cases; a genuinely wedged process defeats
  both the polite interrupt AND graceful shutdown for the same reason.
  This is the direct motivation for PR #277's `force_kill()` (a separate,
  same-day follow-up: docs/field-notes/2026-08-14-force-kill-reliability-
  across-all-three-backends.md). (validated-live)
- **A freeze self-resolving is real data, not just "it went away"** --
  236.7s is a genuine, measured duration a real freeze lasted before
  clearing on its own; treat self-resolution as a possible outcome
  alongside "needs a kill," not evidence the freeze wasn't real.
  (validated-live)
- **An API call made while a system is stuck, followed by that system
  un-sticking, is not evidence the call caused the recovery** -- timing
  proximity alone doesn't establish causation; this note deliberately
  declines to credit the resume/stop calls with fixing anything.
  (methodology note, not a technical finding)

## Not done here

- A clean repro of the ORIGINALLY intended scenario (genuinely paused,
  then stressed) -- both runs here accidentally tested a different real
  stress shape instead, due to the harness's own clipping bug (root-caused
  and fixed in the companion note).
- Root-causing WHY `readline()` blocks this long on the codex app-server
  side specifically -- still an open question, same as before this
  session.
