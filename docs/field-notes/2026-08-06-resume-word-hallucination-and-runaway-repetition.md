---
title: A biased hotword can trap the STT decoder into a runaway repetition loop on short audio, and it reliably falls through into a real safeword hard-stop
status: validated-live
date: 2026-08-06
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #210/#213/#215 (fix/web-transcript-forwarding-parity branch), faster-whisper 1.2.1, ctranslate2 4.8.1, stt.device=cuda, stt.temperature=0.0, stt.hotwords="stop brake eject mayday listening resume alpha bravo delta"
evidence:
  - convobox-tui.log, 2026-08-06 22:37:10-23:08:xx session (D:\LegionForge\convobox-UAT, live PR #213 UAT)
  - .aec-dumps/20260806-223710/mic-raw.wav (continuous raw mic capture, forensic cross-check)
  - src/convobox/stt/transcriber.py (transcribe() call site, confirms repetition_penalty/no_repeat_ngram_size are never passed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT session, triggered every incident below)
    - Claude Code (Anthropic claude-sonnet-5) -- live log investigation, forensic AEC-dump cross-check, root-cause analysis, writing
  org: https://legionforge.org
  created: 2026-08-06T23:09:55-05:00
  revised: 2026-08-06T23:09:55-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A biased hotword can trap the STT decoder into a runaway repetition loop, and it reliably fires the safeword

**Context for outsiders.** ConvoBox biases faster-whisper's decoder toward a
small vocabulary of safety-critical short phrases (`stt.hotwords`) so they
transcribe more reliably. This session found a real cost to that bias: on
short, low-signal audio, the decoder can get trapped repeating a biased
word tens of times instead of producing a normal (even if wrong) short
guess -- and because the safeword match is a substring check, a runaway
repeat of a hotword-listed safe phrase reliably fires a real hard-stop,
even when nothing dangerous was said. This note also resolves a separate,
recurring "is it frozen?" concern from the same session -- it wasn't, and
here's the forensic proof.

## Problem 1: runaway repetition hallucination on hotword vocabulary

Three times in one session, a very short utterance (0.9-1.6s of audio)
transcribed as the same hotword repeated 70+ times:

```
22:48:42,696 Processing audio with duration 00:00.928
22:48:43,247 transcript='stop brake brake brake ... [repeated ~70x] ... brake' lang=en (0.63) dec=0.84  [HARD STOP]
22:48:43,247 hard stop matched safeword 'brake brake brake'

22:52:08,536 Processing audio with duration 00:01.408
22:52:08,986 transcript='brake brake brake ... [repeated ~70x] ... brake' lang=en (0.69) dec=0.83  [HARD STOP]
22:52:08,986 hard stop matched safeword 'brake brake brake'

23:03:55,045 Processing audio with duration 00:01.472
23:03:55,615 transcript='brake brake brake ... [repeated ~70x] ... brake' lang=en (0.64) dec=0.87  [HARD STOP]
23:03:55,615 hard stop matched safeword 'brake brake brake'
```

**Proof this is a decoder pathology, not mis-heard real speech**: each
instance's captured audio is under 1.5 seconds. No human speaks a single
word ~70 times in that span (would require ~20ms per repetition, far
faster than natural speech). The audio duration and transcript length are
inconsistent by roughly two orders of magnitude.

**The hallucinated word is always a configured hotword.** `stt.hotwords`
for this session: `stop brake eject mayday listening resume alpha bravo
delta`. All three runaway instances repeat `brake`; two shorter,
plausibly-genuine repeats earlier in the session (`'brake brake brake
brake'` at 22:40:07, dec=0.75; `'stop stop stop stop'` at 22:40:13,
dec=0.65) are consistent with real repeated testing speech (normal
decode confidence, normal ~4s-repetition timing) and are NOT counted as
hallucinations here -- the distinguishing evidence is the audio-duration
mismatch, not the repeated word alone.

**Timing correlation with pause/hard-stop events**: two of the three
runaway instances fired within 4-5 seconds of a preceding hard-stop or
pause trigger (23:03:51 `'stop stop stop stop'` hard-stop -> 23:03:55
runaway `brake`; 22:52:03 `paused listening (matched 'stop listening')`
-> 22:52:08 runaway `brake`). Plausible but NOT confirmed as causal --
could also reflect that pause/resume cycling produces more short,
low-signal utterances in general (background noise, partial words,
mic-open artifacts), which is the known trigger class for this failure
mode regardless of the preceding event.

## Mechanism

Hotwords bias the decoder toward specific vocabulary
(`transcribe(hotwords=...)`). On short/ambiguous audio the decoder has
little real signal to anchor on; combined with `hotwords` heavily
weighting a small set of short words, the decoder can enter a repetition
loop reinforcing the same biased token instead of terminating normally.
This is a documented Whisper/faster-whisper failure class independent of
this project.

**Root gap, confirmed by reading the code**: faster-whisper's
`WhisperModel.transcribe()` exposes `repetition_penalty` (default `1.0`,
no penalty) and `no_repeat_ngram_size` (default `0`, disabled) --
parameters that exist specifically to prevent this failure mode.
`src/convobox/stt/transcriber.py`'s `transcribe()` call only passes
`hotwords` and `condition_on_previous_text`; neither
`repetition_penalty` nor `no_repeat_ngram_size` is set anywhere in this
codebase, so both stay at faster-whisper's permissive defaults.

**Consequence for safety UX**: because `ApprovalDetector`/safeword
matching is a normalized-substring check (by design, so real variation
in how an operator says a hard-stop phrase still matches), a runaway
repeat of any hotword-listed safeword phrase will *always* also satisfy
the substring match. This session's hard-stops at 22:48:43, 22:52:08,
and 23:03:55 were all real, correctly-triggered hard-stops from the
safeword detector's point of view -- the bug is upstream, in what the
STT handed it, not in the safeword logic itself.

## Problem 2: resume-word recognition is unreliable enough to need the web-UI fallback repeatedly

Configured `interaction.resume_word: "resume listening"`. At least three
separate pause->stuck->web-button-resume cycles in this session, each
with multiple genuine voice attempts that failed to match:

- 22:42:51-22:42:57: `'resume resume resume resume'`, `'and then listen'`
  -- neither contains the substring `"resume listening"`. Resumed via
  web UI at 22:43:55.
- 22:51:26-22:51:39: `'please turn this end'`, `'this is your last
  name.'`, then `'resume listening'` finally matched cleanly at
  22:51:39,467 (dec=0.53) -- voice recovery worked this time.
- 23:05:57-23:07:33: six consecutive failed attempts (`'\u0440\u0430\u0437\u044a\u0435\u043c
  \u043d\u0430 \u0441\u0442\u043e\u043b'` [Russian, low confidence 0.86 lang-id but
  garbled], `'was him listening'`, `'is your must'`, `"please don't miss
  that"`, `'and then listen'`, `"trying to stay the week where it's
  going to soon listen"`, and finally `'\u043c\u0430\u0441\u0442\u0435\u0440'` [Russian
  "master", lang-id probability only 0.33]) before falling back to the
  web UI resume button at 23:07:33.

The Cyrillic mis-transcriptions are the same short/low-signal hallucination
class already documented for this project's *original* resume word
("Athena" hallucinated as Cyrillic text and unrelated fluent sentences,
per commit `c7a84f3`'s own message) -- switching the resume word from
"Athena" to "resume listening" did not eliminate this failure class, only
changed its surface. The operator is now considering a further change
(candidate: "pineapple", already sanity-tested earlier the same day at
15:47:45-15:47:57 with clean high-confidence transcriptions each time).

## Problem 3: none of this was ever a frozen pipeline -- forensically confirmed

Every one of the above incidents *felt* like a hang to the operator in
the moment (multiple "still stuck" / "locked up" reports through the
session). None were. Two independent checks rule out an actual pipeline
freeze:

1. **Decode latency, every instance, zero exceptions**: every
   `Processing audio` -> `transcript`/`dropped` pair in the entire
   session completed in under one second, including the runaway
   repetition instances themselves (22:48:42,696 -> 22:48:43,247 =
   551ms for the ~70-word hallucinated transcript). The decoder was
   never slow; it was fast and wrong.
2. **Raw mic capture, forensic cross-check independent of the app's own
   logging**: `.aec-dumps/20260806-223710/mic-raw.wav` -- 21,985,280
   frames @ 16000Hz = 1374.1s of continuously captured audio, consistent
   with the session's real elapsed wall-clock length. Per-checkpoint
   arithmetic across the specific window that felt most "stuck"
   (22:53:02 -> 22:56:48, capture frames 95001 -> 117628): 226.3s of
   audio captured against 226s of real elapsed time. If mic capture had
   actually died mid-session, the file would show a permanent shortfall
   against real elapsed time from that point forward; it doesn't. Same
   diagnostic method as the project's own earlier VAD-lockup finding
   (`docs/field-notes/2026-08-05-vad-segmenter-silent-unbounded-lockup.md`).

**What actually produced the "stuck" perception**: paused state (from a
real or hallucinated hard-stop) + several failed voice resume attempts +
alarming leftover hallucinated text (the 70-word "brake" wall) still
visible on screen from a few minutes earlier, with nothing distinguishing
"quietly waiting for you to speak" from "processing" from "frozen" at a
glance. A genuine UX gap, not a technical one.

## What transfers

- **Hotword/prompt-bias parameters and repetition-guard parameters are a
  package deal for short-phrase-vocabulary use cases** -- biasing a
  decoder toward a small set of short words without also capping
  repetition is a real, live-reproduced failure mode, not a theoretical
  risk. (validated-live)
- **A safeword/hard-stop detector built on substring matching inherits
  every upstream STT hallucination that happens to contain the matched
  substring** -- this is a deliberate, correct tradeoff (real phrase
  variation must still match), but it means STT reliability work IS
  safety-relevant work for a voice-native safety mechanism, not a
  separate accuracy concern. (validated-live, this instance)
- **Changing a short problem phrase (resume word, safeword, hotword) to
  a different short phrase does not by itself close the short-phrase
  hallucination failure class** -- it just moves where it surfaces.
  (validated-live: "Athena" -> "resume listening", same failure class,
  different symptom)
- **"Feels frozen" and "is frozen" require different evidence** -- log
  silence during a real freeze and log silence during genuine quiet are
  indistinguishable after the fact from the log alone; an independent,
  continuously-written artifact (here, the AEC raw-capture dump) is what
  actually resolves the ambiguity. Worth keeping `--aec-dump` (or an
  equivalent always-on liveness artifact) enabled for exactly this kind
  of live-UAT triage, not just its originally-documented delay-tuning
  purpose. (validated-live)

## Fix, proposed (not yet implemented)

Add `stt.repetition_penalty: float | None` and `stt.no_repeat_ngram_size:
int | None` to `STTConfig`, same opt-in pattern as
`condition_on_previous_text`/`temperature` (None/unset = faster-whisper's
own default, zero behavior change until explicitly set), wired through
`transcriber.py`'s `transcribe()` call. Community-cited starting values
for this failure class: `repetition_penalty` around `1.1-1.3`,
`no_repeat_ngram_size` around `2-3` -- not yet validated against this
project's own real audio, worth live-testing once implemented, not
assumed correct from community numbers alone.
