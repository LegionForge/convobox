---
title: Full aec_delay_ms x system-volume grid (5x5, N=10, 250 real trials) -- reproduces the known macOS "AEC worse than off at high volume" finding (GitHub issue #119) at statistically robust scale, across the whole standard delay-candidate set and a full volume range
status: validated-live
date: 2026-08-27
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 (includes PR #347, --text mode approval-exit fix, unrelated to this note); WebRTC AEC3 via aec-audio-processing; tts.engine=piper, voice=en_US-lessac-medium, tts.volume=4.0 (Piper linear gain, matches the 2026-08-11 sweeps for comparability); interrupt_preset=conversational; barge_in_min_speech_ms=250 (the UNMITIGATED default -- see "Known mitigation not tested here" below); vad.threshold=0.5 (default); scripts/acoustic_calibration.py
hardware:
  computer: Mac mini M4 (2024) -- same machine as every 2026-08-11 sweep and this repo's other macOS field notes. Single "Built-in speaker" per Apple's own spec (not a stereo pair), independently reported as prone to distortion at volume -- the leading corroborated (not directly measured) root-cause hypothesis for this whole finding class.
  microphone: AIRHUG 28 USB conference mic, 360-degree omnidirectional pickup, built-in DSP with an "AI Noise Reduction" mode -- NOT independently re-confirmed off this session (no direct LED observation logged this time); assumed unchanged from the 2026-08-11 sessions' confirmed-off state since no one touched the mic hardware between sessions.
  mic_placement: unchanged from 2026-08-11 (not re-measured this session).
  output_device: "Mac mini Speakers" (Core Audio, confirmed default). input_device: "AIRHUG 28" (Core Audio, confirmed default). Both left as `audio.output_device`/`input_device` unset (system default) in convobox.yaml.
room: unchanged from 2026-08-11 (not re-measured this session -- see that note for RT60/floor/wall detail).
evidence:
  - 250 real live trials: 5 aec_delay_ms candidates (auto, 222, 272, 309, 322) x 5 macOS system output volume levels (100%, 75%, 50%, 35%, 20%) x N=10 repeats each, scripts/acoustic_calibration.py, one script invocation per volume level (macOS has no --volume-candidates automation -- pycaw/Windows and wpctl/Linux only -- so the volume axis was driven externally via `osascript -e "set volume output volume N"`, the same manual method the 2026-08-11 sweeps used, wrapped in a small driver script this session).
  - Full JSON reports and per-trial WAV/diagnostics under /tmp/convobox-full-grid-sweep-20260827/vol{100,75,50,35,20}/<timestamp>/report.json (scratch, not committed -- this field note is the durable record of the aggregate numbers; raw JSON available on request while the scratch dir still exists on this machine).
  - Driver log: /tmp/convobox-full-grid-sweep-20260827/driver.log (per-trial console output, all 250 trials).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; requested the full delay x volume grid at N=10 per combination specifically to get robust real trial counts for updating the field notes, going from N=1/N=7 in the source findings below to N=10 across a full delay x volume cross; ran the sweep unattended overnight while asleep)
    - Claude Code (Anthropic claude-sonnet-5) -- built the macOS volume-sweep driver (the script's own --volume-candidates doesn't support macOS), ran all 250 trials unattended, aggregated the data, wrote this note
  org: https://legionforge.org
  created: 2026-08-27T23:15:00-05:00
  revised: 2026-08-27T23:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Full delay x volume grid -- reproduces the known AEC-worse-at-high-volume finding at scale

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents: mic and speakers run simultaneously, and acoustic echo
cancellation (AEC) is supposed to keep the assistant's own TTS output
from being picked back up by the mic and misread as the user
interrupting ("self-barge-in"). `scripts/acoustic_calibration.py` plays
a known synthesized response through the real speakers, captures it
back through the real mic before and after WebRTC AEC3 processing, and
runs both signals through the same VAD-based barge-in simulation
ConvoBox itself uses live.

**This is NOT a new finding.** GitHub issue #119 already established,
on this exact machine, that AEC-processed audio can produce MORE false
barge-ins than AEC being off entirely at high system volume:
`docs/field-notes/2026-08-11-self-barge-in-mitigation-at-demo-volume.md`
(N=1 per delay, 7 delays, at a fixed 75% volume: raw false-barges = 1
every time, AEC-processed = 8-13 depending on delay) and
`docs/field-notes/2026-08-11-full-volume-sweep-raw-data-and-room-rt60.md`
(119 trials pinning the bad-to-good transition at roughly 30-40% system
volume, delay fixed to `auto`). A follow-up,
`docs/field-notes/2026-08-11-self-barge-in-combined-mitigation-and-hardware-notes.md`,
found a real mitigation (below). This session's contribution is scale
and coverage, not discovery: **N=10 instead of N=1, across the full
standard delay-candidate set instead of one fixed delay, crossed with
5 volume levels instead of one** -- 250 trials total, confirming the
effect is not a small-sample artifact and holds at every one of the 5
tested delays, not just `auto`.

## Method

Full cross of 5 `aec_delay_ms` candidates (`auto`, 222, 272, 309, 322 --
this repo's standard candidate set, used in every prior delay sweep) x
5 macOS system output volume levels (100%, 75%, 50%, 35%, 20%), N=10
repeats per combination = **250 real live trials**, one continuous
overnight run. `tts.volume=4.0` (Piper's own linear gain) held fixed,
matching the 2026-08-11 sweeps exactly for comparability.
`interrupt_preset=conversational`, `barge_in_min_speech_ms=250` -- this
project's real default, and deliberately the UNMITIGATED value (see
below, this sweep does not test the known fix). Each of the 5 volume
levels got its own full 45s ambient-noise capture (zero false
utterances at any VAD threshold, all 5 levels -- confirms every false
barge-in below is playback-induced, not room noise) followed by its own
5-candidate x N=10 delay sweep, system volume restored to the pre-run
25% afterward.

macOS caveat: `--volume-candidates` in `scripts/acoustic_calibration.py`
only supports Windows (pycaw) and Linux (wpctl/PipeWire) -- there is no
macOS branch. The volume axis here was driven externally, one script
invocation per level via `osascript -e "set volume output volume N"`,
the same manual method the 2026-08-11 sweeps used by hand. A real
macOS branch (osascript/CoreAudio) would remove this manual step for
any future sweep here -- flagged as a follow-up, not built this session.

## Results: AEC-processed audio false-barges far more than raw at high volume, across every delay tested

Averaged across all 5 delay candidates (50 trials per volume level):

| system volume | mean attenuation | mean suppression | raw mic false-barges (total/50, mean/trial) | AEC-processed false-barges (total/50, mean/trial) | ratio (AEC/raw) |
|---|---|---|---|---|---|
| 100% | 12.14 dB | 10.03 dB | 50 / 1.00 | 495 / 9.90 | 9.9x worse |
| 75%  | 11.32 dB | 9.87 dB  | 50 / 1.00 | 491 / 9.82 | 9.8x worse |
| 50%  | 14.07 dB | 11.05 dB | 50 / 1.00 | 230 / 4.60 | 4.6x worse |
| 35%  | 12.39 dB | 10.41 dB | 118 / 2.36 | 90 / 1.80 | 0.76x (AEC helps) |
| 20%  | 6.10 dB  | 5.89 dB  | 76 / 1.52  | 64 / 1.28 | 0.84x (AEC helps) |

Matches the 2026-08-11 75%-volume finding closely in shape (raw stays
near-flat around 1 false-barge/trial regardless of volume; AEC-processed
is dramatically worse at high volume) and confirms the earlier
119-trial sweep's ~30-40% transition point: this grid's own crossover
sits between 50% (4.6x worse) and 35% (already better than raw),
consistent with that range.

**Per-delay-candidate breakdown, 100% volume** (worst case, 10 trials
each) -- confirms the effect is not specific to `auto`'s live-estimated
delay, or to any single candidate:

| candidate | actual delay used | mean attenuation | mean suppression | raw false-barges (total/10) | AEC false-barges (total/10) |
|---|---|---|---|---|---|
| auto | 238ms | 14.59 dB | 11.39 dB | 10 | 102 |
| 222ms | 222ms | 13.16 dB | 10.21 dB | 10 | 98 |
| 272ms | 272ms | 10.83 dB | 9.65 dB | 10 | 101 |
| 309ms | 309ms | 10.91 dB | 9.38 dB | 10 | 97 |
| 322ms | 322ms | 11.22 dB | 9.54 dB | 10 | 97 |

Every candidate at 100% volume produces roughly 10x more AEC-processed
false barges than raw. Note: `400ms` -- the one delay the 2026-08-11
note found to help most (8 false-barges vs. 10-13 for the others, still
worse than raw's 1) -- is NOT in this repo's standard candidate set and
was not tested in this grid. That gap is worth closing in a future run.

## Mechanism (from one representative trial, `auto-r1` at 100% volume)

Raw mic: 1 utterance detected during the whole ~33s known response, 1
false barge-in -- consistent with the assistant's own leaked TTS being
heard as one continuous speech-like event, the expected raw-echo
behavior.

AEC-processed mic, same trial: 5 separate utterances, spread across the
response (`utterance_seconds: [1.312, 2.08, 0.928, 2.592, 1.152]`), 11
false barge-ins at times spanning almost the entire clip
(`barge_in_times_s`: 3.2s through 32.9s). Peak VAD speech probability on
the AEC-processed signal: 0.9999; p95: 0.9508 -- Silero VAD is highly
confident these fragments are real speech, not marginal noise crossing
threshold. AEC3's own output, at this hardware/volume combination, is
being chopped into several distinct speech-like fragments spread across
the whole response, each independently confident enough to fire the
barge-in monitor -- consistent with the 2026-08-09 field note's own
divergence framing ("AEC3's spectral suppression metrics and downstream
VAD-based self-trigger rejection point to different conclusions") on
different hardware (a laptop-internal array), and with issue #119's
corroborated-but-not-directly-measured hypothesis: the Mac mini's single
built-in speaker distorting at volume, feeding AEC3's linear echo model
nonlinear content it structurally can't cancel cleanly.

## Known mitigation NOT tested in this grid

`docs/field-notes/2026-08-11-self-barge-in-combined-mitigation-and-hardware-notes.md`
found that stacking `aec_delay_ms=400` + `barge_in_min_speech_ms=1200`
(at 75% volume, N=4) brought AEC-processed false-barges down to a mean
of 1.25, close to raw's own ~1. This grid used the standard delay
candidates (not including 400ms) and left `barge_in_min_speech_ms` at
its unmitigated default (250ms) throughout, specifically to
characterize the raw problem's scale and shape across delay x volume --
not to re-validate the known fix. A natural next sweep: the same 5x5
volume grid, but with `barge_in_min_speech_ms=1200` and `400ms` added
to the delay-candidate set, to see whether the 2026-08-11 mitigation
holds at N=10 across the full volume range, not just at 75%.

## What this does NOT show

- **Not a delay-hint problem.** Every candidate, including live
  auto-estimation, shows the same pattern at high volume.
- **Not ambient noise.** Zero false utterances at any VAD threshold
  during all 5 ambient captures (45s each, no playback).
- **Not proof AEC should be disabled outright.** At 35%/20% volume it
  measurably helps (below 1x ratio); the finding is specifically that
  it hurts at the volume range most people would actually use, on this
  specific hardware.
- **Not a new root-cause finding.** The distortion-at-volume hypothesis
  is issue #119's, corroborated again here by scale, not newly
  established or directly measured by this grid.

## Recommended next steps (not decided or built this session)

1. Re-run this same 5x5 grid with `barge_in_min_speech_ms=1200` and
   `aec_delay_ms=400` added, to check whether the known mitigation holds
   at N=10 across the full volume range (currently only validated at
   N=4, one volume).
2. A `--volume-candidates` macOS branch in `scripts/acoustic_calibration.py`
   (osascript-based) would remove the manual driver-script step for any
   future sweep on this machine.
3. NS/AGC (`docs/KNOWN-ISSUES.md`'s "WebRTC APM's noise suppression /
   auto gain control are unused" candidate, still awaiting go-ahead) is
   worth reconsidering specifically as a mitigation for this
   fragmentation pattern, not just for the mic-gain problem it was
   originally scoped for.
4. This finding needs a decision from JP, not a unilateral fix -- root
   cause is still corroborated-not-measured (a real speaker-driver
   distortion test, e.g. checking the raw playback WAV for clipping at
   high volume, would confirm or rule it out directly).
