---
title: Real external speakers on the Mac mini M4's front 3.5mm port essentially eliminate the AEC-worse-at-volume problem at 75% and below -- direct confirmation the built-in speaker was the driver, not AEC3 itself
status: validated-live
date: 2026-08-28
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 8bdd7db; WebRTC AEC3 via aec-audio-processing; tts.engine=piper, voice=en_US-lessac-medium, tts.volume=4.0; interrupt_preset=conversational; barge_in_min_speech_ms=250 (baseline/unmitigated, same as the 2026-08-27 internal grid); vad.threshold=0.5 (default); scripts/acoustic_calibration.py
hardware:
  computer: Mac mini M4 (2024), same machine as every prior note in this series.
  microphone: AIRHUG 28 USB conference mic, unchanged from prior sessions.
  output_device_A (internal, prior grids): "Mac mini Speakers" (Core Audio) -- single built-in driver.
  output_device_B (this note): small amplified Logitech computer speakers ("nothing special" per JP), own physical volume dial at roughly 24-50% of max throughout, physically connected to the Mac mini's front 3.5mm analog port this session -- macOS names this output "External Headphones" generically regardless of what's actually plugged in. Mic placement asymmetric relative to the pair: ~0.75m from the right speaker, ~1.5m from the left.
  volume_calibration: speaker's own physical gain was unknown at session start. Confirmed via three manual single-trial checks (JP listening) before the unattended sweep: 20% system volume = too quiet to hear; 50% = still quiet; 100% = "comfortable, good level." Full 100/75/50/35/20 range then run unattended, same range as every internal-speaker grid.
room: unchanged from prior notes.
evidence:
  - 250 real live trials: 5 aec_delay_ms candidates (auto,222,272,309,322) x 5 volumes (100,75,50,35,20) x N=10, external speakers, baseline (250ms) barge-in threshold -- directly comparable to the 2026-08-27 internal-speaker grid (same candidates, same volumes, same threshold, only the output device differs).
  - 3 manual pre-sweep single trials (20%/50%/100%) for volume-safety calibration.
  - Full JSON under /tmp/convobox-external-baseline-sweep-20260828/vol{100,75,50,35,20}/<timestamp>/report.json (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; physically attached the external speakers mid-session, confirmed comfortable volume for each pre-sweep check, asked for a follow-up N=20 fixed-setting comparison -- see the next field note in this series)
    - Claude Code (Anthropic claude-sonnet-5) -- built the driver, ran the volume-safety checks and the full sweep unattended, aggregated and compared against the internal-speaker baseline, wrote this note
  org: https://legionforge.org
  created: 2026-08-28T22:10:00-05:00
  revised: 2026-08-28T22:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# External speakers essentially eliminate the AEC-worse-at-volume problem

**Context.** Every note in this series so far (2026-08-09, 2026-08-11
x4, 2026-08-20 on Windows, 2026-08-27, 2026-08-28 mitigation) measured
or mitigated a real, repeated finding: on this Mac mini M4's own
built-in speaker, AEC-processed audio produces MORE false barge-ins
than leaving AEC off, worse at higher volume. The leading hypothesis
throughout has been **corroborated but never directly measured**: the
single built-in speaker (Apple's own spec lists it singular; reviews
describe it as prone to distortion at volume) may be acoustically
distorting, and a linear echo canceller (AEC3) structurally cannot
cancel a nonlinear/distorted path. This session, JP attached real
external powered speakers -- the first time this hypothesis could be
tested directly rather than inferred.

## Result: external speakers mostly make the problem disappear

Same volumes, same delay candidates, same (unmitigated) threshold as
the 2026-08-27 internal-speaker grid -- external vs. internal, mean
false-barges per volume level (50 trials each):

| volume | external raw | external AEC | internal raw | internal AEC |
|---|---|---|---|---|
| 100% | 2.54 | 3.80 | 1.00 | 9.90 |
| 75%  | 0.00 | 0.02 | 1.00 | 9.82 |
| 50%  | 0.00 | 0.00 | 1.00 | 4.60 |
| 35%  | 0.04 | 0.04 | 2.36 | 1.80 |
| 20%  | 0.00 | 0.00 | 1.52 | 1.28 |

**At 75% and 50% volume, external speakers produced essentially zero
false barge-ins of any kind** -- not "AEC helps," but almost no
echo-driven VAD triggering at all, raw or processed. Compare internal's
same two volumes: 9.82 and 4.60 mean AEC-processed false-barges. This
is the single clearest piece of evidence yet that the built-in speaker,
not AEC3 or the VAD pipeline generally, is the primary driver of this
whole finding class.

