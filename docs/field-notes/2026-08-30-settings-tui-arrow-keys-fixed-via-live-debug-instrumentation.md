---
title: Settings TUI arrow-key bug root-caused and fixed via live key-by-key debug instrumentation, after two prior hypotheses were tested and ruled out
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 + local settings-TUI fixes; scripts/settings_tui.py's read_key()/run_tui(); openSUSE Tumbleweed; Sager-class laptop, 4th-gen Intel i7
evidence:
  - Two live CONVOBOX_TUI_DEBUG_KEYS runs on the operator's real terminal, before and after the 1.0s timeout fix, full logs quoted below
  - key_probe.py, a minimal standalone raw-mode byte probe, run live to rule out the SS3 hypothesis
  - tests/test_settings_tui.py, 156/156 throughout every step
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran every live test, provided the debug logs, asked for the write-up)
    - Claude Code (Anthropic claude-sonnet-5) -- diagnosed each hypothesis, built the debug instrumentation, applied the fix, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T20:15:00+00:00
  revised: 2026-08-30T20:15:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Settings TUI arrow keys: the debugging path that actually worked

This is a process note as much as a bug note -- worth keeping because
the first two hypotheses were each individually plausible, individually
testable, and both wrong for this operator's actual symptom. The full
detail lives in `docs/KNOWN-ISSUES.md`'s own arrow-key entry; this note
is the narrative version.

## Attempt 1: SS3 vs. CSI escape sequences -- ruled out by direct evidence

`read_key()` originally only recognized the CSI form of arrow keys
(`"\x1b[A"`); many terminals send the SS3 form instead (`"\x1bOA"`)
depending on cursor-key mode. Real, fixable gap -- but a minimal probe
script (`key_probe.py`, three unconditional blocking reads, no timeout
logic at all) run live on the operator's actual terminal showed
perfectly standard CSI bytes for both arrows, with no delay. The SS3 fix
was kept (genuine robustness improvement, zero regression risk) but
wasn't the explanation.

## Attempt 2: widen the 50ms select() timeout to 300ms -- tested live, still not enough

`read_key()` gates on `select.select([sys.stdin], [], [], TIMEOUT)`
between the leading `\x1b` and the rest of an escape sequence. On a
slower machine, if the timeout fires before the rest of the sequence
arrives, a real arrow key gets misread as a bare `ESC`. Widened 50ms ->
300ms and asked the operator to retest. Their report: *"the arrow keys
work better? sorta? but I have to hit the arrow keys multiple times
before it goes to another setting."* Plausible-sounding fix, live
report says "still not really working" -- worth stating plainly rather
than declaring victory on a partial improvement.

## The actual fix: live key-by-key instrumentation instead of a third guess

Rather than propose another timeout value blind, `run_tui()` gained an
opt-in debug trace behind `CONVOBOX_TUI_DEBUG_KEYS=<path>` (zero effect
unless set) logging every `read_key()` result, how long it took, and
whether `selected_section`/`selected_field` actually moved. First live
capture, at the 300ms setting, gave a direct answer instead of another
inference:

```
19:56:17 read_key()='ESC' (took 405.7ms) before=section0/field0
  -> handled, after=section0/field0 (moved=False)
19:56:17 read_key()='[' (took 0.0ms) before=section0/field0
  -> handled, after=section0/field0 (moved=False)
19:56:17 read_key()='C' (took 0.0ms) before=section0/field0
  -> handled, after=section0/field0 (moved=False)
```

One physical Right-arrow press, three separate `read_key()` calls: the
`select()` timing out on `'ESC'`, then `'['` and `'C'` each arriving
"instantly" (0.0ms) on the *next* two calls -- meaning they were already
sitting in the kernel's input buffer, just a beat too late for that
call's own 300ms window. Each of the three lands as an independent
no-op. This pattern repeated throughout the log, interspersed with
occasional clean single-token `'RIGHT'`/`'LEFT'`/`'UP'`/`'DOWN'` reads --
intermittent, exactly matching "sorta, but multiple presses needed."

Widened the timeout again, this time to 1.0s (still imperceptible for
the one real standalone-`ESC` use -- cancelling a modal -- while giving
a large margin over this hardware's observed inter-byte latency). Asked
for a second debug-log run, cleared and recaptured from scratch rather
than reusing the first:

```
20:06:07 read_key()='RIGHT' (took 2008.0ms) before=section0/field0
  -> handled, after=section1/field0 (moved=True)
20:06:07 read_key()='RIGHT' (took 286.0ms) before=section1/field0
  -> handled, after=section2/field0 (moved=True)
...
```

Every arrow press across the full ~50-line log resolves into exactly
one token and moves the selection correctly. The only non-moving
entries are genuine boundaries (top of a field list, the last section
tab) -- zero `ESC`/`[`/letter splits anywhere. Confirmed, not assumed.

## What made this take three rounds

Two of the three hypotheses were each independently reasonable given
the evidence available *at the time* -- SS3 is a real, common terminal
behavior; a fixed 50ms timeout is a real, known-fragile pattern. Neither
survived contact with direct live evidence from the operator's actual
terminal. The difference between round 2 (a plausible fix, asked "does
this work now?", got an ambiguous "sorta") and round 3 (a fix verified
against a live, timestamped, before/after log showing the exact
mechanism) is the actual lesson here: for an interactive-input bug this
project's own sandbox cannot reliably reproduce (pty automation kept
failing for environment reasons, documented in the KNOWN-ISSUES entry),
instrumenting the real code path and asking the operator to run it
live produced a confirmed answer in one round, where guessing had
already taken two.

## What's left

- `CONVOBOX_TUI_DEBUG_KEYS` stays in the code, opt-in and inert by
  default -- available if a different terminal or a future regression
  needs the same kind of direct evidence again.
- The operator's earlier "another test reveals a QC error" comment
  (raised between attempts 2 and 3) was never followed up with detail --
  if it names something real and separate, it's still open.
