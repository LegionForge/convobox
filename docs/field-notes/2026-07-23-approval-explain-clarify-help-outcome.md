---
title: "Explain"/"clarify"/"help" during a pending approval now gets a spoken answer, not silence
status: diagnosed
date: 2026-07-23
project: ConvoBox (github.com/LegionForge/convobox)
versions: n/a (pure Python logic, no external dependency version-specific)
evidence:
  - JP's feature request, 2026-07-23, mid-session after PR #133/#139 shipped the full permission_mode approval flow to main
  - src/convobox/approval/detector.py's own pre-existing "discuss" outcome and docstring (shipped 2026-07-14): "the interesting one: the user asks a question about the pending action instead of deciding" -- already anticipated this gap existed, just never closed it
  - scripts/run_convobox.py's discuss-handling block, pre-fix: `if approval_outcome == "discuss": log.info(...); continue` -- no spoken reply of any kind
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (feature request, design-tradeoff decision on how much detail to speak)
    - Claude Code (Anthropic claude-sonnet-5) — design, implementation, tests, writing
  org: https://legionforge.org
  created: 2026-07-23T00:15:00-05:00
  revised: 2026-07-23T00:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# "Explain" during a pending approval now gets a spoken answer

**Context for outsiders.** ConvoBox can gate a coding agent's destructive
actions (writes, shell commands) behind a spoken approval — the operator
must say a chosen approval phrase, or "no" to decline. `ApprovalDetector`
already classified any other utterance as `"discuss"` since 2026-07-14, on
the theory that an operator reviewing a pending action might ask a
question first rather than deciding immediately. This note is about
closing the gap between "the design anticipated this" and "it actually did
anything."

## Problem

`ApprovalDetector.check()` has classified non-approve/non-deny speech as
`"discuss"` since it was first built, with an explicit design rationale
(its own docstring): a pending approval must stay answerable across a
clarifying exchange rather than being dropped. But the actual consumer of
that outcome (`scripts/run_convobox.py`'s main loop) only ever did this:

```python
if approval_outcome == "discuss":
    log.info("approval remains pending while the operator reviews/discusses: %r", text)
    continue
```

The prompt correctly stayed open — but the operator's question got
**zero spoken feedback**. Saying "wait, what is this?" during a real
pending approval produced total silence, indistinguishable from the mic
not hearing anything at all. The design doc itself flagged this as
future work ("`discuss` deliberately does NOT forward the utterance to
the backend... it just keeps the prompt open and stays pending") but
never specified what, if anything, should happen for the operator in that
moment.

## Mechanism (the fix)

`ApprovalDetector` gets a fourth outcome, `"explain"`, checked between
`"deny"` and the `"discuss"` fallback — a distinct trigger-phrase list
(`DEFAULT_EXPLAIN_PHRASES`: explain/explanation/clarify/help), same
word-boundary/normalization convention as approve/deny, same
construction-time overlap guards (can't collide with the approval phrase
or deny phrases). `ApprovalPromptGate` now retains the pending request's
full detail (`pending_explanation`, set via `start_waiting`'s new
`explanation` kwarg) so it survives from the moment the request arrives
to whenever the operator asks about it — potentially several
utterances later, since `"explain"`/`"discuss"` both keep the wait open
and reset the timeout clock.

The one real design decision (JP's call, since the existing code had a
documented reason NOT to do this automatically): `render_approval_request_for_speech`'s
terse automatic announcement deliberately never reads the command/args
aloud — "commands can be long, misleading, or sensitive out of context."
An explicit "explain" request is a different situation: the operator is
asking specifically because they want to know, and the full detail is
already visible in the TUI/log to anyone looking at the screen — voice
just didn't have equivalent access to information a keyboard/screen user
already had. Chose to speak the full detail (same content, `event.content`
for Codex or a `tool`/`tool_input`-rendered fallback for Claude Code,
whichever adapter populated), not a restricted summary — reversing the
"don't announce this automatically" caution only when explicitly asked,
not removing it as a default.

## What transfers

- **A classifier anticipating a case ("discuss" exists to keep the prompt
  answerable") is not the same as that case actually being handled.**
  The detector was architecturally correct for over a week before the gap
  (silent non-response) was found — worth periodically checking whether
  every classified outcome actually has a consumer that DOES something,
  not just one that doesn't crash.
- **"Don't do X automatically" and "never do X" are different design
  decisions, and conflating them blocks otherwise-reasonable features.**
  The existing caution against speaking commands aloud was specifically
  about the *automatic* announcement (every approval, unconditionally);
  an explicit, operator-initiated request for the same information is a
  different risk calculus entirely, and treating the two as one
  inseparable rule would have blocked a feature the original design's own
  reasoning didn't actually preclude.
- **Status: diagnosed, not yet validated-live.** The detector/gate logic
  is fully unit-tested (word-boundary matching, cross-backend content
  resolution, timeout/clock-reset behavior). What hasn't been confirmed:
  whether the actual spoken readback of a real (possibly long) pending
  command is genuinely useful/intelligible when heard for real, on either
  backend. See `docs/UAT-checklist.md`'s `[U12]`.
