---
title: 100-trial synthetic volume sweep on Linux (4th-gen i7) pins the self-barge-in transition zone at 20% system volume, tighter than the operator's own 30-35% live estimate
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 + local uncommitted settings-TUI fixes; scripts/acoustic_calibration.py; interaction.interrupt_preset conversational; audio.echo_cancellation true; aec_delay_ms auto (resolved 76ms this session); openSUSE Tumbleweed; Sager-class laptop, 4th-gen Intel i7
evidence:
  - Unattended run of scripts/acoustic_calibration.py --volume-candidates 100,90,80,70,60,50,40,30,20,10 --delay-candidates auto --repeat-each 10, full report.json (100 trials) and per-trial raw/AEC-processed WAVs, not committed (large binary output) -- table below is the full aggregate, reproducible from the same command
  - systemd-inhibit (sleep:idle:handle-lid-switch) held for the run's duration, tied to the actual process's lifetime rather than a fixed guess
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; requested the battery, asked for a written record before falling asleep mid-run)
    - Claude Code (Anthropic claude-sonnet-5) -- built the inhibitor, ran the sweep, aggregated the report, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T07:15:00+00:00
  revised: 2026-08-30T07:15:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# 100-trial volume sweep, Linux, 4th-gen i7

Requested directly after the same day's live `conversational` + AEC
session (`docs/field-notes/2026-08-30-conversational-mode-plus-aec-
first-live-codex-linux-session.md`), where the operator narrated
self-barge-in improving with volume and guessed the real safe floor was
"30% or 35%." This runs the same synthetic, unattended methodology as
`docs/field-notes/2026-08-24-linux-volume-sweep-reproduces-high-volume-
aec-regression.md` and the earlier macOS 119-trial sweep
(`docs/field-notes/2026-08-11-full-volume-sweep-raw-data-and-room-rt60.md`),
scaled to 10 volume levels x 10 repeats = 100 trials, `aec_delay_ms=auto`
throughout (resolved to 76ms every trial this session).

Config used matched the live session exactly: `interrupt_preset:
conversational`, `echo_cancellation: true`. A `systemd-inhibit
--what=sleep:idle:handle-lid-switch` process was held for the run's
actual duration (tied to the real worker PID via a wait-loop, not a
fixed sleep) since the operator went to sleep mid-run and this needed
~35 minutes of uninterrupted real speaker/mic access.

## Full results

Aggregated from `report.json`'s 100 trials (10 per volume level):

| Volume | raw false-barges (sum/10) | AEC-processed false-barges (sum/10) | trials with ≥1 AEC false-barge | mean attenuation (dB) | mean external suppression (dB) |
|---:|---:|---:|---:|---:|---:|
| 100% | 35 | 14 | 6/10 | 14.40 | 12.06 |
| 90%  | 33 | **49** | 9/10 | 13.15 | 10.70 |
| 80%  | 35 | 21 | 9/10 | 11.70 | 10.07 |
| 70%  | 36 | 17 | 8/10 | 11.68 | 10.03 |
| 60%  | 34 | 10 | 7/10 | 10.24 | 9.73 |
| 50%  | 43 | 6 | 5/10 | 9.84 | 9.58 |
| 40%  | 38 | 2 | 2/10 | 9.44 | 9.68 |
| 30%  | 16 | 2 | 2/10 | 10.57 | 9.96 |
| **20%** | **0** | **0** | **0/10** | 7.64 | 8.10 |
| 10%  | 0 | 1 | 1/10 | 8.67 | 9.58 |

## Headline: clean floor at 20%, not 30-35%

**20% is the only volume level with zero false barge-ins across all 10
trials, both raw and AEC-processed.** 30% and 40% are a large
improvement over anything above them (2 false-barges across 10 trials
each, vs. 6-49 at 50%+) but are **not** clean -- 2 of 10 trials at each
level still show at least one residual false barge-in. The operator's
live, ear-based read ("doing really well" at 30-35%, "only barged in
once" at 40%) was a good qualitative read of the improving trend, but
the actual zero-incidents floor sits one step lower than that estimate.
10% shows one anomalous AEC-processed false-barge (see below) despite
zero raw utterances registering at all in that same trial -- worth a
second look, not fully explained here.

## A real anomaly, flagged not explained: 90% is worse than 100%

Every other volume level's AEC-processed count is well below its raw
count (AEC helping, sometimes substantially -- e.g. 100%: 35 raw -> 14
AEC). **90% inverts this: 33 raw -> 49 AEC-processed, AEC making it
worse than doing nothing, and in 9 of 10 trials.** This is the same
qualitative shape the macOS study found consistently *above* its 30-40%
transition zone (`docs/field-notes/2026-08-11-full-volume-sweep-raw-
data-and-room-rt60.md`: "AEC consistently makes false barge-ins worse
than AEC-off" at high volume) -- but here it shows up as a spike at one
specific level (90%) rather than a consistent pattern across the whole
upper range (100% itself doesn't show it). Single run, N=10 at that
level -- could be this specific volume interacting badly with this
hardware's speaker/mic response curve, could be noise in one unlucky
batch. **Not corroborated with a second pass yet** (the macOS study's
own methodology used 3 corroborating up/down cycles before trusting its
transition-zone number) -- treat as a real, specific data point worth
re-running, not yet a confirmed hardware characteristic.

## What this does and doesn't tell us

**Does:** gives a real, quantified, reproducible-command floor for this
specific machine's self-barge-in behavior under `conversational` +
AEC, at the exact settings the operator was live-testing with.

**Doesn't:** replace a real human double-talk sample -- `report.json`'s
own `recommendation` field says as much (`"A real human double-talk
sample is required before raising the VAD threshold; this unattended
run only rejects unsafe settings, it does not prove sensitivity."`).
This is the same synthetic-vs-live-ear gap already flagged in
`docs/field-notes/2026-08-25-linux-first-real-human-speech-demo-
safeword-and-self-barge-in-confirmed.md`: synthetic and live-ear
methods have agreed well on the *qualitative* trend before (both
"much better below ~40%"), and do so again here, but a synthetic
sweep's precise crossover point (20% here) and a live ear's subjective
read (30-35% here) are not automatically the same number, and only one
of the two has been checked against this specific hardware combination
so far.

## Not done here

- No corroborating second pass at 90% (or generally) yet -- single
  N=10-per-level run.
- No live human re-test at exactly 20% to confirm the synthetic floor
  matches real conversational feel, the way 2026-08-25 corroborated the
  macOS numbers.
- Raw/processed WAVs and diagnostics for all 100 trials are on disk
  (`/tmp/.../scratchpad/volume-sweep-battery/20260830-015647/`, this
  machine's scratchpad, not committed) if a specific trial's audio needs
  inspecting later.
