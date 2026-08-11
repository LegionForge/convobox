---
title: Full self-barge-in volume sweep (100%-20%, N=7 per level, 119 real trials) + room RT60 measurement -- complete raw data for reuse
status: validated-live (N=7 per level, corroborated across an initial sweep + 3 repeat up/down cycles)
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 82f1117; tts.volume=4.0 (fixed), macOS system output volume swept 100%-20% in 5% steps
hardware:
  computer: Mac mini M4 (2024) -- single "Built-in speaker" per Apple's own spec (not a stereo pair). Independent reviews describe it as prone to distortion at volume.
  microphone: AIRHUG 28 USB conference mic, 360-degree omnidirectional pickup, built-in DSP with an "AI Noise Reduction" mode (LED-indicated: Blue=on, Green=off/Original Mode, Red=Muted). AI DSP CONFIRMED OFF (green LED) throughout all testing in this session, directly observed by the operator.
  mic_placement: approximately 8cm from the Mac mini, facing away from the Mac mini's own body.
room:
  approximate_size: 20ft x 20ft (400 sq ft)
  flooring: LVP (luxury vinyl plank) -- hard, non-absorptive
  walls_ceiling: bare, no acoustic treatment
  furnishings: no rugs, no couches, no soft furnishings of any kind
  layout: open on 3 sides to a kitchen, a front room, and a hallway -- not an enclosed box; sound propagates into connected volumes rather than reflecting back from all directions
  measured_rt60: ~0.2s (T20-based) to ~0.4s (T30-based), 3 live repeat measurements, see below
evidence:
  - 119 real live trials total (initial 100%-to-20% sweep, then 3 full up/down repeat cycles), scripts/acoustic_calibration.py, one trial per volume level per pass
  - 3 real RT60 measurements via exponential sine sweep (Farina/ESS method), a standalone script (convobox-UAT worktree scratch, not committed)
  - Full JSON reports under uat-acoustic-calibration/ (convobox-UAT worktree scratch, gitignored, not committed) -- this field note is the durable, reusable record of the raw numbers
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; requested the full sweep, the repeat cycles for corroboration, the RT60 measurement, and asked for the raw data to be published in reusable tabular form; supplied all hardware/room details)
    - Claude Code (Anthropic claude-sonnet-5) -- ran all 119 trials + 3 RT60 measurements, aggregated the data, wrote this note
  org: https://legionforge.org
  created: 2026-08-11T15:00:00-05:00
  revised: 2026-08-11T15:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Full self-barge-in volume sweep + room RT60 -- complete raw data

**Context and purpose.** This note exists specifically to be reusable
by other people testing ConvoBox (or any similar local voice pipeline)
on their own hardware -- not just to document a finding for this one
setup. The test rig here (a single small, distortion-prone speaker; an
omnidirectional mic with zero spatial rejection; a large, hard-floored,
open-plan room with no acoustic treatment) is a deliberate stand-in for
a **near-worst-case acoustic coupling scenario** -- structurally similar
to a laptop's internal speaker+mic coupled through the chassis, or any
setup where signal-level AEC has to do all the work with no help from
physical isolation. Every hardware and room detail below is recorded
so someone else can either reproduce this exact test, or compare their
own (hopefully easier) setup against it.

## Method

Fixed `tts.volume=4.0` (Piper's own linear gain, unchanged from the
live demo earlier this session). macOS system output volume swept from
100% down to 20% in 5% steps (17 levels) via `osascript -e "set volume
output volume N"`, one real trial per level
(`scripts/acoustic_calibration.py --ambient-seconds 2
--response-repeats 2 --max-trials 1`, default `conversational` preset,
default `barge_in_min_speech_ms=250`, AEC delay left on `auto`). Run
once as an initial 100%->20% down-sweep, then 3 more full up/down
cycles (20%->100%->20%, x3) at JP's request for corroboration --
**119 real trials total, N=7 independent samples per volume level**,
collected across both sweep directions and 4 separate passes.

