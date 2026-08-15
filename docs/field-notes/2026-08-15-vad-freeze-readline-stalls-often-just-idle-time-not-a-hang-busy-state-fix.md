---
title: A major correction -- many/most "readline() stall" warnings this session, including possibly some catches previously called severe, are ordinary idle time between turns misdiagnosed as freezes, because the diagnostic never distinguished "waiting for a response" from "correctly idle, nothing sent"; fixed by adding busy state to the log line and live-verified
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch fix/readline-stall-diagnostic-busy-state (off main @ 219a2d1), backend=codex, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 8. A new scratch wrapper (`_run_with_write_log.py`, not committed) monkeypatching CodexAdapter._write() to log every JSON-RPC payload sent to codex's stdin, with a timestamp, run against a real 10-cycle stress batch
  - Full raw session log correlating every STDIN_WRITE against every readline() stall/recovery, quoted verbatim below
  - src/convobox/adapters/codex.py's own busy-tracking fields (`self._busy`), confirmed False for the entire duration of the specific stall analyzed
  - A real code fix (readline_with_stall_diagnostic() gains an optional `busy` callback, wired into codex.py's and claude_code.py's own call sites), full test suite green (1287 passed, 5 skipped), ruff/mypy clean, live-verified against a real codex session showing busy=True correctly during an actual in-flight tool call
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the original ask this session to "eliminate the possibility that the test or the test harness is adversely affecting the results or their accuracy" -- this is exactly that, applied to the diagnostic itself, not just the audio harness)
    - Claude Code (Anthropic claude-sonnet-5) -- capture, analysis, code fix, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T05:40:00-05:00
  revised: 2026-08-15T05:40:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A major correction: many "stalls" are ordinary idle time, not a hang

**Context.** Following directly from the previous round's root-cause
capture (codex blocked on its own stdin, ConvoBox's event loop
genuinely idle), this round set out to capture what ConvoBox actually
wrote to codex's stdin in the moments before a stall, to close the loop
on that diagnosis. What it found instead is a correction to this
session's own methodology, significant enough to revisit how much of
tonight's "freeze" data represents a real bug.

## What the write-log capture showed

Instrumented `CodexAdapter._write()` to log every JSON-RPC payload with
a timestamp, then ran a real 10-cycle stress batch. Correlating writes
against stall/recovery lines around the longest stall in this batch
(34.5s):

```
05:32:58.773  STDIN_WRITE id=6 turn/start "Stomp, stomp."
05:33:00.723  response: "I heard: 'stomp, stomp.'"
05:33:00.760  backend event type=done          <- turn 6 completed NORMALLY
05:33:01.261  readline() still pending after 0.5s   <- stall diagnostic starts firing
   ... (grows continuously: 5.5s, 10.5s, 15.5s, 20.5s, 25.5s, 30.5s ...)
05:33:26.820  hard stop matched safeword 'stop stop stop'  <- busy=False logged explicitly
   (no STDIN_WRITE at this point -- busy=False means nothing was in flight to interrupt)
05:33:35.261  STDIN_WRITE id=7 turn/start "Stop, stop."
05:33:35.264  readline() finally returned after 34.5s total   <- 3ms after the new write
```

**Turn 6 completed successfully in ~2 seconds.** After that, nothing
new was sent to codex for the next 34+ seconds -- not because anything
was broken, but because the stress harness's own cadence (and STT
misses in between) genuinely produced no new utterance worth routing
to the backend during that window. `readline()` legitimately has
nothing to read when codex has nothing more to say and nothing new has
been asked of it. The stall "resolved" the instant a NEW turn was
written -- not because anything hung and then un-hung, but because
that's exactly when codex had something to say again.

## Why this matters: the CPU-forensics signature doesn't distinguish this from a real hang either

