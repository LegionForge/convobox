---
title: The idle-trigger freeze does not reproduce on opencode either -- 5+ minutes idle plus two probe utterances stayed fully responsive; includes a self-caught false alarm worth keeping as a methodology note
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=opencode (localhost:4096), permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 5. Same idle-trigger test shape as the codex and claude-code versions (docs/field-notes/2026-08-15-vad-freeze-idle-trigger-confirmed-no-active-stress-needed.md, docs/field-notes/2026-08-15-vad-freeze-idle-trigger-does-not-reproduce-on-claude-code.md) -- one stress cycle, then idle time, run against opencode instead
  - Two follow-up probe utterances after ~5 minutes idle, to directly test responsiveness rather than rely on a stall-diagnostic log line (opencode's own SSE stall diagnostic, added earlier tonight, is on a different unmerged branch than the one this test ran from)
  - Full raw session log (/tmp/convobox_idle_oc.log, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T04:10:00-05:00
  revised: 2026-08-15T04:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# opencode does not freeze under the idle-trigger condition either

**Context.** Completes the idle-trigger comparison matrix across all
three backends: codex reliably freezes under this condition (~90-100s
onset, confirmed severe), claude-code showed zero stalls of any kind
across 240s. This round tests opencode, the one backend not yet tried
under this specific condition (previously tested only for `force_kill()`
behavior and one clean 6-cycle stress batch).

## Method

Same shape as the other two idle-trigger tests: one stress cycle, then
zero further deliberate audio for several minutes. **Difference from
the codex/claude-code versions**: opencode's adapter has no
`readline()`-style stall diagnostic on the branch this test ran from
(the fix built earlier tonight, `anext_with_stall_diagnostic()`, lives
on a separate unmerged branch, `fix/opencode-sse-stall-diagnostic`, not
this session's `feat/force-kill-and-kill-phrase-safety`). Without that
log signal, responsiveness was tested directly instead: two follow-up
probe utterances after the idle window, checking whether the session
actually processes new input rather than inferring health from a log
line that doesn't exist on this branch.

## Result: no freeze, plus a real false alarm worth keeping as a methodology note

The session stayed alive and the opencode server stayed reachable
(`http_code=200` throughout) across 5+ minutes of idle time. **First
probe utterance produced zero new log activity** -- initially read as a
possible freeze. **Investigated before writing it up**: sent a second,
different probe phrase (the safeword) 40 seconds later, and it was
picked up and processed completely normally, immediately, no stall.
This means the first probe was an ordinary STT/VAD miss (the
already-documented "dropped (no input, STT heard nothing recognizable)"
tier-1 outcome, which happens routinely and isn't itself a bug), not
evidence of a hang. **Session was never actually stuck.**

## What transfers

- **A one-shot "did it respond" check is not enough to distinguish a
  real freeze from an ordinary transcription miss -- always send a
  second, different probe before concluding a hang.** This session
  almost wrote up a false freeze report for opencode based on exactly
  this mistake; catching it before publishing is the correct outcome,
  but it's worth naming explicitly as a live methodology risk, not just
  quietly correcting it. Every prior severe-freeze catch this session
  used CPU forensics (byte-identical `ps` samples) specifically because
  a log-silence-alone signal is this ambiguous -- this incident is a
  concrete demonstration of why that discipline exists. (validated-live)
- **opencode joins claude-code as NOT reproducing the idle-trigger
  freeze**, completing the comparison: codex is the only one of the
  three backends where this session found the severe idle-trigger
  freeze. Whatever the mechanism is (still unconfirmed, leading
  hypothesis remains audio-pipeline/event-loop contention specific to
  codex's own subprocess I/O shape), it does not appear to be a
  ConvoBox-wide architectural issue -- it's backend-specific. (validated-
  live, small samples across all three)

## Not done here

- A second independent opencode idle run (this note has n=1, same
  caveat as every other single-run comparison tonight).
- Re-running this SAME test on a branch that has the opencode SSE
  stall diagnostic merged in, so a future attempt gets the same
  log-based signal the codex/claude-code versions had, rather than
  relying on manual probe-and-verify.
- Investigating the STT miss on the first probe utterance further --
  treated here as ordinary/expected, not investigated as its own
  finding.
