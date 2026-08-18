---
title: Bare "Athena" mis-transcribed by real human speech on Windows, extending the existing macOS/Piper-only finding
status: validated-live
date: 2026-08-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ 2dd83b3 (post-0.3.1-rc1); claude-code backend; faster-whisper 1.2.1, stt.device=cpu, stt.hotwords="stop brake eject mayday listening resume alpha bravo delta halt abort" (does NOT include "athena"), interaction.resume_word=resume listening (Athena not the active resume word this session); Windows 11 (helios), real human speech
evidence:
  - convobox-UAT/convobox-tui.log, 2026-08-18 12:27:18-12:27:34 (timestamps quoted verbatim below)
  - docs/field-notes/2026-08-15-safety-phrase-reliability-battery-halt-and-bare-athena-unreliable.md (the original finding this extends -- macOS, Piper-synthesized speech only, explicitly flagged as needing real-voice confirmation)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT session on helios, said "Athena" bare per the original finding's own open recommendation to test with real speech)
    - Claude Code (Anthropic claude-sonnet-5) -- log correlation, writing
  org: https://legionforge.org
  created: 2026-08-18T12:40:31-05:00
  revised: 2026-08-18T12:40:31-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Bare "Athena" mis-transcribed by real human speech on Windows

**Context for outsiders.** ConvoBox's default resume word is "Athena" --
a short, distinctive word chosen so it's unlikely to appear in ordinary
conversation. `docs/field-notes/2026-08-15-safety-phrase-reliability-
battery-halt-and-bare-athena-unreliable.md` found it misheard 3/5 times
on macOS using Piper-synthesized speech, and explicitly flagged that
result as needing confirmation against real human speech before treating
it as more than a synthesis artifact. This session provides that
confirmation, independently, on a different platform.

## Problem

JP said "Athena" bare (not as part of a longer phrase) as a deliberate
test, per the original finding's own recommendation. STT misheard it
twice in a row before he gave up and switched to describing the problem
out loud instead.

## Evidence

```
2026-08-18 12:27:18,228 INFO transcript='Latina' lang=en (0.80) dec=0.53 busy=False
2026-08-18 12:27:24,358 INFO transcript='patina' lang=en (0.73) dec=0.42 busy=False  [BARGE-IN]
2026-08-18 12:27:34,458 INFO transcript="I'm trying to say the wake word Athena and I don't know if it's actually getting transcribed correctly." lang=en (0.99) dec=0.71 busy=False  [BARGE-IN]
```

Two consecutive real attempts, two different wrong transcriptions
("Latina", "patina") -- both phonetically close to "Athena" but neither
correct. JP's own real-time description of what he was doing (captured
verbatim in the third line) removes any ambiguity about what he actually
said.

**Note on this session's config:** `interaction.resume_word` was set to
`"resume listening"`, not the default `"Athena"`, so this instance is not
a report of the resume-word *feature* failing to fire -- Athena wasn't
the active trigger phrase. It's a direct measurement of STT's raw
transcription accuracy on the word "Athena" itself, which is exactly what
the original 2026-08-15 finding also measured (a phrase-reliability
battery, not a live resume-flow test). Both mis-hears (`dec=0.53`,
`dec=0.42`) also decoded at meaningfully lower confidence than this same
session's own typical range (compare the brake-hallucination note's
`dec=0.82`, or this log's own later `dec=0.71`/`0.99` lines) -- low
confidence correlating with a real miss, not a high-confidence wrong
answer.

## Mechanism

Not independently re-diagnosed this session -- this is corroborating
evidence for the existing finding's own framing, not a new mechanism.
The original note's hypothesis stands: "Athena" is short and, spoken
bare/alone, apparently sits close enough to several more common words
("Latina", "patina", "Adina", "Aficino" -- the macOS/Piper note's own
mis-hears) that faster-whisper's language model prior pulls the decode
toward a common word over the correct uncommon one. `stt.hotwords` does
not include "athena" in this config, so no hotword bias was in play here
either way -- consistent with the original note's own hotwords config at
the time.

## What transfers

- **validated-live, now on a second platform with real (not synthesized)
  speech:** bare "Athena" is unreliable STT output, independent of
  platform (macOS Piper-synthesized -> Windows real human voice) and
  independent of whether it's the actively-configured resume word.
  This closes the original finding's own explicitly-stated gap ("this
  note's evidence is Piper-only").
  - **This is a limited sample (n=2, one operator, one session)** --
    treat as reinforcing the existing 3/5-macOS data point, not as an
    independent statistically meaningful measurement on its own.
- **Unchanged from the original finding, still not decided:** whether to
  drop "Athena" as the default resume word, add a Settings-TUI warning
  (matching `ROUNDTRIP_REJECTED_RESUME_WORDS`'s existing shape), or
  extend it to a required multi-word phrase. See the original note's
  own "Recommendations" section -- nothing here changes that list, this
  just adds real-voice weight to the case for acting on it.
