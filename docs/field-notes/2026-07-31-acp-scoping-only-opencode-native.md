---
title: "ACP scoping: only OpenCode exposes it as a first-party protocol today -- Claude Code and Codex only have third-party bridges"
status: diagnosed
date: 2026-07-31
project: ConvoBox (github.com/LegionForge/convobox)
versions: "opencode acp (native CLI subcommand, opencode.ai docs dated 2026-07-31); @agentclientprotocol/claude-agent-acp 0.64.0; codex-acp (agentclientprotocol org, separate zed-industries and cola-io forks also exist); ACP protocol ~v0.13.6"
evidence:
  - https://opencode.ai/docs/acp/
  - https://github.com/agentclientprotocol/claude-agent-acp
  - https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp
  - https://github.com/agentclientprotocol/codex-acp
  - https://github.com/zed-industries/codex-acp
  - https://github.com/cola-io/codex-acp
  - https://github.com/openai/codex/issues/9085
  - https://agentclientprotocol.com
  - docs/ROADMAP.md ("Agent Client Protocol (ACP) support" entry, 2026-07-29)
  - docs/field-notes/2026-07-29-openlive-comparison-and-acp-direction.md (prior note, posed this exact question)
  - src/convobox/adapters/claude_code.py, src/convobox/adapters/codex.py, src/convobox/adapters/opencode.py (current bespoke adapters this compares against)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, directed the scoping task while occupied with unrelated work)
    - Claude Code (Anthropic claude-sonnet-5) -- investigation, writing
  org: https://legionforge.org
  created: 2026-07-31T13:59:14-05:00
  revised: 2026-07-31T13:59:14-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# ACP scoping: only OpenCode exposes it as a first-party protocol today -- Claude Code and Codex only have third-party bridges

