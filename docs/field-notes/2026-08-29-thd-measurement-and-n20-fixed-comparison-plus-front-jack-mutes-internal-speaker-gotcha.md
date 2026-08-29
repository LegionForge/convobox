---
title: A tight N=20 fixed-setting comparison reproduces the grid finding exactly; a THD sweep gives the first objective distortion measurement (clean signal at 4kHz, matching JP's own "tinny" report); and a real methodology gotcha found along the way -- something plugged into the Mac mini's front jack mutes the internal speaker regardless of software output-device selection
status: validated-live
date: 2026-08-29
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 4b845e6; scripts/acoustic_calibration.py; new ad hoc scratch script thd_sweep.py (not committed, not part of the ConvoBox package)
hardware: same Mac mini M4 + AIRHUG 28 mic as every note in this series. External speakers: small amplified Logitech computer speakers, own gain ~24-50% of max, asymmetric mic distance (0.75m right / 1.5m left) -- see the 2026-08-28 note for full detail. This note's Phase 5/6 data was collected both with the external speakers connected (external half) and physically unplugged (internal half, confirmed via `sd.query_devices()` -- the "External Headphones" device disappeared entirely).
evidence:
  - Phase 5: N=20 fixed-setting comparison (100% system volume, `aec_delay_ms=309`, baseline 250ms threshold), internal vs external, `scripts/acoustic_calibration.py`. Internal data collected AFTER unplugging (see the jack-mute finding below for why a first attempt was invalid).
  - Phase 6: THD sweep, new script (200/1000/4000Hz tones x 5 volumes x N=3, both speakers), with a noise-floor-gated SNR check added mid-session after an initial ungated version produced nonsensical results.
  - Raw JSON under /tmp/convobox-phase5-fixed-comparison-2026082{8,9}/ and /tmp/convobox-thd-sweep-20260829/ (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the N=20 fixed comparison and, separately, for a THD/frequency-sweep test; reported the internal speaker's own front-jack behavior after being asked to unplug; provided the "very loud, tends to distort... tinny" listening report referenced from the 2026-08-28 note)
    - Claude Code (Anthropic claude-sonnet-5) -- built and debugged the THD script, ran all sweeps, root-caused the jack-mute confound, wrote this note
  org: https://legionforge.org
  created: 2026-08-29T15:00:00-05:00
  revised: 2026-08-29T15:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# N=20 confirmation, first THD measurement, and a real jack-mute gotcha

## Methodology gotcha: something in the front 3.5mm jack mutes the internal speaker, regardless of software output-device selection

While running a tight internal-vs-external comparison with the external
speakers still connected (output explicitly pinned to `audio.
output_device: "Mac mini Speakers"` in convobox.yaml, NOT relying on
the macOS system default), the internal-speaker half came back
suspiciously clean (mean AEC false-barges 0.85, vs. the 2026-08-27
grid's own 9.7 at the identical nominal setting). Root cause, found by
comparing `raw_playback_rms` between the two runs: **0.0047 with the
external speakers plugged in vs. 0.0944 with them unplugged -- a 20x
drop**, despite both being "100% system volume" through explicitly
selected "Mac mini Speakers."

**Finding: physically plugging anything into the Mac mini's front
3.5mm analog jack attenuates/mutes the internal speaker at the hardware
level, even when a different output device is explicitly selected in
software.** The jack-sense circuit does not appear to respect an
application-level output-device override. Confirmed by unplugging and
rerunning: `raw_playback_rms` returned to 0.0971, matching the original
grid almost exactly.

**Practical implication for anyone testing multiple output devices on
this class of hardware**: any internal-speaker measurement taken while
something is plugged into the front port is suspect, REGARDLESS of
which device the software thinks it's using. Always verify
`raw_playback_rms` (or just listen) is in the expected range, not just
that `sd.query_devices()`/config shows the intended device selected.

## Phase 5: N=20 fixed-setting comparison confirms the grid finding exactly

Once genuinely isolated (external unplugged), 100% system volume,
`aec_delay_ms=309`, baseline 250ms threshold, N=20 each:

| | raw false-barges | AEC-processed false-barges |
|---|---|---|
| **Internal** | 1.00 (zero variance across all 20 trials) | **9.90** |
| **External** | 2.10 | **3.15** |

Internal is ~3.1x worse than external at this tight, controlled
sample -- closely matching the 2026-08-27/28 grids' own 100%-volume
numbers (9.90 vs. 3.80 in the broader grid). This confirms the earlier
grid-level finding was real, not a grid-averaging artifact.

## Phase 6: first objective THD measurement -- a real methodology bug found and fixed along the way

Built a new ad hoc script (pure tones at 200/1000/4000Hz, FFT-based
harmonic-ratio measurement) to move the "speaker distorts at volume"
hypothesis from corroborated-by-citation-and-listening-report to
directly measured. **First attempt was badly wrong**: THD appeared to
*increase* as volume *decreased* (up to 148%+ at 200Hz, 20% volume) --
backwards from any real distortion mechanism. Root cause: the room's
ambient noise floor is roughly volume-independent, so a weak tone at
low system volume sinks toward it, inflating the harmonic/fundamental
ratio spuriously -- a classic THD-measurement pitfall, not a real
finding. **Fixed** by capturing a noise-floor sample immediately before
each tone (same trial, same conditions), denoising each harmonic bin
against its own noise floor, and flagging any trial with fundamental
SNR below 20dB as unreliable rather than silently trusting it.

### Clean signal: 4kHz, right in the perceptually "tinny" range

| volume | internal 4kHz THD | external 4kHz THD |
|---|---|---|
| 100% | **4.66%** | 1.02% |
| 75% | 0.66% | 1.58% |
| 50% | 0.81% | 9.79%* |
| 35% | 0.36% | too quiet to measure |
| 20% | 0.47% | too quiet to measure |

Internal speaker's 4kHz THD peaks at 100% volume then drops and stays
low across the rest of the range -- a real, modest, volume-dependent
distortion signature, and the first genuinely objective measurement
supporting this whole field-note series' hypothesis. At the same
setting, internal is 4.6x more distorted than external. \*External's
50% value is a single-frequency outlier against an otherwise-clean
lower range -- not investigated further, possibly a room mode or a
borderline-SNR artifact even above the 20dB gate.

### Murkier signal: 200Hz and 1000Hz

200Hz on internal: 17.59% (100%) -> 3.15% (75%) -> 2.17% (50%) -> 2.80%
(35%) -> 6.52% (20%) -- a U-shape, not a clean monotonic trend.
1000Hz on internal is stranger still: roughly flat at 14-20% across
100%/75%/50%/35% (only 20% dropped for low SNR) -- essentially
volume-INDEPENDENT, which does not fit a simple "driver distorts more
at higher output level" story. **Not explained by this session's
data.** Plausible candidates, none confirmed: a chassis/enclosure
resonance near 1kHz's own harmonics that's excited similarly regardless
of drive level (a Q-peaked mechanical resonance, not electrical/
acoustic nonlinearity); a room mode at one of the harmonic frequencies;
residual SNR effects even above the nominal 20dB gate. Worth its own
follow-up (a proper swept-sine frequency response, not just 3 discrete
tones) if this specific question matters going forward.

## Direct human corroboration, restated with the new data in context

JP's own listening report (2026-08-28, quoted more fully in the
2026-08-28 field note): internal speaker "very loud and tends to
distort" starting ~75% volume, described as "loud, a bit tinny and
annoying," explicitly not digital clipping -- with the caveat that this
is a subjective judgment from an experienced musician with disclosed
tinnitus (~8-8.5kHz) and hearing loss above ~9kHz. The 4kHz THD result
above is the first measurement that overlaps with a frequency range
JP's own hearing should still register clearly (below his ~9kHz
rolloff), and it does show real, if modest, distortion peaking exactly
at the volume JP reported hearing it. This is now a three-legged
finding: behavioral (AEC false-barge grids), subjective (JP's ear), and
objective (THD at 4kHz) -- all pointing the same direction, though none
alone would have been fully conclusive.

## What this does NOT show

- Does not explain the 1kHz flat-THD anomaly -- flagged, not resolved.
- Does not constitute a full frequency-response characterization --
  only 3 discrete tones tested, not a continuous sweep.
- The external speaker's low-volume THD is simply unmeasurable with
  this setup (too quiet relative to room noise below 75% volume) --
  absence of a high-THD reading there is not evidence of low
  distortion, just an SNR ceiling on what this test can say.
- 4kHz's clean result is one frequency, one session, N=3 per condition
  -- a real signal, not yet a large-sample-validated one.

## Addendum (same day, after the above): external speakers ALSO distort badly when their own gain is maxed

All prior external-speaker data in this and the 2026-08-28 note was
collected with the speakers' own physical gain dial at ~24-50% of max.
Later the same day, JP turned that dial to 100% and asked for a quick,
carefully incremental safety-checked sweep (single trials, JP
confirming comfort/safety by ear before each step up: 5% -> 15% -> 30%
-> 50% -> 75% system volume, stopped there deliberately before 100%):

| system volume (dial maxed) | raw false-barges | AEC false-barges |
|---|---|---|
| 50% | 1 | 5 |
| 75% | 1 | **18** |

At 75% system volume with the speaker's own gain maxed, JP described
the sound directly: "loud and almost painful, just below that of a
rock concert" at ~2m -- and the AEC-processed false-barge count spiked
to 18 in a single trial, the highest single-trial count recorded
anywhere in this entire field-note series (higher even than the
internal speaker's own worst 100%-volume trials, which topped out
around 14). **This is a real, if small-N, confirmation that the
external speakers are not immune to this problem in general -- they
just don't hit it in their normal (~24-50% gain) operating range.**
Consistent with the working hypothesis throughout this series: it's
driving ANY speaker hard enough into its own distortion regime that
breaks AEC3's linear model, not something uniquely wrong with the Mac
mini's internal driver specifically. JP stopped the sweep at 75%
deliberately (further volume judged unnecessary for the data and
approaching genuinely uncomfortable/risky territory) -- this is N=1 per
level, directional, not a replacement for the fuller N=3/N=10 grids
run at the speakers' normal gain elsewhere in this series.

**Practical note for JP:** worth turning the external speakers' own
gain dial back down to roughly its prior 24-50% setting for regular
use -- this addendum confirms running them at max gain reaches
uncomfortable listening levels well before 100% system volume, with no
practical AEC benefit to compensate.

## Recommended follow-ups (not started)

1. A proper root-caused explanation (or at least a ruled-out list) for
   the 1kHz flat-THD anomaly -- possibly worth a swept sine (continuous
   frequency response) rather than 3 discrete points.
2. JP's own suggestion: consolidate THD + RT60 (2026-08-11, also never
   committed) + the existing AEC/VAD grid into one committed
   `scripts/hardware_profile.py` -- a standing "hardware health check"
   tool for this and other realtime-voice projects, rather than another
   scratch script rediscovered from a field note later.
3. JP's longer-horizon idea, explicitly out of scope for this session:
   a ConvoBox mobile frontend (Android/mobile Linux/iOS) with a
   local-vs-server-side STT/TTS architecture decision -- flagged as a
   good idea worth its own dedicated planning session.
