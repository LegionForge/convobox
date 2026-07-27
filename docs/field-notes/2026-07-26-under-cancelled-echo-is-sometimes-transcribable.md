---
title: Under-cancelled AEC echo can be loud enough for STT to transcribe real words out of the assistant's own voice, which then get accepted and echoed back as if the operator said them
status: validated-live
date: 2026-07-26
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ a4fcaa7; WebRTC AEC3 via aec_audio_processing; vad.threshold 0.65; aec_delay_ms auto-tune (measured 222ms this session); faster-whisper STT (CPU fallback, cuBLAS unavailable)
evidence:
  - convobox-UAT/convobox-tui.log.old.20260726, 20:04-20:18 session (lines ~30141-30826 of the pre-rotation log)
  - convobox-UAT/.incident-captures/20260726-200623/ (reference.wav, mic-raw.wav, mic-processed.wav, manifest.json)
  - convobox-UAT/.incident-captures/20260726-201249/ (same)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live voice UAT, by-ear review of both incident captures against the log's AEC-verdict numbers)
    - Claude Code (Anthropic claude-sonnet-5) — session-log analysis, incident selection, cross-referencing AEC stats to STT transcripts, writing
  org: https://legionforge.org
  created: 2026-07-26T21:10:00-05:00
  revised: 2026-07-26T21:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Under-cancelled echo can be loud enough for STT to transcribe -- and act on -- the assistant's own voice

**Context for outsiders.** ConvoBox is a local voice frontend for CLI coding
agents: mic and speakers are open simultaneously, and acoustic echo
cancellation (AEC) is what keeps the assistant's own TTS output from being
picked back up by the mic and misread as user speech. This note is about
what happens when AEC only partially does that job.

## Problem

A UAT session run to validate an unrelated `vad.threshold` change (0.55 ->
0.65, see the 2026-07-25 field notes) instead surfaced near-constant
self-triggered barge-ins: **42 of 45 responses (~93%)** interrupted
themselves. Of those 42, the AEC stats line logged **15 as
`UNDER-CANCELLING`** -- a verdict meaning real, measurable echo was left
unresolved, not just a diagnostic false start.

## Evidence

Incident 1 (`.incident-captures/20260726-200623/`), worst of the session:

```
20:06:18,905  response(spoken): 1. Atom
20:06:23,820  barge-in: sustained speech during playback -- stopping audio
20:06:24,040  AEC stats: attenuation=1.3dB of ~14.1dB measurable  delay=222ms
              frames(reverse=1171, capture=9318)
              [UNDER-CANCELLING: ~12.9dB of echo headroom remains]
20:06:26,798  transcript='one, and two, and open.' lang=en (0.86) dec=0.36
              busy=False  [BARGE-IN]
20:06:29,027  response: Heard: "one, and two, and open."
```

Only **1.3dB of a required ~14.1dB** was actually cancelled. `reverse=1171`
means ~11.7s of reference audio had already been fed this session --
well past AEC3's normal few-hundred-ms convergence window, so this isn't a
cold-start artifact. Whisper's transcript ("one, and two, and open.") reads
as a garbled hearing of the assistant's own list ("1. Atom" plus further
items ending in "...open"); it was accepted as real speech and the system
replied by repeating it back.

By-ear confirmation (operator, `mic-processed.wav`): the assistant's list
content was audible "without amplification," matching Whisper's
independent (and separately garbled) transcription of the same leaked
audio -- two different listeners (one human, one STT model) extracted
recognizably related words from the same under-cancelled signal.

Incident 2 (`.incident-captures/20260726-201249/`), a lower-headroom
sample picked specifically to test whether the dB reading tracks
audibility:

```
20:12:44,579  response: fjord, magnolia, zircon, paddle, ...
20:12:49,709  barge-in: sustained speech during playback -- stopping audio
20:12:49,947  AEC stats: attenuation=2.2dB of ~6.9dB measurable  delay=222ms
              [UNDER-CANCELLING: ~4.7dB of echo headroom remains]
20:12:54,114  transcript='Barching in, barging in, barging in, barging in.'
              lang=en (0.98) dec=0.68  busy=False  [BARGE-IN]
```

