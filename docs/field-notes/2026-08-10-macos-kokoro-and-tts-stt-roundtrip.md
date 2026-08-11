---
title: Kokoro TTS confirmed working live on macOS; a naive TTS-speaker-mic-Whisper round-trip reproduces the known [E6] far-field hallucination pattern, not a new accuracy bug
status: validated-live (Kokoro); diagnosed, matches a pre-existing known issue (STT round-trip)
date: 2026-08-10
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 0df9129; macOS 26.x, Apple Silicon; AIRHUG 28 (USB mic), Mac mini Speakers
evidence:
  - Real Kokoro synthesis + playback via scripts/run_convobox.py --text, real speakers
  - A standalone TTS->speaker->mic->STT round-trip script (convobox-UAT worktree scratch, gitignored, not committed) -- 12 phrases total (6 x 2 engines), real Piper + Kokoro synthesis, real AIRHUG 28 capture, real faster-whisper transcription
  - docs/UAT-checklist.md's pre-existing [E6] entry
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked what else to test besides AEC: "kokoro? piper/whisper?")
    - Claude Code (Anthropic claude-sonnet-5) -- ran both tests, diagnosed the round-trip's low scores against the RMS trace before concluding anything, wrote this note
  org: https://legionforge.org
  created: 2026-08-10T22:15:00-05:00
  revised: 2026-08-10T22:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Kokoro on macOS + a TTS/STT round-trip that reproduces a known issue

## 1. Kokoro TTS: confirmed working live on macOS, for the first time

README's own support matrix has listed Kokoro as "real synthesis
verified programmatically against the actual model, not yet a live
voice session with real speakers" since 2026-07-24, on any platform.
Ran `scripts/run_convobox.py --config <kokoro config> --text "reply
with exactly the word banana"` against a real claude-code backend:
`voices-v1.0.bin` (28MB) auto-downloaded cleanly on first use (the
larger `kokoro-v1.0.onnx` model file was already present from an
earlier session), synthesis succeeded, and `playback: first audio
block reached output device` confirmed real audio actually played
through the Mac mini Speakers. **First live confirmation of Kokoro
with real speakers, on any platform, per this repo's own docs.**

## 2. TTS -> speaker -> mic -> Whisper round-trip: low scores, diagnosed before concluding anything

Built a standalone script: synthesize 6 known phrases (both Piper
`en_US-lessac-medium` and Kokoro `af_sarah`), play each through the
real Mac mini Speakers, capture back through AIRHUG 28 in real time,
transcribe with the real faster-whisper (`base` model, this repo's own
documented default) STT engine, score word-level accuracy against the
known text.

**Raw result: mean word accuracy 10.3% (Piper) / 23.4% (Kokoro)** --
several transcripts came back empty, several came back as unrelated
hallucinated text (e.g. `"I'm sorry, I'm sorry, I'm sorry, I'm sorry,
I'm sorry."` for "the quick brown fox jumps over the lazy dog").

**Before concluding STT is broken, checked the actual captured audio.**
RMS-per-100ms trace of one captured phrase showed a large transient at
t=0.00s then real levels around 0.005-0.007 for the rest of the
utterance -- the same order of magnitude as tonight's AEC calibration
`raw_playback_rms` readings (0.0053-0.0088 across every volume level
tested), so the mic genuinely was picking up the played TTS audio, not
silence or a clipped/truncated capture. The audio is real; Whisper's
transcription of it is just bad.

**This matches `docs/UAT-checklist.md`'s pre-existing `[E6]` entry
exactly**: "Whisper hallucination loops on far-field echo... one
transcript repeated a clause five times" -- the `"I'm sorry"` x5
transcript above is the same failure signature. **`[E6]`'s own note
says this is already caught by the overlap window in real ConvoBox
sessions** -- production's overlap gate / echo-tail guard / spoken-text
echo filter exist specifically to keep this exact class of degraded
transcript from ever reaching a user or a backend. This round-trip
script bypassed all of that on purpose (fed raw captured audio straight
to STT, no gates in the loop) to isolate raw transcription quality --
so the low score is diagnosing Whisper's raw behavior on quiet,
far-field, speaker-through-air audio, not measuring what a real
ConvoBox session's user-facing accuracy looks like.

## What transfers

- **Kokoro works on macOS with real hardware** -- closes a real,
  previously-open gap in README's support matrix specifically for this
  engine. (validated-live)
- **The `[E6]` far-field Whisper hallucination pattern reproduces on
  macOS**, not just wherever it was first observed -- useful
  cross-platform confirmation of an already-diagnosed, already-mitigated
  issue, not a new bug. No code change implied; the existing mitigation
  (overlap gate) already covers this class of failure in real sessions.
- **This round-trip methodology is NOT a valid proxy for "how accurate
  is ConvoBox's STT for a real user speaking"** -- speaker-through-air
  TTS at ~1-3m is a harder, quieter, more far-field-echo-prone signal
  than a person actually talking into a nearby mic. The genuinely open
  question -- real human speech, close to the mic, transcribed
  correctly -- is still untested on macOS and still needs a human at
  the machine. Don't reuse this script's numbers as an STT-quality
  headline without that caveat attached.
- **Ran the cleaner version too, same session: confirms it conclusively.**
  Fed the exact same 6 synthesized phrases directly into the transcriber
  (bypassing `MicrophoneStream`/`AudioPlayer` entirely -- no speaker, no
  mic, no room acoustics) for both engines: **100% word accuracy, both
  Piper and Kokoro, all 6 phrases, verbatim correct transcriptions with
  only expected capitalization/punctuation differences.** This isolates
  the variable completely -- faster-whisper's `base` model, this exact
  config, this exact machine, transcribes cleanly-delivered audio
  perfectly. The degraded round-trip scores above are entirely an
  artifact of the far-field speaker-through-air acoustic path (matching
  `[E6]`), not any defect in the model, the config, or this hardware's
  STT path in general.
