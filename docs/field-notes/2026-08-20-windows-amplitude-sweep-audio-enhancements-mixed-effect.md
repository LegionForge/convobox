---
title: A real Windows amplitude sweep confirms AEC struggles at high volume there too, but the effect of Windows' own Audio Enhancements on it is genuinely mixed, not simply good or bad
status: validated-live (N=3 matched comparison, one session, one room); directional only for the initial single-trial passes
date: 2026-08-20
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + feat/aec-volume-sweep-windows-2026-08-20 @ c08b43c; Windows 11; WebRTC AEC3 via aec-audio-processing; aec_delay_ms auto (222ms); tts.engine piper, voice en_GB-alba-medium; Realtek onboard audio (headphone jack) feeding an amplified Creative Labs 7.1 system (running 2.1 channels); Logitech 1080P Pro Stream (MME) as input; Windows Advanced Sound Properties > Signal Enhancements > Enable Audio Enhancements toggled both ways
evidence:
  - scripts/acoustic_calibration.py --volume-candidates <levels> --delay-candidates auto --repeat-each <N>, four real live runs this session
  - uat-acoustic-calibration/20260820-215746/report.json (single-trial 30% comfort check, enhancements off)
  - uat-acoustic-calibration/20260820-220158/report.json (N=1 sweep 30->5%, enhancements off)
  - uat-acoustic-calibration/20260820-221607/report.json (N=3 sweep 30->5%, enhancements ON)
  - uat-acoustic-calibration/20260820-223445/report.json (N=3 sweep 30->5%, enhancements OFF -- the matched comparison)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran all live trials, made the volume-safety calls, toggled Audio Enhancements, provided hardware/mic context)
    - Claude Code (Anthropic claude-sonnet-5) -- built the --volume-candidates feature, ran the trials, analysis, writing
  org: https://legionforge.org
  created: 2026-08-20T23:10:00-05:00
  revised: 2026-08-20T23:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A real Windows amplitude sweep, and a genuinely mixed result for Audio Enhancements

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents: mic and speakers run simultaneously, and acoustic echo
cancellation (AEC) keeps the assistant's own TTS output from being picked
back up by the mic and misread as user speech. GitHub issue #119 already
established, on macOS (Mac mini, single built-in speaker), that AEC can
make false self-triggered barge-ins *worse* than AEC being off entirely at
high system volume (30-40%+), likely because a linear AEC can't model a
speaker driver distorting at volume. That finding had never been tested on
Windows. This note is that test, on real different hardware, plus an
unplanned second experiment that fell out of it.

## Problem

Two questions: (1) does the "AEC gets worse at high volume" finding
reproduce on Windows, on completely different hardware (Realtek onboard
audio into an amplified 7.1 system, not a laptop's single built-in
speaker)? (2) once the volume-sweep tooling existed, does Windows'
own "Audio Enhancements" (Advanced Sound Properties > Signal Enhancements)
help or hurt the acoustic path ConvoBox depends on?

## What got built first

