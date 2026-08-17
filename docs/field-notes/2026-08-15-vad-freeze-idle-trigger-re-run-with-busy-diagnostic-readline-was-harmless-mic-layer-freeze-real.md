---
title: The definitive idle-trigger re-run -- a readline() "stall" that grew to 335.6s (longer than any prior severe catch) was entirely harmless (busy=False the whole time, confirmed with the corrected diagnostic), while a SEPARATE mic-layer freeze (round 6's variant, no readline() stall at all) occurred TWICE in the same session and was the only genuine problem
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch fix/readline-stall-diagnostic-busy-state (off main @ 219a2d1), backend=codex, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 10. The direct idle-trigger re-run flagged as the natural next step in the two prior rounds' own notes -- same condition (one interaction, then idle) that produced this session's most convincing severe-freeze evidence earlier tonight, this time with the busy-aware diagnostic in place
  - Direct responsiveness probes (five total across the session, differently phrased each time) to independently verify mic-pipeline health apart from what the readline() diagnostic reports
  - Full raw session log (/tmp/convobox_idle_busytest.log, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T06:45:00-05:00
  revised: 2026-08-15T06:45:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The definitive idle-trigger re-run: readline() stalls were harmless, a separate mic-layer freeze was the real problem -- twice

**Context.** Two rounds in a row recommended re-running the specific
idle-trigger condition (one interaction, then idle) with the new busy-
aware diagnostic, since that condition produced this session's most
convincing severe-freeze "evidence" earlier tonight. This round does
exactly that -- and the result reframes the night's central finding
again, in a way that resolves rather than deepens the ambiguity.

## What happened, in order

1. **Session start, before any successful interaction**: the harness's
   own one-cycle warm-up (6 scripted phrases) produced **zero** mic-
   pipeline activity -- no codex subprocess ever spawned. A direct probe
   also got nothing. A SECOND, differently-phrased probe ~75 seconds
   after session start finally got a response. **This matches round 6's
   mic-layer-freeze variant exactly** (no readline() stall involved at
   all, since no backend process existed to have one) -- except this
   time it happened at the very start of the session, before ANY prior
   successful turn, not after one.

2. **Once recovered, the harness's own `readline()` diagnostic began
   firing** -- climbing past 30s, 60s, 90s... all the way to **335.6
   seconds** (5 minutes 35 seconds) before the observation window ended
   -- **the longest single stall this entire session has recorded, by a
   wide margin** (the previous longest was 114.0s). Under the OLD
   diagnostic, this would have read as by far the most alarming freeze
   of the night. **With the busy-aware fix: `busy=False` for every
   single one of its 79 "still pending" log lines, without exception.**
   This was genuinely, confirmedly harmless the entire time -- ConvoBox's
   own state correctly knew nothing was in flight.

3. **While that readline() stall was still climbing, a SEPARATE
   responsiveness probe was sent and got nothing** -- a second instance
   of the mic-layer freeze, this time roughly 44 seconds long, occurring
   concurrently with (but mechanistically unrelated to) the harmless
   long readline() wait. A THIRD, differently-phrased probe finally
   broke through: normal response, `busy=True` briefly, clean return to
   `busy=False`.

## The corrected picture

This session spent all night treating "a long readline() stall" as the
primary freeze signal. Tonight's most extreme instance of exactly that
signal (335.6s, dwarfing every prior "severe" catch) turned out to be
**completely inert** once busy state was visible. Meanwhile, the actual
problem in THIS session -- occurring **twice**, independently of
whatever the readline() diagnostic was doing at the time -- was the
mic-pipeline-layer freeze first documented in round 6: total silence in
`Processing audio`, no codex subprocess needed to explain it, confirmed
real only by direct probe-and-verify (not by any existing log
diagnostic, since none exists for this layer).

**This flips which variant deserves the "severe" label.** The
readline()-stall variant, this session's original headline finding
(94.4s stuck readline + 2min silence, from the very first catch
tonight), now looks like it was very likely conflating two things: a
harmless idle readline() wait (now explained) plus an actual
mic-pipeline freeze happening around the same time (the "2min total
silence" part of that original catch was almost certainly this same
mic-layer phenomenon, not a consequence of the readline() stall itself).
The mic-layer freeze is the one that has now been independently
confirmed real, twice, tonight -- once in round 6, twice more in this
round -- has no existing diagnostic instrumenting it at all, and is the
actual safety-relevant gap.

## What transfers

- **The single most important correction from tonight's entire
  investigation**: the readline()-stall diagnostic, even after PR #274
  added it specifically to give "real telemetry instead of the silence
  every prior repro produced," was itself producing a misleading
  signal for most of its firings. The busy-state fix (this session,
  round 8) was necessary, and this round's 335.6s-but-harmless result is
  the clearest possible demonstration of exactly how misleading the
  unfixed version was. (validated-live)
- **The mic-layer freeze deserves to be the primary focus of any future
  investigation session, not the readline() stall.** It has now
  recurred independently in two separate sessions/rounds tonight
  (round 6, round 10-twice), has a real, confirmed mechanism gap (no
  codex subprocess involved, so nothing in the backend-adapter layer
  can explain or diagnose it), and has zero existing telemetry -- unlike
  the readline() stall, which now has a working, trustworthy diagnostic.
  (validated-live)
- **A "duration alone is alarming" heuristic is now conclusively wrong
  for this codebase's readline() stalls** -- 335.6s of busy=False stall
  produced zero user-visible harm (confirmed by probes working normally
  throughout the surrounding window, when the mic layer itself was
  healthy). Any future alerting on this diagnostic must gate on `busy`,
  not duration. (validated-live)

## Not done here

- Building any diagnostic instrumentation for the mic-layer freeze
  itself (equivalent to what PR #274 + this session's busy-fix did for
  the readline() stall) -- this is now clearly the highest-value next
  engineering task this investigation has surfaced, not yet started.
- Determining whether the two mic-layer freezes THIS round (session-
  start, and mid-session) share a trigger, or are independent
  occurrences of the same underlying bug.
- Revisiting this session's ORIGINAL severe-freeze field notes (5-cycle,
  10-cycle batches) to specifically separate out which parts of those
  original catches were readline()-stall (now understood, likely
  harmless) vs. mic-layer-freeze (now understood, likely the real
  issue) -- their raw logs weren't preserved, so this would need
  re-reading this session's own written field notes for clues rather
  than raw data, not attempted here.
