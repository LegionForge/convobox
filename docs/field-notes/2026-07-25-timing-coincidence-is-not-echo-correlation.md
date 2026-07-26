---
title: Reference-vs-mic timing coincidence looked like echo, briefly -- a real cross-correlation said otherwise, and the actual mechanism was already fixed
status: validated-live
date: 2026-07-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ fdd1b76; WebRTC AEC3; Silero VAD; is_backchannel() post-PR#108
evidence:
  - convobox-UAT/.incident-captures/20260725-214824/ (reference.wav, mic-raw.wav, mic-processed.wav, manifest.json)
  - convobox-UAT/convobox-tui.log lines ~29955-29964 (2026-07-25 21:48 session)
  - docs/field-notes/2026-07-20-self-barge-in-was-backchannel-not-echo.md (the original, correct diagnosis of this same failure class)
  - docs/UAT-checklist.md [G7] (the still-open "no resume after backchannel classification" gap this reconfirms)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT, real-time self-report of the incident, ran the calibration re-check)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, cross-correlation analysis, self-correction, writing
  org: https://legionforge.org
  created: 2026-07-25T22:35:00-05:00
  revised: 2026-07-25T23:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Reference-vs-mic timing coincidence looked like echo, briefly

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents with open mic and speakers. This note documents a live
misdiagnosis, caught before it was published, plus what it reconfirmed
and what it corrected in this project's own prior findings.

## Problem

A bounded incident recording (`.incident-captures/20260725-214824/`,
enabled by same-night commit `fdd1b76`'s new `--capture-incidents`
flag) captured a barge-in whose only transcribed content was "Thank you
very much.", `dropped (backchannel, not a real interrupt attempt)`.
Unlike a companion finding from the same night
([[2026-07-25-player-is-playing-races-ahead-of-first-audio]]), this
response's `reverse` frame count *did* advance normally (real audio was
genuinely playing), and its own logged verdict was `attenuation=1.9dB
of ~13.8dB measurable [UNDER-CANCELLING: ~12.0dB of echo headroom
remains]`.

## Evidence and the mistake, in the order it happened

**First read (wrong): genuine echo leak.** `reference.wav` showed a
loud peak (rms 6911, the loudest moment in the clip) at t=4.1s;
`mic-raw.wav`/`mic-processed.wav` showed a real, substantial amplitude
bump (8-30x baseline) starting at t=4.4s -- about 300ms later, close to
the measured 222ms render-to-capture delay. Combined with the logged
`UNDER-CANCELLING` verdict, this looked like a clean, evidenced case of
real acoustic echo leaking through at a volume peak AEC's linear filter
couldn't track.

**This did not survive a real cross-correlation.** Two things were
wrong with the "evidence": (1) `reference.wav` only contains samples
recorded *while TTS is actively playing* (a concatenation, not a
wall-clock-continuous stream), while `mic-raw.wav` is fully continuous
-- so indexing both at "t=4.1s" compared unrelated points on two
different clocks; (2) even setting that alignment error aside, RMS
envelope timing coincidence was never sufficient evidence in the first
place -- exactly the shortcut
[[2026-07-20-aec-delay-hint-was-a-red-herring]]'s companion note,
`2026-07-20-self-barge-in-was-backchannel-not-echo.md`, already warns
against, requiring an actual cross-correlation before concluding echo.

Running that correlation properly (full-signal FFT cross-correlation of
the `reference.wav` template against the entire `mic-raw.wav`,
searching every possible lag, not just a plausible-looking window):

```
mic 15.02s, ref 5.02s, valid offsets: 159885
best |corr| = -0.0174 at mic-offset 0.134s
corr stats: mean=0.0000 std=0.0027 max=0.0164 min=-0.0174
```

A peak correlation of 0.017 against a noise floor of std=0.0027 is not
a real match -- it is noise-floor level, the same qualitative finding
(peak ~0.15, also called noise-floor) as the 2026-07-20 note's own
correlation check. **This was not echo.**

## Mechanism

What the evidence is actually consistent with: a real, if brief and
possibly not consciously registered, vocalization from the operator
during playback (a "thank you"-type acknowledgment) -- correctly
captured (confirmed uncorrelated with the TTS reference), correctly
classified as backchannel by `is_backchannel()` (the PR #108 fix for
exactly this phrase class is live and working), but muted anyway,
because `BargeInMonitor` decides from raw VAD timing alone, before STT
classification exists to consult. This is not a new mechanism -- it is
a live reconfirmation of the gap `docs/UAT-checklist.md`'s `[G7]`
already names: *"that gap is the false-interruption-recovery item
flagged in `docs/DESIGN-barge-in.md`'s open questions, not yet built."*

**A second, separate correction, surfaced while checking whether to
touch `aec_delay_ms` in response to this incident:**
[[2026-07-20-aec-delay-hint-was-a-red-herring]] concluded, from two
calibration runs (`uat-acoustic-calibration/20260716-*`), that
`aec_delay_ms: 309` was empirically the best value on this hardware and
`222` (auto-tune) the worst. Checking the *complete* available dataset
-- six same-day follow-up runs (`uat-acoustic-calibration/20260720-*`)
using the calibration script's own ranking metric
(`processed_vad.false_barge_ins`, not `raw_vad`, which measures
pre-cancellation echo and is largely delay-invariant by construction --
reading the wrong field was a second near-miss this same investigation
caught before publishing) -- shows the recommended delay bouncing
between `222` (auto, winner in 2 of 6 runs), `272`, `122`, and `172`,
with no value consistently best. The original two-run sample was too
small to support its own conclusion. A larger, `--repeat-each 5
--force-delay-sweep` calibration run was started 2026-07-25 22:10 to
get a statistically stable answer.

