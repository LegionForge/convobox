---
title: kill_phrase force-kills a genuinely stuck codex backend live through the real mic pipeline -- and it was needed because the configured resume word failed every attempt
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: feat/force-kill-and-kill-phrase-safety @ 3f718e8 (PR #277, unmerged); backend=codex, model=gpt-5.6-terra; config: safeword.kill_phrase="eject eject eject", interaction.resume_word="resume listening" (operator override of DEFAULT_RESUME_WORD="Athena"); working_dir D:/LegionForge/convobox-UAT (Windows/helios)
evidence:
  - convobox-tui.log, D:/LegionForge/convobox-UAT, 2026-08-15 20:43:21-20:44:58 (timestamps quoted verbatim below)
  - PR #277 body, "Still open" item -- "does saying 'eject eject eject' live through the real mic pipeline reliably reach force_kill()... needs a real mic session"
  - docs/field-notes/2026-08-05-stt-hotwords-athena-resume-inconclusive.md (predecessor resume-word reliability investigation, different phrase/mic/session)
  - docs/KNOWN-ISSUES.md's readline()-blocks-with-no-timeout entry (names the freeze class this note's kill_phrase test fired against)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran the live UAT session on helios, hit the freeze and the resume failures firsthand, chose to test kill_phrase in the moment rather than wait it out)
    - Claude Code (Anthropic claude-sonnet-5) -- log correlation, mechanism analysis, writing
  org: https://legionforge.org
  created: 2026-08-15T20:50:00-05:00
  revised: 2026-08-15T20:50:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# kill_phrase live-verified during a genuine freeze -- and it was needed because the resume word failed every attempt

**Context for outsiders.** ConvoBox is a voice assistant that shells out to
an LLM coding-agent CLI (here, `codex`) as its backend. Prior sessions
found that backend's stdout `readline()` can block indefinitely with no
timeout, wedging the whole pipeline; PR #277 added `force_kill()` (an
OS-level terminate/kill of the backend process, no RPC round-trip) and an
opt-in spoken `kill_phrase` as a last-resort escape hatch, but its voice
trigger path had never been tested through a real mic. This session tested
it live, and it fired during an actual freeze, not a staged one.

## Problem

Live UAT on `feat/force-kill-and-kill-phrase-safety` (helios, Windows,
codex backend). Mid-session, a real long-running `readline()` stall began.
The operator hard-stopped via voice, the session correctly paused, but
every subsequent attempt to say the configured resume word
(`"resume listening"`) was misheard by STT -- ten consecutive failures.
With the backend still not responding and no working way back in, the
operator said the kill phrase (`"eject eject eject"`) instead.

## Evidence

### The freeze: a `readline()` call outstanding from before the hard stop through the kill

```
2026-08-15 20:43:26,833 WARNING codex app-server _read_loop: readline() still pending after 5.5s ...
2026-08-15 20:43:31,843 WARNING ... still pending after 10.5s ...
2026-08-15 20:43:36,832 WARNING ... still pending after 15.5s ...
2026-08-15 20:43:41,823 WARNING ... still pending after 20.5s ...
2026-08-15 20:43:44,919 INFO Detected language 'en' with probability 0.96
2026-08-15 20:43:45,675 INFO transcript='stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop stop' lang=en (0.96) dec=0.72 busy=False  [HARD STOP]
2026-08-15 20:43:45,675 INFO hard stop matched safeword 'stop stop stop'
2026-08-15 20:43:46,834 WARNING ... still pending after 25.5s ...
```

(The 32x-repeated `'stop stop stop...'` transcript is itself an STT
hallucination -- the operator said the phrase once or twice; substring
matching against `hard_stop_phrases` was robust to the repetition and
still fired correctly. Not the focus of this note, but worth recording:
hallucinated repetition of a safeword did not prevent detection.)

The same `readline()` call -- started roughly 20:43:21, before the hard
stop was even spoken -- kept climbing straight through the pause and every
failed resume attempt below, past 90 seconds:

```
2026-08-15 20:43:54,656 INFO paused listening (matched 'stop listening') -- hard-stopped in-flight work; say 'resume listening' to resume
2026-08-15 20:43:56,835 WARNING ... still pending after 35.5s ...
2026-08-15 20:44:01,843 WARNING ... still pending after 40.5s ...
2026-08-15 20:44:06,818 WARNING ... still pending after 45.5s ...
2026-08-15 20:44:11,839 WARNING ... still pending after 50.5s ...
2026-08-15 20:44:16,850 WARNING ... still pending after 55.5s ...
2026-08-15 20:44:21,867 WARNING ... still pending after 60.5s ...
2026-08-15 20:44:26,864 WARNING ... still pending after 65.5s ...
2026-08-15 20:44:31,853 WARNING ... still pending after 70.5s ...
2026-08-15 20:44:36,861 WARNING ... still pending after 75.5s ...
2026-08-15 20:44:41,849 WARNING ... still pending after 80.5s ...
2026-08-15 20:44:51,863 WARNING ... still pending after 90.5s ...
```

