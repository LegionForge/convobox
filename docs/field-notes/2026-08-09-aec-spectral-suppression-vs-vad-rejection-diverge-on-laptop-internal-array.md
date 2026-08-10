---
title: On a laptop-internal dual-mic/speaker array in a reflective room, AEC3's spectral suppression metrics and downstream VAD-based self-trigger rejection point to different conclusions -- optimizing one does not optimize the other
status: validated-live
date: 2026-08-09
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 192d25c (feat/web-file-upload branch); WebRTC AEC3 via aec-audio-processing; faster-whisper STT (CPU fallback, cuBLAS unavailable this session); vad.threshold 0.5 (default); scripts/acoustic_calibration.py
evidence:
  - D:/LegionForge/_uat-246-file-upload-scratch/acoustic-calibration/20260809-194144/report.json (run 1, 5 trials)
  - D:/LegionForge/_uat-246-file-upload-scratch/acoustic-calibration/20260809-194644/report.json (aborted run, ambient too high for a sweep -- 1 trial only)
  - D:/LegionForge/_uat-246-file-upload-scratch/acoustic-calibration/20260809-195238/report.json (run 2, 5 trials)
  - D:/LegionForge/_uat-246-file-upload-scratch/acoustic-calibration/20260809-195652/report.json (run 3, 5 trials)
  - D:/LegionForge/_uat-246-file-upload-scratch/acoustic-calibration/20260809-200106/report.json (run 4, 5 trials)
  - D:/LegionForge/_uat-246-file-upload-scratch/calibration-run.log, calibration-run2.log, calibration-3runs.log (raw console output, all 5 runs)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; provided the hardware, room, and asked for repeated runs specifically to get real variance data, not a single-sample answer)
    - Claude Code (Anthropic claude-sonnet-5) -- ran all 5 calibration invocations, identified the exact hardware model via WMI, aggregated cross-run statistics, analysis, writing
  org: https://legionforge.org
  created: 2026-08-09T20:10:00-05:00
  revised: 2026-08-09T20:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# AEC3 spectral suppression and VAD-based self-trigger rejection diverge on a laptop-internal array

**Context for outsiders.** ConvoBox is a local voice frontend for CLI coding
agents: mic and speakers run simultaneously, and acoustic echo cancellation
(AEC) keeps the assistant's own TTS output from being picked back up by the
mic and misread as user speech. `scripts/acoustic_calibration.py` is a
controlled experiment: it plays a known synthesized response through the
speakers, captures it back through the mic, and reports how well WebRTC
AEC3 cancelled it at several candidate `aec_delay_ms` hint values, one of
ConvoBox's own tunable settings.

## Problem

Which `aec_delay_ms` value is best for a laptop's own built-in dual-mic
array and bottom-facing speakers -- a fully closed, chassis-internal
acoustic path, distinct from every device pair calibrated in this repo
before now (all of which used an external mic and either headphones or
external speakers; see `docs/DESIGN-echo-and-barge-in.md`)? And separately:
is a single calibration run enough to trust, or does it need repeats?

**Hardware** (identified via `Get-CimInstance Win32_ComputerSystem`/
`Win32_BIOS`/`Win32_Processor`, not assumed from memory): ASUS ROG Strix
G614JV (SystemFamily "ROG Strix", BIOS `G614JV.334`), 13th Gen Intel
Core i7-13650HX, 64GB RAM. Audio path: built-in Realtek High Definition
Audio codec, dual top-mounted mics flanking the webcam, bottom-firing
speakers toward the user. **Room**: bedroom, bed present, bare LVP
(luxury vinyl plank) floor, bare walls -- operator's own description:
"relatively wet acoustics," i.e. reflective, untreated, similar in kind
(if not degree) to the reflective room already documented in
`docs/field-notes/2026-07-27-headphone-choice-does-not-eliminate-under-cancelled-echo.md`.

## Evidence

Five independent invocations of `scripts/acoustic_calibration.py`
(default settings: 45s ambient capture, 5 delay trials of {auto/222,
122, 172, 272, 322}ms per invocation, each invocation's own fresh
ambient sample -- not repeats within one process):

