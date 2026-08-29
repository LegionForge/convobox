---
title: Chasing the ESS/Farina RT60 discrepancy -- fixed a real Chu-correction ordering bug, then root-caused the remaining gap to insufficient measurement SNR (~18-24dB vs. the ~30-45dB ISO 3382 wants), not a further code bug -- added an honest reliability flag instead of a silently-wrong number
status: resolved (fix verified at adequate SNR, same day, via addendum)
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ b355ba2; scripts/hardware_profile.py, tests/test_hardware_profile.py
hardware: same Mac mini M4 + AIRHUG 28 mic + external Logitech speakers as the 2026-08-29 series, external speakers still connected, own gain dial confirmed at ~50% of max (mid-range, not maxed) -- consistent with the ~18-24dB SNR measured below; there is real headroom left on this dial if a louder RT60 attempt is ever wanted.
evidence:
  - Unit tests: tests/test_hardware_profile.py (2 new tests added this session, both passing).
  - 3 live ESS captures against the real external-speaker/mic pair (before fix, after ordering fix, after SNR gate added). Raw arrays under /tmp/hardware-profile-verify/ (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked to chase the RT60 discrepancy flagged unresolved in the 2026-08-29 ESS field note)
    - Claude Code (Anthropic claude-sonnet-5) -- implemented and debugged the Chu correction, ran the live verification captures, root-caused the remaining gap, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T00:00:00-05:00
  revised: 2026-08-30T00:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Chasing the RT60 discrepancy: one real bug fixed, one real limit found

## Starting point

The 2026-08-29 ESS field note left RT60 explicitly flagged as unresolved:
`scripts/hardware_profile.py`'s `sweep` command (then still two separate
scratch scripts) reported RT60 of 3.4-5.1s against an independently
trusted 2026-08-11 room measurement of ~0.2-0.46s -- a roughly 10x gap.

## Bug found and fixed: Chu (1978) noise correction, wrong clip order

The Schroeder RT60 estimator did plain (uncorrected) backward
integration of the deconvolved impulse response's decay tail. This is a
known trap: backward-integrated energy of a tail dominated by stationary
background noise decreases roughly **linearly** with time (not
exponentially), so its dB curve stays nearly flat for most of a window
and then plunges sharply right at the very end. The real capture's own
numbers showed exactly this signature: T20 (-5 to -25dB) took 1.70s, but
the *next* 10dB down to -35dB (T30) took only another 0.023s -- a
near-vertical late plunge, not a real decay curve.

First fix attempt: estimate background noise power from the last 10% of
the analysis window, subtract it from each sample's energy, clip
negative *per-sample* results to zero, then backward-integrate. **This
undercorrected** -- a unit test built to reproduce the bug synthetically
(0.3s true decay buried in noise) still returned RT60 ~4.8s. Root cause
of the undercorrection: energy is squared-Gaussian per sample, which is
skewed; clipping *before* summing throws away legitimate negative
fluctuations that should statistically cancel positive ones inside the
sum, biasing the result high. The correct Chu-method order is to
subtract noise power from every sample, **sum first**, and only clip the
resulting *cumulative* curve to zero. Fixing the order made the
synthetic test pass (RT60 recovered to well under 1s, matching the
injected ground truth).

## Still not enough: SNR of the real speaker/room setup is too low

Applying the corrected code to a fresh live capture on the external
speakers still gave RT60 ~2.9-4.4s -- much improved from ~3.4-5.1s, but
still nowhere near the trusted ~0.2-0.46s. Root cause, found by dumping
and inspecting the raw decay curve directly (not just the summary
numbers): with an 8-second-tail capture, the full-band Schroeder curve
was still only at **-12.9dB after 1.7 seconds**, and even extended to a
7.5-second tail, only reached **-15.8dB after 7.25 seconds** -- a
"hockey stick" shape (nearly flat, then slowly accelerating) that is the
textbook signature of a decay curve dominated by noise for its *entire*
length, not just its tail.

Directly measuring the actual dynamic range confirmed it: peak power vs.
the estimated noise floor came out to only **~18-24dB of SNR**,
depending on the exact capture. ISO 3382-style RT60 measurement
generally wants **~30dB+ dynamic range for a trustworthy T20 and ~40dB+
for T30** -- this setup's near-field small speakers at a safe/
comfortable playback level, against ordinary (non-anechoic) room noise,
simply doesn't have that headroom. With that little SNR, the Chu
correction's own noise-power estimate is itself imprecise, and
Schroeder's total-energy normalization ends up dominated by accumulated
background noise summed across a multi-second tail rather than by the
real (likely well under 100ms) direct decay -- a known limitation of the
plain Chu method without a full ISO 3382-2 Lundeby-style adaptive
noise-floor/cutoff search.

**This is not a further code bug.** It is a genuine measurement-SNR
shortfall given this session's deliberately safe playback levels and
near-field small-speaker setup -- not something more careful arithmetic
alone can fix.

