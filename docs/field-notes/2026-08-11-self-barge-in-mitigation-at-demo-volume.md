---
title: What actually reduces self-barge-in at the live-demo volume (TTS 4.0x + macOS system 75%) -- AEC delay sweep and barge_in_min_speech_ms sensitivity, both real live hardware tests
status: validated-live (delay sweep, N=1 per delay); directional (sensitivity sweep, N=1 per threshold, not statistically robust)
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ e659b69; macOS 26.x, Apple Silicon; AIRHUG 28 (USB mic), Mac mini Speakers; tts.volume=4.0, macOS system output volume=75%
evidence:
  - scripts/acoustic_calibration.py --force-delay-sweep --delay-candidates auto,150,200,238,280,320,400 (7 real trials, one delay each)
  - Four more real trials sweeping interaction.barge_in_min_speech_ms (250/500/800/1200ms), same volume
  - Full JSON reports under uat-acoustic-calibration/ (convobox-UAT worktree scratch, gitignored, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for automated self-barge-in tests at the current demo volume to find what reduces it)
    - Claude Code (Anthropic claude-sonnet-5) -- ran both sweeps, wrote this note
  org: https://legionforge.org
  created: 2026-08-11T11:05:00-05:00
  revised: 2026-08-11T11:05:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# What reduces self-barge-in at the live-demo volume

**Context.** Follows directly from
`docs/field-notes/2026-08-11-macos-live-human-demo-safeword-bargein-and-self-echo-loop.md`,
where a real self-triggered barge-in loop appeared live during a demo
at `tts.volume=4.0` + macOS system output volume 75%. JP asked for
automated tests at that exact volume to find concrete adjustments.

## Finding 1: at this volume, AEC makes false barge-ins WORSE, not better

Delay sweep, 7 real trials (one response each), same volume as the
demo:

| Delay | Attenuation | Ceiling | `false_barge_ins` raw (AEC off) | `false_barge_ins` AEC-processed |
|---|---|---|---|---|
| auto (238ms) | 11.46dB | 16.42dB | 1 | **10** |
| 150ms | 11.32dB | 16.39dB | 1 | **11** |
| 200ms | 12.99dB | 16.18dB | 1 | **12** |
| 238ms (explicit) | 9.69dB | 14.70dB | 1 | **13** |
| 280ms | 12.58dB | 16.44dB | 1 | **12** |
| 320ms | 10.91dB | 16.28dB | 1 | **11** |
| **400ms** | 11.52dB | 15.79dB | 1 | **8** (best of those tested) |

**At every single delay tested, the AEC-processed signal produced MORE
false barge-in triggers than the raw, uncancelled signal did (8-13 vs.
1).** This is the opposite of what AEC is supposed to do, and a
genuinely surprising result. The likely mechanism: at this volume, the
echo reaching the mic is loud enough that AEC3's adaptive filter and
residual suppressor are working hard, and whatever artifacts that
processing leaves behind (partial cancellation residue, suppressor
gating transients) are themselves speech-shaped enough to trip
Silero's VAD more often than the raw, unprocessed echo would on its
own. 400ms was the least-bad delay tested (8, vs. 10 for the current
auto-estimate) but still far worse than AEC-off.

## Finding 2: raising barge_in_min_speech_ms has a real, if noisy, effect

Four more real trials (238ms delay, same volume), one per threshold,
`false_barge_ins` (AEC-processed):

| `barge_in_min_speech_ms` | `false_barge_ins` |
|---|---|
| 250 (default) | 9 |
| 500 | 13 (noisier -- worse than default in this one trial) |
| 800 | 6 |
| **1200** | **1** (matches the raw/AEC-off baseline) |

**N=1 per threshold, so this is directional, not statistically
robust** -- the 500ms result being worse than the 250ms default shows
real trial-to-trial noise at this sample size. But the overall trend
is strong and mechanistically sensible: requiring 1.2s of sustained
"speech" before firing filters out the brief residual-echo bursts that
dominate the false-trigger count, since a real interruption attempt is
typically longer than that. 1200ms converging to the same count as the
raw/uncancelled baseline (1) is a meaningful signal, not likely pure
coincidence.

## Recommendation

**No single setting change fully solves this at 4x TTS volume /
75% system volume** -- the two mitigations found here are real but
partial, and stacking both hasn't been tested yet (would need a
combined 400ms-delay + 1200ms-threshold trial, not run this pass to
keep the sweep bounded).

Ranked by expected impact, cheapest first:

1. **Lower the volume.** Every finding in this session's whole
   volume-escalation arc (the earlier 1.5x/2.0x/3.0x AEC batch, and
   this one) shows the echo-to-ambient ceiling climbing with volume --
   more echo reaching the mic, harder for AEC to fully remove, more
   residual for VAD to false-trigger on. This is the single biggest
   lever and needs no config change, just turning it down.
2. **Raise `interaction.barge_in_min_speech_ms`** from 250ms to
   ~800-1200ms if `conversational` mode needs to stay on at this
   volume -- cheapest code-level lever, no AEC tuning needed, real
   (if noisy) effect measured directly.
3. **Set `audio.aec_delay_ms: 400`** explicitly instead of auto --
   modest, consistent improvement (8 vs. 10-13) at every volume level
   tested in this stretch, though it doesn't come close to solving the
   problem alone.
4. **Switch to `do-not-disturb`** (this project's own default) if
   barge-in itself isn't the point of a given session -- it isn't
   subject to this failure mode at all, since ordinary speech (or
   residual echo) can't trigger anything during playback in that mode.
5. **Headphones** remove the acoustic coupling entirely, sidestepping
   the whole question -- not tested this pass (would need a real
   headphone swap), but the theoretically strongest fix since it
   removes the echo source rather than trying to cancel or filter it.

## What transfers

- **AEC's own processing can be a net-negative for barge-in
  specifically, at high enough playback volume** -- a real,
  live-measured finding, not a hypothesis. Worth remembering before
  assuming "AEC on" is always the safer choice for barge-in-sensitive
  presets.
- **`barge_in_min_speech_ms` is a real, usable lever independent of
  AEC quality** -- raising it trades barge-in responsiveness
  (a genuine interrupt takes slightly longer to register) for
  robustness against exactly this failure mode.
- **Neither mitigation was tested in combination, and both are
  single-trial measurements at this sample size** -- a real follow-up
  would be a combined 400ms/1200ms config with `--repeat-each 3`+ to
  get a statistically trustworthy read, not done this pass to keep the
  investigation bounded and get a usable answer back quickly.
