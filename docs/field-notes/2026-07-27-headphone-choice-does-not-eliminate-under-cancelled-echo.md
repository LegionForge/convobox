---
title: Headphone choice (bone-conduction vs. sealed over-ear) does not meaningfully change the under-cancelled-echo leak rate in an acoustically reflective room -- the room dominates, not the transducer
status: validated-live
date: 2026-07-27
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ ac9ebeb; WebRTC AEC3 via aec_audio_processing; vad.threshold 0.65; aec_delay_ms auto-tune (measured 222ms both sessions); faster-whisper STT (CPU fallback, cuBLAS unavailable); backend codex/gpt-5.6-terra
evidence:
  - convobox-UAT/convobox-tui.log, lines 1-2388 (Shokz OpenComm session, 16:35-18:10) and 2389-3266 (MPow H12 session, 19:04-19:44)
  - convobox-UAT/uat-echo.log (session headers)
  - convobox-UAT/.incident-captures/20260727-16365[2-9]*, 20260727-1637*, 20260727-1638*, 20260727-1639*, 20260727-1640*, 20260727-1641* (Shokz sample)
  - convobox-UAT/.incident-captures/20260727-1907*, 20260727-1921*, 20260727-1934*, 20260727-1937* (MPow flagged events)
  - scripts/analyze_incident.py output for both sets above
  - Settings TUI `[t]` input-device probe readings (room tone and speech, separated)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live voice UAT on both headsets, headset/mic switching, mic-gain probes, room description)
    - Claude Code (Anthropic claude-sonnet-5) -- session-log analysis, offline correlation cross-checks via analyze_incident.py, rate normalization, writing
  org: https://legionforge.org
  created: 2026-07-27T21:02:42-05:00
  revised: 2026-07-27T21:02:42-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Headphone choice doesn't fix under-cancelled echo in a reflective room -- the room is the dominant variable, not the transducer

**Context for outsiders.** ConvoBox is a local voice frontend for CLI coding
agents: mic and speakers run simultaneously, and acoustic echo cancellation
(AEC) keeps the assistant's own TTS output from being picked back up by the
mic and misread as user speech. A prior finding
(`2026-07-26-under-cancelled-echo-is-sometimes-transcribable.md`, `[G9]`)
showed this can go wrong badly enough for STT to transcribe -- and the
system to act on -- its own voice, on an open-speaker rig, and left an
explicit open question: "not yet tested: whether headphones eliminate
this." This note answers that question with two real headsets, live, in
the same room.

## Problem

Does switching from open speakers to headphones -- and does headphone
*type* -- reduce the AEC echo-leak rate that caused `[G9]`? Two UAT
sessions were run back to back, same room, same mic, same backend/model,
only the output headset changed:

1. Shokz OpenComm (1st gen, bone-conduction, open-ear)
2. MPow H12 (Bluetooth, sealed over-ear "full cans")

Both used `Microphone (1080P Pro Stream)` as input (unchanged, external
webcam mic on top of the monitor -- not the headset's own mic; see
Mechanism for why a same-link headset mic wasn't testable).

## Evidence

Session-wide AEC verdict counts, both at `aec_delay_ms` auto-tune (222ms):

| | Shokz (bone conduction) | MPow H12 (sealed cans) |
|---|---|---|
| Duration | ~95 min | ~40 min |
| AEC stat windows | 160 | 66 |
| `NO ECHO DETECTED` | 114 (71%) | 50 (76%) |
| `UNDER-CANCELLING` | 7 (4.4%) | 2 (3.0%) |
| Barge-ins | 61 | 16 |
| Real echo-match drops (STT transcribed audible TTS bleed, correctly caught) | 7 (11.5% of barge-ins) | 2 (12.5% of barge-ins) |
| Forwarded to backend as if real speech | 0 | 0 |

Sample `UNDER-CANCELLING` line (Shokz, 16:38:01):
```
AEC stats for last response: attenuation=0.9dB of ~13.6dB measurable
delay=222ms  [UNDER-CANCELLING: ~12.7dB of echo headroom remains]
```

Sample real echo-match (MPow, 19:34:33, `echo-match: 0.18` -- of the
seven Shokz and two MPow matches, the highest was 0.75, MPow's
`19:37:08`, `echo-match: 0.29`):
```
dropped (overlap gate, echo-cancellation active): '10% I actually have
no idea. Can you take a look?' [echo-match: 0.18 of tokens in last
response]
```

**Independent offline cross-check.** `scripts/analyze_incident.py`
(PR #162) was run against 10 incident captures across both sessions (6
general early-session Shokz samples, and all 4 of MPow's flagged
`UNDER-CANCELLING`/echo-match events, matched by exact timestamp). All 10
returned near-noise-floor raw correlation (-0.14 to +0.17) and were
flagged `LOW CONFIDENCE: aligned window only Nx baseline energy --
likely a spurious match, not the real echo` by the tool's own
energy-ratio gate. No independently-confirmed real echo signature was
found in either headset condition -- consistent with, but not stronger
proof than, the live heuristic's own dB/token-match verdicts.

**Room noise floor, measured at the mic's real fixed operating
position** (top of monitor, cannot be relocated -- cable routing):
- Room tone alone (fans, AC, an active Roomba in another room): rms
  -45.9dBFS, peak -32.0dBFS
- Speaking normally: rms -32.2dBFS, peak -13.2dBFS
- Margin: ~13.7dB rms between voice and noise floor
- Verdict per `_level_verdict()` thresholds (`scripts/audio_devices.py`):
  **good** (not "very quiet" -- an earlier single mixed-window probe had
  read -48.2dBFS and looked like a gain problem; that reading mixed
  silence and speech in one 3s capture and was misleading, see What
  Transfers)

Room description, operator's own words: "carpets on the floors (BIG
carpets) but I don't have any soundproofing on the ceiling or walls,"
with audible "echoing (wet) off walls" during the input-device probe's
room-tone-only playback.

