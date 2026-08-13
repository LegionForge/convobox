---
title: A low-confidence STT hallucination bypassed the error-ladder's language-probability gate and landed two-of-three words into the configured approval phrase
status: validated-live
date: 2026-08-12
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #271 branch (docs/vad-freeze-live-repro-2026-08-12) @ fcf72a1; stt.device=cpu, stt.model=base; approval_phrase="alpha bravo delta"; resume_word="resume listening"
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, --tui --web -v, real codex backend, JP speaking live into a real mic
  - convobox-tui.log timestamps quoted verbatim below
  - docs/field-notes/2026-08-06-resume-word-hallucination-and-runaway-repetition.md (related, distinct prior hallucination finding)
  - docs/UAT-checklist.md [E6] (related, distinct far-field-echo hallucination-loop finding)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; caught the mismatch between what he actually said and what was transcribed, insisted on tracing it precisely rather than accepting the first, wrong explanation)
    - Claude Code (Anthropic claude-sonnet-5) -- log tracing, gate-logic analysis, writing
  org: https://legionforge.org
  created: 2026-08-12T21:55:00-05:00
  revised: 2026-08-12T21:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A low-confidence hallucination bypassed the language-probability gate and landed near the approval phrase

**Context for outsiders.** ConvoBox's STT pipeline rejects low-confidence
transcripts through an "error ladder" before they ever reach the
backend. This note documents a real, live-caught instance where a
transcript containing invented words -- not just garbled ones -- passed
that gate anyway, because the gate checks a different confidence signal
than the one that was actually low for this utterance.

## Problem

JP was speaking live, testing variations on "stop listening" / "resume
listening" ("I want you to resume listening", "please resume
listening", etc.) -- deliberately not saying "mayday" or "alpha bravo"
at any point. The pipeline transcribed:

```
21:47:20,695 DEBUG Processing segment at 00:00.000
21:47:21,281 DEBUG Log probability threshold is not met with temperature 0.0 (-1.187185 < -1.000000)
21:47:21,282 INFO transcript='mayday listening resume alpha bravo' lang=en (0.62) dec=0.31 busy=False
```

This was not rejected -- it went through to the backend as ordinary
conversation, and the assistant responded acknowledging "Mayday,
listening resume, Alpha Bravo" as if JP had said it.

**Two separate things are true about this transcript, confirmed by
direct comparison against JP's own real-time report of what he actually
said:** it isn't a garbled version of his real utterance (word-order
noise, partial capture) -- it contains words ("mayday", "alpha", "bravo")
that were never spoken at all. And its own decode confidence (`dec=0.31`)
is *lower* than two other transcripts from the same session that WERE
correctly rejected minutes earlier:

```
21:47:03,725 dropped low-confidence transcript='stop brake' lang=en (0.40 < 0.40) [ERROR-LADDER: tier 3]
21:46:59,038 dropped low-confidence transcript='stop please' lang=hi (0.37 < 0.40) [ERROR-LADDER: tier 3]
```

## Mechanism

The error ladder's low-confidence rejection compares **language-detection
probability** against a 0.40 threshold (`lang=... (X < 0.40)` in the log
lines above) -- not the separate **decode confidence** (`dec=...`) also
logged on every transcript. The hallucinated transcript's language
probability (0.62) was comfortably above that threshold even though its
decode confidence (0.31) was the lowest of the three examples here. A
transcript can therefore be highly likely to be *some* real English
sentence (high `lang`) while the decoder itself was much less sure of
the specific words (`low dec`) -- and only the first of those two
numbers currently gates rejection. This is a plausible, evidence-backed
mechanism for why a fabricated transcript slipped through where two
merely-garbled ones didn't; not yet confirmed as the general rule (needs
more than three data points), but directly consistent with all three
observed here.

## Why this matters beyond STT accuracy in general

The hallucinated content -- `"alpha bravo"` -- is two of the three words
in this session's actual configured `approval_phrase` (`"alpha bravo
delta"`). It fell one word short and nothing unsafe happened. But this
is a genuine near-miss on a security-relevant phrase, produced by
low-confidence hallucination rather than the user's real speech, on a
gate that measured the wrong confidence signal to catch it. Distinct
from the already-documented hallucination patterns (2026-08-06's
runaway-repetition loops; UAT-checklist [E6]'s far-field-echo loops) --
this one is a single clean utterance, not a loop, and specifically
exposes the language-probability-vs-decode-confidence gap.

## What transfers

- **A confidence gate is only as good as the specific signal it checks.**
  "The model is confident this is English" and "the model is confident
  these are the right words" are different questions; this pipeline
  currently only gates on the first for its low-confidence rejection
  path. (validated-live, single instance -- not yet confirmed as a
  systematic gap across more samples)
- **A hallucination landing near a security-relevant phrase is worth
  treating as a real safety-adjacent finding, not just an STT-quality
  curiosity**, even when it falls short of actually matching and
  triggering anything. (validated-live)

## Next steps, not done this session

1. Check whether adding `dec` (decode confidence) as a second condition
   in the error-ladder's low-confidence check would have caught this
   specific case without materially raising the false-rejection rate on
   good transcripts -- needs real data on `dec`'s distribution across
   both accepted and rejected transcripts, not just this one sample.
2. Worth a deliberate (not accidental) collection of more hallucination
   samples with both `lang` and `dec` recorded, to see whether `dec` is
   reliably the more discriminating signal or whether this was a
   coincidence of one sample.
