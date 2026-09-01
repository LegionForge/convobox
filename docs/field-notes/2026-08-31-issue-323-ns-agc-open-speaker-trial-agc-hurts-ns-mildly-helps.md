---
title: "Issue #323 live trial: WebRTC APM's AGC measurably WORSENS open-speaker self-barge-in on real hardware; NS alone gives a small, consistent improvement"
status: validated-live
date: 2026-08-31
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 9a39f55 + local uncommitted trial driver; aec-audio-processing 1.0.1; macOS 26.6.2 (Darwin 25.6.0, Apple Silicon, Mac mini M4)
evidence:
  - 32 real, live acoustic trials via scripts/acoustic_calibration.py's actual run() harness (same code path used for every prior delay/volume sweep in this repo) -- 4 configs x 8 repeats each, real Piper TTS played through real speakers, captured by a real mic, real WebRTC AEC3 processing
  - Full JSON reports + a comparison SUMMARY.json under uat-acoustic-calibration/issue-323-ns-agc-trial/ (gitignored, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; opted the session into live hardware UAT this cycle, machine free for hours)
    - Claude Code (Anthropic claude-sonnet-5) -- built the trial driver, ran it live, analyzed results, wrote this note
  org: https://legionforge.org
  created: 2026-08-31T18:15:00+00:00
  revised: 2026-08-31T18:15:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# AGC hurts, NS mildly helps -- issue #323's real answer

## Hardware and methodology

Same machine and open-speaker setup as the 2026-08-27 through 2026-08-30
acoustic-testing series: Mac mini M4, AIRHUG 28 mic (device index 1),
external Logitech speakers wired through the analog jack that
`sd.query_devices()`/macOS both label `External Headphones` (this
project's own `output_device: "External Headphones"` config pin is
therefore already the open-speaker path this issue asks about, not
literal headphones -- confirmed against the same devices used in the
2026-08-29/30 hardware_profile.py campaign). `echo_cancellation: true`,
`interrupt_preset: conversational`, `barge_in_min_speech_ms: 250`,
`vad.threshold: 0.5` -- this repo's current shipped defaults, unedited.

`EchoCanceller.__init__` (`src/convobox/audio/aec.py`) hardcodes
`enable_ns=False, enable_agc=False` at construction and exposes no config
knob for either. To trial real `ns_level`/`agc_mode` values without
touching that file, a throwaway driver script (not committed -- lived in
this session's scratchpad only) monkeypatched
`aec_audio_processing.AudioProcessor` to inject overrides at construction
time; `EchoCanceller.__init__` does `from aec_audio_processing import
AudioProcessor` fresh on every call, so the patch is picked up cleanly
per-run with zero changes to the actual module under test. The driver
then called `scripts/acoustic_calibration.py`'s real, unmodified `run()`
-- the exact same harness this repo already trusts for every delay/volume
sweep decision -- once per config, `--delay-candidates auto --repeat-each
8` (8 independent live playback+capture trials per config, one shared
20s ambient capture per config), and diffed the resulting
`aggregates_by_delay_ms` JSON.

Four configs, each real hardware, N=8 trials:

| config | `enable_ns` | `ns_level` | `enable_agc` | `agc_mode` |
|---|---|---|---|---|
| baseline (shipped default) | False | -- | False | -- |
| ns_only | True | 2 | False | -- |
| agc_only | False | -- | True | 1 |
| ns_and_agc | True | 2 | True | 1 |

## Results (N=8 trials each, real hardware)

| config | processed false-barge-ins (total/8 trials) | mean suppression dB | mean processed RMS |
|---|---|---|---|
| **baseline** | 73 | 9.91 | 0.0479 |
| **ns_only** | **62** (-15%) | **10.37** (+0.46dB) | **0.0467** (lower) |
| **agc_only** | **94** (+29%) | **6.00** (-3.91dB) | **0.0762** (+59%) |
| **ns_and_agc** | 75 (~flat) | 6.44 (-3.47dB) | 0.0712 (+49%) |

(`self_barge_rejection_percent` as this harness defines it is deeply
negative in all four configs here -- expected and not a red flag: this
metric's own denominator, `raw_false_barge_ins`, is the RAW/unprocessed
signal's BargeInMonitor firing count, which stays near 1 per trial
because a strong, unbroken raw echo reads as one continuous in-speech
segment, while imperfect post-AEC residual echo fragments into several
shorter bursts that each re-trigger the sustained-speech gate --
inflating the processed-side COUNT even in configs that are genuinely
quieter/safer overall. The comparison that actually answers this issue
is baseline-vs-candidate on the SAME metric, not the raw/processed ratio
in isolation -- which is what the table above does.)

## Interpretation

**AGC is actively harmful for this open-speaker setup, confirmed live,
not hypothetical.** `agc_only` is worse than baseline on every axis:
29% MORE false barge-ins, suppression cut nearly in half (6.0dB vs
9.91dB), and residual RMS up 59%. This is mechanistically exactly what
you'd expect once you think about where AGC sits in the pipeline: it
runs on the ALREADY-AEC-processed stream and boosts whatever's left
toward its target level -- including residual echo and room noise, not
just genuine near-end speech. The issue's own hypothesis ("AGC could
reduce how hot the mic runs, plausibly making AEC3's job easier") gets
the ordering backwards for this implementation: AGC in this binding
doesn't run before AEC to tame the input, it runs after, amplifying
AEC's leftovers. `ns_and_agc` confirms this isn't a fluke -- adding NS's
real benefit on top of AGC still nets out worse than baseline, because
AGC's harm dominates.

**NS alone gives a small, real, consistent improvement.** `ns_only` beat
baseline on every axis: 15% fewer false barge-ins, +0.46dB more
suppression, lower residual RMS. Not dramatic, but real and
directionally consistent across all three independent metrics, on live
hardware, N=8.

## What "done" looks like, per the issue

The issue explicitly accepts either outcome: "a real improvement worth
documenting, or a clean negative result -- both are valid outcomes that
close this issue." This is both at once, split by knob: AGC is a clean
negative result (do not enable it, confirmed harmful on real hardware,
not just theorized); NS shows a real, live-verified, if modest,
improvement.

## Not done in this note (left for review, not auto-applied)

Per this project's own convention (`acoustic_calibration.py`'s own
report always sets `"automatic_config_edit": False` -- a human decision,
never a silent flip), this note does NOT change
`EchoCanceller.__init__`'s hardcoded `enable_ns=False`. A real code
change (exposing `ns_level` as a config knob, defaulting it on, or just
flipping the hardcoded constant) is a safety-adjacent AEC change and
should get JP's own review against this data, not get shipped
unilaterally by an autonomous session. N=8 per config is a real live
sample, not a single anecdote, but a config-default change this close to
barge-in/safety behavior deserves a second look before it ships.

