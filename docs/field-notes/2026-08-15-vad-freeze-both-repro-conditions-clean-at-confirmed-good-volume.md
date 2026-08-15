---
title: Both of tonight's original freeze-repro conditions -- the 6-cycle rapid-fire stress harness and the idle-trigger-then-probe scenario -- ran completely clean once system output volume was confirmed at 65% instead of the 25% found last round; 35/35 utterances processed, zero gap-watcher hits, max readline stall 150.6s and fully explained by genuine idle time
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch docs/vad-freeze-volume-confound-2026-08-15 (off main @ 219a2d1, includes the prob/triggered/trailing_silence_windows/speech_windows trace diagnostic from the previous round), backend=codex, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), output volume confirmed 65% for the full session (was found at 25% last round)
evidence:
  - Confirmed output volume via `osascript -e "get volume settings"` BEFORE starting (25%, matching last round's confound), raised to 65%, re-confirmed after raising, before any test traffic
  - Ran `_test_vad_freeze_macos.py 6` (the same scratch harness used for this session's original "severe" catches: pause -> 3x rapid-fire safeword burst -> resume -> followup, 6 cycles) against a live session with `trace_silero_calls: true`. Result: 6/6 cycles completed, 30/30 utterances produced a `Processing audio` line, max readline() stall across the whole run was 5.5s, zero gap-watcher hits (>=3s log-silence threshold)
  - Immediately followed with the idle-trigger scenario specifically (one probe, then ~100s of deliberate idle -- the exact condition that produced round 10's 335.6s stall and this session's original headline 94.4s-stall-plus-2min-silence catch): readline() stall climbed to 150.6s during the idle window (expected, matches the idle duration), a follow-up probe at 08:36:22 produced a normal `Processing audio` line 2.4s later with `prob` climbing cleanly to 0.99+ and `triggered=True` throughout the utterance -- no mic-layer freeze
  - Full session totals: 35 utterances processed, 0 gap-watcher hits, across ~5 minutes of combined stress-cycle and idle-trigger testing
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop running this investigation all night)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T08:37:00-05:00
  revised: 2026-08-15T08:37:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Both original freeze-repro conditions ran clean once volume was confirmed good

**Context.** The previous round found that a live mic-layer-freeze
reproduction attempt was actually a test-harness confound: macOS
system output volume was at 25%, too quiet for `afplay`/`sd.play`
-synthesized test audio to cross the VAD's speech threshold at the
mic. That round recommended re-running this session's actual EARLIER
severe-freeze scenarios -- not just a fresh ad-hoc probe -- with volume
confirmed loud, since the raw logs from those original catches weren't
preserved and couldn't be checked for the same confound retroactively.
This round does exactly that, using the same scratch harness
(`_test_vad_freeze_macos.py`) that produced this session's original
"severe" catches.

## What was re-run, and how

1. **The 6-cycle rapid-fire stress harness** -- pause phrase, three
   back-to-back safeword bursts with 0.2s gaps, resume word, a
   normal-shaped followup utterance, repeated 6 times. This is the
   exact same script (unmodified) used earlier tonight for the batches
   that produced this session's severe-freeze field notes.
2. **The idle-trigger scenario** -- a single probe, then ~100 seconds
   of deliberate silence, then a follow-up probe. This specific
   pattern (one interaction, then idle) is what produced this session's
   two most extreme catches: the original 94.4s-stuck-readline +
   2min-total-silence finding, and round 10's 335.6s stall.

Both were run against a live session with `trace_silero_calls: true`
and the trigger-state diagnostic added last round, with output volume
confirmed at 65% (up from the 25% found at the start of this round,
matching what was found last round too -- strong circumstantial
evidence the whole night's testing was running quiet).

## Result: clean on both

- Stress harness: 6/6 cycles, 30/30 utterances got a `Processing
  audio` line, longest readline() stall was 5.5s (trivially explained
  by normal turn-taking), zero gap-watcher hits.
- Idle-trigger: readline() stall grew to 150.6s during the deliberate
  idle window (expected and correct -- nothing was said, so codex
  correctly had nothing to read), and the follow-up probe after the
  idle window was picked up and processed normally within 2.4
  seconds -- `prob` climbed cleanly into the 0.9-1.0 range and stayed
  `triggered=True` for the full utterance, no stall, no missing
  `Processing audio` line.
- Whole-session total: 35/35 utterances processed, 0 gap-watcher hits.

## What this means

This round doesn't retroactively prove every prior "severe" catch
tonight was the same volume confound -- their raw logs weren't
preserved, so that specific check can't be done. But it does show that
**the exact same stress conditions that produced this session's most
severe findings, re-run under the same harness with volume confirmed
good, produce zero freezes.** Combined with the previous round's direct
demonstration (identical files, 0/3 clean at 25% vs. 3/3 clean at
70%), the weight of evidence now points toward "most or all of
tonight's mic-layer-freeze findings on macOS were this volume
confound" rather than toward a real segmenter/pipeline bug -- though
this remains an inference from re-running the same conditions, not a
direct re-examination of the original data.

The readline()-stall diagnostic itself continues to behave exactly as
documented in round 8/10's busy-state work: long stalls during genuine
idle time are normal and harmless, and duration alone was never a
valid severity signal.

## What transfers

- **Re-running the SAME stress conditions that produced a finding,
  with one variable corrected, is stronger evidence than a fresh
  ad-hoc probe under corrected conditions.** The previous round's
  finding (a decisive volume test) established the mechanism; this
  round's finding (re-running the actual repro harness) establishes
  that the mechanism plausibly explains this session's actual data,
  not just a hypothetical. (validated-live)
- **A test harness's own environmental preconditions (system volume,
  in this case) deserve the same "verify, don't assume" discipline as
  the code under test.** This session spent most of a night chasing a
  pipeline bug that a single `osascript -e "get volume settings"`
  check, run once at the start of testing, would have caught
  immediately. (validated-live)

## Not done here

- claude-code and opencode backends were not re-tested this round
  (this round focused on codex specifically, matching the branch that
  originally showed "severe" findings). The task's original ask to try
  opencode for the freeze scenario (not yet tested, only tested for
  force_kill) is still open.
- No attempt to determine whether the ORIGINAL severe-freeze sessions
  tonight (before this investigation started using `trace_silero_calls`
  or checking system volume) were actually running quiet -- there is
  no way to check this without a time machine; the honest conclusion is
  "strongly suspected, not proven" for those specific historical
  catches.
- Merging any of tonight's several field-note/diagnostic branches --
  still all separate, unmerged branches off main, left for JP's review.
