---
title: Safety-phrase reliability battery -- "halt halt halt" (a default hard-stop phrase) failed 4/5 round-trip tests, and the bare single-word resume word "Athena" failed 3/5 (vs. the codebase's own documented "5/5" claim, reproduced only when phrased WITH surrounding context); "stop stop stop" and "abort abort abort" are fully reliable; no false positives found from gibberish or foreign-language phrasing
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, stt.model=base, stt.device=cpu, faster-whisper (via ctranslate2), Piper en_US-lessac-medium voice, macOS Darwin 25.6.0
evidence:
  - A new scratch harness, _test_safety_phrase_battery.py (not committed) -- Piper TTS synthesizes each test case, faster-whisper transcribes it (no mic/speakers, in-memory WAV -> transcribe(), removing the audio-hardware confound entirely per this session's own harness-confound field note), the real SafewordDetector/PauseListeningDetector/ResumeWordDetector check the resulting transcript against a hand-labeled ground truth
  - Follow-up targeted batches (5 reps each) isolating "halt halt halt" vs "abort abort abort" vs "stop stop stop" vs "Athena" for a fair reliability comparison
  - src/convobox/safeword/detector.py, src/convobox/listening_pause/detector.py, src/convobox/resumeword/detector.py (read directly to confirm exact matching semantics -- plain normalized-substring, word-boundary padded, no fuzzy/ML matching anywhere)
  - src/convobox/config.py's own comment on why "abort"/"halt" were added 2026-08-09, and src/convobox/resumeword/detector.py's own comment claiming "5/5 correct transcriptions" for "Athena" when it was chosen 2026-07-13
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked whether hotwords/safewords are "relatively safe and reliable" or producing false positives/negatives, and for gibberish/other-language testing specifically)
    - Claude Code (Anthropic claude-sonnet-5) -- harness design/implementation, live testing, writing
  org: https://legionforge.org
  created: 2026-08-15T03:05:00-05:00
  revised: 2026-08-15T03:05:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Safety-phrase reliability battery: "halt" and bare "Athena" are unreliable; "stop"/"abort" are solid; no false positives found

**Context.** JP asked three related questions: are the safety phrases
(safewords, pause phrase, resume word) reliable or producing false
positives/negatives; does gibberish or foreign-language speech
accidentally trigger or block them; and should any of this inform
defaults or advanced-config gating. This note answers all three with
real round-trip (Piper TTS -> faster-whisper) data, the same
verification discipline `resumeword/detector.py`'s own comments
describe using when "Athena" was originally chosen -- applied here to
every safety phrase, including the two nobody re-verified since.

## Method

23 hand-labeled test cases across five categories (realistic human
phrasing, benign near-misses, gibberish, foreign-language phrasing, and
the opt-in kill-phrase), each synthesized via Piper and transcribed via
the real STT engine, checked against the real detector classes.
**Limitation, stated up front**: "foreign-language" cases were
synthesized through Piper's English voice model (the only one installed
on this machine), which renders non-English text using English
phonetics/pronunciation rules -- this is NOT authentic native-speaker
audio. It's a reasonable proxy for "how does an English-tuned STT
pipeline handle non-English or heavily-accented input," but a claim like
"Spanish speech won't trigger the English safeword" should be read as
"English-voice-mispronounced Spanish text didn't," not as verified
against real Spanish speech.

## Result 1: no false positives found anywhere in the battery