By-ear review confirmed the pattern holds even at less than half the
first incident's headroom: "Fjord Magnolia can clearly be heard (barely
any attenuation), though it's quiet" in `mic-processed.wav`, at roughly
the same relative position `mic-raw.wav` shows it clean and loud. The
operator's own barge-in speech immediately after was, correctly,
*un*attenuated (AEC only ever targets the far-end reference, never the
near-end mic path). This time the transcript ("barging in" x4) was the
operator's own live narration, not another echo misread -- a useful
negative control showing not every barge-in in this session is this bug.

## Mechanism

WebRTC AEC3 (via the `aec_audio_processing` wrapper, `src/convobox/audio/aec.py`)
is leaving a real, audible fraction of the far-end signal in the
mic-processed output on this acoustic path: an amplified Creative Labs
7.1 speaker system (front L/R channels only -- this machine has no true
7.1 output), fed from the Realtek onboard jack (the `output_device` key
is misleadingly named `Headphones (2- Realtek(R) Audio, MME` but is not
a headset), into a webcam mic (`1080P Pro Stream`). `aec_delay_ms` was
left at auto-tune (measured 222ms this session, consistent with the
2026-07-25 finding that no fixed delay beats auto-tune on this rig).

When the residual echo is loud enough, it clears the STT confidence bar
just like real speech would, and nothing in the pipeline checks whether
a `[BARGE-IN]`-tagged transcript might just be the assistant's own words
coming back. The transcript is accepted, and in the incident-1 case
the acknowledgment ("Heard: ...") makes the self-hearing loop audible to
the operator as well as visible in the log.

**A secondary, not-yet-explained fluctuation was noticed by ear within
incident 2's own capture window**: the early segment ("fjord, magnolia")
was "barely attenuated," while a later utterance in the *same* 15-second
file ("No spoken reply, barge-in received") was, by the operator's own
description, "significantly attenuated (garbled) ... in comparison to
mic-raw.wav." Two candidate explanations, neither confirmed:
- word-list-style TTS content (the operator was separately stress-testing
  STT with strings of unrelated words) has different prosody/pacing than
  conversational phrasing and may adapt worse in AEC3's filter;
- double-talk (the operator's barge-in speech overlapping the far-end
  signal) contaminated the adaptive filter's error signal for a period
  before it recovered.
Flagged as an open question, not claimed as mechanism -- distinguishing
these needs a same-speaker-content vs. word-list A/B, which wasn't run
here.

**Ruled out**: this is not the same mechanism as `[G8]` (`is_playing()`
racing ahead of the first real audio, see
`docs/field-notes/2026-07-25-player-is-playing-races-ahead-of-first-audio.md`)
or `[G7]` (backchannel classification gap) -- both of those involve
either no real audio yet, or correctly-classified speech. This is real,
present, audible echo that a human and an STT model both independently
extracted words from.

## What transfers

- **The logged dB-headroom heuristic and human perception agreed closely
  across two independently sampled incidents (12.9dB and 4.7dB)** --
  when the verdict says meaningful headroom remains, a human listening to
  the post-cancellation feed can reliably confirm audible bleed-through,
  down to recognizable words. (validated-live, n=2)
- **A voice pipeline that treats "AEC ran" as "echo is gone" is exposed to
  a self-hearing loop**: under real-world (not lab-clean) acoustic
  conditions, under-cancelled echo can be intelligible enough for STT to
  transcribe actual words, which then pass through as if genuinely
  spoken -- including being spoken back to the operator as an
  acknowledgment. This generalizes past ConvoBox to any system that gates
  "is this real user input" on STT confidence alone, without an
  echo-content check. (validated-live)
- **AEC cancellation quality is not stable even within one continuous
  session** -- two utterances roughly 15 seconds apart, in the same
  incident capture, showed clearly different by-ear cancellation quality.
  Root cause not distinguished (word-list content vs. double-talk
  contamination). (hypothesis)
- **Not yet tested**: whether headphones eliminate this. This repo's own
  earlier UAT history (`[L6]`/`[L7]`) found near-zero false barge-ins on a
  headset rig; this session's open-speaker (amplified desktop speakers)
  + webcam-mic path is the specific configuration already flagged there
  as AEC's hard case. (open question)
