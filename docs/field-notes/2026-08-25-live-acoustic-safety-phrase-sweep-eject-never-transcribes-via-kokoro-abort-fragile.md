---
title: Real speaker-to-mic safety-phrase sweep (50%-20% volume, N=10) finds "eject eject eject" (the configured kill_phrase) NEVER transcribes correctly via Kokoro TTS at ANY volume (0/70) despite working with a real human voice hours earlier the same day; "abort abort abort" (a shipped default) is also far less reliable than either digital round-trip test suggested; "stop stop stop" remains solid through 35%
status: validated-live (with an explicit, load-bearing caveat about what a TTS voice can and cannot stand in for -- see Mechanism)
date: 2026-08-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + this session's uncommitted convobox.yaml (safeword.kill_phrase: "eject eject eject"); tts.engine kokoro, voice af_sarah; stt.model base, device auto (cpu), language auto; openSUSE Tumbleweed 20260822 (Sager P17SM-A laptop, i7-4810MQ); no AEC in this script (see Mechanism for why)
hardware: same rig as the same-day AEC volume-sweep field notes -- Clevo P17SM-A (Sager), onboard HDA Intel PCH (Realtek ALC892), PipeWire. Operator present at the machine for this run (unlike the two AEC sweeps).
evidence:
  - New scripts/safety_phrase_battery_live.py -- 490 real trials (7 phrases x 7 volumes x N=10), each a real TTS phrase played through real speakers, captured via the real mic, transcribed via the real STT engine, checked against the real SafewordDetector/PauseListeningDetector/ResumeWordDetector classes
  - uat log at (session-local) /tmp/safety-phrase-live-sweep.log -- full per-trial transcripts for every miss, not just pass/fail counts
  - Directly contrasted against the SAME day's earlier docs/field-notes/2026-08-25-linux-first-real-human-speech-demo-safeword-and-self-barge-in-confirmed.md's follow-up, where a real human voice saying "eject, eject, eject." was correctly transcribed and force-killed the session -- and against scripts/safety_phrase_battery.py's in-memory (no hardware) run, which found 10/10 for every phrase including "eject eject eject" and "mayday mayday mayday"
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for an automated N=10 live-mic sweep from 50% down by 5% specifically to check hotword transcription reliability, present at the machine for this run)
    - Claude Code (Anthropic claude-sonnet-5) -- built the harness, found and fixed a real design flaw in it before trusting its output, ran the real sweep, wrote this note
  org: https://legionforge.org
  created: 2026-08-26T09:42:00-05:00
  revised: 2026-08-26T09:42:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Live acoustic safety-phrase sweep: a real, safety-relevant surprise, with a caveat that matters as much as the finding

**Context for outsiders.** Two other tests already existed for "does this
safety phrase actually transcribe": `scripts/safety_phrase_battery.py`
(committed the same day, in-memory TTS->STT, no hardware -- found 90/90,
every phrase perfectly reliable) and a real human saying "eject, eject,
eject." live earlier the same session (also successful, force-killed the
session correctly). This note is a third, different test: the SAME
phrases, synthesized by the SAME TTS engine, but played through REAL
speakers into a REAL room and picked up by the REAL mic -- the one
acoustic step neither of the other two tests included. It found something
neither of them could have: a phrase that's fine in clean digital audio
and fine when a real human says it can still be effectively unrecognizable
when a TTS engine's own rendering of it travels through a real room.

## Problem

Do the configured safewords, the new `kill_phrase`, the pause phrases, and
the resume word actually survive a real speaker-to-mic acoustic path
across a realistic system-volume range -- not just a clean digital
round-trip?

## Method (and a real bug found and fixed before trusting any result)