Every benign near-miss correctly did NOT trigger: a single "stop" (not
tripled), "stop" said twice but not three times in a row, "stopped" as a
substring of a real word, three "stop"s scattered across a sentence
instead of consecutive, pure gibberish syllables, and all three foreign-
language stop-word renderings that aren't also English safewords
(Spanish "alto", French "arrête", Japanese "tomare" -- none matched, all
correctly transcribed as generic English words like "auto"/"read"/"tomer"
that don't contain the trigger phrase). The kill-phrase's benign context
case ("we need to eject the old cartridge... eject the new one") also
correctly did NOT fire despite containing "eject" twice, since the two
instances aren't adjacent/tripled. **The word-boundary-aware, exact-
substring, no-fuzzy-matching design (explicitly documented in each
detector's own module docstring as a deliberate safety choice) is doing
its job** -- zero false positives across this entire battery.

One expected non-finding, not a bug: "Athena is a city in Greece" DID
trigger the resume-word check, exactly as the detector's own design
(context-blind substring match) predicts -- flagged in the test cases as
an intentional "documents existing behavior" case, not a surprise.

## Result 2: "stop stop stop" and "abort abort abort" are fully reliable

5/5 round-trip for both, across repeated independent synthesis runs.
These two of the three default hard-stop phrases can be trusted as
currently configured.

## Result 3: "halt halt halt" -- a real default safeword -- failed 4/5 times

```
run 1: said='Halt, halt, halt.'  heard='halt, halt, halt'     triggered=True
run 2: said='Halt, halt, halt.'  heard='HOT POT POT'          triggered=False
run 3: said='Halt, halt, halt.'  heard='Hold, hold, hold.'    triggered=False
run 4: said='halt halt halt'     heard='Hold, hold, hold.'    triggered=False
run 5: said='halt halt halt'     heard='Hold, hold, hold.'    triggered=False
```

This was originally discovered incidentally, testing German "Halt, halt,
halt" as a foreign-language false-positive case (German for "stop" is
also spelled "halt" and happens to already be one of ConvoBox's own
English defaults) -- the "false positive" framing was wrong; it's a
**false negative on a real, currently-shipped safeword**. `"Hold, hold,
hold"` is the dominant mis-hearing (3/5), phonetically close to "halt"
(/hoʊld/ vs /hɔːlt/) -- a plausible genuine acoustic confusion for this
STT model, not obviously a Piper-only artifact, though this note did not
test it against real human speech to rule that out. **This directly
matches the same failure class `resumeword/detector.py` already
documents for `ROUNDTRIP_REJECTED_RESUME_WORDS` ("ConvoBox" ->
"Control Box", etc.) -- but that verification discipline was never
applied to the safeword defaults themselves** when "halt"/"abort" were
added (`config.py`'s own comment on the 2026-08-09 addition names the
vocabulary-collision reasoning for choosing "abort"/"halt" over
"kill"/"freeze", but doesn't mention a round-trip transcription test).

## Result 4: the default resume word "Athena" is markedly LESS reliable bare than the codebase's own claim, and highly phrasing-dependent

```
Bare "Athena." x5 (isolated, no surrounding words):     2/5 triggered
Varied phrasing (matching the original methodology,
  "Athena"/"hey Athena"/"Athena, stop"/"okay Athena"/
  "Athena?") x1 each:                                   4/5 triggered
```

`resumeword/detector.py`'s own comment claims "5/5 correct
transcriptions across varied phrasings" as the justification for
choosing "Athena" over rejected alternatives. The varied-phrasing
re-test here (4/5) is close to that original claim -- some natural
run-to-run STT variance is expected and this isn't a contradiction. **The
bare single-word case is the real, new finding**: saying just "Athena."
alone, with no surrounding phrase, measurably underperforms (2/5) the
same word said with a few words of context around it. Failed
transcriptions of the bare word: "patina", "Adina", "Aficino" -- all
lose the word entirely, not near-misses. This is consistent with a
general STT phenomenon (isolated short words carry less acoustic/
language-model context for the decoder to disambiguate) rather than
something specific to "Athena" -- but it means the SIMPLEST, most
natural way a user would actually resume ("just say the resume word")
is the worst-tested condition, not the best one.

## What transfers

- **Apply the SAME round-trip verification discipline to every safety
  phrase, not just the resume word** -- this investigation exists
  because that discipline was documented and followed once (for the
  resume word, 2026-07-13) but not repeated when new safewords were
  added later (2026-08-09). A phrase choice needs live re-verification,
  not a one-time check that ages out of scope as new phrases are added.
  (validated-live)
- **"Reliable in a multi-word phrase" and "reliable said bare/alone" are
  different claims for short, common words** -- a resume/wake word
  chosen via a phrase-embedded test can still fail in its own most
  natural, minimal usage. Worth testing the bare-word case explicitly
  whenever choosing or validating a wake/resume word. (validated-live)
- **Foreign-language and gibberish input did not produce a single false
  positive in this battery** -- the deliberately simple, non-fuzzy
  matching design is earning its keep here. This is a genuine point of
  confidence for the current architecture, not just a list of problems.
  (validated-live, with the Piper-voice-not-native-speech caveat noted
  above)

## Recommendations (this session's synthesis, not yet reviewed/decided by JP)

1. **Re-evaluate "halt halt halt" as a default hard-stop phrase.** A
   4/5 failure rate on a phrase whose entire purpose is a safety-critical
   abort is a real gap, not a minor UX nit. Options: drop it from the
   default list (keeping "stop stop stop"/"abort abort abort", both
   verified reliable here); keep it but add a Settings-TUI-visible
   warning (the same shape `ROUNDTRIP_REJECTED_RESUME_WORDS` already
   uses for resume words); or test against real human speech before
   deciding, since this note's evidence is Piper-only.
2. **Document the bare-word resume-word reliability gap somewhere a user
   will see it** -- e.g. the same "say 'stop listening' to pause...say
   'Athena' to resume" startup log line, or onboarding copy, could
   suggest "say it with a word or two around it" rather than implying a
   bare single word is equally reliable.
3. **Extend the setup-wizard "test-transcribe a few times" UX** (already
   named as not-yet-built in `resumeword/detector.py`'s own comment) to
   cover hard-stop phrases too, not just the resume word -- this note's
   own methodology (synthesize + transcribe + check, no mic needed) is a
   ready-made template for that feature, and could run automatically at
   setup time rather than requiring a user to say the phrase live
   several times themselves.
4. **This is NOT evidence that safewords/hotwords broadly need gating
   behind an "advanced config" warning** -- the false-positive side of
   JP's question is fully answered clean (zero false positives across a
   real battery including gibberish and foreign-language input). The
   actual gap found is narrower and more specific: one particular
   default phrase and one particular usage pattern (bare wake word),
   not the safety-phrase architecture as a whole. A blanket "wall
   delicate items behind advanced config" response would be broader
   than what this data supports -- the fix belongs at the phrase-choice
   level (item 1/2 above), not at the config-exposure level.

## Not done here

- Testing "halt halt halt" or bare "Athena" against real human speech
  (not Piper) to separate "this is a genuine STT acoustic confusion
  anyone would hit" from "this is specific to Piper's particular
  rendering of these words."
- Testing other STT models/sizes (this session used `stt.model: base`
  throughout, matching the rest of tonight's investigation, not
  `small`/`medium`/`large`, which might show different reliability for
  either phrase).
- A full multilingual battery using an actual non-English TTS voice
  (none installed on this machine) -- the foreign-language cases here
  are English-voice approximations, explicitly flagged as a limitation
  above, not a substitute for real native-language audio testing.
- Testing hotwords (`stt.hotwords`) interaction directly -- this
  session's battery ran with no hotwords configured, matching the
  existing documented decision (2026-08-06 field note) to deliberately
  keep safewords out of the hotwords bias list.