### Ten straight failed resume attempts

Every attempt at the configured resume word (`"resume listening"`) was
misheard, none matching:

```
20:43:58,988 INFO dropped (paused, not the resume word): 'sound listening'
20:44:03,276 INFO dropped (paused, not the resume word): 'stop listening'
20:44:07,498 INFO dropped (paused, not the resume word): 'do you hear listening'
20:44:12,269 INFO dropped (paused, not the resume word): 'clean from shown listening'
20:44:17,122 INFO dropped (paused, not the resume word): 'please press your listening'
20:44:21,369 INFO dropped (paused, not the resume word): 'brake for as room listening'
20:44:29,239 INFO dropped (paused, not the resume word): 'insertion less thing'
20:44:33,537 INFO dropped (paused, not the resume word): 'reset last night please'
20:44:38,138 INFO dropped (paused, not the resume word): "let's go listening please"
20:44:42,462 INFO dropped (paused, not the resume word): 'self listening please'
```

Both "resume" and "listening" are individually present in
`stt.hotwords`, and every attempt did land on the word "listening" -- but
none reconstructed the exact two-word phrase.

### The kill phrase fired and ended the session in ~2.9s

```
2026-08-15 20:44:55,520 INFO transcript='eject eject eject' lang=en (0.44) dec=0.86 busy=False  [HARD STOP]
2026-08-15 20:44:55,520 WARNING kill phrase matched 'eject eject eject' -- force-killing backend
2026-08-15 20:44:56,143 WARNING codex app-server _read_loop: readline() still pending after 0.5s ...
2026-08-15 20:44:56,279 WARNING codex app-server _read_loop: readline() finally returned after 0.6s total ...
2026-08-15 20:44:58,269 INFO AEC dump closed -- .aec-dumps\20260815-203928: ...
2026-08-15 20:44:58,399 INFO exiting
```

STT's confidence on this final utterance was low (`lang=en (0.44)`) --
still matched correctly against the exact `kill_phrase` string.

## Mechanism

The `readline()` stall matches the class already named in
`docs/KNOWN-ISSUES.md`: the codex app-server's stdout pipe blocking with
no timeout. What's new: it was outstanding *before* the hard stop was
spoken and never cleared through the entire pause/resume-failure window,
consistent with `send_hard_stop()`'s polite interrupt riding the same
stuck pipe it couldn't reach -- exactly the scenario PR #277 was scoped
to answer. `force_kill()` doesn't wait on that pipe at all (direct
`terminate()`/`kill()` of the OS process), which is why it resolved in
under 3 seconds where the polite path had already failed for 90+.

The resume-word failures are a separate, STT-side problem: this
checkout's `resume_word` is operator-configured as `"resume listening"`
(overriding the shipped default `"Athena"`), and none of ten attempts
transcribed cleanly enough to match, despite both words individually
being in `stt.hotwords`. This note does not establish why a two-word
phrase with both words hotword-biased still failed this consistently --
worth a targeted follow-up, not diagnosed here.

## What transfers

- **`kill_phrase`/`force_kill()`'s live voice-trigger path is confirmed
  working, closing PR #277's last open test-plan item** -- it correctly
  matched through real STT (even at low confidence) and terminated a
  genuinely stuck backend process in ~2.9s, in situ, not a synthetic
  repro. (validated-live)
- **A hallucinated, heavily-repeated safeword transcript still matches
  correctly** -- substring matching against `hard_stop_phrases` is robust
  to STT repeating a phrase 30+ times instead of once. (validated-live)
- **An operator-configured multi-word `resume_word` can be substantially
  less STT-reliable than expected, even with every component word
  hotword-biased** -- ten consecutive misses for `"resume listening"` in
  one session is a real, live data point, not a synthetic worst case.
  (validated-live, single session -- not yet a confirmed rate)
- **When the polite recovery path (hard stop + resume) and the STT-side
  recovery path (resume word) both fail at the same time, the kill
  phrase is the only path that actually got the operator out** -- direct,
  first-hand confirmation of the scenario that motivated building it.
  (validated-live)

## Not done here

- Root-causing why `"resume listening"` specifically fails this often --
  a shorter/more distinct resume phrase, or the existing `"Athena"`
  default, has not been re-tested under the same conditions for
  comparison.
- Root-causing WHY the codex app-server's `readline()` blocked this long
  in the first place -- still open, same as every prior note on this
  class of freeze.
- No comparison run without the `resume listening` override (i.e.
  against shipped defaults) to isolate whether this is phrase-specific or
  a broader resume-word reliability gap.
