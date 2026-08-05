---
title: The same stt.hotwords-active branch produced both the best and worst "Athena" resume runs of the day — no clean signal yet
status: hypothesis
date: 2026-08-05
project: ConvoBox (github.com/LegionForge/convobox)
versions: feat/stt-hotwords-bias (PR #204, commits c7a84f3/c1f78a1, authored 2026-08-03); faster-whisper base, device=cpu, compute_type=int8, stt.hotwords="Athena stop break brake eject mayday Whiskey Tango Foxtrot", stt.temperature/condition_on_previous_text both unset (default); backend=codex
evidence:
  - convobox-tui.log 2026-08-05 14:41:35-14:43:10 (session 1: original incident)
  - convobox-tui.log 2026-08-05 15:36:22-15:43:21 (session 2: fix/web-listening-bridge-tui-desync, no hotwords support present)
  - convobox-tui.log 2026-08-05 16:19:22-16:23:17 (session 3: feat/stt-hotwords-bias merged with main @ c59e117)
  - PR #204 (open, unmerged) — its own live-UAT checklist item this note answers
  - src/convobox/config.py:76-104 (STTConfig: temperature/condition_on_previous_text default to a faster-whisper no-op when unset)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran all three sessions, correctly pushed back on treating session 3 alone as proof)
    - Claude Code (Anthropic claude-sonnet-5) — live log investigation, writing
  org: https://legionforge.org
  created: 2026-08-05T16:35:00-05:00
  revised: 2026-08-05T16:35:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The same hotwords-active branch produced both the best and worst "Athena" resume runs of the day

**Context for outsiders.** ConvoBox's voice pipeline uses a spoken resume
word ("Athena" by default) to un-pause listening after a pause phrase.
Short proper nouns are known to mis-transcribe on faster-whisper (this
project's own recurring "Athena" hallucination pattern, the whole reason
PR #204 exists). #204 adds `stt.hotwords`, a free-text prompt bias passed
to `transcribe(hotwords=...)`, with one open live-UAT question on its own
checklist: does it actually reduce the miss rate? This note is the answer
so far — and it is not a clean yes.

## Problem

Three real sessions across one afternoon gave three different resume-word
success rates. The instinct is to credit the best one to `stt.hotwords`.
The timeline doesn't support that instinct cleanly.

## Evidence

| Session | Time | Branch | `stt.hotwords` actually active? | Result |
|---|---|---|---|---|
| 1 (original incident) | 14:41-14:43 | `feat/stt-hotwords-bias` | **Yes** | 0-for-3: "Pina" (mis-transcribed), then two no-input drops. Never resumed by voice; fixed via the web button (separate issue, see the 2026-08-05 web-resume field note). |
| 2 (follow-up) | 15:36-15:43 | `fix/web-listening-bridge-tui-desync` (off `main`) | **No** — confirmed by grep: no `hotwords` field on `STTConfig` on this branch; the config key is silently dropped (pydantic `extra="ignore"`) | 3 cycles, 1/2/2 attempts before each success |
| 3 (this session) | 16:19-16:23 | `feat/stt-hotwords-bias` merged to `main` @ `c59e117` | **Yes** | 3-for-3, first try every time: 16:20:30, 16:20:54, 16:22:27 |

Session 1's transcript: `26517:...dropped (paused, not the resume word):
'Pina'`, `26520`/`26523`: two consecutive `dropped (no input, STT heard
nothing recognizable)`. Session 3's transcript: `26891`, `26918`, `27055`,
all `resumed listening (resume word matched): 'Athena'`, each on the
immediately-following STT attempt after its pause.

Session 1 and session 3 ran the **identical hotwords-active code and
config** — `feat/stt-hotwords-bias` has carried `stt.hotwords` since
commits authored 2026-08-03, well before either session, and
`convobox.yaml`'s `hotwords:` value was unchanged between them. Same
branch, same setting, opposite outcomes (0-for-3 vs. 3-for-3).

## Mechanism

Ruled out as a clean explanation: **hotwords being present or absent**,
since it was present in both the best and worst runs. Whatever produced
session 3's clean run, it isn't simply "hotwords was on this time."

Candidate confounds, none isolated:

- **STT decoding isn't pinned.** `stt.temperature` and
  `stt.condition_on_previous_text` are both unset this whole day, so
  faster-whisper's own temperature-fallback ladder is live — the same
  audio can decode differently run to run for reasons that have nothing
  to do with hotwords. This alone is enough to explain session-to-session
  variance without invoking hotwords at all.
- **Operator state.** Session 1's resume attempts followed a
  false-positive pause (the "No I tried to say the stop listening
  phrase" trigger) and landed inside a genuinely confusing, frustrating
  incident (see the web-resume field note). Session 3's resume attempts
  were calm, deliberate UAT. Speech clarity/pace under frustration vs.
  calm testing is a real, unmeasured variable.
- **Time-of-day / room drift.** ~1.5 hours apart; operator reports no
  perceived change in room noise or mic position, but "no perceived
  change" isn't a controlled measurement of either.
- **Small n regardless.** 3 genuine "Athena" attempts per session, 9
  total across the whole day. Not enough to support a rate claim in
  either direction even before the confounds above.

## What transfers

- **Don't credit a single session's result to the one variable you
  changed if an earlier session with the same variable already
  contradicts it.** Session 1 alone would have been "hotwords doesn't
  help, still 0-for-3." Session 3 alone would have been "hotwords fixed
  it, 3-for-3." Both readings are wrong; only comparing across sessions
  surfaced that. (validated-live, as a methodology lesson — not a claim
  about hotwords itself)
- **A same-proper-noun resume-word test needs decoding pinned before
  attributing anything to a bias parameter.** With `temperature`/
  `condition_on_previous_text` left at their sampling-ladder defaults,
  hotwords is not the only source of run-to-run variance, and no live
  session run so far isolates it. (hypothesis — the actual isolating
  experiment hasn't been run)
- **Recommended follow-up, not yet done:** pin `stt.temperature: 0.0`
  (removes the STT's own randomness as a variable), then within **one
  sitting** — same room, same mic position, same operator state — toggle
  `stt.hotwords` on and off every few pause/resume cycles via the
  Settings TUI, logging which is active each cycle. That is the minimum
  design that could actually attribute a rate difference to hotwords
  specifically. PR #204's live-UAT checklist item stays unchecked until
  this (or something like it) runs.
