---
title: OpenLive confirms ConvoBox's coding-agent-first niche and surfaces ACP as the direction for standardized backend support
status: diagnosed
date: 2026-07-29
project: ConvoBox (github.com/LegionForge/convobox)
versions: "katipally/openlive (pushed 2026-07-18T08:09:19Z, MIT, TypeScript/Electron); ConvoBox main @ 1f145ac (2026-07-29, includes PR #174/#178)"
evidence:
  - https://github.com/katipally/openlive
  - https://github.com/katipally/openlive/blob/main/README.md
  - https://agentclientprotocol.com
  - docs/ROADMAP.md (mission framing, 2026-07-12; VS Code/VSCodium mid-term item)
  - docs/field-notes/2026-07-28-other-claude-code-web-uis-dont-transfer-much.md (prior, different-category survey)
  - src/convobox/adapters/claude_code.py (module docstring: live-probed permission-gate/PreToolUse-hook mechanism)
  - scripts/run_convobox.py (BargeInMonitor, EchoAwarePlayer, EchoTailGuard)
  - PR #174, #178, #179 (this session's live-verified barge-in/echo work)
  - README.md (ConvoBox's own self-description)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, directed the comparison and made the ACP/focus decision)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, writing
  org: https://legionforge.org
  created: 2026-07-29T18:40:00-05:00
  revised: 2026-07-29T18:40:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# OpenLive confirms ConvoBox's coding-agent-first niche and surfaces ACP as the direction for standardized backend support

**Context for outsiders:** ConvoBox is a local, backend-agnostic voice
frontend for CLI coding agents (Claude Code, Codex, OpenCode) — you talk,
it talks back, the coding agent does the real work in its own sandboxed
directory. This note compares it against
[katipally/openlive](https://github.com/katipally/openlive), a
similarly-scoped open-source project (219 stars, 42 forks, MIT,
TypeScript/Electron) that JP surfaced directly, asking whether it
differentiates ConvoBox at all.

## Problem

JP: "I really like OpenLive, but I think I'd really steer us towards
coding, development, heavy lifting with CLI... I want to make a voice
driven dev environment." Is that already ConvoBox's stated direction, or
a pivot? And does OpenLive's existence change what ConvoBox should build
next?

## Evidence

**OpenLive** (per its README and GitHub API metadata): an Electron desktop
app (macOS/Windows/Linux installers) pairing an on-device voice loop
(Silero VAD, Whisper STT, Smart-Turn end-of-turn, Kokoro/Supertonic TTS)
with either a direct BYO-model chat (Anthropic/OpenAI/Google/xAI/
DeepSeek/Groq/Ollama, its own built-in provider turn loop) or a coding
agent (Claude Code, Codex, Cursor, OpenCode, Hermes) driven over the
[Agent Client Protocol](https://agentclientprotocol.com) (ACP,
JSON-RPC/stdio). Additional scope beyond the voice loop: camera/screen
vision with an on-demand `look` tool, local zero-shot voice cloning
(ZipVoice, ~208MB optional), a floating always-on-top mini mode, agent
CLI install/auth management from within the app, and session continuity
into the agent's own native session store (`~/.claude/projects/...`).
Explicitly frames itself the same way ConvoBox's own README does: "a
cascaded pipeline (speech to text to model to speech), not a full-duplex
speech-to-speech model" — an almost identical tradeoff statement,
independently arrived at.

**ConvoBox** (per README.md, docs/ROADMAP.md, and this session's own
work): a pure-Python CLI tool, no packaged distribution, mission set by
JP 2026-07-12 as "voice is a first-class communications channel for
driving coding agents... do one thing well first: voice-operate any
coding agent — before 'frontend any LLM anywhere.'" No vision, no voice
cloning, no BYO-raw-model chat mode — backend.name is exactly one of
opencode/claude-code/codex. Each backend adapter hand-speaks that CLI's
own native protocol, documented via extensive live probes (e.g.
`claude_code.py`'s module docstring: headless-mode permission-gate
behavior confirmed dead-air for 25s+ before diagnosis, `--permission-mode`
semantics measured per value, a PreToolUse-hook-based voice-gated
approval channel built and live-verified). Barge-in/echo handling is a
first-class, heavily-instrumented problem (`EchoAwarePlayer`,
`EchoTailGuard`, AEC delay auto-tuning, incident-capture/replay tooling,
`BargeInMonitor`'s two-axis interrupt-preset design) — including the
exact tail-of-response drop bug found and fixed live this same session
(PR #179), one live interrupt-timing edge case at a time.

## Mechanism

Both projects independently converged on the same core architecture
(cascaded VAD→STT→LLM→TTS pipeline, local-first, barge-in support,
explicit rejection of full-duplex speech-to-speech as the near-term
target) and the same broad problem space (voice control of coding
agents). They diverge on scope and depth, not premise:

- **OpenLive went broad**: vision, voice cloning, direct BYO-model chat,
  desktop packaging, a standardized cross-agent protocol (ACP), polish
  and public distribution (installers, 219 stars in under a month).
- **ConvoBox went narrow and deep on the hardest sub-problem in this
  space**: open-mic/speaker acoustic echo and barge-in timing, and
  rigorous, empirically-verified understanding of each backend CLI's
  actual runtime protocol behavior (not assumed from docs — measured
  against a real installed CLI, discrepancies documented inline).
  Nothing in OpenLive's README suggests comparable depth on either axis;
  its "Barge-in: interrupt any time" is one bullet, not a subsystem.

The one clear standardization gap: ConvoBox has three bespoke,
hand-written adapters (one per backend CLI's own protocol) with no path
to a fourth (Cursor, Hermes, ...) without repeating that work from
scratch. OpenLive's ACP approach covers five agents through one client
implementation. This is a real, portable idea — not something ConvoBox's
current adapter architecture gets "for free" the way the settings-editor
comparison in the 2026-07-28 note did.

## What transfers

- **(decided, JP 2026-07-29) Reaffirm the coding-agent-first niche.**
  OpenLive's broader scope (vision, voice cloning, raw-model chat) is
  explicitly NOT where ConvoBox is headed — same "do one thing well"
  rule JP set 2026-07-12, now confirmed against a real, more broadly-scoped
  competitor rather than in the abstract. Do not chase OpenLive's feature
  breadth.
- **(decided, JP 2026-07-29) Pursue ACP support**, inspired directly by
  OpenLive's use of it — see the new ROADMAP.md entry
  ("Agent Client Protocol (ACP) support") for the concrete scoping
  questions (which CLIs actually expose an ACP server today; whether
  ACP's permission primitives cover what the hand-built PreToolUse-hook
  approval channel already does live-verified; which adapters ACP could
  actually replace vs. where the bespoke protocol work still wins).
  Not yet scoped as an implementation task — this note and the ROADMAP
  entry are the record of the decision, not a design.
- **(reaffirmed, unchanged) VS Code / VSCodium editor integration**
  remains the mid-term target it already was per the 2026-07-12 roadmap
  ("editor as canvas, voice as channel") — JP's 2026-07-29 restatement
  ("eventually even a hook into VSCode, VSCodium... a voice driven dev
  environment") is continuity with that existing direction, not a new
  ask. ACP is the most likely mechanism it rides on, if it turns out to
  cover editor-navigation actions too (unconfirmed, first ACP scoping
  question).
- **(diagnosed) ConvoBox's actual differentiation today is depth, not
  breadth**: the echo/barge-in engineering and the live-verified
  per-backend protocol understanding are real, hard-won advantages that
  a broader competitor hasn't matched in its own published scope. This is
  worth protecting, not diluting, while ACP work is scoped.