New `scripts/safety_phrase_battery_live.py`. First version fed the
played phrase back into this project's real `EchoCanceller` as the AEC
reference (mirroring how ConvoBox's own barge-in path works). This was
wrong for what this script tests, and caught before trusting the
results: feeding a canceller the EXACT signal it's supposed to detect as
"echo to remove" makes it do precisely that -- confirmed live, real
captured RMS dropped ~10-20x post-AEC on every single case (0.10-0.14 ->
0.003-0.017), correctly and uselessly cancelling the very phrase under
test. Real AEC (does it correctly leave an independent human utterance
alone while cancelling the assistant's own concurrent playback) is a
different question `scripts/acoustic_calibration.py` already answers.
Fixed by dropping AEC entirely -- this script now tests only "does the
phrase, played through the real speaker/room/mic chain at a given system
volume, transcribe reliably at all."

7 phrases (the 3 configured safewords, the `kill_phrase`, both pause
phrases, the resume word, plus the "mayday mayday mayday" candidate) x 7
volumes (50% down to 20% by 5%, wpctl/PipeWire) x N=10 = **490 real
trials**, one continuous run, `systemd-inhibit`-wrapped.

## Evidence

Full summary table (pass count / 10 repeats):

| Case | 50% | 45% | 40% | 35% | 30% | 25% | 20% |
|---|---|---|---|---|---|---|---|
| `stop stop stop` (safeword) | **10** | **10** | **10** | **10** | 4 | 0 | 0 |
| `abort abort abort` (safeword) | 2 | 3 | 6 | 6 | 2 | 0 | 0 |
| `eject eject eject` (kill_phrase) | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| `mayday mayday mayday` (candidate) | 7 | 2 | 1 | 0 | 0 | 0 | 0 |
| `stop listening` (pause) | 10 | 10 | 10 | 8 | 7 | 0 | 0 |
| `pause listening` (pause) | 10 | 10 | 8 | 3 | 0 | 0 | 0 |
| `resume listening` (resume) | 10 | 9 | 10 | 1 | 0 | 0 | 0 |

**"eject eject eject" -- zero successes across all 70 real trials,
every volume tested.** The real mistranscriptions are the interesting
part, not just the zero: `'Ejak Ejak Ejak'`, `'Egek Egek Egek'`,
`'Ecek, ecek, ecek.'`, `'İçer, İçer, İçer.'`, even `'إجاك إجاك إجاك'`
(Arabic script) and `'Bırak Bırak Bırak'`/`'Hicap Hicap Hicap'`
(Turkish-looking) -- Whisper never once resolves Kokoro's rendering of
"eject" to anything containing the English word, at ANY volume,
including 50% where five of the other six phrases hit 10/10. This is
not a volume/loudness effect (the failure is total and volume-
independent down to 35%, where everything else is still working) --
it's a specific mismatch between how Kokoro synthesizes this word and
what Whisper's English model expects to hear.

**"abort abort abort" -- a shipped default safeword, far less reliable
than either digital test suggested.** Best case 6/10 (35-40%), never
better; 2/10 at the loudest (50%) and quietest-still-working (30%)
volumes tested; 0/10 at 25% and 20%. The mistranscriptions are
consistent and specific, not random: `'A board, a board, a board.'` /
`'a board, a board, a board.'` / `'aboard, aboard, aboard.'` -- Whisper
systematically hears "abort" as "aboard," a real, repeatable phonetic
confusion.

**"stop stop stop" is the clear standout -- perfect 10/10 through
35%**, only degrading at 30% (4/10) and failing at 25%/20% (0/10, mostly
genuinely empty transcripts -- too quiet to register at all, not a
mishearing).

**Pause/resume phrases degrade on staggered, individually distinct
curves**: `stop listening` stays strong through 30% (7/10); `pause
listening` falls off faster (0/10 already at 30%); `resume listening`
has a sharp, specific cliff at 35% (1/10, almost uniformly misheard as
`"We're doing listening."` / `"We're still listening."`) despite being
9-10/10 at 40% and 50% on both sides of it -- a real, repeatable
near-homophone confusion, not noise.

**Below ~25%, nearly everything fails, mostly as empty transcripts** (a
few `'. . . .'` artifacts at 20%, suggesting Whisper detecting something
faintly audio-shaped but nothing coherent) -- consistent with this same
rig's own previously-measured "hard floor" around 20-30% from the
same-week AEC volume-sweep field notes, now confirmed as a floor for
basic speech intelligibility too, not just self-echo/barge-in behavior.

## Mechanism -- the caveat that matters as much as the headline finding

**This result does NOT mean "eject eject eject" is an unreliable
kill_phrase for a real user.** The SAME session, hours earlier, has a
real, live, human-voice counter-example: JP said "eject, eject, eject."
out loud, it transcribed correctly (`0.83` confidence, plain English),
matched the safeword, and force-killed the session cleanly (see
`2026-08-25-linux-first-real-human-speech-demo-safeword-and-self-barge-
in-confirmed.md`'s own follow-up). That's a real human voice succeeding
at the exact thing this script's synthetic voice fails at 100% of the
time. The honest reading: **Kokoro's own text-to-speech rendering of
"eject eject eject" is a bad acoustic proxy for a human saying it** --
whatever Kokoro's phonemizer/prosody does with this specific word
produces something Whisper doesn't recognize as English, in a way a
real human's natural pronunciation apparently doesn't share. This is a
genuine, useful finding about **the test methodology's own limits** as
much as about the phrase: a TTS-voice-through-real-speakers test is a
good proxy for volume/room/mic effects (confirmed by "stop stop stop"
and "abort abort abort" both showing real, volume-dependent, physically
plausible degradation), but it can silently fail to be a good proxy for
a SPECIFIC WORD's pronunciation, and this note found a case where that
gap is total, not partial.

"abort abort abort" is a murkier case: `"abort"` -> `"aboard"` is a
generic, plausible English phonetic confusion that could very well
happen with a real human voice too (unlike "eject"'s bizarre non-English
mistranscriptions, which look more Kokoro-specific) -- this note does
NOT have a real-human-voice data point for "abort" the way it does for
"eject" to settle which explanation is right. Flagged as a real open
question, not resolved here.

## What transfers

- **The kill_phrase's real-world reliability depends heavily on WHO/WHAT
  is speaking it, not just the phrase text itself** -- a genuinely
  important, generalizable lesson for anyone using synthetic voice
  testing as a stand-in for human speech in this kind of safety-phrase
  validation. A phrase passing 10/10 in a TTS-based test (this session's
  own in-memory battery, or this live one for "stop stop stop") is
  meaningful; a phrase FAILING in a TTS-based test needs a real human
  voice check before being treated as evidence the phrase itself is bad
  -- exactly the gap this note found for "eject."
- **"abort abort abort" needs a real human-voice reliability check** --
  this note's data alone is not enough to conclude it's a bad default,
  but it's real enough evidence (a systematic, repeated
  abort-vs-aboard confusion, unlike anything the 2026-08-15 or same-day
  in-memory batteries found) to treat the earlier "fully reliable" verdict
  on it as no longer settled.
- **"stop stop stop" remains the most trustworthy configured safeword**
  across every test this project has now run on it -- digital round-trip,
  live acoustic sweep, and real human voice all agree.
- **~25-30% system volume is a real, repeatable floor for basic speech
  intelligibility on this rig**, not just for self-echo/barge-in
  behavior -- below it, nearly every phrase fails regardless of which
  one it is.
- **A TTS-voice-through-real-speakers test is a genuinely different, and
  sometimes contradictory, instrument than either an in-memory TTS->STT
  round trip or a real human voice** -- this session now has all three
  for at least one phrase ("eject") with three different outcomes (human:
  works; in-memory TTS: works; live-acoustic TTS: never works), which is
  itself the most reusable finding here.

## Not done here

- No real human-voice equivalent of this exact volume sweep -- the one
  human-voice data point that exists (eject, at whatever volume the
  operator was speaking at) isn't controlled the way this synthetic sweep
  is, so it can't be directly compared point-for-point.
- Did not test whether a DIFFERENT TTS voice/engine (Piper, or a
  different Kokoro voice) renders "eject" more intelligibly -- the
  in-memory battery only ever used `af_sarah`.
- Did not investigate WHY Kokoro's rendering of "eject" specifically
  confuses Whisper (phonemizer output, prosody, stress pattern) -- only
  that it does, consistently, at every volume.
- Did not re-test "abort abort abort" with a real human voice to settle
  whether its real-world reliability matches this session's TTS-driven
  finding or the more optimistic 2026-08-15/in-memory results.
- No AEC in this script at all (see Method) -- this is a real gap versus
  actual ConvoBox usage, where AEC is normally active; a phrase's
  reliability WITH AEC correctly engaged (cancelling genuine assistant
  playback, not the phrase itself) while a human speaks it is not
  measured here.
