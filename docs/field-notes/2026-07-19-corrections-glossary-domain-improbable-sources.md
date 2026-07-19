---
title: A speech-correction glossary must map FROM tokens you would never say — not from real words
status: validated-live
date: 2026-07-19
project: ConvoBox (github.com/LegionForge/convobox)
versions: faster-whisper base; ConvoBox stt.corrections (TranscriptCorrector)
evidence:
  - src/convobox/stt/corrections.py (deterministic word-boundary replacement)
  - PR #96 (glossary feature), PR #100 (finding surfaced during pick-a-phrase review)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the glossary review)
    - Claude Code (Anthropic claude-fable-5) — glossary probing, analysis, writing
  org: https://legionforge.org
  created: 2026-07-19T14:51:00-05:00
  revised: 2026-07-19T14:51:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A speech-correction glossary must map FROM tokens you would never say

**Context for outsiders.** Speech-to-text (Whisper here) makes recurring,
predictable mistakes — it hears "barge-in" as "bargain". A common fix is an
operator-maintained corrections glossary: a map of wrong→right applied to the
transcript before it is acted on. This note is about a failure mode that
makes such a glossary quietly *worse* than none.

## Problem

Three example corrections were seeded from real mishears:
`the green → I agree`, `AAC → AEC`, `bargain → barge-in`. Each fixes a
genuine STT error. Each also silently corrupts legitimate speech.

## Evidence

Run against the real `TranscriptCorrector` (deterministic, word-boundary,
case-insensitive replacement):

```
"lets talk about the green button"  -> "lets talk about I agree button"
"the AAC codec vs opus"             -> "the AEC codec vs opus"
"we got a bargain on that"          -> "we got a barge-in on that"
```

Every one of the three sources is *also a thing a user legitimately says*:
"the green" (a color), "AAC" (a real audio codec — very likely to come up in
an audio project), "bargain" (an everyday word). The correction cannot tell
"the mistake I want fixed" from "the user actually meaning this word",
because at the transcript level they are byte-identical.

## Mechanism

A corrections glossary is a context-free rewrite. It has no way to know
whether "bargain" in this transcript is a mis-heard "barge-in" or a real
"bargain" — the disambiguating information (what the user actually said
acoustically) was already thrown away by STT. So any correction whose
*source* is a valid, in-domain utterance trades one error for another, and
the new error is worse: it is silent and it corrupts correct input.

The safe corrections are the ones where the source token is **improbable in
your domain** — something STT produces that a user would essentially never
mean literally. If Whisper renders "barge-in" as "Barack in" or "large in",
those sources are safe: nobody testing a voice barge-in feature says "Barack
in". The fix is not "don't use a glossary" — it is "choose sources no user
would ever actually say."

Ruled out: making the matcher smarter (fuzzy, longest-match, etc.) does not
help — the ambiguity is semantic, not lexical. Only source selection fixes
it.

## What transfers

- **A correction is only safe if its source token is one the user would
  never legitimately utter in this domain.** Mapping from a real word/phrase
  guarantees false positives the moment the user means it. (validated-live)
- **Prefer the improbable rendering over the plausible one.** For a recurring
  mishear, seed the glossary from STT's *weird* outputs ("Barack in"), not
  from the plausible-but-wrong word ("bargain") — even though the plausible
  one feels like the "obvious" entry. (diagnosed)
- **This same principle governs wake words and pause phrases**, which are
  matched the same context-free way: pick something distinct and unlikely in
  normal conversation, so it neither misfires on ordinary speech nor needs a
  glossary exception to be recognized. (validated-live; see the safeword/VAD
  note and the pick-a-phrase help text)
- **Audit every glossary entry against "would the user ever mean this
  literally?"** before shipping it. An entry that fails that test is a latent
  silent-corruption bug. (validated-live)
