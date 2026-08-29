---
title: Building a Farina/ESS sweep tool from scratch surfaces two real implementation bugs (a missing 2*pi in the sweep phase, and a 1.5s frequency-response window that measured room decay instead of the speaker) -- caught only because independent discrete-tone THD data already existed to sanity-check against
status: validated-live
date: 2026-08-29
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 0995413; new ad hoc scratch script ess_sweep.py (not committed, not part of the ConvoBox package)
hardware: same Mac mini M4 + AIRHUG 28 mic + external Logitech speakers as the same day's other 2026-08-29 note. All devices confirmed native 48kHz (`sd.query_devices()[i]["default_samplerate"]`).
evidence:
  - 4 iterative smoke-test runs of a new exponential-sine-sweep (ESS/Farina) measurement script, each fixing one real bug found by comparing against the already-validated discrete-tone thd_sweep.py data from earlier the same day.
  - Raw JSON under /tmp/ess-smoketest{,2,3,4}.json and /tmp/ess-external-real.json (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for a denser/continuous frequency-response measurement to explain the same day's unresolved "1kHz THD sits flat" anomaly, and to combine RT60 + frequency response + harmonic distortion into one tool; explicitly chose to keep debugging rather than abandon the approach when the first results looked wrong)
    - Claude Code (Anthropic claude-sonnet-5) -- built the script, found and fixed both bugs, ran the corrected version, wrote this note
  org: https://legionforge.org
  created: 2026-08-29T17:30:00-05:00
  revised: 2026-08-29T17:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Building an ESS/Farina sweep tool: two real bugs, caught by cross-checking against independent data

## Why this tool

The same day's `thd_sweep.py` work (see the other 2026-08-29 field note)
left the internal speaker's 1kHz THD result unexplained -- it sat flat
around 14-20% across almost every volume, not fitting a simple
"distortion grows with level" story, using only 3 discrete test tones
(200/1000/4000Hz). A proper exponential sine sweep (ESS, the Farina
method) measures a CONTINUOUS frequency response, RT60, and per-harmonic
distortion from a single sweep+deconvolution -- a more rigorous
instrument for exactly this question, and the same technique the
(never-preserved) 2026-08-11 RT60 field note used.

## Bug 1: missing 2*pi in the sweep's phase formula

**Symptom.** First smoke test's frequency-response bands showed an
implausible ~100dB cliff above 1.5kHz (energy at 700-1500Hz: +45dB;
at 3000-5000Hz: -73dB). Cross-checked against the SAME day's discrete-
tone THD data (validated, SNR-gated): the actual difference in signal
strength between 1kHz and 4kHz tones on the same hardware was only
about 6dB, not 100+dB. This mismatch is what triggered the decision to
keep debugging rather than trust the ESS output.

**Root cause.** The exponential sweep's phase should be
`phi(t) = 2*pi*f1*T/R * (e^{t*R/T} - 1)` (R = ln(f2/f1)) -- the `2*pi`
converts the frequency parameters (in Hz) into radians for `sin()`.
The script's first version omitted it: `K = duration * f1 / R` instead
of `K = 2*pi*duration*f1/R`. This doesn't just add a phase offset -- it
scales the ENTIRE instantaneous frequency sweep down by a factor of
`2*pi` (~6.28x): a sweep nominally configured for 100Hz-8000Hz was
actually only ever playing real acoustic energy across roughly
16Hz-1270Hz. Everything the script reported above ~1.3kHz was measuring
noise floor, not the speaker -- which lines up closely with where the
cliff appeared.

**Ruled out first (before finding the real cause):** sample-rate
mismatch. `sd.query_devices()` showed every device (AIRHUG 28,
External Headphones, Mac mini Speakers, even the unrelated JetKVM
capture device) natively runs at 48000Hz; the script defaulted to
44100Hz, forcing an OS-level resample. Farina deconvolution is
extremely timing-sensitive (unlike discrete-tone FFT peak-finding,
which tolerates modest clock drift fine), so this looked like a
plausible cause -- fixed the sample rate to 48000Hz first. It did NOT
fix the cliff, which is what motivated looking at the sweep-generation
math itself next, where the real bug was found.

## Bug 2: a 1.5-second frequency-response window measured room decay, not the speaker

Fixing bug 1 alone still left the frequency-response bands noisy.
Separately, the script's frequency-response window (meant to capture
the direct-sound peak of the deconvolved impulse response) was sized
at 30% of the sweep's own duration -- with a 5s sweep, a 1.5-SECOND
window. A window that long captures mostly reverberant room decay, not
the speaker's direct output -- and since reverberant energy decays
faster at high frequencies in most rooms (air/surface absorption), a
too-long window produces a spurious low-pass-shaped "frequency
response" that actually just shows which frequencies persist longest
in the room. Fixed to a standard ~20-25ms window (5ms pre-peak, 20ms
post-peak) -- direct sound plus at most one early reflection, not the
tail.

