---
title: PR #276's fix live-verified -- hard stop and pause both register from one chained safeword-plus-"stop listening" utterance, twice in a row
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: fix/pause-phrase-registers-even-with-safeword-in-same-utterance @ 551a678 (PR #276, unmerged); backend=codex, model=gpt-5.6-terra; interaction.resume_word="resume listening" (operator override of default "Athena"); working_dir D:/LegionForge/convobox-UAT (Windows/helios)
evidence:
  - convobox-tui.log, D:/LegionForge/convobox-UAT, 2026-08-15 20:57:31-20:57:50 (timestamps quoted verbatim below)
  - PR #276 body, "Test plan" -- "Live re-verification (repeat the chained-safeword-plus-'stop listening' utterance, confirm hard stop fires AND session ends up paused) -- deliberate next step, not done in this PR"
  - docs/field-notes/2026-08-12-safeword-and-pause-phrase-are-mutually-exclusive-within-one-utterance.md (the original bug this PR fixes, live-caught 2026-08-12)
  - docs/field-notes/2026-08-15-kill-phrase-live-verified-during-a-genuine-freeze-resume-word-stt-unreliable.md (same operator, same session block, immediately prior test on a different unmerged branch -- see Mechanism for why "eject eject eject" behaved differently here)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran the live UAT session on helios, spoke the chained utterance deliberately to re-test the fix)
    - Claude Code (Anthropic claude-sonnet-5) -- log correlation, writing
  org: https://legionforge.org
  created: 2026-08-15T21:04:24-05:00
  revised: 2026-08-15T21:04:24-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# PR #276's fix live-verified: hard stop and pause both register from one chained utterance

**Context for outsiders.** 2026-08-12 caught a real bug live: if a spoken
utterance contained both a safeword (e.g. "stop stop stop") and the
pause phrase ("stop listening"), the hard stop fired correctly but the
pause was silently dropped, because the whole pause-check lived inside an
`if not is_hard_stop:` branch. PR #276 fixed it so `listening_gate.
observe(text)` runs unconditionally regardless of which branch handled
the utterance. This note is the PR's own explicitly-named next step: a
live re-verification through the real mic pipeline, not a unit test
(the affected function, `run()`, has no existing test harness).

## Problem

PR #276's test plan explicitly left this unchecked: "repeat the
chained-safeword-plus-'stop listening' utterance, confirm hard stop
fires AND session ends up paused." Not done in the PR itself. This note
closes that gap.

## Evidence

Two independent utterances in the same live session, both chaining a
safeword with the pause phrase:

```
2026-08-15 20:57:31,874 INFO also paused listening (pause phrase matched in the same utterance as the hard stop): 'stop stop stop listening'
2026-08-15 20:57:31,874 INFO transcript='stop stop stop listening' lang=en (0.99) dec=0.77 busy=False  [HARD STOP]
2026-08-15 20:57:31,874 INFO hard stop matched safeword 'stop stop stop'
```

```
2026-08-15 20:57:43,662 INFO also paused listening (pause phrase matched in the same utterance as the hard stop): 'stop stop stop listening'
2026-08-15 20:57:43,662 INFO transcript='stop stop stop listening' lang=en (0.93) dec=0.86 busy=False  [HARD STOP]
2026-08-15 20:57:43,662 INFO hard stop matched safeword 'stop stop stop'
```

Both times, the hard stop fired AND the session ended up paused (the new
`"also paused listening"` log line only exists post-fix -- pre-fix, the
2026-08-12 note's whole point was that this case produced no pause log
line at all). Both times, `"resume listening"` recovered cleanly on the
very next attempt, no repeats needed:

```
2026-08-15 20:57:37,677 INFO resumed listening (resume word matched): 'resume listening resume listening'
...
2026-08-15 20:57:49,858 INFO resumed listening (resume word matched): 'resume listening resume listening'
```

(Both transcripts show the resume phrase doubled -- `'resume listening
resume listening'` -- an STT repetition artifact like the ones seen
elsewhere this session; matched correctly regardless, same as the
safeword-repetition robustness noted in the companion kill-phrase note.)

## Mechanism

Confirms the fix as shipped: `listening_gate.observe(text)` now runs
unconditionally on every transcript, independent of whether that same
transcript also matched a hard-stop safeword. The existing hard-stop path
itself is unaffected -- still fires first, still unconditional.

Separately, in the same UAT block, "eject eject eject" was spoken
several more times on this branch and only ever matched as an ordinary
hard-stop safeword (`hard stop matched safeword 'eject eject eject'`),
never force-killing anything. This is expected, not a regression:
`fix/pause-phrase-registers-even-with-safeword-in-same-utterance` (PR
#276) and `feat/force-kill-and-kill-phrase-safety` (PR #277) are
independent, unstacked branches off the same `main` commit -- #276 does
not include #277's `force_kill()`/`kill_phrase` code. The absence of the
"kill phrase ... configured" startup banner this run confirms it.

## What transfers

- **PR #276's fix is confirmed working under real mic/STT conditions,
  twice, closing the PR's own last open test-plan item** -- the original
  2026-08-12 bug (pause silently dropped when chained with a safeword) is
  fixed, not just unit-reasoned-through. (validated-live)
- **`resume listening` recovered cleanly here, 2/2, right after this
  note's own resource-cheap pause scenario** -- worth reading alongside
  the companion kill-phrase note's 0/10 failure rate under a genuinely
  stuck-backend scenario: the resume word's reliability may correlate
  with pipeline/system load or session duration rather than being a flat
  rate, though this note does not establish that as fact, only flags it
  as a live discrepancy worth tracking. (validated-live for this
  session's data; the correlation claim itself is a hypothesis)
- **Testing an unmerged branch means only that branch's code is live** --
  worth stating plainly: a feature on a sibling unmerged branch (here,
  #277's kill_phrase) will not be present just because the config file
  still references it. Gitignored per-machine config surviving a branch
  switch is a real footgun for exactly this reason.

## Not done here

- Root-causing the resume-word reliability discrepancy between this note
  (2/2 success) and the companion note (0/10 failure) -- different
  session states (freshly paused vs. paused after 90s+ of a stuck
  backend), not controlled for.
- No test of the chained-utterance case using a DIFFERENT safeword +
  pause-phrase pairing, or with the safeword and pause phrase in the
  opposite order within the utterance.
