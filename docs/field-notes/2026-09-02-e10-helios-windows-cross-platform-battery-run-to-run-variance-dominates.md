---
title: "[E10] Helios/Windows cross-platform NS/AGC battery: run-to-run variance at N=8 dwarfs any config signal, ambient noise is a weak confound not the driver"
status: diagnosed
date: 2026-09-02
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ d2fb06b (PRs #354/#355/#356/#358/#359); aec-audio-processing (Windows prebuilt wheel); Windows 11 Pro 10.0.26200, Helios rig (i7-13650HX/RTX 4060)
evidence:
  - 24 real, live acoustic trials via scripts/acoustic_calibration.py's real run() harness, same code path as the 2026-08-31 Mac mini trial and the 2026-09-01 ns_level sweep -- an initial 4-config single pass (baseline/ns2/ns3/agc, N=8 each) plus a 20-run overnight battery (5 interleaved cycles x 4 configs, N=8 each), all real speaker playback + real mic capture through the analog jack (Realtek "Headphones" endpoint feeding real external open speakers, same convention as the Mac mini's own jack-labeled-Headphones setup)
  - Full JSON reports under uat-acoustic-calibration/e10-helios-baseline/, e10-helios-ns2/, e10-helios-ns3/, e10-helios-agc/, e10-helios-agc-r2/, e10-helios-battery-2026-09-02/ (gitignored, not committed)
  - docs/field-notes/2026-08-31-issue-323-ns-agc-open-speaker-trial-agc-hurts-ns-mildly-helps.md (the Mac mini result this note tests for cross-platform generalization)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; directed the [E10] cross-platform priority, called for the overnight battery to check for spurious data, asleep for the unattended portion -- reachable via mobile)
    - Claude Code (Anthropic claude-sonnet-5) -- ran the trials live, built the interleaved battery driver, analyzed results, wrote this note
  org: https://legionforge.org
  created: 2026-09-02T04:30:00+00:00
  revised: 2026-09-02T04:30:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# `[E10]` on Helios: the Mac mini result does not clearly generalize -- but neither does anything else, because run-to-run noise at N=8 is bigger than any config effect

## Hardware and methodology

Windows 11 rig (Helios), real external open speakers wired into the
analog 3.5mm jack -- Windows/Realtek labels this endpoint `Headphones (2-
Realtek(R) Audio)` regardless of what's physically connected, same
"jack labeled Headphones but really driving open speakers" convention
the Mac mini trial used. Mic: `Microphone (1080P Pro Stream)`. Same
`scripts/acoustic_calibration.py --delay-candidates auto --repeat-each 8`
harness as every prior trial in this series, unmodified.

Two phases:

1. **First pass** (one run per config): baseline, `ns_level=2`,
   `ns_level=3`, `aec_agc`. Results swung wildly between configs and
   between a same-config repeat (`aec_agc` alone went from 0% to 100%
   self-barge rejection between two consecutive runs), which looked at
   first like an ambient-noise confound -- room-noise RMS also happened
   to drop ~8x between those two runs (JP stepped away to eat).