Every prior severe-freeze catch this session (and the "genuinely zero
CPU" confirmation used as evidence each time) relied on `ps` showing
byte-identical CPU time across samples. **A correctly-idle process
waiting for its next input shows exactly the same signature** -- zero
CPU, because it's doing nothing, because there's nothing to do. This
session's CPU forensics discipline was sound methodology for ruling out
"is it busy-looping vs. genuinely blocked" -- it was never capable of
distinguishing "blocked waiting for a response that should be coming"
from "correctly idle because nothing was asked." Confirmed directly
here: `self._busy` was `False` for this stall's ENTIRE duration
(visible in the very same log line that recorded the hard-stop match
mid-stall) -- there was no outstanding turn this readline() call could
even plausibly have been "hung" waiting on.

**This does not retroactively clear every severe catch this session
documented as harmless.** It means the existing telemetry couldn't
tell the difference, which is a real gap this note fixes going forward,
not a claim that re-examines each prior catch's specific busy state
after the fact (several of those sessions' own raw logs may still
contain the needed evidence for a future pass to re-check, but that
recheck wasn't done here).

## The fix: busy state added to the diagnostic itself

`readline_with_stall_diagnostic()` (`src/convobox/adapters/base.py`)
gains an optional `busy: Callable[[], bool] | None` parameter, called
fresh at each warning and included in the log line
(`busy=True`/`busy=False`/`busy=unknown` if not wired). Wired into both
of `codex.py`'s and `claude_code.py`'s call sites (`_read_loop` for
both, plus `claude_code.py`'s `_drain_stderr`) using each adapter's own
existing busy-tracking (`self._busy` for codex, `self.is_busy()` for
claude-code, which already exists as a public method). Live-verified
against a real codex session: a genuine in-flight tool call correctly
shows `busy=True` in the stall warning; idle time will now show
`busy=False`.

Full suite green (1287 passed, 5 skipped), ruff/mypy clean on touched
files.

## What transfers

- **A diagnostic built to catch a suspected bug can itself carry a
  false-positive rate if it doesn't distinguish "waiting for something
  that should arrive" from "correctly waiting for nothing in
  particular."** `readline_with_stall_diagnostic()` (PR #274) was a real
  improvement over total silence, but conflated two very different
  states under one warning shape for its first ~5 hours of production
  use. Worth auditing any other "stall/timeout" diagnostic in this
  codebase for the same gap. (validated-live)
- **This directly answers the "eliminate the possibility the test/
  harness is affecting results" ask from earlier tonight** -- not by
  ruling the audio pipeline out again, but by finding a real ambiguity
  in the MEASUREMENT tool itself. The right response wasn't "distrust
  all the data," it was "add the missing signal and re-baseline
  interpretation going forward." (validated-live)
- **A live write-log capture correlated against the exact stall/
  recovery timestamps is a genuinely different, complementary technique
  to the native stack sampling from the prior round** -- sampling shows
  WHAT a process is blocked on; write-log correlation shows WHETHER it
  should be blocked at all, given what was actually sent. Worth keeping
  both techniques available for any future live-freeze investigation.

## Not done here

- Re-examining this session's three prior "severe" readline()-stall
  catches (the original 5-cycle, the 10-cycle, and the idle-trigger
  ones) against their own raw logs to determine whether `busy` was
  actually `True` or `False` during each -- the raw logs for those
  sessions weren't preserved (not committed, per this whole
  investigation's scratch-file convention), so this would need a fresh
  capture with the new busy-aware diagnostic, not a re-read of old data.
  **This is the single most valuable next step**: re-run the idle-
  trigger and stress-batch harnesses with this fix in place and see
  whether the "severe" pattern still appears with `busy=True`, or
  turns out to have been idle time all along.
- The separate mic-layer-only freeze variant (round 6's note) is
  unaffected by this fix -- that one showed a real absence of
  `Processing audio` activity despite deliberate probes, a different
  signal entirely, not explained or resolved by this correction.
- PR review / merge decision on whether this fix should land as part of
  PR #277 (force_kill(), which already touches these adapters) or as
  its own standalone PR -- built on a fresh branch off main
  specifically to leave that choice open, not pre-committed to either.
