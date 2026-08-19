---
title: The severe VAD freeze does NOT require active stress -- one ordinary interaction followed by 100+ seconds of pure idle listening was enough to trigger it (third independent severe freeze this session, 100.7s to unblock via manual kill)
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=codex, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 3. A real ConvoBox session driven through exactly ONE stress cycle (pause/burst/resume/followup), then deliberately left idle -- zero further audio injection -- while watching for a freeze from ordinary residual activity alone
  - Full raw session log (/tmp/convobox_idle_test.log, not committed)
  - ps CPU-time forensics on the hung app-server subprocess, same discipline as this session's other severe-freeze catches
  - Cross-reference: this session's two prior severe catches (5-cycle and 10-cycle stress batches), and the original 2026-08-14 Windows finding this whole investigation traces back to (a 41-minute freeze "triggered by ordinary low-volume activity, not a stress burst")
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round, and the standing ask to keep testing new combinations)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T03:10:00-05:00
  revised: 2026-08-15T03:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The severe freeze does NOT require active stress -- confirmed directly

**Context.** This session's two prior severe-freeze catches both
happened to occur at the tail end of a multi-cycle stress batch, after
the harness's own deliberate audio injection had already finished --
noted at the time as "consistent with, not proof of" the idle-activity
hypothesis, since a stress batch was still running earlier in both
cases. This round tests the hypothesis directly and cleanly: drive
exactly ONE ordinary interaction, then go completely quiet.

## Method

Started a real ConvoBox session (codex backend), ran the stress
harness for exactly **one** cycle (pause phrase, 3x safeword burst,
resume word, one followup utterance -- roughly 15 seconds of scripted
audio), then injected **zero** further audio. No more Piper synthesis,
no more speaker playback, nothing -- just real ambient room
conditions reaching the real mic, same as an ordinary idle ConvoBox
session between conversational turns.

## Result: a severe freeze developed with no further deliberate trigger

Roughly 90 seconds after the last "Processing audio" log line (the
followup utterance's own response), `codex app-server _read_loop`'s
`readline()` stall began and grew continuously -- 5.5s, 10.5s, 15.5s...
past 90s -- with **no new audio injected the entire time**. CPU
forensics, same discipline as every prior catch: two `ps` samples 3
seconds apart showed byte-identical `TIME 0:00.62` -- genuinely zero
CPU, not merely slow. Manually killed via `kill -TERM` at **100.7s**
total; `readline()` unblocked immediately (`proc.returncode=-15`), the
same kill-unblocks-the-read behavior every prior macOS catch has shown.

**This is the third independent severe freeze caught this session**
(5-cycle stress batch, 10-cycle stress batch, now this deliberately-
idle single-interaction test), and the cleanest evidence yet: this run
had no ongoing stress activity at all when the freeze began, only one
ordinary prior interaction and then silence. It directly confirms what
the two earlier catches could only suggest, and matches the original
2026-08-14 Windows incident's own characterization exactly -- "triggered
by ordinary low-volume activity, not a stress burst."

## What transfers

- **A stress-only test harness under-tests this specific failure mode.**
  If the trigger condition is "ordinary idle listening after normal use,"
  a harness built around deliberate rapid-fire bursts is testing a
  DIFFERENT, and possibly less representative, condition than what a
  real user's session actually experiences most of the time (mostly
  idle, listening, between occasional real requests). Any future
  reliability estimate for this freeze should weight idle-condition
  testing at least as heavily as active-stress testing, not treat stress
  batches as the primary methodology. (validated-live)
- **Three independent catches across three different test shapes (5-
  cycle stress, 10-cycle stress, single-interaction-then-idle) is strong
  enough evidence to stop treating this as rare or hard to hit on macOS.**
  A rough working estimate from tonight's data: this session hit the
  severe variant roughly once per real session/extended-use period,
  regardless of whether that period involved heavy stress-testing or a
  single ordinary interaction followed by idle time. (validated-live,
  small sample, but consistent across three independent attempts)

## Not done here

- A longer idle-only run (this test went ~100s before intervening;
  unknown whether NOT killing it would have let it self-resolve like
  some of the shorter-but-still-long stalls this session found, or
  whether it would have stayed hung indefinitely like the original
  2026-08-14 Windows 41-minute incident).
- Testing whether TRUE idle (zero prior interaction at all, not even one
  warm-up cycle) can trigger this, vs. needing at least one real turn
  first to put the backend/mic pipeline into whatever state precedes the
  freeze.
- Root-causing the actual mechanism -- this note adds a third data point
  on WHEN the freeze happens, not WHY. The audio-pipeline-coupling
  hypothesis from this session's harness-confound field note remains the
  leading candidate, unconfirmed at the mechanism level.