**That run also turned out not to test what it looked like it tested.**
`--repeat-each` is only consumed inside the script's explicit
`--delay-candidates` branch (`scripts/acoustic_calibration.py:649`);
the default auto + `--force-delay-sweep` path (lines 651-670) has no
repeat loop at all, so `--repeat-each 5` silently ran exactly 1 trial
per delay -- the same n=1-per-value situation as every prior report,
07-20's included. Separately, the report's own tie-break metric
(`_aggregate_trials`'s ranking key: `processed_false_barge_ins`, then
`processed_utterances`, then `mean_processed_rms`) picked `322ms` as
"best" this run purely because it had the lowest residual mic RMS among
four delays that all tied at zero measured false-barges -- meanwhile
`auto/222ms` was the *only* delay in that run that faced a real
self-echo event (2 raw false-barges) and cancelled it completely
(100% rejection, 9.79dB attenuation, several times better than the
other four's 1.5-3dB). "Best trial" in the printed summary did not mean
"performed best." A corrected invocation
(`--delay-candidates auto,222,272,309,322 --repeat-each 5`) was run
shortly after, same session, to get real repeats.

**That run resolves the open question.** 25 trials, real repeats (10 at
222ms since `auto` and the literal `222` candidate are the same delay,
5 each at 272/309/322ms):

| delay | mean suppression | population stdev | raw false-barge | processed false-barge |
|---|---|---|---|---|
| 222 (auto) | 2.856dB | 1.524 | 1/10 | 0/10 |
| 272ms | 2.538dB | 0.285 | 0/5 | 0/5 |
| 309ms | 2.164dB | 0.205 | 0/5 | 0/5 |
| 322ms | 2.208dB | 0.197 | 0/5 | 0/5 |

Every one of the 25 trials fully suppressed whatever raw echo reached
the mic (`processed_false_barge_ins = 0` throughout); only 1 of 25
trials had any raw false-barge to suppress in the first place. Mean
suppression differences across delay values are small and within each
other's variance -- no statistically meaningful winner. If anything,
`auto` (222ms, the currently configured value) had the *highest* mean
suppression of the four, and `309ms` -- the value
[[2026-07-20-aec-delay-hint-was-a-red-herring]] concluded was
empirically best -- had the *lowest*. The tool's own printed
`"best trial: 272ms"` is, again, a tie-break among functionally
equivalent results, not a real signal.

**Resolution:** on this hardware/room, with an adequately powered
sample, no explicit `aec_delay_ms` value measurably outperforms
`auto`. Recommend leaving `aec_delay_ms` unset (auto-tune) -- it
matches or exceeds any fixed value tested, and self-adjusts if
stream latency ever changes, which a hardcoded value cannot. The
2026-07-20 note's "309ms is empirically best" conclusion is superseded
by this larger sample; it was correct that "was it deliberately
chosen" is the right question to ask, but the small-sample answer it
got does not hold up.

## What transfers

- **Timing coincidence between a reference signal and a captured
  signal is not evidence of correlation between them — run the actual
  cross-correlation, at the actual sample-rate resolution, over the
  actual full recordings.** A plausible-looking ~300ms gap matching a
  known device delay is exactly the kind of coincidence that invites
  false confidence. (validated-live)
- **Before indexing two recordings against each other, verify they
  share a timeline.** A reference/log stream that's only populated
  during specific events (playback-active-only) is a common enough
  pattern that "do these two files actually share a clock" is worth
  checking explicitly, not assuming. (validated-live)
- **A metric name that sounds like the answer (`raw_vad`) may not be
  the one the system itself uses to rank ("`processed_vad`" — check
  what the ranking function (`_trial_rank`) actually reads before
  trusting any derived summary.** (validated-live)
- **A conclusion drawn from a small sample (n=2) should be re-checked
  against a larger one before being treated as settled**, even when the
  small sample produced a clean, confident-sounding narrative — the
  n=6 same-day dataset showed no stable winner, and a properly-repeated
  n=25 run resolved it outright: no explicit `aec_delay_ms` value
  measurably beat `auto`, and the original "309ms is best" pick was, in
  the larger sample, the *worst* mean performer of the four tested.
  (validated-live)
- **A CLI flag that parses successfully and produces plausible-looking
  output can still be a silent no-op on the path you actually took.**
  `--repeat-each` only did anything when combined with the
  non-default `--delay-candidates` flag; nothing in the tool's output
  indicated the requested repeat count was ignored. Worth a follow-up
  fix (out of scope for this note): either honor `--repeat-each` on the
  default sweep path too, or warn loudly when it has no effect.
  (validated-live)
- **A ranking metric can pick a "best" result among options that never
  faced a real test.** Four of five delays in one run tied at zero
  measured false-barges not because they performed well, but because
  none of them had a genuine echo event to reject that trial; the
  tie-break (lowest residual RMS) then picked a winner from
  essentially arbitrary noise. Reading the printed "best trial" line
  without checking whether the underlying counts were a real signal or
  a four-way tie would have reintroduced the same over-confident,
  small-sample mistake this note is about. (validated-live)
- **This project's existing `[G7]` gap (no resume path once a
  mid-playback interruption is classified as a harmless backchannel) is
  confirmed still open and still the most likely explanation for any
  future "it barged in on itself for no reason, and the AEC verdict
  looked fine" report on this codebase** — check that before reaching
  for an AEC or VAD explanation. (validated-live)