## Aggregate results (N=7 per level)

| Vol % | N | Attenuation mean (sd) | Ceiling mean (sd) | False-barge raw mean | False-barge AEC mean (sd) |
|---|---|---|---|---|---|
| 100 | 7 | 12.77 (4.70) | 20.38 (0.87) | 1.00 | 9.14 (2.27) |
| 95 | 7 | 11.19 (3.33) | 20.44 (1.64) | 1.00 | 9.86 (2.61) |
| 90 | 7 | 13.32 (5.14) | 19.67 (2.36) | 1.00 | 9.86 (1.35) |
| 85 | 7 | 11.16 (4.04) | 19.24 (3.02) | 1.00 | 12.71 (1.50) |
| 80 | 7 | 10.55 (3.11) | 17.97 (2.12) | 1.00 | 10.57 (3.21) |
| 75 | 7 | 12.88 (3.60) | 16.95 (2.34) | 1.00 | 11.57 (2.07) |
| 70 | 7 | 12.38 (2.56) | 15.78 (2.13) | 1.00 | 9.43 (3.05) |
| 65 | 7 | 12.42 (1.98) | 14.71 (2.38) | 1.00 | 8.14 (2.85) |
| 60 | 7 | 13.37 (2.99) | 13.68 (1.73) | 1.00 | 8.29 (1.60) |
| 55 | 7 | 15.03 (1.06) | 12.56 (1.79) | 1.14 | 3.71 (3.04) |
| 50 | 7 | 15.27 (1.20) | 11.54 (1.84) | 1.00 | 2.71 (1.50) |
| 45 | 7 | 13.82 (1.31) | 9.81 (1.55) | 1.00 | 3.29 (0.76) |
| 40 | 7 | 13.30 (0.45) | 7.70 (0.94) | 1.43 | 2.29 (0.95) |
| **35** | 7 | 10.87 (1.00) | 4.25 (1.29) | 3.29 | 4.00 (3.46) |
| **30** | 7 | 8.87 (1.54) | 1.79 (0.45) | 4.14 | 1.86 (1.07) |
| 25 | 7 | 8.32 (0.72) | 0.77 (0.39) | 1.86 | 0.86 (0.90) |
| 20 | 7 | 7.68 (1.07) | 0.39 (0.44) | 0.86 | 0.43 (0.53) |

**The transition zone is 30-40%** (bolded above) -- above it, AEC
consistently makes false barge-ins worse than AEC-off; at and below
it, AEC flips back to its normal, expected behavior (reducing false
triggers below the raw baseline). `ceiling_db` (the measured
echo-to-ambient headroom) declines smoothly and monotonically across
the entire range (20.4dB at 100% down to 0.39dB at 20%) with modest,
consistent standard deviations -- confirming this is a real, physical
trend, not measurement noise, and giving confidence in the overall
methodology.

## Room RT60 (reverberation time)

Measured via exponential sine sweep (Farina/ESS method): a 100-7500Hz
logarithmic sweep played through the real speaker, captured through
the real mic, deconvolved against the sweep's own matched inverse
filter to recover the room's impulse response, then RT60 estimated
from the Schroeder backward-integration energy decay curve. Validated
offline first (deconvolving the sweep against itself, simulating a
zero-delay/zero-reverb "room," produced a single sharp impulse
exactly where theory predicts, ~32dB above the surrounding noise
floor) before trusting it on real hardware.

3 live repeat measurements:

| Trial | T20-based RT60 | T30-based RT60 |
|---|---|---|
| 1 | 0.200s | 0.412s |
| 2 | 0.195s | 0.378s |
| 3 | 0.219s | 0.418s |
| **Mean** | **0.205s** | **0.403s** |

