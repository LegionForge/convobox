---
title: Interactive prompts deadlock voice-driven coding agents — the question no one can hear or answer
status: validated-live
date: 2026-07-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: opencode 1.18.3 (server API); ConvoBox main circa PR #96; faster-whisper base
evidence:
  - docs/UAT-checklist.md finding [L9]
  - docs/DESIGN-backend-questions.md (design response)
  - PR #96 (slice-1 fix: spoken announcement)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; hit the deadlock live)
    - Claude Code (Anthropic claude-fable-5) — live investigation, API verification, fix, writing
    - opencode build agent (OpenCode Zen hy3-free) — the backend whose question caused the incident
  org: https://legionforge.org
  created: 2026-07-19T12:55:00-05:00
  revised: 2026-07-19T12:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Interactive prompts deadlock voice-driven coding agents

**Context for outsiders.** ConvoBox is a local voice frontend for coding-agent
CLIs (opencode, Claude Code, Codex): mic → VAD → STT → orchestrator →
agent backend → TTS. Coding agents increasingly expose *interactive tools* —
multiple-choice questions, approval prompts — designed for a terminal UI.
This note documents what happens when one fires inside a voice session, and
why conversational repair cannot fix it.

## Problem

The operator asked the agent, by voice, "can you help me test?" The agent
called its interactive `question` tool (a multiple-choice prompt: "What kind
of testing do you want to run…"). The voice session then went silent for
5+ minutes while the operator talked to it, with no indication anything was
wrong.

## Evidence

- The tool call blocked in `status: running` from 18:53:32; read live from
  the server: `GET /api/session/{id}/question` returned the full pending
  question while the session appeared dead.
- ~15 subsequent operator utterances were each accepted by the backend
  (HTTP 200, admitted as steer prompts) and **never materialized in the
  session's message list** — they queued invisibly behind the blocked tool.
- The operator's barge-in muted TTS playback at 18:53:40 — *mid-question* —
  so the question was never even heard.
- The status heartbeat reported "backend still working (126s) — thinking or
  running a tool" while the truth was "waiting for YOUR answer."

## Mechanism

Three failures compound:

1. **The question travels the wrong channel.** It is a tool call, not
   response text, so a speech pipeline that only voices response text says
   nothing. The user experiences an unexplained stall.
2. **Conversational repair is structurally impossible through the agent.**
   The human instinct — "can you repeat the question?", "are you okay?" —
   becomes just another queued prompt behind the blocked tool. Repair
   *cannot* be delegated to the LLM whose turn is blocked; it must be
   handled by the voice layer itself, which (crucially) can read the
   pending question locally and speak it deterministically.
3. **The liveness indicator lied by omission.** "Still working" and
   "waiting for you" are opposite states needing opposite user actions;
   collapsing them into one message hides the deadlock.

Ruled out: server wedge (other endpoints responded normally), STT failure
(utterances transcribed fine — they queued after transcription).

## What transfers

- **Inventory every blocking interactive tool your backend can call**
  (questions, approvals, confirmations). Each one is a voice-session
  deadlock until it has an explicit voice path. (validated-live)
- **The voice layer must announce, re-prompt, and answer interactive
  prompts itself** — the asker's obligation to re-ask after silence
  (standard conversation-design No-Input laddering) applies to the
  *system*, not the blocked LLM. (diagnosed; announcement slice shipped,
  ladder pending)
- **Distinguish "working" from "waiting on the user" in any liveness
  signal.** They are different states with different correct user actions.
  (validated-live)
- An emergency abort phrase is the wrong *primary* exit for a routine
  question — if users need the fire alarm to answer multiple choice, the
  turn-taking contract is broken. Keep the abort, build the repair.