2. **Overnight battery** (this note's real evidence): 5 cycles of all 4
   configs, **interleaved** (not blocked) so any monotonic drift in room
   noise over the night doesn't systematically favor one config, with
   ambient RMS captured and logged alongside every run's own
   `report.json` aggregates specifically to test the confound hypothesis
   against real repeated data instead of one pair of runs.

## Results

**Per-cycle detail, all 20 runs (5 cycles x 4 configs, N=8 trials each):**

| config | cycle | ambient RMS | false-barges/8 | rejection% | suppression dB | utterance leaks |
|---|---|---|---|---|---|---|
| baseline | 1 | 0.002730 | 1 | 87.5 | 14.38 | 0 |
| baseline | 2 | 0.002778 | 5 | 37.5 | -- | 4 |
| baseline | 3 | 0.002754 | 3 | 62.5 | -- | 1 |
| baseline | 4 | 0.002755 | 2 | 84.6 | -- | 1 |
| baseline | 5 | 0.001916 | 3 | 62.5 | -- | 1 |
| ns2 | 1 | 0.001731 | 6 | 25.0 | -- | 2 |
| ns2 | 2 | 0.001642 | 4 | 50.0 | -- | 4 |
| ns2 | 3 | 0.001204 | 0 | 100.0 | -- | 0 |
| ns2 | 4 | 0.001758 | 5 | 37.5 | -- | 5 |
| ns2 | 5 | 0.002847 | 1 | 87.5 | -- | 0 |
| ns3 | 1 | 0.000346 | 3 | 62.5 | -- | 2 |
| ns3 | 2 | 0.000355 | 0 | 100.0 | -- | 0 |
| ns3 | 3 | 0.000357 | 0 | 100.0 | -- | 0 |
| ns3 | 4 | 0.000346 | 1 | 87.5 | -- | 0 |
| ns3 | 5 | 0.000442 | 1 | 87.5 | -- | 1 |
| agc | 1 | 0.000354 | 2 | 75.0 | -- | 2 |
| agc | 2 | 0.000351 | 1 | 87.5 | -- | 0 |
| agc | 3 | 0.000359 | 2 | 75.0 | -- | 0 |
| agc | 4 | 0.000361 | 7 | 12.5 | -- | 2 |
| agc | 5 | 0.000340 | 1 | 87.5 | -- | 0 |

**Pooled per-config (mean across 5 cycles, N=40 trials per config):**

| config | mean rejection% | stdev (rejection%) | mean ambient RMS | mean false-barges/8 | total utterance leaks (of 40 trials) |
|---|---|---|---|---|---|
| baseline | 66.9 | 20.3 | 0.002587 | 2.80 | 7 |
| ns2 | 60.0 | **32.4** | 0.001836 | 3.20 | **11** |
| **ns3** | **87.5** | **15.3** | 0.000369 | **1.00** | 3 |
| agc | 67.5 | 31.4 | 0.000353 | 2.60 | 4 |

**Ambient-RMS-vs-rejection correlation** (Pearson, checking whether
ambient noise is actually driving the outcome): **-0.203 overall across
all 20 runs** -- weak, and the wrong shape to explain the earlier
0%-to-100% single-pair swing on its own. Within-config correlations:
baseline 0.083, ns2 0.126, ns3 0.100 (all near zero -- ambient noise
barely moves the outcome for these three once you have more than one
data point), **agc -0.662** (a real, moderate effect specific to AGC --
it degrades disproportionately as the room gets louder, which is
mechanistically sensible: AGC boosts whatever's left after AEC, so a
noisier residual floor gives it more to amplify).

## Interpretation

**The ambient-noise-confound hypothesis from the first pass was wrong,
or at least incomplete.** With 20 runs instead of 2, ambient RMS does
not cleanly predict outcome (overall r=-0.2, near-zero within three of
the four configs). The real driver of the wild first-pass swings is
**run-to-run variance in this test at N=8, full stop** -- the SAME
config, same room, same hour of the night, produces rejection rates
that swing 25-50 percentage points from one 8-trial run to the next
(`ns2`: 25% -> 100% across its 5 cycles; `agc`: 87.5% -> 12.5%). This
variance is comparable in size to the difference BETWEEN configs, which
is exactly why the first pass's single-run comparison was misleading:
`ns3`'s single first-pass run (0% rejection, the worst-looking result of
the whole night) turned out to be an unlucky outlier of `ns3`'s own
5-run range (62.5-100%, mean 87.5% -- the BEST-performing config once
pooled).

**Pooled across all 5 cycles, a tentative ranking emerges, but every
config's own spread is wide enough that this is not a confident
result:** `ns3` (mean 87.5%, tightest spread, fewest false-barges and
utterance leaks) looks best; `ns2` (mean 60%, widest spread, most
utterance leaks) looks worst -- worse than doing nothing. `agc` and
`baseline` land in the middle, statistically indistinguishable from
each other (67.5 vs 66.9%) despite AGC's real ambient-sensitivity.

**This does NOT confirm the Mac mini's `ns_only`-vs-baseline result on
Helios** (Mac mini: ns_level=2 beat baseline by 15%; here, `ns2`
underperforms baseline on every pooled metric). **It weakly, tentatively
supports the Mac mini's `ns_level=3`-beats-`ns_level=2` result**
(Helios: 87.5% vs 60.0% mean rejection, a real gap given ns3's own tight
spread) -- but this is one machine's 5 runs, the same "needs a second
platform" caveat the Mac mini result itself carried before tonight.

## What "done" looks like, and what this note does NOT settle

`[E10]`'s original framing -- "one real confirming/contradicting run on
a second platform is worth more than a bigger N on the same machine" --
undersells how noisy a single N=8 run actually is, at least on this
platform/setup. **The real finding of tonight isn't "NS helps" or "NS
hurts" on Windows -- it's that the acoustic_calibration.py methodology's
own N=8-per-run resolution is too coarse to trust a single pass,
anywhere, without either more repeats or a redesigned protocol** (larger
N per run, or explicit multi-run averaging built into the tool rather
than left to manual repeats). This applies retroactively to caution
around the Mac mini's own single-pass numbers too, though its 2-vs-3
repeat check (2026-09-01 follow-up, same file) is exactly the kind of
repeat-to-confirm step that was missing here until tonight's battery.

**Not applied anywhere.** `convobox.yaml`'s `audio.aec_ns`/`aec_agc`
were restored to `false` (off, shipped default) at the end of the
battery run. No default in `src/convobox/config.py` or
`docs/KNOWN-ISSUES.md` changes as a result of this note alone -- per
this project's standing rule (a human decision, never a silent flip),
this needs JP's own review against the pooled numbers above before
`[E10]`'s status moves past "diagnosed," especially given the
tentative-not-confident framing throughout.

## Caveats