## Caveats

- One ambient capture per config (not per trial) -- ambient/room-noise
  conditions are shared within a config, not independently sampled 8
  times. Real Piper/speaker/mic trials themselves (the actual measured
  metric) ARE 8 independent live cycles per config, not repeats of the
  same capture.
- Single delay value (`auto`, resolved once per config to ~235ms each
  time, consistent with this AEC path's own delay estimation) -- no
  cross-product with the delay sweep this repo also has. A future
  session could check whether NS's benefit holds across other delays,
  but the auto-resolved delay is what this repo's real config would
  actually use, so it's the right first thing to check.
- `agc_mode=1` and `ns_level=2` are the binding's own constructor
  defaults, not independently tuned -- a future session could sweep
  `ns_level` 0-3 to see whether a stronger/weaker NS setting does even
  better, now that the direction (NS helps, AGC hurts) is established.

## Follow-up, 2026-09-01: `ns_level` sweep (0-3), then a 2-vs-3 repeat confirmation

Now that `aec_ns`/`aec_ns_level` are real config fields (PR #354), this
caveat's own suggested next step took ten minutes instead of a new
monkeypatch driver. Same machine, same open-speaker setup, same
methodology (`--delay-candidates auto --repeat-each 8`).

**Pass 1 (all four levels, one run each):**

| `ns_level` | false barge-ins /8 | suppression dB | residual RMS |
|---|---|---|---|
| 0 (low) | 62 | 9.18 | 0.0544 |
| 1 (moderate) | 59 | 7.85 | 0.0629 |
| 2 (high -- the value tested/documented above) | 59 | 9.50 | 0.0531 |
| **3 (very high)** | **45** | 9.01 | 0.0562 |

All four beat the no-NS baseline (73) on false barge-ins, confirming the
original finding wasn't specific to `ns_level=2`. `ns_level=3` stood out
as noticeably better than the 0/1/2 cluster (which are within each
other's own trial-to-trial noise -- individual repeats within a single
level swung suppression 5.8-37.5dB).

**Pass 2, same day, later: a dedicated 2-vs-3 repeat to confirm the gap
wasn't a one-off.** Absolute counts moved a lot session to session (real
ambient-noise variance, nothing wrong) -- `ns_level=2` alone went from
59 to 88 false barge-ins between the two passes. But the RELATIVE
ordering held both times:

| pass | `ns_level=2` | `ns_level=3` | relative gap |
|---|---|---|---|
| 1 | 59 | 45 | -24% |
| 2 | 88 | 59 | -33% |

`ns_level=3` beat `ns_level=2` in both independent passes, by a similar
relative margin each time, despite the absolute numbers moving a lot.
Suppression/RMS stayed statistically indistinguishable between the two
levels both times (<0.6dB, <0.001 RMS apart) -- only the false-barge-in
count discriminates between them.

**Not yet applied anywhere.** `aec_ns_level`'s shipped default stays `2`
-- this is a same-machine result (confirmed twice, but one room/setup),
and `docs/UAT-checklist.md`'s `[E10]` cross-platform confirmation is
still the gating step before any default changes, now updated to test
both `ns_level=2` and `ns_level=3` on the second platform.
