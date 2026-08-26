---
title: Safety-phrase reliability battery is now a committed, reusable script -- 90/90 real TTS->STT round trips across every configured safeword/kill_phrase/pause-phrase/resume-word, plus a "mayday" candidate, all fully reliable on Kokoro (contrast with the 2026-08-15 Piper battery's "halt"/"Athena" failures)
status: validated-live
date: 2026-08-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0) + this session's uncommitted convobox.yaml (safeword.kill_phrase: "eject eject eject"); tts.engine kokoro, voice af_sarah; stt.model base, device auto (resolved cpu), language auto; openSUSE Tumbleweed 20260822 (Sager P17SM-A laptop, i7-4810MQ)
evidence:
  - New committed script, scripts/safety_phrase_battery.py -- 90 real TTS->STT round trips (9 cases x N=5, then again x N=10), each checked against the REAL SafewordDetector/PauseListeningDetector/ResumeWordDetector classes, not a hand-labeled transcript comparison
  - docs/field-notes/2026-08-15-safety-phrase-reliability-battery-halt-and-bare-athena-unreliable.md -- the prior session that established this exact methodology, using an explicitly uncommitted scratch harness (`_test_safety_phrase_battery.py`) and Piper TTS instead of Kokoro
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for automated testing of kill/quit phrasing, interrupt words including a "mayday" candidate, and pause/resume keywords, specifically to see if transcription reliability warrants changing the hotword logic)
    - Claude Code (Anthropic claude-sonnet-5) -- built the committed harness, ran it live, wrote this note
  org: https://legionforge.org
  created: 2026-08-25T20:58:00-05:00
  revised: 2026-08-25T20:58:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Safety-phrase battery is now a committed script, not a one-off scratch harness

**Context for outsiders.** ConvoBox listens for several specific spoken
phrases that must transcribe reliably to work at all: safewords
("stop stop stop", "abort abort abort", an optional `kill_phrase`), a
pause phrase, and a resume word. `docs/field-notes/2026-08-15-safety-
phrase-reliability-battery-halt-and-bare-athena-unreliable.md` already
established a real methodology for testing this -- synthesize each
phrase via TTS, transcribe via the real STT engine, check the transcript
against the real detector classes, no mic/speakers needed -- and found
two real, shipped defaults were unreliable ("halt halt halt" 4/5,
bare "Athena" 3/5). That harness was explicitly never committed. This
session's operator asked for exactly this kind of testing again, this
time specifically for the newly-configured `kill_phrase`
("eject eject eject"), a candidate interrupt word ("mayday", tried
live/unscripted earlier the same day), and the pause/resume phrases --
and to make it reusable this time.

## Problem

Does every currently-configured safety phrase, plus one candidate the
operator is curious about, actually transcribe reliably enough to
trigger its intended detector -- and is the existing "battery"
methodology durable, or does it need rebuilding from scratch every time
someone asks this question (as it did on 2026-08-15)?

## Method

New `scripts/safety_phrase_battery.py`, following `scripts/
roundtrip_smoketest.py`'s own "remove the audio-hardware confound
entirely" discipline (real TTS synthesis -> real STT transcription,
in-memory, no mic/speakers) but built specifically around the three real
safety-phrase detector classes (`SafewordDetector`, `PauseListeningDetector`,
`ResumeWordDetector`) rather than a hand-labeled ground truth. Nine
cases, each run N=5 then re-run N=10 for stronger confidence given the
safety stakes and this project's own prior lesson (a previous "5/5"
resume-word claim was later found to only be 3/5 under more rigorous
testing):

1. Configured safewords: `"stop stop stop"`, `"abort abort abort"`,
   `"eject eject eject"` (this session's `kill_phrase`).
2. Candidate, not configured: `"mayday mayday mayday"` -- the operator's
   own ad-hoc phrase from earlier the same day's live human-voice session.
3. Both default pause phrases: `"stop listening"`, `"pause listening"`.
4. The default resume word: `"resume listening"`.
5. Two benign near-misses (false-positive check, same discipline as the
   2026-08-15 battery): a bare, non-tripled `"stop"` and a bare, non-tripled
   `"mayday"` -- neither should ever fire the safeword detector.

## Evidence

**90/90 across two full runs (45 real trials at N=5, then 45 more at
N=10) -- every case fully reliable, zero exceptions:**

```
✅ safeword (configured): 'stop stop stop': 10/10
✅ safeword (configured): 'abort abort abort': 10/10
✅ safeword (configured): 'eject eject eject': 10/10
✅ safeword (CANDIDATE, not configured): 'mayday mayday mayday': 10/10
✅ pause phrase: 'stop listening': 10/10
✅ pause phrase: 'pause listening': 10/10
✅ resume word: 'resume listening': 10/10
✅ benign near-miss: bare 'stop' (not tripled): 10/10
✅ benign near-miss: bare 'mayday' (not tripled): 10/10
```

No unreliable case, no false positive, at either N.

## Mechanism

No mechanism to diagnose here -- unlike the 2026-08-15 battery, nothing
failed. Worth naming explicitly why this result differs from that
session's real failures: **this is a different TTS engine (Kokoro, this
project's current default) against the same STT engine family
(faster-whisper, `base` model, auto language)**, not a re-run of the
identical prior conditions. "halt halt halt" and bare "Athena" were
found unreliable on **Piper** (`en_US-lessac-medium`). This result does
not contradict that finding -- it's a real, useful, separate data point
that Kokoro's synthesis of these specific phrases round-trips cleanly
through the same STT stack, not evidence that Piper's own failure mode
was fixed or was never real.

## What transfers

- **Every currently-configured safety phrase, including this session's
  new `kill_phrase`, is confirmed highly reliable on the Kokoro+
  faster-whisper stack** -- real, live, repeated evidence, not an
  assumption carried over from when "stop"/"abort" were last checked
  (2026-08-15, on a different TTS engine).
- **"mayday mayday mayday" transcribes just as reliably as the shipped
  safewords** (10/10, same as "stop"/"abort"/"eject") -- a real,
  positive data point if this project ever wants to consider it as an
  additional configured phrase. This note does not recommend adding it
  (that's a product decision, not a data one) -- it only answers the
  reliability question the operator actually asked.
- **The safety-phrase battery methodology is now a committed, reusable
  script** (`scripts/safety_phrase_battery.py`), closing the gap the
  2026-08-15 note's own harness left ("not committed" -- can't be
  re-run without rebuilding it). Future changes to any safety phrase,
  or a switch to a different TTS/STT engine/model, can be re-verified
  with one command instead of rebuilding a scratch harness again.

## Not done here

- Did not re-test "halt halt halt" or bare "Athena" against Kokoro to
  see whether Kokoro's synthesis specifically resolves what was
  unreliable on Piper, or whether the STT side was the actual problem
  either time -- a real, answerable follow-up this note doesn't close.
- Did not test any phrase in a noisy/real-room acoustic environment --
  this is a clean, no-hardware TTS->STT round trip, same limitation the
  2026-08-15 battery already disclosed for its own harness.
- Did not test non-English renderings of any phrase this time (the
  2026-08-15 battery's own "foreign-language phrasing through an
  English voice" category was not repeated here).
- Whether "mayday mayday mayday" is worth actually adding as a
  configured phrase is an open product question, not answered by this
  note's reliability data alone.
