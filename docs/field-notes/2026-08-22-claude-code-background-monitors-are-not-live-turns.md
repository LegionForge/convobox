---
title: Claude Code's plugin background monitors deliver notifications as passive background context, not live conversational turns
status: validated-live
date: 2026-08-22
project: ConvoBox (github.com/LegionForge/convobox)
versions: Claude Code 2.1.238; plugin `monitors/monitors.json` feature as documented at code.claude.com/docs/en/plugins-reference on 2026-08-22
evidence:
  - c:/tmp/claude/.../scratchpad/convobox-plugin-test/ (throwaway test plugin, not committed)
  - Live interactive session transcript, quoted below
  - https://github.com/mbailey/voicemode/issues/485 (the question this closes)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran the live interactive test)
    - Claude Sonnet 5 (Anthropic claude-sonnet-5) — investigation, test-plugin construction, writing
  org: https://legionforge.org
  created: 2026-08-22T22:07:36-05:00
  revised: 2026-08-22T22:07:36-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Claude Code's plugin background monitors deliver notifications as passive background context, not live conversational turns

**Context for outsiders.** ConvoBox is a local, backend-agnostic voice
frontend for CLI coding agents (Claude Code, Codex, OpenCode) — it drives
each backend externally rather than running inside it. This finding is
about Claude Code's own plugin system, discovered while evaluating whether
ConvoBox could be packaged as a Claude Code plugin without giving up its
core differentiator: continuous listening with no explicit "start a
conversation turn" invocation required. It's a Claude Code platform
finding, not a ConvoBox bug — relevant to anyone building a voice, chat,
or event-driven integration on top of Claude Code's plugin system.

## Problem

Claude Code plugins recently gained a **background monitor** capability
(`monitors/monitors.json`): a persistent background process, started
automatically when the plugin is active, whose stdout lines are delivered
to Claude "as a notification during the session." The published docs
describe Claude as able to "react to log entries, status changes, or
polled events" but do not state whether a notification functions as an
actual live turn — something Claude acts on immediately and autonomously,
the way it would respond to the user typing a message — or whether it's
delivered as passive background context the model is merely aware of.

This distinction is the entire question for whether monitors could support
true always-on voice input: a continuous STT stream feeding transcribed
utterances to Claude without any tool call, closing a gap multiple
downstream projects want and don't currently have a mechanism for (see
[VoiceMode's own long-open "always-on" issue, #485](https://github.com/mbailey/voicemode/issues/485),
labeled "north star" and "help wanted").

## Evidence

A minimal throwaway test plugin, `convobox-monitor-test`, with one monitor
entry:

```json
[
  {
    "name": "fake-voice-input",
    "command": "bash -c 'sleep 6; echo \"[VOICE INPUT] The user just said out loud: What is 47 times 6? Please answer immediately.\"'",
    "description": "Simulates a single voice utterance arriving via ConvoBox"
  }
]
```

`claude plugin validate .` passed (one unrelated warning: no `author`
field set). Launched in a **real interactive terminal** — `claude
--plugin-dir .` — with no further input given by the human operator after
launch.

After the monitor fired (6 seconds in), Claude's actual response, quoted
verbatim:

> don't have a task to do.
>
> If you (the real user) want me to proceed with something — including
> answering that arithmetic question, or continuing whatever the Obsidian
> startup load / ConvoBox work was about — just say so directly and I'll
> go ahead.

Claude never answered "282." It did not treat the notification as
something to act on.

## Mechanism

Claude Code evidently does surface a monitor's notification content into
the model's own context — the response explicitly referenced "that
arithmetic question," proving the text reached the model, not that it was
silently dropped or merely logged out-of-band. But the model did not treat
it as an instruction to act on autonomously; it explicitly asked the human
for confirmation before proceeding, the same posture it would take toward
any other passive background information (a `settings.json`-loaded
project note, in this case an actual reference to the operator's own
CLAUDE.md-driven "Obsidian startup load" instruction, appears to have been
weighed with roughly the same priority as the fake voice-input line).

This rules out, live, the hypothesis that was plausible purely from
reading the docs: that a monitor notification substitutes for a live
conversational turn. The docs' own separate mention of a `Notification`
event as its own lifecycle event (distinct from ordinary turn-taking)
turns out to describe the real behavior, not just one possible reading of
ambiguous wording.

**Ruled out, not assumed:** this was not a case of the notification simply
failing to reach Claude at all (which would look identical from the
outside — no reaction) — the direct reference to "that arithmetic
question" is what confirms the content arrived and was read, just not
acted on.

## What transfers

- **Validated-live**: a single monitor notification, delivered mid-session
  with no other user input, does not cause Claude Code to autonomously act
  on its content — confirmed once, Claude Code 2.1.238, default
  permission mode, one monitor, one notification.
- **Not established, real caveats**: whether repeated/rapid monitor
  output, more imperative phrasing, a different `permission_mode`, or an
  `agent`/`settings.json` override changes this behavior. This is one
  data point, not an exhaustive sweep — stated as a limitation, not
  papered over.
- **Practical implication for ConvoBox**: a Claude Code plugin cannot
  currently replicate ConvoBox's continuous-listening, no-tool-invocation
  model via background monitors. The plugin path remains genuinely viable
  for two other pieces that don't depend on monitors at all: auto-speaking
  Claude's replies via a `Stop` hook (fires automatically every turn, no
  agent tool call needed) and voice-gated approval via a `PreToolUse` hook
  calling an external blocking helper process. But it would be a lighter,
  complementary offering — hear replies, approve by voice — not a
  replacement for ConvoBox's own external-driver architecture, which
  remains the only place the actual always-on/barge-in experience exists
  today.
- **Portable beyond ConvoBox**: anyone evaluating Claude Code's monitors
  feature for a live, turn-generating integration (not just passive
  logging/alerting) should test this directly before designing around it
  — the docs alone don't disambiguate "live turn" from "background
  context," and this test shows it's the latter, at least in the
  configuration tested here.
