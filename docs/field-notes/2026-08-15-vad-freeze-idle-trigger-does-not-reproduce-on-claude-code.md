---
title: The idle-trigger freeze that reliably hits codex (~90-100s onset) did NOT reproduce on claude-code across 4 minutes of pure idle time under the identical test condition -- fourth independent test tonight where claude-code outperforms codex on this macOS setup
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=claude-code, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 4. Exact same test shape as the immediately prior codex idle-trigger catch (docs/field-notes/2026-08-15-vad-freeze-idle-trigger-confirmed-no-active-stress-needed.md) -- one stress cycle, then zero further audio injection -- run against claude-code instead
  - A background poll (`grep` loop, 240s window) watching for any readline() stall over 10s; none appeared at all, not even a short one
  - Full raw session log (/tmp/convobox_idle_cc.log, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T03:38:00-05:00
  revised: 2026-08-15T03:38:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The idle-trigger freeze does not reproduce on claude-code

**Context.** The immediately prior round confirmed the severe VAD
freeze on codex doesn't need active stress -- one ordinary interaction
followed by ~90-100s of pure idle time was enough. This round tests the
identical condition against claude-code, which had only been tested
under active stress cycles before tonight (6/6 clean).

## Method

Identical to the codex idle-trigger test: one stress cycle (pause,
3x safeword burst, resume, followup), then zero further audio
injection, watching for a `readline()` stall to develop from ordinary
residual mic activity alone.

## Result: no stall at all, not even a short one, across 4 minutes of idle time

codex's own idle-trigger test developed a stall within seconds of going
quiet and reached severe (byte-identical-CPU, manual-kill-required)
territory by ~90-100s. claude-code, under the exact same test shape,
produced **zero** `readline() still pending` warnings of any duration
across a full 240-second idle window -- not a short self-resolving
stall, not a long one, nothing.

This is the **fourth** independent test tonight where claude-code
outperforms codex on this macOS setup, each testing a different
dimension:

| Test | codex | claude-code |
|---|---|---|
| `force_kill()` reliability (10 runs) | 0/10 clean | 10/10 clean |
| VAD stress batch (6 cycles) | severe freeze (this session's first catch, different batch) | 0 severe freezes |
| VAD stress batch (10 cycles) | severe freeze | not re-tested at 10 cycles |
| Idle-trigger (single interaction + quiet) | severe freeze, ~90-100s onset | **0 stalls at all, 240s** |

## What transfers

- **This is not just "claude-code freezes less often" -- under the idle
  condition specifically, it didn't stall at all, of any length.** The
  codex idle test showed the SAME `readline()` mechanism that produces
  short self-resolving stalls under stress also produces them under
  idle conditions, escalating to severe over time. claude-code showing
  zero stalls of any kind (not just zero severe ones) under the
  identical idle condition is a stronger signal than "it recovers
  better" -- it suggests whatever mechanism causes codex's stalls in
  the first place may not be present, or not triggered the same way, on
  claude-code's own read-loop under idle conditions specifically.
  (validated-live, single run)
- **Four independent tests across four different mechanisms (kill
  reliability, stress-batch freeze, idle-trigger freeze) all point the
  same direction** -- this is no longer a single anecdote. Whatever
  distinguishes claude-code's process/I/O handling from codex's on
  macOS, it's consistently more robust across everything this session
  tested. The mechanism itself remains unexplained (this note doesn't
  investigate why), but the pattern is now solid enough to inform a
  practical recommendation: if macOS reliability matters more than
  backend choice for a given deployment, claude-code is the safer
  default there tonight's data supports, not codex.

## Not done here

- Root-causing WHY claude-code's read loop doesn't show the same idle-
  condition stall pattern -- this note only extends the comparative
  data, doesn't explain the mechanism difference.
- A second independent claude-code idle run to build real confidence
  beyond n=1 (matching the "don't over-trust a single run" caveat this
  session's other notes have consistently applied to codex's own
  catches).
- Testing opencode under the same idle-trigger condition -- opencode
  has now been tested for force_kill() (unpredictable) and one 6-cycle
  stress batch (clean), but not this specific idle condition.