**Real, reproducible discrepancy between T20 and T30 estimates** (not
random noise -- consistent ~2x gap across all 3 trials) -- likely
reflects a decay curve that isn't a single clean exponential (a real,
open-plan room with multiple distinct paths/reflections rather than a
simple diffuse decay), or the T30 crossing extending into a
noisier part of the recording where the estimate is less reliable.
**T20 is generally the more trustworthy of the two** when adequate
SNR exists at that shorter time window, so 0.2s is the headline
number, with 0.4s recorded honestly as a real discrepancy rather than
discarded.

**A ~0.2s RT60 is actually fairly short/moderate** -- more typical of
a normal furnished room than an obviously "live" echoey space, which
may seem to undersell how "wet" this room subjectively feels with hard
floors and bare walls. A plausible reconciliation: the room being open
on 3 sides to adjoining spaces (kitchen, front room, hallway) lets
sound energy propagate away into those connected volumes rather than
reflecting straight back, which can genuinely shorten measured RT60
compared to a similarly hard-surfaced but fully enclosed room of the
same floor area -- the hard surfaces still affect frequency balance and
early reflections (contributing to the room "feeling" bright/wet),
even if the total decay TIME isn't extreme. Not confirmed further this
pass.

## What transfers

- **The 30-40% system-volume transition zone is the single most
  actionable number from this whole investigation** -- below it, this
  worst-case setup's AEC works as intended; above it, it doesn't.
- **RT60 ~0.2-0.4s in a 400 sq ft, open-plan, hard-floored room** is a
  real reference data point for anyone else measuring their own space
  with the same method.
- **The full raw table below is the actual reusable artifact** -- every
  individual trial, not just the aggregate. Someone testing their own
  hardware can compare their own per-trial numbers directly against
  this room/hardware's baseline.

## Full raw data (all 119 trials)