- N=40 pooled per config (5 x 8) is still modest given the observed
  spread -- a stdev of ~20-32 percentage points on a 0-100% metric means
  even 40 trials leaves real uncertainty on the pooled means above; this
  note reports them descriptively, not as a settled statistical result.
- All 20 runs used a single ambient capture per run (45s), not per
  trial -- same limitation the Mac mini note flagged for its own design.
- Only the `auto`-resolved delay (222ms throughout) was tested, no
  cross-product with a delay sweep.
- This session did not attempt to identify WHY run-to-run variance is
  this large at N=8 (mic self-noise drift, STT/VAD threshold edge
  sensitivity, subtle speaker driver behavior, or something else) --
  that's a real open question this note surfaces but does not answer.

## Update (2026-09-02, later same day): extended to 15 cycles (N=120/config), real pairwise significance now possible

JP asked whether more repeats would add real statistical value given the
spread above, not just more of the same noise. Answer: yes -- standard
error shrinks with 1/sqrt(n), and it was worth doing. Ran 10 more
interleaved cycles (same driver, same methodology) on top of the
original 5, for N=15 cycles / 120 trials per config total. One
operational hiccup mid-run: cycles 13-15 initially failed 12/12 runs
outright (`RuntimeError: another ConvoBox microphone session is
running`) because a separate `run_convobox.py --web` instance, started
for an unrelated live CSP-verification check on this same session, was
left running and held ConvoBox's single-instance mic lock. Killed that
process, cleared the failed placeholder entries, and re-ran cycles
13-15 clean -- final dataset is 60/60 successful runs, no failures.

**Also directly confirms JP's own live prediction, made mid-battery:**
daytime AC/HVAC activity raising the noise floor. Ambient RMS climbed
from ~0.0013 (cycles 1-5, overnight) to a sustained ~0.0024-0.0031
(cycles 6-15, daytime) -- roughly double, and the interleaved-not-blocked
design meant this drift landed on all four configs evenly rather than
confounding one of them.

**Pooled, N=15 cycles / 120 trials per config:**

| config | mean rejection% | stdev | 95% CI | mean ambient RMS | utterance leaks (of 120) |
|---|---|---|---|---|---|
| baseline | 72.3 | 21.1 | [61.6, 83.0] | 0.002556 | 12 |
| ns2 | 58.5 | 23.6 | [46.6, 70.5] | 0.002670 | 16 |
| **ns3** | **80.0** | **14.1** | **[72.9, 87.2]** | 0.001874 | 7 |
| agc | 59.8 | 28.2 | [45.5, 74.0] | 0.001313 | 23 |

**Pairwise differences (95% CI on the difference, normal approximation)
-- this is the part N=8-per-run single passes could never answer:**

| comparison | difference | 95% CI | distinguishable at 95%? |
|---|---|---|---|
| ns3 vs ns2 | +21.5 pts | [+7.6, +35.4] | **yes -- real** |
| ns3 vs agc | +20.3 pts | [+4.3, +36.2] | **yes -- real** |
| ns3 vs baseline | +7.7 pts | [-5.1, +20.6] | no -- crosses zero |
| ns2 vs baseline | -13.8 pts | [-29.8, +2.2] | no -- crosses zero |
| agc vs baseline | -12.5 pts | [-30.4, +5.3] | no -- crosses zero |

**The honest, defensible conclusion at this sample size:** `ns_level=3`
is now a REAL, statistically significant improvement over `ns_level=2`
and over `aec_agc` on Helios -- not just the best point estimate, a gap
that survives a real confidence interval. It is NOT yet distinguishable
from simply leaving NS/AGC off entirely (`ns3` vs `baseline` crosses
zero), though it has the tightest spread and highest point estimate of
all four configs. Neither `ns2` nor `agc` can be shown to differ from
baseline at this N either, despite both looking worse in the raw means
-- the spread is still wide enough that "worse than doing nothing"
isn't confirmed, just unconfirmed-to-help.

**Ambient correlation stayed unstable across the two passes** (N=5:
baseline +0.08, ns2 +0.13, ns3 +0.10, agc -0.66; N=15: baseline -0.48,
ns2 +0.02, ns3 -0.35, agc -0.50) -- signs flipped for 3 of 4 configs
between passes. Given ambient RMS itself shifted this much within the
session (night vs. day), a single Pearson coefficient over a
non-stationary noise floor is a weak tool here; this note is not
treating either pass's correlation numbers as reliable, just noting
both for completeness.

**Revised recommendation for `[E10]`:** if a shipped-default change is
made based on this Helios data, `ns_level=3` (not the currently
documented `ns_level=2`) is the only candidate value with a real,
significant, cross-metric edge over the other NS/AGC configs -- but
"beats level 2" is not the same claim as "beats off," and that second
claim still isn't established here. A third platform (Linux) run, and/or
a similarly-sized N on the Mac mini re-run with `ns_level=3` specifically
against baseline (not just against level 2), would be the natural next
step before any default changes. Still not applied anywhere --
`convobox.yaml` is back at the safe baseline.