| Run (started, local) | Ambient RMS | Trials completed | Per-run "best" (script's own picker) |
|---|---|---|---|
| 19:41:44 | 0.00451 | 5 | delay-322ms |
| 19:46:44 | 0.01029 | **1 (aborted)** | n/a -- "no measurable speaker echo reached the mic; skipping meaningless delay sweep" |
| 19:52:38 | 0.00350 | 5 | delay-272ms |
| 19:56:52 | 0.00436 | 5 | delay-122ms |
| 20:01:06 | 0.00114 | 5 | delay-122ms |

The second run's ambient floor (0.01029) was ~2.3-9x every other run's
(0.00114-0.00451) -- high enough that the script's own signal gate
correctly refused to run a sweep rather than report a number it
couldn't trust. This by itself is a real, useful behavior to note: the
tool fails safe on a noisy room rather than silently returning garbage.

**Aggregated across all 4 completed sweeps (n=4 per delay, 20 trials
total), by delay value:**

| Delay | Mean attenuation | Mean external suppression | Mean reference-correlation reduction | Mean VAD self-rejection |
|---|---|---|---|---|
| 122ms | 20.2dB (σ=2.7) | 11.6dB | 78.6% | **+8%** |
| 172ms | 18.7dB (σ=9.0) | 10.8dB | 69.8% | -21% |
| 222ms (auto) | 13.6dB (σ=3.4, n=5 incl. aborted run's 1 trial) | 8.7dB | 72.5% | -67% |
| 272ms | 17.3dB (σ=6.7) | 10.8dB | 84.2% | -71% |
| **322ms** | **26.0dB (σ=5.7)** | **12.5dB** | **94.8% (tightest cluster: 93.0-96.4%)** | -48% |

"VAD self-rejection" = `1 - (processed_utterances / raw_utterances)`,
i.e. how much AEC reduced the count of VAD-detected utterances during
pure TTS playback with nobody talking. Positive = fewer spurious
utterances after AEC (good); negative = AEC processing produced *more*
spurious utterances than the raw, uncancelled signal (bad).

Sample raw line (run 1, delay-322ms):
```
trial delay-322ms: delay=322ms attenuation=26.93dB ceiling=27.23dB
suppression=12.12dB echo-lag=318.0ms corr=0.063802/0.004075
false-barge raw/aec=0/0 utterances raw/aec=3/7
```
Attenuation and correlation reduction both look excellent here (0.0638
raw correlation collapsed to 0.0041, a 93.6% reduction) -- yet the raw
signal produced 3 VAD utterances during playback and the *AEC-processed*
signal produced 7. AEC made the spectral echo smaller and the VAD's
opinion of it *worse* in the same trial.

## Mechanism

**322ms is the clear, consistent winner on every spectral metric** --
highest mean attenuation, highest mean suppression, and by far the
tightest, highest reference-correlation reduction (93-96% across all 4
runs, versus 55-98% scattered ranges for every other delay). This is a
real, repeatable finding: this laptop's actual acoustic path -- however
short it looks structurally (same chassis) -- behaves, from AEC3's
perspective, like it wants roughly the same delay hint (~310-340ms
`estimated_echo_lag_ms`, consistent across literally every trial in
every run regardless of which `delay_ms` was configured) as the
external-mic-plus-headphones rig documented elsewhere in this repo at
309ms. Chassis-internal does not mean acoustically instantaneous.

**But VAD self-rejection is negative for every single delay value on
average**, including 322ms (-48%). AEC's own spectral cancellation is
demonstrably working (correlation dropping >90%), but it is not
translating into fewer Silero VAD hits -- and is often actively
producing more. The most parsimonious explanation, consistent with this
repo's own prior research (`docs/DESIGN-echo-and-barge-in.md`'s section
on nonlinear residual echo suppression): AEC3's nonlinear post-filter
stage, when it can't fully null a reflective room's multipath echo,
tends to leave behind spectrally uneven "musical noise" / warbling
artifacts rather than a clean, uniformly attenuated residual. A uniform
-20dB residual of the ORIGINAL signal is much less likely to cross a
VAD's speech-probability threshold than a smaller-amplitude but
spectrally choppy artifact -- so a technically "quieter" (lower
correlation, lower RMS) processed signal can still look "more speech-
like" to a VAD trained on real speech's spectral irregularity, exactly
the opposite of what raw dB numbers would predict. **This is a
hypothesis, not confirmed** -- it is the standard, well-documented
failure mode of nonlinear residual suppressors, but nothing in this data
set directly inspects the processed audio's spectral shape to prove it;
the WAV files (`*-aec-mic.wav` in each run's output directory) exist for
exactly this follow-up if it's worth pursuing.

**Which delay "wins" is unstable when judged by the per-run picker
(which weights utterance-count over spectral dB)**: 322ms once, 272ms
once, 122ms twice, across only 4 runs. Judged by spectral metrics alone,
322ms wins all 4 runs, no exceptions. These are two different, both
legitimate questions -- "how much did AEC reduce the echo signal" vs.
"how much did AEC reduce false self-triggers downstream" -- and this
data set is the first evidence in this repo that they can disagree on
the same hardware, same room, same delay value, same trial.

**Ruled out**: this is not simply "one noisy run corrupting the
average" -- the second (aborted) run was excluded from all delay-value
aggregates above precisely because its ambient floor was too high to
trust, and the divergence between spectral and VAD-based metrics still
holds across the 4 remaining, individually-clean runs.

## What transfers

- **A laptop-internal dual-mic/dual-speaker array does not behave like a
  "short acoustic path, therefore small delay" case.** Measured
  `estimated_echo_lag_ms` was consistently ~310-340ms here, in the same
  range as a completely different external-mic/headphone rig calibrated
  elsewhere in this repo. Chassis proximity is not a reliable predictor
  of AEC delay-hint magnitude. (validated-live, n=1 laptop, one room)
- **Spectral suppression quality (attenuation/dB, reference-correlation
  reduction) and downstream VAD-based false-trigger rejection are not
  the same metric and can point to different "best" settings on the
  same hardware.** A delay value that best cancels the echo signal
  itself is not guaranteed to best reduce the rate at which that
  residual still trips a speech-activity detector -- and can make it
  worse. Anyone tuning AEC for a VAD-gated pipeline (not just for
  perceived audio quality) should measure the downstream VAD-utterance
  metric directly, not infer it from suppression dB. (validated-live,
  n=4 runs, one room, one hardware pair)
- **A single calibration run is not enough to trust a delay
  recommendation** -- the per-run "best" picker disagreed across 3 of 4
  clean runs (322/272/122/122ms), even though the underlying spectral
  data consistently favored 322ms once aggregated. `--repeat-each N`
  within one process reuses a single ambient sample for all repeats;
  running the whole script multiple times (independent ambient captures
  each time) is what actually exposed this variance here. (validated-
  live)
- **The calibration script's own noise gate is a real, useful safety
  behavior**, not just a formality: it correctly aborted a sweep rather
  than report a number when ambient RMS was ~2.3-9x the other runs'
  baseline, avoiding a confidently-wrong recommendation from a
  contaminated sample. (validated-live)
- **Nonlinear residual echo suppression artifacts as an explanation for
  the spectral/VAD divergence is a hypothesis, not yet confirmed** --
  plausible and consistent with this repo's own prior AEC3 research, but
  not directly verified against the processed audio's spectral content
  in this session. (hypothesis)

## Open question for a future session

Given the divergence documented here, what is the actual right choice
for `aec_delay_ms` on this hardware? 322ms is defensible if the goal is
minimizing echo signal reaching the mic at all (best for anything
downstream that reads raw audio, e.g. a future double-talk detector);
122ms is defensible if the immediate goal is minimizing this specific
VAD's false-trigger rate today. No single number optimizes both by this
data. Not resolved in this session -- config currently still has
`aec_delay_ms` unset (auto-tune, ~222ms), which this data shows is
worse than either alternative on every metric measured.