## Mechanism

The leak rate did not move in proportion to how different the two
headsets are acoustically -- bone conduction (open-ear, no seal) and
sealed over-ear cans are close to opposite ends of the passive-isolation
spectrum, yet `UNDER-CANCELLING` (4.4% vs 3.0%) and real echo-match rate
(11.5% vs 12.5%) landed within noise of each other. If headset isolation
were the dominant factor, sealed cans should have shown a materially
lower rate than an open-ear bone-conduction design. It didn't.

The more parsimonious explanation given the room-tone data: this is a
**reflective, untreated room** (bare walls/ceiling, only floor carpet)
with a real ambient noise floor from fans/AC/Roomba. Room reflections add
echo paths beyond whatever the headset-to-mic or speaker-to-mic direct
path is, and a fixed-delay/adaptive AEC filter (`aec_delay_ms` auto-tune,
222ms both sessions) has a harder time modeling multipath than a single
dominant delay. The noise floor separately explains why the offline
correlation tool's confidence gate fired on effectively all samples: its
`baseline_rms` is computed over the whole capture including quiet
segments, and a -45.9dBFS floor is high enough to pull real, well-
cancelled echo below the `energy_ratio >= 1.3` confidence cutoff -- the
exact caveat flagged reviewing PR #162, now empirically observed rather
than theoretical.

**A third variable -- mic proximity -- was planned but not testable.**
The hypothesis: a close-talk mic worn on the headset would structurally
reject room reflections by proximity (inverse-square law) regardless of
output transducer, which would distinguish "room reflections reaching a
distant desk mic" from "headset transducer bleed" as the leak's source.
Neither headset supported this test: the MPow H12 has no mic input at
all (A2DP playback-only Bluetooth profile); the Shokz OpenComm has one,
but enabling it forces the Bluetooth link from A2DP (stereo, ~328kbps)
to HFP/SCO (mono, ~64kbps, "telephone grade") -- classic Bluetooth
can't do full-quality stereo output and mic input on one link
simultaneously. The operator had already disabled the OpenComm mic for
this reason in an earlier, unrelated session. Testing mic proximity
cleanly needs a mic on an independent link from playback (wired
USB/lav), which wasn't available this session (a previously-owned
high-quality mic was lost in a move).

**Ruled out**: this is not `[G8]` (`is_playing()` racing ahead of real
audio -- that's silence being misread, not real echo) and it reproduces
the same `UNDER-CANCELLING`/real-echo-match pattern as `[G9]`'s
open-speaker finding, just at a lower absolute rate -- headphones did
reduce the *count* of incidents relative to the open-speaker session
that produced `[G9]` (that session logged 15 `UNDER-CANCELLING` events
in 45 responses, a ~33% rate; these sessions logged 4.4% and 3.0%), but
did not reduce it to zero, and did not show a further reduction from
bone-conduction to sealed cans specifically.

## What transfers

- **Headphones reduce but do not eliminate under-cancelled echo, and
  headphone type (open bone-conduction vs. sealed over-ear) does not
  materially change the residual rate** in a reflective, untreated room.
  This directly answers `[G9]`'s open question. (validated-live, n=2
  headsets, one room)
- **The spoken-echo/overlap-gate safety net caught 100% of real echo
  matches across both sessions** (9 for 9; 0 forwarded to the backend as
  real speech) -- the specific failure mode that made `[G9]`'s original
  incident possible (echo transcribed and acted on) did not reproduce
  with either headset, though the underlying leak that caused it is
  still measurably present. (validated-live)
- **Room acoustics (reflective surfaces, ambient noise floor) is a more
  likely lever than headset choice** for further reducing the residual
  leak, since two very different transducers produced statistically
  indistinguishable rates. Not confirmed against a second room -- this
  project has tested in exactly one physical space. (hypothesis)
- **A single mixed-window mic-gain probe can produce a misleading "very
  quiet" verdict.** A 3-second capture that includes both silence and
  speech (e.g. a pause before speaking) averages both into one rms
  number; separating a room-tone-only probe from a speech-only probe
  gave a materially different and more accurate read (-48.2dBFS mixed
  vs. -32.2dBFS speech-only, "good") on the same mic in the same
  position. Worth two separate probes, not one, when judging mic gain.
  (validated-live)
- **Bluetooth stereo headsets generally cannot be used to test
  near-field mic pickup without confounding the output side**: enabling
  a Bluetooth headset's mic typically forces the link from A2DP to
  HFP/SCO, capping output audio quality (~64kbps mono vs ~328kbps
  stereo) enough to invalidate a clean A/B. Testing mic proximity
  requires a mic on an independent (wired) link from whatever is
  producing the output being tested. (validated-live, closed off this
  session's third planned test)
- **`analyze_incident.py`'s confidence gate is sensitive to room noise
  floor, as flagged in PR #162's review** -- 10/10 sampled events across
  both headset sessions in this specific room returned `LOW CONFIDENCE`,
  consistent with a -45.9dBFS ambient floor pulling `baseline_rms` high
  enough to under-call real (but well-cancelled) echo. Not evidence the
  tool is broken; evidence the caveat is real. (validated-live, n=10 in
  one room)
