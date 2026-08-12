---
title: First live confirmation of ConvoBox's real mic loop AND the safeword hard-stop on macOS, via synthetic audio injection (no human speaker)
status: validated-live
date: 2026-08-10
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 0df9129; macOS 26.x, Apple Silicon; AIRHUG 28 (USB mic), Mac mini Speakers; backend=claude-code, permission_mode=plan
evidence:
  - Real `scripts/run_convobox.py` process (no --text, real mic loop), full log at convobox-UAT worktree scratch (/tmp/live_loop.log, not committed)
  - A synthetic-speech injection helper (convobox-UAT worktree scratch, gitignored, not committed) that plays TTS phrases through the real speaker for the live session's own real mic to pick up
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked what else could be tested without an interactive human, approved building this)
    - Claude Code (Anthropic claude-sonnet-5) -- built the harness, ran it, diagnosed a detection-reliability issue live before it worked, wrote this note
  org: https://legionforge.org
  created: 2026-08-10T22:55:00-05:00
  revised: 2026-08-10T22:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# First live mic-loop + safeword confirmation on macOS

**Context for outsiders.** Every test earlier tonight either used
`run_convobox.py --text` (which bypasses the real microphone entirely)
or fed audio into isolated scripts, never the real, continuously-running
mic loop `run_convobox.py` runs with no `--text` flag. This note is the
first time that real loop was exercised live on macOS -- using
synthesized "spoken" audio played through the real speaker for the real
mic to pick up, since no human was available. This is a materially
different test than anything else tonight: it exercises the actual
`UtteranceSegmenter.segment()`/`feed_async()` real-time consumption
path, not the offline `_simulate_vad()` helper `scripts/
acoustic_calibration.py` uses, and not any code path `--text` mode
touches at all.

## Method

1. Started a real `scripts/run_convobox.py` process (claude-code
   backend, `permission_mode: plan`, isolated `working_dir`, no
   `--text`) as a background process, logging to a plain file.
2. Built a small helper that synthesizes a phrase with Piper and plays
   it through the real Mac mini Speakers -- landing on the SAME
   speaker the live session's own TTS responses play through, for the
   live session's own AIRHUG 28 mic to pick up over the air, exactly
   like a person speaking into the room.
3. Injected a real command, waited for the real backend to respond and
   speak, then injected the safeword mid-playback.

## Finding 1: the real live loop needed louder injected audio than expected

The first two injection attempts (default TTS volume) produced
**zero** log activity at all -- not even a "dropped, no input" line,
meaning `UtteranceSegmenter` never even detected speech onset. Checked
before assuming a bug: opened a standalone `MicrophoneStream` and
confirmed the physical audio path still worked (RMS ~0.003-0.005,
consistent with tonight's earlier AEC calibration `raw_playback_rms`
readings at the same volume) -- so the signal WAS reaching the mic at
a real, previously-measured level. The likely explanation, consistent
with tonight's AEC-volume-escalation note's own finding (`raw_vad`
utterance rate varied 4/10 to 9/10 across otherwise-identical runs at
default volume): **~0.005 RMS sits right at Silero VAD's
speech-probability threshold on this hardware, marginal enough that
whether any given utterance crosses it is inconsistent.** Raising
injection volume to 3.0x (already validated safe and non-clipping
earlier tonight) made detection reliable -- confirmed on the very next
attempt.

## Finding 2: the full live loop works end-to-end

With louder injection, a command ("please explain in detail how a
binary search tree works...") was heard, transcribed (garbled by the
same far-field effect this session's own `[E6]`-reproduction note
already documented -- "So much to you works, including the social
life...", not the literal prompt), sent to the real claude-code
backend, answered, and the answer was spoken back through the real
speaker -- **the first live, human-free, full-pipeline confirmation of
VAD -> STT -> backend -> TTS on macOS.**

## Finding 3: the safeword genuinely halts a live session, twice

Injected `"stop stop stop stop stop stop"` (repeated for redundancy
against far-field STT misses) during two separate live responses:

```
transcript='Stop, stop, stop, stop, stop!' lang=en (0.49) dec=0.63 busy=False  [HARD STOP]
hard stop matched safeword 'stop stop stop'
```

Both times the safeword was correctly recognized and fired, matching
`hard_stop_phrases`'s substring-match design exactly as documented.
No traceback, no hang; the session stayed alive and listening after
each.

**Caveat, said plainly:** both times, `busy=False` at the moment of
the hard stop -- the backend's own turn had already completed (TEXT +
DONE events arrive before TTS playback even starts, and these test
responses were short enough that playback itself often outlasted the
turn). This means neither trial exercised the specific `was_busy=True`
branch this session's own earlier `Orchestrator.hard_stop()` "honesty
fix" commit (`177fd46`) added -- that commit's own message already
flagged "no live mic session on this machine" as unverified; this note
narrows that gap (the mechanism now HAS a live mic session behind it)
without fully closing it (still no live confirmation of the
`was_busy=True` caveat message itself firing). Reproducing that
specific case would need injecting the safeword while the backend is
still actively generating (e.g. a real tool call in progress), which
needs more precise timing than a quick text-only response gives.

## What transfers

- **The real mic loop works on macOS, end to end, for the first time
  confirmed** -- not just its individual pieces (AEC, STT accuracy,
  backend connectivity) tested in isolation earlier tonight.
- **The safeword works live on macOS, for the first time confirmed** --
  the single most safety-relevant claim this whole session's testing
  could make, and it held up twice.
- **Injection/detection reliability needs real volume margin** on this
  hardware -- default TTS volume was too marginal for consistent VAD
  triggering in a live session; 3x was reliable. This is a testing-
  methodology finding about THIS synthetic-injection technique, not a
  claim about how loud a real human needs to speak (a human's voice
  has different spectral characteristics than speaker-replayed TTS,
  and is typically closer to the mic).
- **Still not closed**: a live confirmation of `hard_stop()`'s
  `was_busy=True` branch specifically, and of course still no real
  human speech tested on macOS at all -- the standing gap this whole
  session's testing has been chipping at from every other angle.