`scripts/acoustic_calibration.py` already automated AEC on/off comparison
(every trial reports both `raw_vad` and `processed_vad`) and delay sweep
(`--delay-candidates`). Amplitude was the missing axis -- the macOS study
found its result by manually changing system volume between runs. Added
`--volume-candidates`, which sweeps *system* output volume via pycaw's
`IAudioEndpointVolume` (not `tts.volume` -- the finding under test is about
the physical driver distorting at real playback volume, which a digital
pre-DAC gain doesn't reproduce), restoring the original volume in a
`finally` block regardless of how the run ends. Full detail in the
commit/PR (`feat/aec-volume-sweep-windows-2026-08-20`, PR #320).

**Caught live while building this:** `uv sync --extra calibration` (to
install pycaw) alone silently uninstalled every other previously-installed
extra in the shared dev venv (fastapi/piper/aec all gone) -- the exact
shared-venv footgun `AGENTS.md` rule #13 (added earlier the same night,
PR #319) describes. Caught via a direct import check, not assumed;
re-synced with the full extra set and verified restored before continuing.

**Also caught live:** the dev checkout's `convobox.yaml` had
`tts.voice: en_GB-alba-medium` (a Piper voice name) with no `tts.engine`
set, so it silently defaulted to Kokoro and failed the voice lookup on the
very first calibration attempt -- no audio played, clean failure. Fixed by
adding `engine: piper` (gitignored, per-machine config, not part of any
PR).

## Volume ceiling: this hardware is NOT comparable to the macOS study's percentages

The macOS study swept 100% down to 20% system volume. On this rig,
**25% was already extremely loud and 40% would have been unbearable** --
confirmed live before any sweep: a single trial at 30% was loud but clean,
no clipping or distortion audible, and the operator declined to go any
louder. This matters beyond just "be careful": **the macOS study's 30-40%
"transition zone" is not a portable loudness reference** -- it's tied to
that Mac mini's own amplifier/speaker gain curve, not a physical constant.
Every volume percentage in this note should be read as specific to this
one rig (Realtek onboard audio -> amplified Creative Labs 7.1, currently
running 2.1), not compared numerically against the macOS figures.

## Evidence

Four real live runs, same room, same devices throughout. Windows'
own audio-enhancement DSP is confirmed off for the first two (operator
directly checked Advanced Sound Properties > Signal Enhancements before
running); on for the third; off again (re-toggled and confirmed) for the
fourth.

### Pass 1-2: initial exploratory sweeps, enhancements off, N=1 (directional only)

A single comfort-check trial at 30% first, then a full 30/25/20/15/10/5
sweep at N=1 each (one trial per volume -- not enough for statistical
confidence, matching this project's own "N=1 is directional, not robust"
discipline from the macOS notes). Results, `auto`-delay (222ms) throughout:

| Volume | Attenuation | Ceiling | Raw false-barge | AEC-processed false-barge |
|---|---|---|---|---|
| 30% | 29.96dB | 17.55dB | 1 | 1 |
| 25% | 23.29dB | 15.19dB | 1 | **2** |
| 20% | 25.62dB | 12.34dB | 1 | 1 |
| 15% | 18.64dB | 9.15dB | 1 | 0 |
| 10% | 15.35dB | 4.76dB | 1 | 0 |
| 5% | 12.22dB | 1.22dB | 1 | 0 |

25% showed AEC-processed doing *worse* than raw (2 vs. 1) in this single
pass -- a small echo of the macOS finding, but on N=1 this is noise until
repeated. It was: see Pass 3-4 below.

### Pass 3-4: the real experiment -- matched N=3, Audio Enhancements on vs. off

Same six volumes, same delay, three repeats each (18 trials per
condition, 36 total), the only variable changed between passes was the
Windows Audio Enhancements toggle. Sums across the 3 repeats per volume:

**Raw (uncancelled) false-barges:**

| Volume | Enhancements OFF | Enhancements ON |
|---|---|---|
| 30% | 3 (1, 1, 1 -- exactly stable) | **15** (2, 4, 9 -- spiking) |
| 25% | 3 (1, 1, 1) | 3 (1, 1, 1) |
| 20% | 3 (1, 1, 1) | 2 |
| 15% | 3 (1, 1, 1) | 2 |
| 10% | 3 (1, 1, 1) | 1 |
| 5% | 3 (1, 1, 1) | 1 |
| **Total** | **18** | **24** |

**AEC-processed (what would actually reach the backend):**

| Volume | Enhancements OFF | Enhancements ON |
|---|---|---|
| 30% | 4 (2, 2, 0) | 6 (0, 3, 3) |
| 25% | **5** (2, 3, 0) | **0** (0, 0, 0) |
| 20% | 0 | 1 |
| 15% | 0 | 1 |
| 10% | 1 | 1 |
| 5% | 2 | 0 |
| **Total** | **12** | **9** |

## Mechanism -- genuinely mixed, not a clean story either way

**Enhancements off gives a far more stable raw signal.** Across all 18
off-condition trials, raw false-barges were exactly 1, every single trial,
at every volume -- essentially zero trial-to-trial variance. With
enhancements on, raw is both noisier and volume-dependent, spiking hard at
30% (2, 4, 9 across three reps at the same nominal volume). This is
consistent with Windows' enhancement DSP doing some form of dynamic
processing (loudness normalization, automatic gain, or similar) that
becomes more aggressive/unstable at higher output levels -- not confirmed
at the mechanism level, only the effect is observed.

**But AEC's actual output had *more* total residual with enhancements
off** (12 vs. 9 across the whole sweep), almost entirely because of 25%:
off left 5 false-barges through at that one volume where on left zero.
30% is the one volume both conditions agree is the worst for AEC, on or
off -- consistent with (though not proof of) the same driver/amplifier-
distortion-at-volume mechanism the macOS study proposed, now observed on
completely different hardware.

No single mechanism explains both halves of this cleanly. A plausible
combined picture: enhancements-on trades a noisier, more volume-sensitive
raw signal for something (an automatic gain stage smoothing input levels?)
that make AEC's adaptive filter converge better across most of the sweep --
except at the top of the volume range, where whatever the enhancement is
doing stops helping and the raw signal chaos overwhelms it. This is a
hypothesis, not validated further here.

## What transfers

- **The "AEC can make barge-in worse than AEC-off at high volume" finding
  from macOS is real on Windows too, on completely different hardware**
  (Realtek onboard + amplified 7.1, vs. a Mac mini's single built-in
  speaker) -- 30% was the worst volume for AEC in both the on and off
  conditions here. (validated-live, N=3, one session)
- **Volume percentages do not transfer across rigs.** This machine's
  practical ceiling (30%, uncomfortably loud) is nowhere near the macOS
  study's 100%-20% sweep range. Any future comparison needs to normalize
  by something like measured dB SPL at the mic, not the OS volume slider
  percentage, to be meaningful across hardware. (validated-live, this
  session)
- **Windows Audio Enhancements' effect on the AEC pipeline is real but not
  simply "helps" or "hurts."** It measurably changes both the raw echo
  signal's stability and AEC's residual output, in different directions
  at different volumes. A recommendation to enable or disable it should
  wait for a second independent session confirming the pattern holds, not
  be made off this one matched comparison alone. (validated-live within
  this session; not yet independently replicated)
- **Mic self-noise/room-tone check, informal:** operator's own Audacity
  A/B listening (not part of this automated sweep) across four mics --
  Logitech 1080P Pro Stream (webcam, MME/Realtek path), Logitech Brio,
  a lavalier (USB), and a professional XLR condenser (phantom power + amp
  -> USB) -- found the 1080P Pro Stream fuller and warmer than the
  condenser at distances beyond arm's length (the condenser only
  performed best very close to the capsule); this matches its continued
  use as the input device across every AEC session in this repo's
  history. Subjective, not measured, but consistent enough to record.

## Not done here

- No second independent session confirming the Audio Enhancements
  pattern -- this is one matched N=3 comparison, one room, one night.
- Did not test above 30% system volume -- deliberately, for comfort
  (late-night testing, operator declined to go louder).
- Did not test a genuinely different room, or reposition the mic within
  this room, to see whether "the room dominates" (the 2026-07-27 macOS-
  session-adjacent finding, tested on Windows previously) generalizes to
  this hardware too -- discussed as a live option, not done tonight.
- Did not test a true single-link headset (mic physically isolated from
  the speaker path, same device) -- not for lack of trying: the
  operator's OpenComm headsets' own mic would drop input/output to
  phone-call quality and that headset is unreliable running simultaneous
  mic+headphone over Bluetooth; a separate headset needs a TRRS adapter
  of unconfirmed compatibility with this machine's audio jack; a
  previously-usable Astro A50 has since failed across multiple points
  (dead mic, fried headset audio, dead wireless battery, degraded
  earcups) and is no longer usable for this kind of test.
- Did not investigate WHAT Windows Audio Enhancements actually does under
  the hood (which specific DSP stages, whether it's driver-vendor-specific
  behavior tied to this Realtek chipset rather than a general Windows
  behavior) -- observed the effect, did not root-cause the mechanism.
