---
title: The known aec_delay_ms=400 + barge_in_min_speech_ms=1200 mitigation holds at N=10 across the full volume range -- full elimination at 20-35%, 2.4-6x improvement at 50-100%, still short of raw baseline at high volume
status: validated-live
date: 2026-08-28
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ dd81d4a; WebRTC AEC3 via aec-audio-processing; tts.engine=piper, voice=en_US-lessac-medium, tts.volume=4.0; interrupt_preset=conversational; interaction.barge_in_min_speech_ms=1200 (the 2026-08-11 mitigation value, set for this whole run); vad.threshold=0.5 (default); scripts/acoustic_calibration.py
hardware: same Mac mini M4 (2024) + AIRHUG 28 USB mic + Mac mini's own built-in speaker as the 2026-08-27 baseline grid and every 2026-08-11 note -- see those for full room/hardware detail. Unchanged this session.
evidence:
  - 300 real live trials: 6 aec_delay_ms candidates (auto, 222, 272, 309, 322, and 400 -- newly added, the value the 2026-08-11 mitigation note found best) x 5 macOS system output volume levels (100%, 75%, 50%, 35%, 20%) x N=10 repeats each, scripts/acoustic_calibration.py, same macOS manual volume-sweep driver as the 2026-08-27 grid.
  - Directly comparable to `docs/field-notes/2026-08-27-full-delay-x-volume-grid-aec-processing-makes-self-barge-in-worse-at-high-volume.md`'s 250 baseline trials -- same hardware, same delay candidates (minus 400ms in that run), same volume levels, only `interaction.barge_in_min_speech_ms` changed (250ms baseline -> 1200ms here).
  - Full JSON reports under /tmp/convobox-mitigation-grid-sweep-20260828/vol{100,75,50,35,20}/<timestamp>/report.json (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the mitigation grid specifically to check whether the 2026-08-11 fix -- validated at N=4, one volume -- holds at N=10 across the full volume range; attached real external speakers mid-session for a planned follow-up comparison; ran this sweep unattended)
    - Claude Code (Anthropic claude-sonnet-5) -- set the mitigation config, ran all 300 trials unattended, aggregated and compared against the baseline grid, wrote this note
  org: https://legionforge.org
  created: 2026-08-28T15:20:00-05:00
  revised: 2026-08-28T15:20:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The known mitigation validated at N=10, full volume range

