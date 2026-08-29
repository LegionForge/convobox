---
title: Filling a real gap in the 2026-08-29 THD data -- external speakers' 200/1000/4000Hz THD across the full 20-75% volume range, revealing a genuinely new finding at 200Hz that gets WORSE with volume (13% at 50% to 48% at 75%), unlike their clean 1kHz/4kHz behavior
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 45d35f9; scripts/hardware_profile.py (thd subcommand)
hardware: same Mac mini M4 + AIRHUG 28 mic + external Logitech speakers as the 2026-08-27 through 2026-08-30 series. Speaker gain dial confirmed at ~50% of max (mid-range, not maxed), same as the RT60 session.
evidence:
  - THD sweep, scripts/hardware_profile.py thd, 200/1000/4000Hz x 20/35/50/75% macOS system volume x N=3, external speakers, run autonomously during a self-paced /loop R&D session while JP was away.
  - Raw JSON under /tmp/hardware-profile-loop-20260830/external-thd-gapfill.json (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for a self-paced autonomous /loop to do R&D "around convobox and the test suite and what we can test while the external speakers are connected at 50%" while away)
    - Claude Code (Anthropic claude-sonnet-5) -- identified the gap, ran the sweep, found the finding, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T00:00:00-05:00
  revised: 2026-08-30T00:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# External speaker 200Hz THD gap-fill: a new, volume-dependent bass distortion finding

## Why this run

The 2026-08-29 THD field note fully characterized the INTERNAL speaker's
THD at 200/1000/4000Hz across volumes, but only reported the EXTERNAL
speaker's THD at 4kHz (1.02% at 100% volume, clean) -- 200Hz and 1000Hz
were never captured for the external speakers at all. With the external
speakers still connected mid-session (dial confirmed at ~50% of max,
unchanged) and JP away, this was flagged as a genuinely new, non-
redundant gap worth filling before anything gets unplugged.

## Result: 1kHz and 4kHz stay clean across the whole range, but 200Hz gets WORSE with volume

| volume | 200Hz THD | 1000Hz THD | 4000Hz THD |
|---|---|---|---|
| 20% | unreliable (SNR 8-13dB) | 2.2% | 0.1-2.0% |
| 35% | unreliable (SNR 16-19dB) | 0.7-0.9% | 0.05-0.72% |
| 50% | **13.0-14.5%** (SNR 21-23dB, reliable) | 0.24-0.49% | 0.13-0.49% |
| 75% | **46.1-48.4%** (SNR 29-34dB, reliable) | 0.41-0.42% | 0.43-0.50% |

1kHz and 4kHz both stay comfortably under 1% THD across the entire
volume range once SNR clears the 20dB gate -- consistent with, and now
extending, the earlier 4kHz-only finding that these external speakers
are clean at midrange/treble frequencies even at fairly high volume.

**200Hz is a completely different story.** Once SNR is high enough to
trust the number (50% volume and up), THD is already 13% and climbs to
a striking **46-48% at 75% volume** -- an order of magnitude worse than
1kHz/4kHz at the same setting, and clearly volume-dependent in a way
1kHz/4kHz are not. The 20-35% readings are flagged `snr_ok: False` by
the tool's own gate and should not be read as "low distortion" -- they
may well be similarly bad, just unmeasurable at this SNR (same caveat
the original 2026-08-29 note gave for external speaker low-volume
measurements generally).

## Plausible mechanism (not confirmed)

This is consistent with a small, ported/passive-radiator consumer
speaker driver reaching its mechanical excursion limit at low
frequencies well before the same happens at midrange/treble -- a common
signature in cheap computer speakers, sometimes audible as "chuffing"
or "farting" port noise at loud bass. Not confirmed by any independent
method this session (no accelerometer/near-field port measurement, no
listening test specifically at 200Hz) -- flagged as the most likely
explanation given the frequency-selective, volume-climbing pattern, not
asserted as proven.

## Why this matters for the AEC/barge-in question this whole series has chased

This adds a plausible NEW contributor to "why does AEC get worse at high
volume" specifically for content with real bass energy: TTS speech has
relatively little energy at 200Hz compared to speech-band frequencies,
so this finding likely does not change the primary conclusion of the
2026-08-27 through 2026-08-29 grids (which used real Piper TTS audio,
not pure tones) -- but it does mean any future test signal, tool, or
real-world audio path with meaningful low-frequency content on these
external speakers should expect materially worse linearity than the
1kHz/4kHz numbers alone would suggest.

## What this does NOT show

- Does not identify the physical mechanism (driver excursion vs. port
  resonance vs. enclosure vibration) -- pattern-matched to a common
  failure mode, not measured directly.
- Does not retest the internal speaker at this same volume grid for
  200Hz -- the 2026-08-29 note already has that data (17.59% at 100% down
  to 2.17-6.52% at lower volumes on internal, its own separate U-shaped
  pattern) and it was not rerun here to avoid redundant live-audio time
  while the loop's actual purpose (broad autonomous R&D) continued.
- 20-35% volume readings are explicitly unreliable per the tool's own
  SNR gate, not evidence of low distortion at those levels.

## Recommended follow-ups (not started)

1. If this speaker's bass distortion ever matters practically (e.g. a
   future ConvoBox feature plays audio with real bass content), consider
   a simple gentle high-pass/bass-limiting default for this class of
   small consumer speaker, distinct from the already-proposed TTS output
   limiter (`docs/KNOWN-ISSUES.md`'s soft-limiter candidate), which was
   scoped around overall level, not frequency-selective distortion.
2. A closer, port-adjacent mic placement (not this session's normal
   listening-position placement) would help confirm/refute the port-
   noise hypothesis specifically, if it's ever worth the effort.
