---
title: Codex silently rejected every voice-approved write because the decision value was invalid, not declined on purpose
status: validated-live
date: 2026-07-21
project: ConvoBox (github.com/LegionForge/convobox)
versions: codex-cli 0.144.6, ConvoBox interaction.approval_phrase / backend.permission_mode=approve
evidence:
  - PR #109 (fix/codex-approval-accept-not-approve), merged onto wip/approval-echotailguard
  - codex app-server generate-json-schema (codex-cli 0.144.6) — CommandExecutionRequestApprovalResponse.json, FileChangeRequestApprovalResponse.json
  - convobox-UAT/convobox-tui.log, session 2026-07-20 23:26-23:57 (bug) and 2026-07-21 00:06-00:25 (fix validated live)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, live UAT testing, reported the symptom)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, implementation, writing
  org: https://legionforge.org
  created: 2026-07-21T00:45:00-05:00
  revised: 2026-07-21T01:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Codex silently rejected every voice-approved write because the decision value was invalid, not declined on purpose

**Context for outsiders**: ConvoBox drives Codex CLI's `app-server`
JSON-RPC interface as a spawned subprocess. When Codex wants to run a
command or write a file under a restrictive sandbox policy, it sends an
approval request over the same pipe and blocks the turn until ConvoBox
answers. ConvoBox's voice-gated approval flow lets the operator say a
configured phrase to approve, or "no" to deny.

## Problem

With `backend.permission_mode: approve` and a real `interaction.
approval_phrase` configured, the operator said the approval phrase, and
ConvoBox's own log confirmed recognition (`"approved pending Codex
approval: '...'"`) — but Codex's response was consistently that the
action had been rejected anyway: *"the temporary write probe was declined
before it ran"*, *"the file creation was again rejected before it ran"*.
Every approval, for both a command-execution request and a file-change
request, failed the same way.

## Evidence

`CodexAdapter.resolve_pending_approval()` sent
`{"decision": "approve"}` on a real approve outcome. Ran
`codex app-server generate-json-schema --out <dir>` against the actually
installed codex-cli (0.144.6) and read the real response schemas:

```
CommandExecutionApprovalDecision: enum ["accept", "acceptForSession",
  <acceptWithExecpolicyAmendment object>, <applyNetworkPolicyAmendment
  object>, "decline", "cancel"]
FileChangeApprovalDecision: enum ["accept", "acceptForSession",
  "decline", "cancel"]
```

Neither enum has an `"approve"` member. The value ConvoBox was sending was
schema-invalid, and Codex silently treated it as equivalent to a decline
— no error surfaced back over the JSON-RPC connection, just the eventual
"rejected" response text.

The adapter's own module docstring had extensively live-verified the
**decline** path against a real app-server (`{"decision": "decline"}`
confirmed correct for both approval methods) but never live-tested the
**approve** path at all — the gap that let this ship unnoticed.

## Mechanism

Not a logic bug in ConvoBox's approval-gate state machine (phrase
recognition, timeout handling, and turn-blocking all worked correctly) —
purely a wrong literal string in the JSON-RPC response payload for the
one branch that was never independently exercised against the real
schema.

## Fix

Changed the approve payload to `{"decision": "accept"}`. PR #109,
2 commits: the schema-value fix itself, plus a follow-up UX change (speak
a delayed "Approval confirmed." announcement ~2s after a real approve,
rather than immediately, since a Codex turn resumes running the moment
it's approved — an immediate announcement risked a self-barge-in on the
announcement interrupting the tool call that had just started).

## What transfers

- **A safety-relevant fail-closed default can hide a totally unrelated
  bug behind "working as designed."** Every approval looked like a correct
  decline from the outside (Codex genuinely never wrote anything) — it took
  checking the real vendor schema, not just re-reading ConvoBox's own code,
  to find the value itself was wrong.
- **Live-verifying one branch of a two-branch safety gate (decline) does
  not verify the other (approve).** Both need independent live
  confirmation before trusting either.
- **Validated live, not just schema-read**: the same UAT session
  immediately after this fix shipped successfully created a file, wrote a
  string into it, and read it back via three separate voice-approved Codex
  actions — direct evidence the fix works end-to-end, not just that the
  schema now matches.