## Result after both fixes: a believable, if imperfect, measurement

External speakers (100Hz-8000Hz sweep, 10% system volume, safe/
comfortable level):

| band | energy (relative dB) |
|---|---|
| 100-300Hz | 46.3 |
| 300-700Hz | 35.0 |
| 700-1500Hz | 36.5 |
| 1500-3000Hz | 37.2 |
| 3000-5000Hz | 34.6 |
| 5000-8000Hz | 41.0 |

Flat within ~12dB across the whole range -- plausible for a small
consumer speaker, and consistent in shape (if not exact numbers, since
volume/method differ) with the earlier discrete-tone THD data's own
implication that this speaker doesn't have an extreme tonal skew.
Harmonics: 2nd 5.4%, 3rd 2.9%, 4th 4.1%, 5th 5.1% -- all in a similar,
plausible single-digit-percent range, unlike the wildly-varying
12-15%-across-every-order pattern the buggy version produced.

## Still unresolved: RT60 does not match the 2026-08-11 measurement

This script's RT60 (Schroeder backward integration of the deconvolved
impulse response's decay) gave T20=1.70s/T30=1.72s, extrapolating to
RT60 estimates of 5.1s (from T20) and 3.4s (from T30) -- both far
longer than, and internally inconsistent with, the 2026-08-11 field
note's own measurement in the same room (T20 0.21s, T30 0.46s, RT60
estimates under 1s, N=50 repeat measurements, tightly reproducible).
**Not resolved this session.** Candidate explanations, none confirmed:
a genuinely different measurement position/distance this time (this
was a near-field loopback test, mic close to the small external
speakers, not the same setup as the original room-acoustics
measurement); a flaw in this script's own Schroeder-integration window
or the underlying deconvolved IR's usability for RT60 specifically
(the ESS technique's RT60 extraction is more failure-prone than its
frequency-response/harmonic extraction, since it depends on a clean
low-noise decay tail that a close-mic'd, short-duration test may not
provide). **Do not trust this script's RT60 output without further
validation** -- treat the frequency response and harmonic numbers
above as the reliable part of this tool for now.

## Lesson, generalizable beyond this one script

Having independent, already-validated data (the discrete-tone THD
sweep) to sanity-check a more sophisticated new measurement against is
what turned "these numbers look weird" into "here are two specific,
fixable bugs" rather than either abandoning the approach or publishing
wrong data. Neither bug was obvious from reading the code alone --
both were only caught by asking "does this match something I already
trust?" This is the same discipline this project's own `AGENTS.md`
already states for code changes generally (verify a bug end-to-end
before proposing a fix); it applies just as directly to a brand-new
measurement tool's own first output.

## Recommended follow-ups (not started)

1. Resolve the RT60 discrepancy -- likely needs either a longer/cleaner
   decay tail, a different windowing approach for the Schroeder
   integration specifically, or accepting that ESS-based RT60 from a
   close-mic loopback setup isn't comparable to the original room-level
   measurement and re-scoping what this tool's RT60 output is actually
   good for.
2. Run the corrected script on the INTERNAL speaker (blocked this
   session by the same front-jack-mute issue documented in the other
   2026-08-29 note -- needs the external speakers unplugged first) to
   get a real continuous frequency-response comparison and finally
   investigate whether the 1kHz THD anomaly corresponds to a visible
   resonance peak in a proper sweep, not just 3 discrete points.
3. **Done (2026-08-29, later the same day):** consolidated this tool and
   `thd_sweep.py` into a committed `scripts/hardware_profile.py` (`thd`
   and `sweep` subcommands), with unit tests on the pure-math paths
   (`tests/test_hardware_profile.py`) -- but RT60 was carried over
   AS-IS, still flagged unverified, not resolved by the consolidation
   itself. The existing `acoustic_calibration.py` AEC/VAD grid was left
   separate, not folded in -- it depends on ConvoBox's own runtime
   (EchoCanceller, VAD, TTS) in a way `hardware_profile.py` deliberately
   does not, to keep it usable as a standalone hardware diagnostic.
