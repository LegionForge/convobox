# Troubleshooting: pause phrase / resume word / safeword recognition

Your pause phrase (`interaction.pause_phrases`, default `"stop listening"`/
`"pause listening"`), resume word (`interaction.resume_word`, default
`"Athena"`), and safeword (`safeword.hard_stop_phrases`, default
`"stop stop stop"`) all work the same way under the hood: a **deterministic,
normalized-substring match against the raw transcript** — no fuzzy matching,
no ML, no LLM (`SafewordDetector`/`PauseListeningDetector`/
`ResumeWordDetector`, see `docs/DESIGN-barge-in.md`). That's deliberate: a
human reading the config can predict exactly when each one fires, which
matters for a hard-stop-class control. The tradeoff is that it only matches
what faster-whisper actually transcribed — if it mis-hears the word, the
match silently fails, and unlike everything else you say to ConvoBox, this
class of phrase is checked **before** the `stt.corrections` glossary runs (on
purpose — a safety-critical check must never depend on a rewritable
dictionary). If your pause/resume/safeword phrase isn't matching reliably,
the *only* real fix is choosing a phrase faster-whisper transcribes well for
your own voice — a corrections-glossary entry can't help here, even though it
can for ordinary conversation.

## Why "Athena" and not something else

The shipped default resume word used to be "ConvoBox" itself (the smart-
speaker convention — Alexa, Siri, Cortana). It shipped for two PRs without
ever being tested against real speech-to-text, and turned out to be broken:
faster-whisper confidently (0.93 probability) mis-transcribes it as "Control
Box" every time — a compound word splitting into two more common real words,
which the language model prefers. The resume word silently never matched,
and there was no way to resume a paused session by voice at all.

"Athena" was chosen by actually testing candidates through the real
Piper → faster-whisper round-trip pipeline, not guessing. Words that failed
that same test (`ResumeWordDetector.ROUNDTRIP_REJECTED_RESUME_WORDS`):
`"ConvoBox"` (→ "Control Box"), `"Copilot"`/`"co-pilot"` (→ "co-pilot" split
oddly), `"Voicebox"` (→ "Boyspicks"). All are compound/portmanteau words —
that's the pattern to avoid.

## Picking a word/phrase that transcribes well

A few properties correlate with reliable transcription, in rough order of
importance:

1. **A single, ordinary dictionary word**, not a compound, portmanteau, or
   brand name. Whisper's language model has strong priors toward common word
   *pairs* — a compound word competes against being split into two more
   likely real words (see "Athena" above).
2. **Multisyllabic**, not a single short syllable. More phonetic content
   gives the model more to work with; a one-syllable word is easy to clip,
   swallow, or confuse with background noise.
3. **Distinctive — not something you'd say in ordinary conversation while
   coding.** Avoids two different failure modes at once: accidentally
   triggering the resume/pause/safeword mid-sentence, *and* the word being
   common enough that its pronunciation varies a lot depending on context
   (a rarely-said word tends to get said more deliberately/clearly).
4. **Not something acoustically close to a command you actually use often**
   (a real false-trigger risk, distinct from #3 — e.g. don't pick a resume
   word that rhymes with a coding term you say constantly).

**Good candidate shapes**, per the above (not exhaustive — the actual test in
the next section is what matters, not this list): distinctive multisyllabic
nouns you wouldn't otherwise say while coding — `"cucumber"`, `"platypus"`,
`"pineapple"`, or the shipped default, `"Athena"`. Proper nouns and uncommon
animals/foods tend to work well for the same reason `"Athena"` did: ordinary
dictionary words, clearly multisyllabic, not something the language model
wants to split into a more common pair.

## Verifying a candidate against *your own* voice before committing

The round-trip test that picked "Athena" used synthesized (Piper) speech —
useful for ruling out words that are broken for everyone, but it can't tell
you how *your* specific voice, accent, microphone, and room transcribe a
word. A candidate that tests fine in the abstract can still be unreliable
for you specifically, and vice versa. Right now this is a manual process (a
guided in-TUI version is on the roadmap — see `docs/DESIGN-barge-in.md`'s
"setup-wizard test-transcribe" note — not built yet):

1. Run with `-v`/`--verbose`: `python scripts/run_convobox.py --tui -v`
   (or add `-v` to whatever launch command you normally use). This is the
   only way to see individual STT attempts — the default log level doesn't
   record a transcript for an utterance that gets silently dropped by the
   pause/resume gate.
2. Say your candidate word 5-10 times, in a few different framings (bare
   word, "hey `<word>`", mid-sentence, your normal speaking pace/volume) —
   matching the kind of variation the original "Athena" test used.
3. Check the log (`convobox-tui.log` in `--tui` mode, or the console
   otherwise) for `transcript=` lines. Confirm the word actually appears,
   verbatim, in what was transcribed — not just that *something* was heard.
4. If it's consistently right, you're done. If it's consistently *wrong* in
   the same way (e.g. always transcribed as some other specific word), you
   have your answer: that word doesn't work for your voice, pick another —
   remember, `stt.corrections` can't rescue this one, unlike ordinary
   conversation.

## Diagnosing an already-chosen phrase that's "sometimes" unreliable

If a phrase is working most of the time but missing occasionally, the
signal to look for is the **STT confidence**, not just whether the text
matched:

1. Re-run with `-v` if you weren't already — this is required to see the
   raw text on a miss at all (the pause/resume gate's own drop-and-continue
   path logs at DEBUG, invisible at the default INFO level).
2. For each attempt, note the `Detected language 'en' with probability
   X.XX` line right before it. A hit usually shows something like `0.90+`;
   a miss with a *low* number (`<0.5` or so) points to an audio-capture
   problem — spoken too quietly, too fast, clipped by VAD timing, mic too
   far away, background noise — not a wording problem. A miss with a
   *high* confidence number but genuinely wrong text is the more
   interesting case: that's a real, consistent mis-transcription, and the
   fix is the same as above — pick a different word, since corrections
   can't apply.
3. If most misses are low-confidence, the word choice probably isn't the
   bottleneck — mic setup/positioning and background noise are worth
   checking first (see the headphones note in `README.md` and
   `docs/DESIGN-echo-and-barge-in.md` for the acoustic side of things).

## Same guidance applies to the safeword

`safeword.hard_stop_phrases` (default `"stop stop stop"`) is checked with
the exact same deterministic mechanism, before corrections, for the same
safety reason (a hard stop must never depend on a rewritable glossary). If
your safeword isn't reliably recognized, the same verification process and
word-choice guidance above applies — and given what a hard stop is *for*,
it's worth verifying this one especially carefully.
