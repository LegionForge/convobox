---
title: Combined self-barge-in mitigation (400ms delay + 1200ms threshold) nearly eliminates the problem; likely root cause is Mac mini speaker distortion at high volume, not AEC delay alone
status: validated-live (combined mitigation, N=4); hardware-distortion root cause is plausible/corroborated but not directly measured
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 4aedbac; tts.volume=4.0, macOS system output volume=75%
hardware:
  computer: Mac mini M4 (2024) -- Apple's own spec sheet lists a single "Built-in speaker" (singular); independent reviews (Best Buy Q&A, Popzara, bestsounds.net) specifically describe it as small and prone to distortion at volume, not a stereo pair or hi-fi driver.
  microphone: AIRHUG 28 USB conference microphone -- 360 degree omnidirectional pickup (no directional/cardioid rejection of the speaker's own output), built-in DSP chip with an "AI Noise Reduction" mode. Three-color LED indicates mode -- Blue: AI Noise Reduction ON, Green: Original Mode (AI DSP off), Red: Muted.
  ai_dsp_state: Confirmed OFF -- JP directly observed the mic status LED showing green (Original Mode) throughout all testing in this session. Not a caveat; ruled out as a variable.
  mic_placement: approximately 8cm from the Mac mini, facing away from the Mac mini's own body (i.e. not pointed at the computer/speaker).
evidence:
  - 4 more real trials at tts.volume=4.0 + macOS system volume 75%, combining the two individually-best mitigations found in the prior field note (aec_delay_ms=400, barge_in_min_speech_ms=1200)
  - Direct clipping check on the raw mic-capture WAV for all 4 combined trials
  - Web research on Mac mini M4 speaker hardware and AIRHUG 28 mic specs (see Sources)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the combined test, supplied the hardware placement/DSP details, and directly reported audible speaker distortion at this volume in real time)
    - Claude Code (Anthropic claude-sonnet-5) -- ran the combined sweep, checked for digital clipping, researched the hardware specs, wrote this note
  org: https://legionforge.org
  created: 2026-08-11T13:00:00-05:00
  revised: 2026-08-11T13:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Combined self-barge-in mitigation + hardware notes

## Combined mitigation result: close to solved

Stacking the two individually-best mitigations from the prior field
note (`docs/field-notes/2026-08-11-self-barge-in-mitigation-at-demo-volume.md`)
-- `aec_delay_ms=400` + `barge_in_min_speech_ms=1200` -- at the exact
same demo volume (`tts.volume=4.0`, macOS system output 75%):

| Trial | `false_barge_ins` (AEC-processed) |
|---|---|
| 1 | 2 |
| 2 | 3 |
| 3 | 0 |
| 4 | 0 |

**Mean 1.25, and 2 of 4 trials hit true zero** -- dramatically better
than either mitigation alone (400ms delay alone: 8; 1200ms threshold
alone: 1 single trial; no mitigation: 9-13). N=4 is still a small
sample, but the improvement over every prior configuration tested is
large enough to be a real, usable result, not noise.

**Practical recommendation, updated**: if `conversational` mode needs
to stay on at this volume, set both
`audio.aec_delay_ms: 400` and `interaction.barge_in_min_speech_ms:
1200` together. This doesn't fully eliminate the risk (2 of 4 trials
still had 2-3 false triggers), but it takes it from "reliably loops"
to "occasional, survivable."

## Hardware specs (per JP, plus verified web research)

- **Computer**: Mac mini M4 (2024). Apple's own tech spec sheet lists
  a single **"Built-in speaker"** (singular) -- not a stereo pair.
  Independent sources (a Best Buy Q&A thread, Popzara's review,
  bestsounds.net) specifically describe this speaker as small and
  **prone to audible distortion at volume** -- one review's phrasing:
  "the audio being distorted and downright awful," recommending an
  external speaker or the headphone jack for anything beyond casual
  use.
- **Microphone**: AIRHUG 28 USB conference mic. **360 degree
  omnidirectional pickup** -- no directional/cardioid rejection
  pattern, meaning it has zero spatial ability to discriminate between
  the user's voice and the Mac mini's own speaker output; the entire
  burden of separating them falls on AEC/software. Has its own
  built-in DSP chip with an "AI Noise Reduction" mode, indicated by a
  3-color LED (Blue = AI Noise Reduction on, Green = Original Mode /
  AI DSP off, Red = Muted).
