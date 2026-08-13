---
title: A safeword match in a transcript silently skips checking that same transcript for a pause phrase, even when both are present in the words
status: validated-live
date: 2026-08-12
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #271 branch (docs/vad-freeze-live-repro-2026-08-12) @ 9efaab3
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, JP speaking live into a real mic
  - convobox-tui.log timestamp quoted verbatim below
  - scripts/run_convobox.py:2507-2547 (the exact gating code)
  - src/convobox/listening_pause/detector.py (PauseListeningDetector, unaffected -- the gap is in the caller, not this class)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; caught the missed "stop listening" live while speaking a long rapid-fire safeword sequence, asked for it to be traced precisely)
    - Claude Code (Anthropic claude-sonnet-5) -- log tracing, code confirmation, writing
  org: https://legionforge.org
  created: 2026-08-12T22:02:00-05:00
  revised: 2026-08-12T22:02:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A safeword match skips checking the same transcript for a pause phrase

**Context for outsiders.** ConvoBox has two separate, deliberately
independent safety/control mechanisms that both act on the same
transcribed text: a safeword (immediate hard-stop) and a pause phrase
("stop listening", enters a resume-word-only state). This note documents
a real, code-confirmed gap in how they interact when both happen to
appear in the same single utterance.

## Problem

JP was speaking a long, rapid-fire sequence of safewords live. The
pipeline transcribed one continuous utterance (11.8s) containing many
repeated safewords AND the pause phrase, chained together:

```
21:57:46,815 INFO transcript='break break break cancel cancel cancel
break break break cancel cancel cancel abort abort abort abort
stop listening cancel cancel cancel abort abort abort cancel cancel
cancel cancel cancel' lang=en (0.97) dec=0.70 busy=False  [HARD STOP]
21:57:46,815 INFO hard stop matched safeword 'break break break'
```

The hard-stop fired correctly (on `'break break break'`, the first
safeword found). `'stop listening'` -- present verbatim in the same
transcript -- was never separately evaluated. The session never entered
the paused state from this utterance.

## Mechanism, confirmed directly in code

`scripts/run_convobox.py:2507-2547`:

```python
# Safeword is checked on the raw transcript BEFORE any quality
# gate or half-duplex drop: a hard stop must never be swallowed.
if not is_hard_stop:
    ...
    # Pause/resume gate runs before every other gate, same
    # reasoning as the safeword: while paused, NOTHING except
    # the resume word should reach the overlap/echo/confidence
    # gates or the backend
    gate_action = listening_gate.observe(text)
```

The entire pause/resume check -- along with the echo-tail guard and the
no-input error-ladder check -- lives inside `if not is_hard_stop:`. When
a safeword matches, `is_hard_stop` is `True` for that transcript, and
`listening_gate.observe(text)` is **never called at all** for it. This
is a real gap, not a bug in `PauseListeningDetector` itself (which would
correctly find `"stop listening"` in the string if it were ever asked).

## Why this is realistically triggered, not just a contrived edge case

This project already has a documented, real hallucination pattern
(`docs/field-notes/2026-08-06-resume-word-hallucination-and-runaway-
repetition.md`) where a single STT segment can span many seconds and
contain long runs of repeated/garbled phrases. A long utterance that
happens to contain both a safeword and a pause phrase is exactly the
shape that pattern produces -- this session hit it live, not via a
contrived test.

## What transfers

- **Two independent safety mechanisms sharing the same input (a
  transcript) can still have an ordering gap if one's check is
  conditionally skipped based on the other's outcome.** The hard-stop
  path here is correctly unconditional and safe on its own terms
  ("a hard stop must never be swallowed," per the code's own comment)
  -- the gap is that skipping the pause check *entirely* rather than
  *after* the hard-stop was a stronger exclusion than necessary.
  (validated-live)
- **A long/hallucinated transcript is a realistic vehicle for two
  different trigger phrases to co-occur in one utterance** -- worth
  checking for this shape whenever auditing phrase-matching gates in
  this codebase, not just single-phrase-per-utterance cases.
  (validated-live)

## Severity read, not a fix

Low-to-moderate: the safety-critical half (hard-stop) always fires
correctly regardless of this gap -- nothing unsafe is missed. What's
lost is specifically the *pause* intent when it's bundled into the same
utterance as a safeword, which the user can always just repeat
("stop listening") in a follow-up utterance. Not a fix proposed this
session -- worth deciding deliberately whether the pause check should
run unconditionally (checking the same transcript for both, independent
outcomes) or stay as designed (mutually exclusive, safeword wins) before
changing it.
