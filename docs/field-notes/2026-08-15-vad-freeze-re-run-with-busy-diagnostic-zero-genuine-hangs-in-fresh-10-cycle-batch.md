---
title: Re-ran the 10-cycle stress batch with the new busy-aware diagnostic -- zero genuine in-flight hangs found; every stall of 5.5s or longer showed busy=False, confirming the prior round's correction generalizes rather than being a one-off
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch fix/readline-stall-diagnostic-busy-state (off main @ 219a2d1), backend=codex, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 9, directly following the previous round's own recommended next step. Real 10-cycle VAD stress batch run against a codex session with the new busy-aware readline_with_stall_diagnostic() in place (fix/readline-stall-diagnostic-busy-state)
  - Full raw session log (/tmp/convobox_busytest.log, not committed), every stall/recovery line's busy state tallied
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, analysis, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T06:10:00-05:00
  revised: 2026-08-15T06:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Re-run with the busy-aware diagnostic: zero genuine hangs in a fresh 10-cycle batch

**Context.** The previous round's own recommendation: re-run the
stress-batch/idle-trigger harnesses with the new `busy`-aware
diagnostic in place, to see whether the "severe freeze" pattern this
session documented all night still appears with a turn genuinely in
flight (`busy=True`), or turns out to have been idle time all along.
This round does exactly that.

## Result: every long stall was idle time; zero genuine hangs

A fresh 10-cycle stress batch (the same shape that caught this
session's own severe freezes earlier) produced 49 total
`readline() still pending` warnings. Tallied by `busy` state:

```
busy=True:   10  (every one of these was the FIRST warning at 0.5s,
                  a turn genuinely just dispatched and still
                  reasonably being worked on)
busy=False:  39  (every warning at 5.5s or longer, without exception)
```

**Zero** `still pending` warnings at 5.5s or longer showed `busy=True`
anywhere in this batch. The longest single stall resolved at 33.1s
total -- and by the time each long stall's own "still pending" checks
fired, `busy` had already gone `False`, meaning the corresponding turn
had already completed (or never needed one) before the diagnostic's own
periodic re-check even ran. No severe (90s+) freeze occurred this
batch. The session's own trailing idle time after the harness finished
showed the identical shape (`busy=False`, growing past 40s, same as
every other idle gap all night) -- correctly harmless, now visibly so.

## What this confirms (and doesn't)

**Confirms**: the prior round's correction generalizes -- it wasn't a
one-off explanation for a single 34.5s stall, it describes the actual,
consistent shape of an entire fresh batch. With the ambiguity removed,
this specific 10-cycle stress condition produced a completely clean
result under the corrected lens: normal turn-dispatch stalls (brief,
`busy=True`, expected) and idle gaps between turns (longer, `busy=
False`, harmless), no third category.

**Does NOT confirm**: that the readline()-stall freeze variant this
session spent all night chasing doesn't exist at all. This is one
batch, one condition (active stress cycling), that happened not to
reproduce it. This session's own prior severe catches (5-cycle batch,
10-cycle batch, idle-trigger) were never re-run with this diagnostic in
place to check their OWN busy state at the time -- their raw logs
weren't preserved, so whether those specific incidents were genuine
`busy=True` hangs or the same idle-time pattern remains genuinely
unknown, not retroactively cleared by this note. The honest state of
this investigation right now: the corrected diagnostic works and this
one fresh batch didn't reproduce a severe freeze under it, which is
useful evidence toward "less common than tonight's raw stall count
implied" but not proof the underlying bug is fully explained away.

## What transfers

- **A fix that closes a measurement gap should be re-run against the
  same conditions that originally motivated it, not just spot-checked**
  -- this round is that re-run, and it's the reason this note can say
  "generalizes" rather than "explained one instance." (validated-live)
- **The corrected diagnostic is now good enough to trust for real
  triage going forward**: a future stall warning showing `busy=True`
  past the first check is now a genuinely actionable signal worth
  investigating immediately; one showing `busy=False` can be treated as
  routine idle time without further forensics. This changes the
  practical workflow for any future freeze investigation on this
  codebase. (validated-live)

## Not done here

- Re-running the specific idle-trigger condition (the one that produced
  this session's most convincing severe-freeze evidence) with the busy
  diagnostic in place -- this round only re-ran the active-stress
  condition. The idle-trigger re-run is the more direct test of whether
  those specific three severe catches would show busy=True or
  busy=False, and hasn't happened yet.
- A larger sample (multiple 10-cycle batches) to build confidence that
  zero severe freezes in one batch isn't itself just this batch's own
  luck, symmetric with every other single-batch caveat this session has
  carried all night.
- Extending this same busy-aware capture to claude-code and opencode's
  own stall paths for a fair three-way comparison under the corrected
  lens (claude-code's own diagnostic already got the busy parameter in
  the same fix, just not yet exercised by a fresh run this round).