**Context for outsiders:** ConvoBox drives three coding-agent CLIs (Claude
Code, Codex, OpenCode) over each one's own native protocol, one bespoke
adapter per backend. The 2026-07-29 field note decided to pursue the
[Agent Client Protocol](https://agentclientprotocol.com) (ACP) as a future
single-adapter replacement, inspired by OpenLive, but left "which CLIs
actually expose an ACP server today" as the first unanswered scoping
question. This note answers it via web research (not a live-running probe
of each CLI -- see Mechanism for what that gap means for confidence).

## Problem

Before scoping any ACP implementation work, ConvoBox needs to know: does
`claude`, `codex`, and `opencode` each speak ACP natively, or would
adopting ACP mean depending on a third party's translation layer sitting
between ConvoBox and the agent? That distinction matters directly --
ConvoBox's Claude Code adapter's PreToolUse-hook-based voice-gated
approval channel and its Codex adapter's app-server JSON-RPC handling are
both live-verified, hard-won engineering (see each adapter's own
docstring). Replacing either with an unofficial bridge would trade a
first-party, already-verified integration for a community-maintained
translation layer of unknown maturity.

## Evidence

- **OpenCode**: [opencode.ai/docs/acp](https://opencode.ai/docs/acp/)
  is opencode's own documentation (not a third party's), dated
  2026-07-31 (today) at fetch time. States plainly: "OpenCode supports
  the Agent Client Protocol (ACP), allowing you to use it directly in
  compatible editors and IDEs." The command is `opencode acp`, described
  as starting "OpenCode as an ACP-compatible subprocess that communicates
  with your editor over JSON-RPC via stdio." Permissions: "OpenCode works
  the same via ACP as it does in the terminal," reusing its existing
  agents/permissions system. One documented gap: `/undo` and `/redo`
  slash commands are unsupported over ACP.
- **Claude Code**: no first-party ACP server found. The available
  package, `@agentclientprotocol/claude-agent-acp` (formerly
  `@zed-industries/claude-code-acp`, now migrated to the
  `agentclientprotocol` GitHub org), is maintained outside Anthropic and
  "implements an ACP agent by using the official Claude Agent SDK" --
  i.e. it wraps the separate Claude Agent SDK (TypeScript), not the
  `claude` CLI binary ConvoBox's `claude_code.py` adapter currently
  drives via stream-json NDJSON. It reimplements its own agent loop
  rather than shelling out to `claude`. Its docs mention "tool calls
  (with permission requests)" as a supported feature but do not detail
  whether that maps onto the same PreToolUse-hook semantics ConvoBox's
  adapter already exploits for voice-gated approval -- unconfirmed
  either way from the docs alone.
- **Codex**: no first-party ACP server found either. Multiple competing
  third-party implementations exist -- `agentclientprotocol/codex-acp`,
  a separate `zed-industries/codex-acp`, and `cola-io/codex-acp` -- all
  of which start the actual Codex App Server and translate ACP requests
  into Codex's own JSON-RPC operations (the same app-server protocol
  ConvoBox's `codex.py` adapter already speaks directly today). OpenAI's
  own tracker has an open, unresolved request for first-party ACP
  support: [openai/codex#9085](https://github.com/openai/codex/issues/9085).
  "Approval" and "sandbox mode" are listed as configurable via these
  bridges, but none of the fetched docs specify how they map onto
  Codex's own approval-policy values -- unconfirmed.

## Mechanism

The three backends split cleanly into two categories, not the uniform
"does it support ACP y/n" the roadmap item implied:

1. **OpenCode has ACP as a first-party protocol its own CLI speaks.**
   Adopting ACP for this backend would mean replacing ConvoBox's current
   HTTP/SSE adapter with a stdio JSON-RPC one, both maintained by the
   same upstream project. Lowest-risk of the three.
2. **Claude Code and Codex only have ACP via third-party bridge
   processes** that either reimplement the agent loop against a
   different SDK layer (Claude) or wrap the exact same app-server
   protocol ConvoBox already hand-verified (Codex). Adopting ACP for
   either would add a dependency on code Anthropic/OpenAI don't publish
   or support, of unverified maturity, sitting between ConvoBox and the
   agent -- for Codex specifically, replacing a direct, already-working
   integration with an indirect one that talks to the identical
   underlying protocol through an extra process hop for no clear gain.

This wasn't confirmed against a live-running instance of any of the
three bridges (no local install/run performed this pass) -- the status
above is diagnosed from each project's own published documentation and
GitHub metadata, not from driving a real ACP handshake. A future pass
should install `opencode acp` (lowest-risk, first-party) and drive one
real ACP session against it to confirm the permission-parity claim
("works the same... as it does in the terminal") empirically, the same
verification bar the rest of this codebase holds itself to.

## What transfers

- **(diagnosed) ACP adoption is not a single decision across all three
  backends** -- it's at minimum three separate ones, with OpenCode
  clearly the lowest-risk candidate (first-party, same maintainer,
  claimed permission parity) and Claude Code/Codex both currently
  requiring a third-party bridge of unverified maturity that doesn't
  obviously improve on the bespoke adapters ConvoBox already has
  live-verified.
- **(diagnosed) The Codex case is the weakest argument for ACP today**:
  the bridge just re-wraps the same app-server JSON-RPC protocol
  ConvoBox's own `codex.py` adapter already speaks directly, adding a
  process hop and a dependency on unofficial code without removing any
  bespoke-protocol work ConvoBox would otherwise carry.
- **(diagnosed) The Claude Code case needs its own follow-up question**
  before any implementation work: does `@agentclientprotocol/claude-agent-acp`
  wrapping the Claude Agent SDK (rather than the `claude` CLI) preserve
  the PreToolUse-hook-equivalent voice-gated approval ConvoBox's current
  adapter depends on, or would switching mean rebuilding that mechanism
  against a different SDK's primitives? Not yet answered -- next
  scoping step if Claude Code ACP adoption is still under consideration.
- **(hypothesis, not yet tested) OpenCode via `opencode acp` may be a
  low-risk, incremental first ACP adoption** -- swap one adapter, keep
  the other two bespoke, and use it as a real-world test of whether ACP
  delivers on its "works the same as the terminal" permission-parity
  claim before touching Claude Code or Codex at all. This is the
  concrete next step if ACP work continues, not yet scoped as an
  implementation task.