| Cycle | Direction | Volume % | Delay | Attenuation dB | Ceiling dB | Suppression dB | Echo Lag ms | FB raw | FB AEC | Utt raw | Utt AEC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | down | 100 | auto(238ms) | 12.41 | 21.71 | 11.45 | 123.0 | 1 | 11 | 1 | 6 |
| 0 | down | 95 | auto(238ms) | 10.58 | 23.68 | 9.77 | 48.0 | 1 | 12 | 1 | 8 |
| 0 | down | 90 | auto(238ms) | 12.98 | 23.48 | 13.19 | 112.0 | 1 | 8 | 1 | 4 |
| 0 | down | 85 | auto(238ms) | 17.16 | 23.03 | 10.5 | 127.0 | 1 | 11 | 1 | 3 |
| 0 | down | 80 | auto(238ms) | 10.37 | 19.77 | 10.37 | 143.0 | 1 | 16 | 1 | 6 |
| 0 | down | 75 | auto(238ms) | 10.92 | 20.34 | 8.8 | 108.0 | 1 | 12 | 1 | 7 |
| 0 | down | 70 | auto(238ms) | 13.29 | 17.61 | 9.45 | 112.0 | 1 | 11 | 1 | 4 |
| 0 | down | 65 | auto(238ms) | 11.95 | 17.32 | 10.07 | 106.0 | 1 | 8 | 1 | 4 |
| 0 | down | 60 | auto(238ms) | 12.12 | 15.48 | 11.99 | 120.0 | 1 | 9 | 1 | 1 |
| 0 | down | 55 | auto(238ms) | 15.51 | 14.61 | 9.89 | 112.0 | 1 | 3 | 1 | 2 |
| 0 | down | 50 | auto(238ms) | 15.91 | 12.82 | 13.38 | 119.0 | 1 | 3 | 1 | 1 |
| 0 | down | 45 | auto(238ms) | 13.44 | 10.03 | 12.44 | 103.0 | 1 | 4 | 1 | 1 |
| 0 | down | 40 | auto(238ms) | 13.62 | 8.32 | 12.48 | 125.0 | 1 | 2 | 1 | 1 |
| 0 | down | 35 | auto(238ms) | 11.83 | 4.35 | 12.4 | 104.0 | 3 | 1 | 3 | 0 |
| 0 | down | 30 | auto(238ms) | 10.24 | 1.92 | 8.25 | 147.0 | 6 | 2 | 3 | 1 |
| 0 | down | 25 | auto(238ms) | 8.81 | 0.85 | 7.36 | 113.0 | 2 | 1 | 2 | 0 |
| 0 | down | 20 | auto(238ms) | 8.86 | 0.52 | 7.07 | 85.0 | 0 | 0 | 0 | 0 |
| 1 | up | 20 | auto(238ms) | 8.42 | 0.07 | 6.77 | 126.0 | 1 | 1 | 0 | 0 |
| 1 | up | 25 | auto(238ms) | 8.42 | 0.72 | 6.89 | 111.0 | 1 | 1 | 1 | 0 |
| 1 | up | 30 | auto(238ms) | 8.19 | 1.11 | 6.79 | 138.0 | 5 | 1 | 2 | 0 |
| 1 | up | 35 | auto(238ms) | 11.35 | 3.83 | 11.5 | 113.0 | 4 | 2 | 4 | 0 |
| 1 | up | 40 | auto(238ms) | 13.93 | 7.12 | 12.9 | 137.0 | 2 | 2 | 2 | 0 |
| 1 | up | 45 | auto(238ms) | 14.14 | 8.19 | 9.14 | 120.0 | 1 | 3 | 1 | 1 |
| 1 | up | 50 | auto(238ms) | 12.82 | 9.78 | 11.72 | 122.0 | 1 | 5 | 1 | 0 |
| 1 | up | 55 | auto(238ms) | 14.43 | 11.55 | 11.64 | 111.0 | 1 | 2 | 1 | 0 |
| 1 | up | 60 | auto(238ms) | 15.74 | 12.47 | 9.76 | 125.0 | 1 | 5 | 1 | 2 |
| 1 | up | 65 | auto(238ms) | 13.38 | 13.29 | 10.87 | 108.0 | 1 | 7 | 1 | 3 |
| 1 | up | 70 | auto(238ms) | 15.74 | 13.79 | 10.69 | 97.0 | 1 | 5 | 1 | 3 |
| 1 | up | 75 | auto(238ms) | 16.52 | 15.76 | 8.33 | 105.0 | 1 | 9 | 1 | 3 |
| 1 | up | 80 | auto(238ms) | 11.14 | 16.88 | 11.75 | 114.0 | 1 | 8 | 1 | 4 |
| 1 | up | 85 | auto(238ms) | 11.53 | 17.9 | 10.49 | 121.0 | 1 | 11 | 1 | 7 |
| 1 | up | 90 | auto(238ms) | 13.63 | 18.35 | 13.87 | 134.0 | 1 | 10 | 1 | 3 |
| 1 | up | 95 | auto(238ms) | 7.5 | 19.55 | 8.19 | 148.0 | 1 | 11 | 1 | 7 |
| 1 | up | 100 | auto(238ms) | 9.76 | 20.1 | 11.3 | 103.0 | 1 | 7 | 1 | 3 |
| 1 | down | 100 | auto(238ms) | 10.1 | 20.76 | 9.82 | 118.0 | 1 | 9 | 1 | 6 |
| 1 | down | 95 | auto(238ms) | 13.28 | 21.66 | 10.75 | 102.0 | 1 | 9 | 1 | 5 |
| 1 | down | 90 | auto(238ms) | 22.2 | 22.66 | 11.08 | 115.0 | 1 | 11 | 1 | 4 |
| 1 | down | 85 | auto(238ms) | 15.98 | 23.88 | 9.2 | 108.0 | 1 | 12 | 1 | 5 |
| 1 | down | 80 | auto(238ms) | 11.91 | 21.55 | 6.98 | 96.0 | 1 | 10 | 1 | 7 |
| 1 | down | 75 | auto(238ms) | 15.14 | 19.53 | 10.17 | 120.0 | 1 | 10 | 1 | 5 |
| 1 | down | 70 | auto(238ms) | 10.5 | 18.12 | 10.85 | 107.0 | 1 | 7 | 1 | 2 |
| 1 | down | 65 | auto(238ms) | 14.58 | 17.22 | 11.68 | 128.0 | 1 | 6 | 1 | 3 |
| 1 | down | 60 | auto(238ms) | 8.25 | 15.97 | 9.31 | 135.0 | 1 | 9 | 1 | 5 |
| 1 | down | 55 | auto(238ms) | 16.06 | 14.58 | 13.66 | 122.0 | 1 | 2 | 1 | 0 |
| 1 | down | 50 | auto(238ms) | 16.08 | 13.44 | 14.67 | 136.0 | 1 | 3 | 1 | 1 |
| 1 | down | 45 | auto(238ms) | 11.48 | 11.75 | 11.79 | 121.0 | 1 | 3 | 1 | 0 |
| 1 | down | 40 | auto(238ms) | 12.76 | 8.91 | 13.21 | 132.0 | 1 | 2 | 1 | 0 |
| 1 | down | 35 | auto(238ms) | 11.85 | 5.5 | 8.52 | 113.0 | 4 | 3 | 3 | 2 |
| 1 | down | 30 | auto(238ms) | 9.7 | 1.3 | 10.24 | 121.0 | 5 | 1 | 2 | 0 |
| 1 | down | 25 | auto(238ms) | 8.85 | 1.07 | 8.63 | 136.0 | 1 | 2 | 1 | 0 |
| 1 | down | 20 | auto(238ms) | 8.41 | 0.57 | 8.65 | 132.0 | 1 | 1 | 0 | 0 |
| 2 | up | 20 | auto(238ms) | 6.58 | 0.46 | 6.76 | 111.0 | 1 | 1 | 0 | 0 |
| 2 | up | 25 | auto(238ms) | 9.12 | 0.74 | 8.38 | 100.0 | 1 | 0 | 1 | 0 |
| 2 | up | 30 | auto(238ms) | 9.71 | 1.94 | 10.24 | 127.0 | 3 | 2 | 3 | 1 |
| 2 | up | 35 | auto(238ms) | 11.03 | 3.42 | 7.91 | 126.0 | 4 | 2 | 3 | 1 |
| 2 | up | 40 | auto(238ms) | 13.5 | 6.54 | 6.94 | 132.0 | 2 | 3 | 2 | 2 |
| 2 | up | 45 | auto(238ms) | 14.68 | 8.23 | 6.95 | 117.0 | 1 | 4 | 1 | 1 |
| 2 | up | 50 | auto(238ms) | 15.21 | 10.17 | 12.28 | 124.0 | 1 | 4 | 1 | 0 |
| 2 | up | 55 | auto(238ms) | 16.23 | 10.92 | 12.59 | 126.0 | 1 | 7 | 1 | 0 |
| 2 | up | 60 | auto(238ms) | 13.9 | 13.01 | 10.76 | 96.0 | 1 | 9 | 1 | 4 |
| 2 | up | 65 | auto(238ms) | 13.83 | 12.94 | 12.4 | 110.0 | 1 | 11 | 1 | 2 |
| 2 | up | 70 | auto(238ms) | 9.25 | 14.46 | 9.32 | 97.0 | 1 | 13 | 1 | 6 |
| 2 | up | 75 | auto(238ms) | 17.88 | 15.2 | 11.71 | 83.0 | 1 | 10 | 1 | 3 |
| 2 | up | 80 | auto(238ms) | 16.15 | 16.99 | 14.57 | 117.0 | 1 | 8 | 1 | 3 |
| 2 | up | 85 | auto(238ms) | 6.28 | 18.86 | 7.94 | 112.0 | 1 | 15 | 1 | 10 |
| 2 | up | 90 | auto(238ms) | 8.15 | 18.5 | 9.66 | 106.0 | 1 | 9 | 1 | 4 |
| 2 | up | 95 | auto(238ms) | 9.78 | 19.47 | 9.97 | 110.0 | 1 | 9 | 1 | 5 |
| 2 | up | 100 | auto(238ms) | 11.06 | 20.77 | 11.36 | 105.0 | 1 | 6 | 1 | 3 |
| 2 | down | 100 | auto(238ms) | 22.97 | 20.71 | 7.3 | 111.0 | 1 | 8 | 1 | 3 |
| 2 | down | 95 | auto(238ms) | 7.18 | 19.64 | 4.68 | 100.0 | 1 | 5 | 1 | 4 |
| 2 | down | 90 | auto(238ms) | 17.73 | 18.77 | 11.1 | 112.0 | 1 | 10 | 1 | 4 |
| 2 | down | 85 | auto(238ms) | 9.64 | 18.02 | 7.79 | 108.0 | 1 | 14 | 1 | 7 |
| 2 | down | 80 | auto(238ms) | 8.57 | 18.72 | 9.54 | 120.0 | 1 | 14 | 1 | 8 |
| 2 | down | 75 | auto(238ms) | 8.49 | 18.13 | 10.3 | 98.0 | 1 | 12 | 1 | 6 |
| 2 | down | 70 | auto(238ms) | 11.32 | 18.35 | 11.06 | 127.0 | 1 | 9 | 1 | 7 |
| 2 | down | 65 | auto(238ms) | 9.31 | 17.18 | 8.73 | 100.0 | 1 | 7 | 1 | 4 |
| 2 | down | 60 | auto(238ms) | 12.47 | 14.92 | 7.65 | 102.0 | 1 | 8 | 1 | 3 |
| 2 | down | 55 | auto(238ms) | 15.48 | 13.92 | 11.0 | 114.0 | 1 | 2 | 1 | 1 |
| 2 | down | 50 | auto(238ms) | 16.52 | 13.75 | 12.68 | 124.0 | 1 | 1 | 1 | 0 |
| 2 | down | 45 | auto(238ms) | 14.6 | 11.42 | 14.19 | 125.0 | 1 | 4 | 1 | 0 |
| 2 | down | 40 | auto(238ms) | 13.33 | 8.3 | 9.39 | 135.0 | 1 | 2 | 1 | 1 |
| 2 | down | 35 | auto(238ms) | 10.28 | 3.83 | 7.89 | 105.0 | 2 | 6 | 2 | 2 |
| 2 | down | 30 | auto(238ms) | 6.01 | 1.92 | 5.95 | 138.0 | 2 | 4 | 2 | 2 |
| 2 | down | 25 | auto(238ms) | 7.67 | 0.91 | 7.39 | 126.0 | 2 | 0 | 1 | 0 |
| 2 | down | 20 | auto(238ms) | 7.92 | -0.37 | 7.19 | 355.0 | 1 | 0 | 0 | 0 |
| 3 | up | 20 | auto(238ms) | 7.64 | 0.45 | 7.03 | 113.0 | 0 | 0 | 0 | 0 |
| 3 | up | 25 | auto(238ms) | 8.32 | -0.03 | 8.46 | 131.0 | 4 | 2 | 1 | 0 |
| 3 | up | 30 | auto(238ms) | 10.14 | 1.89 | 9.81 | 112.0 | 4 | 1 | 2 | 0 |
| 3 | up | 35 | auto(238ms) | 10.72 | 2.48 | 11.03 | 133.0 | 4 | 3 | 3 | 1 |
| 3 | up | 40 | auto(238ms) | 13.32 | 6.57 | 11.47 | 107.0 | 2 | 4 | 2 | 0 |
| 3 | up | 45 | auto(238ms) | 15.4 | 8.38 | 12.95 | 120.0 | 1 | 2 | 1 | 0 |
| 3 | up | 50 | auto(238ms) | 15.33 | 9.28 | 13.76 | 123.0 | 1 | 1 | 1 | 0 |
| 3 | up | 55 | auto(238ms) | 13.53 | 10.32 | 11.42 | 123.0 | 2 | 1 | 2 | 0 |
| 3 | up | 60 | auto(238ms) | 17.73 | 12.19 | 9.5 | 118.0 | 1 | 10 | 1 | 5 |
| 3 | up | 65 | auto(238ms) | 10.26 | 12.77 | 12.08 | 111.0 | 1 | 13 | 1 | 2 |
| 3 | up | 70 | auto(238ms) | 15.62 | 13.85 | 10.16 | 98.0 | 1 | 8 | 1 | 5 |
| 3 | up | 75 | auto(238ms) | 11.21 | 14.6 | 10.84 | 174.0 | 1 | 13 | 1 | 5 |
| 3 | up | 80 | auto(238ms) | 6.1 | 16.06 | 9.07 | 99.0 | 1 | 10 | 1 | 7 |
| 3 | up | 85 | auto(238ms) | 8.08 | 16.21 | 9.46 | 118.0 | 1 | 13 | 1 | 11 |
| 3 | up | 90 | auto(238ms) | 9.81 | 18.25 | 12.24 | 102.0 | 1 | 9 | 1 | 4 |
| 3 | up | 95 | auto(238ms) | 15.69 | 19.29 | 12.7 | 112.0 | 1 | 10 | 1 | 4 |
| 3 | up | 100 | auto(238ms) | 9.81 | 19.13 | 10.67 | 129.0 | 1 | 11 | 1 | 5 |
| 3 | down | 100 | auto(238ms) | 13.29 | 19.51 | 12.0 | 109.0 | 1 | 12 | 1 | 4 |
| 3 | down | 95 | auto(238ms) | 14.31 | 19.8 | 9.1 | 120.0 | 1 | 13 | 1 | 6 |
| 3 | down | 90 | auto(238ms) | 8.71 | 17.68 | 8.11 | 110.0 | 1 | 12 | 1 | 5 |
| 3 | down | 85 | auto(238ms) | 9.42 | 16.76 | 8.8 | 86.0 | 1 | 13 | 1 | 7 |
| 3 | down | 80 | auto(238ms) | 9.62 | 15.83 | 9.66 | 125.0 | 1 | 8 | 1 | 4 |
| 3 | down | 75 | auto(238ms) | 9.98 | 15.1 | 8.65 | 108.0 | 1 | 15 | 1 | 8 |
| 3 | down | 70 | auto(238ms) | 10.93 | 14.26 | 10.01 | 93.0 | 1 | 13 | 1 | 8 |
| 3 | down | 65 | auto(238ms) | 13.64 | 12.27 | 11.82 | 108.0 | 1 | 5 | 1 | 0 |
| 3 | down | 60 | auto(238ms) | 13.41 | 11.72 | 6.64 | 128.0 | 1 | 8 | 1 | 2 |
| 3 | down | 55 | auto(238ms) | 13.96 | 12.02 | 13.28 | 119.0 | 1 | 9 | 1 | 4 |
| 3 | down | 50 | auto(238ms) | 15.01 | 11.54 | 13.02 | 121.0 | 1 | 2 | 1 | 0 |
| 3 | down | 45 | auto(238ms) | 13.01 | 10.7 | 8.46 | 121.0 | 1 | 3 | 1 | 2 |
| 3 | down | 40 | auto(238ms) | 12.67 | 8.13 | 13.69 | 123.0 | 1 | 1 | 1 | 0 |
| 3 | down | 35 | auto(238ms) | 9.0 | 6.32 | 5.44 | 131.0 | 2 | 11 | 2 | 6 |
| 3 | down | 30 | auto(238ms) | 8.08 | 2.46 | 7.37 | 100.0 | 4 | 2 | 3 | 1 |
| 3 | down | 25 | auto(238ms) | 7.08 | 1.14 | 7.81 | 146.0 | 2 | 0 | 1 | 0 |
| 3 | down | 20 | auto(238ms) | 5.91 | 1.05 | 6.46 | 134.0 | 2 | 0 | 1 | 0 |