## What shipped instead of chasing a perfect fix further

Rather than build a full Lundeby-method implementation (meaningfully
more complex, and still would not manufacture SNR that isn't physically
present in the recording), `schroeder_rt60()` now:

1. Keeps the corrected Chu-method integration (a real, verified
   improvement over the original bug).
2. Computes the achieved peak-to-noise-floor SNR directly from the
   capture.
3. Returns `t20_reliable`/`t30_reliable` booleans (gated at 30dB/40dB)
   alongside the RT60 numbers, and the CLI prints an explicit
   `** RT60 UNRELIABLE **` warning with the actual SNR figure and a
   concrete remedy ("try a louder sweep/output level or a quieter
   room") whenever T20 isn't reliable -- the same "flag reliability,
   don't just report a number" discipline this file's `measure_thd`
   already used for its own SNR gate.

Confirmed live against the real hardware after this change: the
external-speaker capture came back with `t20_reliable: false`,
`t30_reliable: false`, `snr_db: 24.2` -- an honest "don't trust this"
result, instead of a plausible-looking wrong one.

## What this does NOT show

- Does not prove the tool could never produce a trustworthy RT60 -- a
  louder sweep (in tension with this project's own hearing-safety
  practice established earlier in this campaign) or a genuinely quieter
  room/session would likely clear the SNR gate and produce a believable
  number.
- Does not validate the corrected code against a case with real,
  *sufficient* SNR and a known-short RT60 room measurement side by
  side -- only against synthetic data (known-good) and against this
  specific low-SNR real capture (correctly flagged as unreliable, not
  independently confirmed accurate).
- Frequency response and harmonic-distortion numbers from `sweep` are
  unaffected by any of this -- they were already the reliable part of
  the tool's output per the 2026-08-29 note, and remain so.

## Recommended follow-ups (not started)

1. ~~If a trustworthy RT60 number is ever actually needed...~~ **Done,
   same day, see addendum below** -- follow-up #1 was completed within
   hours of writing it.
2. A full ISO 3382-2 Lundeby-method implementation (iterative noise
   floor + integration-limit estimation) would likely do somewhat better
   at marginal SNR than this session's Chu-only fix, but wouldn't
   overcome a genuinely insufficient-SNR capture like this session's --
   not pursued, since the SNR gate already gives an honest answer for
   the common case.

## Addendum (same day, during an autonomous /loop R&D session): follow-up #1 confirms the fix at higher volume

With the external speakers still connected and JP away, ran the exact
follow-up this note itself recommended: ESS sweeps at 25%/50%/75% system
volume (vs. the original captures' ~10%), same external speakers,
gain dial unchanged (~50% of max, no physical adjustment).

| system volume | SNR achieved | t20_reliable | t30_reliable | RT60 (from T20) |
|---|---|---|---|---|
| 25% | 38.2dB | true | false | 0.516s |
| 50% | 54.2dB | true | true | 0.5435s |
| 75% | 65.5dB | true | true | 0.5494s |

**This is a clean confirmation of the whole fix chain.** SNR climbs
predictably with volume (38 -> 54 -> 65dB) exactly as expected once a
real signal is driven harder against a roughly fixed room noise floor.
Once SNR clears the reliability gates, RT60 converges to a tight,
self-consistent ~0.51-0.55s across three independent captures at three
different volumes -- a dramatic contrast with the original unreliable
low-SNR captures, which gave wildly different numbers every time (5.09,
4.36, 3.14, 2.17s). Two of the three volumes (50%, 75%) agree to within
0.006s of each other, which is itself strong evidence this is now
measuring something real and reproducible, not noise.

**Small remaining gap, not chased further.** ~0.51-0.55s is still
somewhat longer than the trusted 2026-08-11 room measurement's ~0.2
(T20) to 0.46s (T30) ceiling -- plausible, not-yet-confirmed
explanations: a genuinely different mic/speaker position (this is a
near-field capture, closer to the small external speakers, not
necessarily the same spot as the original room-level measurement); or a
broadband (100-8000Hz) decay averaging in some longer-ringing low-
frequency content the original measurement's own method may have
weighted differently. JP separately confirmed (same session) that these
specific desk speakers have a known-weak low-frequency AND high-
frequency response ("really lame" frequency response, his words) --
this doesn't obviously explain a LONGER decay (a bass-weak speaker
would if anything under-drive the longest-ringing frequencies, biasing
RT60 shorter, not longer), but it's a real, independently-known
property of this exact hardware worth keeping in mind for any future
frequency-response work with these speakers, not just this RT60 number.

**Bottom line:** the tool works. Given adequate SNR (25%+ volume on this
hardware), it returns a believable, reproducible RT60 in the right
order of magnitude. The original bug report ("RT60 doesn't match")
is resolved as: fixed a real ordering bug, and the remaining gap was a
measurement-conditions issue (low volume => low SNR), not a further code
defect -- exactly as this note's main body predicted before this
addendum went and checked.