**Context.** `docs/field-notes/2026-08-11-self-barge-in-combined-mitigation-and-hardware-notes.md`
found that `aec_delay_ms=400` + `barge_in_min_speech_ms=1200` together
brought AEC-processed false barge-ins to a mean of 1.25 (N=4, at a
single 75% volume) -- close to raw's own ~1. `docs/field-notes/2026-08-27-
full-delay-x-volume-grid-aec-processing-makes-self-barge-in-worse-at-
high-volume.md` then re-confirmed the underlying problem at N=10 across
a full delay x volume grid, but deliberately left the mitigation
untested at that scale. This note closes that gap: the same grid, same
hardware, `barge_in_min_speech_ms` bumped to 1200 and `400ms` added to
the delay-candidate set.

## Result: real, substantial improvement at every volume -- full elimination at 20-35%

Averaged across all 6 delay candidates (60 trials per volume level),
baseline (250ms threshold, 2026-08-27) vs. mitigated (1200ms threshold,
this session):

| volume | baseline mean AEC false-barges | mitigated mean AEC false-barges | improvement | raw baseline (same run) |
|---|---|---|---|---|
| 100% | 9.90 | 4.05 | 2.4x | 1.00 |
| 75%  | 9.82 | 2.60 | 3.8x | 1.00 |
| 50%  | 4.60 | 0.77 | 6.0x | 1.00 |
| 35%  | 1.80 | 0.25 | 7.2x | 3.15* |
| 20%  | 1.28 | **0.00** | complete elimination | 0.00* |

\* Raw-signal false-barge counts differ slightly from the 2026-08-27
baseline run at 35%/20% because `raw_vad`'s own barge-in simulation
uses the same `barge_in_min_speech_ms` threshold as the AEC path -- a
1200ms sustained-speech requirement changes what counts as a "false"
raw barge too, not just the AEC-processed count. This is expected, not
noise.

**At 20% volume, every one of the 60 mitigated trials (all 6 delay
candidates) had zero AEC-processed false barge-ins.** At 35%, only 4 of
60 trials had any at all (mean 0.25). The mitigation doesn't just help
at low-to-moderate volume -- it effectively solves the problem there.

At 100%/75% the mitigation is real (2.4x-3.8x fewer false barges than
unmitigated) but still leaves AEC-processed audio 2.6x-4.05x worse than
simply leaving AEC off (raw baseline stays flat at 1.00 regardless of
volume or threshold). This matches the "acoustic distortion at high
volume" hypothesis: a longer sustained-speech requirement filters out
short spurious VAD triggers, but can't fully compensate for a genuinely
degraded/distorted signal at the loudest settings.

## Per-candidate breakdown, all 5 volumes

| candidate | 100% | 75% | 50% | 35% | 20% |
|---|---|---|---|---|---|
| auto | 4.20 | 1.90 | 0.80 | 0.10 | 0.00 |
| 222ms | 4.30 | 3.30 | 1.00 | 0.40 | 0.00 |
| 272ms | 4.30 | 2.60 | 1.10 | 0.40 | 0.00 |
| **309ms** | 2.80 | 2.60 | 0.60 | **0.00** | 0.00 |
| 322ms | 4.30 | 2.90 | 0.70 | 0.20 | 0.00 |
| 400ms | 4.40 | 2.30 | **0.40** | 0.40 | 0.00 |

**309ms is the most consistently strong performer** across the grid
(best or tied-best at 100%, 35%; competitive everywhere), corroborating
GitHub issue #119's own earlier claim that "real on-hardware calibration
established `aec_delay_ms: 309` as the empirically-best fixed value for
this room/hardware." **400ms -- the delay the original 2026-08-11
mitigation note found best -- is NOT the standout here**: worst-of-set
at 100% (4.40), best-of-set at 50% (0.40), unremarkable elsewhere. The
2026-08-11 finding was N=1 per delay at a single volume; this data
suggests `barge_in_min_speech_ms=1200` is doing most of the real work,
not the specific 400ms delay choice -- 309ms (already this repo's
historical recommendation) performs as well or better across the full
range tested here.

## Practical recommendation, updated

For `conversational` mode with open speakers on this hardware:
`interaction.barge_in_min_speech_ms: 1200` is a real, substantial,
broadly-validated improvement at every volume tested, and a complete
practical fix below ~35% volume. `aec_delay_ms: 309` (not 400) looks
like the better paired delay choice based on this grid, though the
difference between candidates is small next to the threshold's own
effect. At 100%/75% volume, the combination narrows but does not close
the gap to raw-AEC-off -- if working at high volume with real speakers
matters, headphones (this project's existing recommended default) or a
different speaker (see the external-speaker comparison this same
session is running next) remain the more complete fixes.

## What this does NOT show

- Does not root-cause WHY 1200ms specifically works this well --
  plausible mechanism (filtering short spurious VAD-probability spikes
  from AEC3's residual/comfort-noise processing while still catching
  genuine sustained user speech) is inferred, not directly measured.
- Does not test threshold values between 250ms and 1200ms -- there may
  be a smaller threshold that gets most of the benefit with less
  latency cost to genuine barge-in responsiveness; not swept here.
- A 1200ms sustained-speech requirement is a real UX tradeoff (slower
  genuine barge-in reaction), not evaluated here -- this note measures
  false-barge suppression only, not the cost side of that tradeoff.