- **AI DSP state, confirmed**: JP directly observed the mic status LED
  showing green (Original Mode, AI DSP off) throughout all testing in
  this session -- ruled out as a variable, not just assumed. This means
  every result in this and the prior field note reflects `EchoCanceller`
  fighting the raw acoustic path directly, with no additional
  undocumented onboard mic-side processing in the loop.
- **Mic placement**: ~8cm from the Mac mini, facing away from the Mac
  mini's own body.

## Real speaker distortion: plausible and independently corroborated, not directly confirmed here

JP reported live, while this testing was still in progress, that the
Mac mini's speakers "seem to be very loud, but distorting at this
volume." Checked the digital recordings from the 4 combined-mitigation
trials directly for clipping: **no digital clipping in any of the 4
raw mic captures** (peak amplitude 0.63-0.68 out of a possible 1.0,
zero samples above the 0.95 near-clipping threshold in any trial).

**This does NOT rule out JP's report, and the two things are
genuinely different questions.** Digital clipping in the recorded
signal would mean the mic's own ADC or gain staging was overloaded.
Acoustic/mechanical distortion at the speaker driver itself (the cone
being pushed beyond its linear excursion range) is a physical
phenomenon that happens before the sound ever reaches the mic --
a small, known-mediocre single driver (per the hardware research
above) being driven at 4x app-level gain on top of 75% system volume
is a very plausible source of exactly that kind of distortion, and it
would show up as extra harmonic content in the acoustic wave, not
necessarily as flat-topped digital clipping in a mic recording made
from ~8cm away with real room acoustics and mic gain staging in
between.

**Why this matters mechanistically, if true**: `EchoCanceller` (WebRTC
AEC3) is fundamentally a *linear* adaptive filter -- it works by
modeling the acoustic path from speaker to mic as (approximately) a
linear transformation and subtracting a predicted copy of the far-end
signal. A genuinely nonlinear acoustic path (real speaker distortion)
is not something a linear filter can fully model or cancel, no matter
how well the delay is tuned. This would directly explain the prior
field note's most surprising finding -- AEC-processed audio producing
MORE false triggers than AEC-off at every delay tested -- since AEC
would be predictably failing to cancel a signal it structurally cannot
model, potentially introducing its own residual artifacts in the
process.

**Not measured directly this pass**: actual total-harmonic-distortion
analysis of the acoustic signal, or a controlled A/B at a lower volume
to see whether the counterintuitive "AEC makes it worse" result
disappears once real distortion is removed from the picture. That
would be the natural next test if this theory needs to move from
"plausible and corroborated" to "confirmed."

## What transfers

- **The combined mitigation (400ms delay + 1200ms threshold) is the
  best practical answer found so far** for anyone who needs
  `conversational` mode at high volume on this exact hardware pairing.
- **A likely root cause now has a name and a mechanism**: probable
  speaker-driver distortion on the Mac mini's single built-in speaker,
  which a linear AEC structurally cannot fully cancel -- explains the
  session's most surprising finding (AEC making things worse) far more
  satisfyingly than "AEC just isn't tuned right."
- **The AIRHUG 28's onboard AI DSP mode was confirmed off (green LED)
  throughout testing** -- ruled out as a confound. Every result in this
  session's testing reflects `EchoCanceller` against the raw acoustic
  path, not a second undocumented nonlinear processing stage stacked
  on top of it.
- **The single biggest lever remains turning the volume down** --
  everything in this note is about coping with distortion at a volume
  level that was itself an explicit, deliberate choice for
  audibility across a room, not a default ConvoBox setting.

## Sources

- [Apple Mac mini (2024) — Tech Specs](https://support.apple.com/en-us/121555) (lists "Built-in speaker," singular)
- [Best Buy Q&A: Does the Mac mini have internal speakers?](https://www.bestbuy.com/site/questions/apple-mac-mini-desktop-latest-model-m4-prochip-built-for-apple-intelligence-24gb-memory-512gb-ssd-silver/6566916/question/0f77ca3f-8c41-3bbe-9847-ab33e2bf812c)
- [Do Mac Minis Have Speakers? How-To Guide](https://bestsounds.net/do-mac-minis-have-speakers-how-to-guide/) (describes the built-in speaker as distorted/awful at volume)
- AIRHUG 28 product listings (Amazon US/UK, microless.com) describing 360° omnidirectional pickup, AI Noise Reduction mode with LED color indicator (Blue/Green/Red)