**At 100% volume, external speakers still show a real but much smaller
effect**: raw jumps to 2.54 (up from its usual flat ~1.00), AEC-
processed to 3.80 -- a ~1.5x AEC-vs-raw ratio, compared to internal's
~10x at the same volume. The raw baseline itself rising at 100% on
external speakers suggests even a real external speaker can start
producing genuine (not necessarily distorted) louder echo at max
volume that any AEC has more work to do on -- a different, milder,
more expected phenomenon than the internal speaker's dramatic
AEC-makes-it-worse pattern.

**Per-delay-candidate at 100% (external)**: auto 2.60, 222ms 3.60,
272ms 4.50, 309ms 4.00, 322ms 4.30 -- no single candidate stands out;
consistent with the internal-speaker grids' own finding that this
isn't a delay-tuning problem.

## Interpretation

This is the first genuinely direct evidence (not corroboration-by-
citation-of-general-speaker-reviews) for the distortion hypothesis this
whole field-note series has carried since 2026-08-09/2026-08-11.
External speakers don't just reduce the problem -- at 50-75% volume
they nearly eliminate it outright, the volume range most people would
actually work at. The residual effect at 100% is real but is a
different, smaller-magnitude phenomenon (raw echo increasing with
volume, a normal AEC challenge) rather than the dramatic
AEC-fragments-the-signal pattern seen on the internal speaker.

## Direct human corroboration of the distortion hypothesis (2026-08-28/29, post-hoc)

After reviewing these results, JP reported directly: the Mac mini's
internal speaker is "very loud and tends to distort" starting around
75% of max volume, continuing at 100% -- perceived as "loud, a bit
tinny and annoying," explicitly **not** digital clipping. This closes
the exact gap the 2026-08-11 combined-mitigation note left open ("No
digital clipping found in the raw mic captures... but that doesn't
rule out acoustic distortion at the speaker itself, a different
phenomenon") with a direct first-person report: acoustic/driver
distortion, not digital clipping, matching this whole series'
hypothesis precisely.

**Caveat, stated by JP himself and worth preserving verbatim in
spirit:** this is a subjective judgment from an experienced musician
(decades with piano, guitar, cello, strings, choir, bass, recorder) but
with disclosed tinnitus around 8-8.5kHz and substantial hearing loss
above ~9kHz. That's a real basis for timbral pattern-recognition, but
not a substitute for an objective measurement -- if anything it
strengthens the case for a real THD/spectral-analysis follow-up (see
"What this does NOT show" below) as the way to move this from a
corroborated, human-reported symptom to a directly measured one,
independent of any one listener's hearing profile.

## External speaker hardware detail (reported after the fact)

The external speakers used this session: small amplified Logitech
computer speakers, "nothing special," own physical volume dial set to
roughly 24-50% of its own max (not maxed) throughout testing. Mic
placement relative to the pair was asymmetric: approximately 0.75m
from the right speaker, 1.5m from the left -- double the distance to
one channel vs. the other. This asymmetry is a plausible contributor to
the 100%-volume external result being noisier than 75%/50% (raw
false-barges rising to 2.54 from a flat ~0 at the lower two volumes) --
not investigated further this session, but worth controlling for (equal
distance to both channels) in any follow-up.

## What this does NOT show

- **Not proof the internal speaker is definitively "broken."** No THD
  measurement was taken on either speaker this session -- JP's own
  direct listening report (above) is a real, corroborating data point,
  but not a substitute for an objective acoustic distortion
  measurement.
- **Not a claim external speakers are a complete fix at every volume.**
  100% volume still shows a real, if much smaller, effect -- and the
  external speakers' own asymmetric placement is a live confound at
  that volume specifically.
- **Not a validated claim about external speakers in general** -- this
  result is about one specific pair (small amplified Logitech
  computer speakers, run well below their own max gain), asymmetrically
  placed. A different external speaker, or better placement, could
  behave differently in either direction.

## Recommendation

For anyone hitting this project's documented self-barge-in problem with
open speakers: **swapping the output away from a small built-in laptop/
Mac mini speaker to almost any real external speaker looks like it
should solve most of the problem outright**, likely a more complete fix
than the `barge_in_min_speech_ms=1200` software mitigation alone
(which still left a 2.4-4x gap at high volume on the internal speaker).
Headphones remain this project's existing recommended default and
sidestep the question entirely by removing the open-air echo path.

Follow-up in progress: a tighter N=20 fixed-setting (100% volume,
`aec_delay_ms=309`) back-to-back internal-vs-external comparison, at
JP's request, for a more statistically pointed sample than this grid's
per-cell N=10.
