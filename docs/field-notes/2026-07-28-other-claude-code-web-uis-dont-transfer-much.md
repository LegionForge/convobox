---
title: General-purpose Claude Code web UIs are IDE-in-a-browser; ConvoBox's web UI is a voice-session companion -- most of their scope doesn't transfer
status: hypothesis
date: 2026-07-28
project: ConvoBox (github.com/LegionForge/convobox)
versions: "web UI v2 (feat/web-ui-v2, commits through 1382c53); surveyed siteboon/claudecodeui, sugyan/claude-code-webui, vultuk/claude-code-web, heng1234/claude-web, jakemor/kanna (READMEs only, not run live)"
evidence:
  - https://github.com/siteboon/claudecodeui
  - https://github.com/sugyan/claude-code-webui
  - https://github.com/vultuk/claude-code-web
  - https://github.com/heng1234/claude-web
  - https://github.com/jakemor/kanna
  - docs/WEB-UI-ARCHITECTURE.md
  - docs/WEB-UI-USAGE.md
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, standing instruction to research and attribute)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, writing
  org: https://legionforge.org
  created: 2026-07-28T12:45:00-05:00
  revised: 2026-07-28T12:45:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# General-purpose Claude Code web UIs are IDE-in-a-browser; ConvoBox's web UI is a voice-session companion — most of their scope doesn't transfer

**Context for outsiders:** ConvoBox is a voice-driven conversational layer
in front of a coding agent (OpenCode, Claude Code, or Codex) — you talk to
it, it talks back, and the coding agent does the actual file editing in its
own working directory. ConvoBox's own web UI (`src/convobox/web/`) is a
read-mostly-becoming-read-write browser companion to a *live voice
session*: transcript, tool calls, approvals, and (as of web UI v2) a
settings editor. This note asks whether the several existing open-source
"web UI for Claude Code" projects have functionality worth porting into
that companion view.

## Problem

JP's standing instruction (this autonomous session, 2026-07-28): survey
other Claude Code / coding-agent web UIs and consider implementing
comparable functionality, with attribution. Before writing any code, is
there real, portable functionality here, or does the comparison mostly
reveal that these are solving a different problem?

## Evidence

Surveyed (via GitHub search + README fetch, not run live — a live trial of
each is a separate, larger piece of work not done here):

- **siteboon/claudecodeui** ("CloudCLI") — the most feature-complete:
  interactive file tree with live editing, a Git explorer (stage/commit/
  branch-switch), an integrated shell terminal, a "Tools settings modal"
  to enable/disable individual Claude Code tools, MCP server management,
  a CLI picker (Claude Code / OpenCode / Cursor CLI / Codex), responsive
  desktop/tablet/mobile layouts, and an optional native companion app.
- **sugyan/claude-code-webui** — streaming chat responses; archived/
  unmaintained per the fetch result.
- **vultuk/claude-code-web**, **heng1234/claude-web** — token streaming,
  tool-call visualization, multi-session management; heng1234's README
  advertises a "checkpoint" concept (rewind/branch a session).
- **jakemor/kanna** — described as "a beautiful web-based UI for Claude
  Code & Codex," no deeper README detail fetched.

## Mechanism

Every one of these projects is, at its core, **a browser-based IDE
frontend for a coding-agent CLI**: file tree, in-browser file editing, Git
operations, a terminal, per-tool permission toggles, multi-project/
multi-session switching. That's the right shape for their stated job —
replacing or supplementing a terminal window running `claude`/`codex`
directly.

ConvoBox's web UI is not that, by design, and re-checking
`docs/WEB-UI-ARCHITECTURE.md`/`WEB-UI-USAGE.md` confirms this was already a
deliberate scope choice, not an oversight:

- ConvoBox never edits files itself — the backend coding agent does, in
  **its own** working directory (`backend.working_dir`), which
  `docs/DESIGN-backend-sandboxing.md`'s security model deliberately keeps
  separate from ConvoBox's own source. A file tree/editor in ConvoBox's
  web UI would mean either reaching into that sandboxed directory from a
  second, unauthenticated surface (widening the attack surface these
  projects' own file-tree/terminal features already accept for themselves)
  or duplicating a feature the backend CLI's own tooling already owns.
- ConvoBox has no terminal of its own to expose — the backend agent's
  shell access is already gated by `backend.permission_mode`
  (`plan`/`approve`/`permissive`) and the voice-driven approval-phrase
  flow. An in-browser terminal would be a second, parallel command-
  execution channel with a different (weaker: no-auth loopback) trust
  model than the one already carefully designed around voice approval.
- Multi-session/multi-project switching solves a problem ConvoBox doesn't
  have: there is exactly one live voice session and one backend agent
  process per `run_convobox.py` invocation; the existing session picker
  already covers "which *past* session's history am I viewing."
- Per-tool enable/disable and MCP server management are real,
  legitimate features — but they're the coding agent's own configuration
  (Claude Code's `--allowedTools`, MCP server list), not ConvoBox's. If
  this is ever wanted, it belongs behind `backend.command`'s existing
  passthrough, not a reimplementation inside ConvoBox's settings API.

**What genuinely is comparable** — the settings/permission surface — turns
out to already be a rough match: siteboon's "Tools settings modal" /
"Permission controls for agent capabilities" maps conceptually to
ConvoBox's `backend.permission_mode` + `interaction.approval_phrase` gate,
already shipped (web UI v2 slice 2b, `WebApprovalBridge`) with an arguably
stronger fail-closed design (silence denies, never implicitly approves).
No action needed there beyond what already exists.

heng1234's "checkpoint" (session rewind/branch) is the one idea with no
ConvoBox analogue at all — but it's a genuinely large feature (needs a
notion of replayable/branchable conversation state that doesn't exist
today) and wasn't investigated further here; flagged as a real *hypothesis*
for a future note, not something to build from this survey alone.

## What transfers

- **(hypothesis) PWA install-ability is a small, genuinely portable idea,
  not yet built.** Several of the surveyed projects lead with "mobile
  browser support" / "touch-optimized navigation." ConvoBox's own bubble-
  chat layout (v2 slice 1) is already mobile-texting-style and its
  buttons already target the >=44px touch guideline (per the Quit button's
  own commit message) — the one thing genuinely missing versus these
  projects is making the single static `index.html` file installable as a
  home-screen PWA (a `manifest.json` + a minimal service worker are still
  just static files, no build step, no framework — compatible with
  `WEB-UI-DEV.md`'s "don't reach for React/Vite/npm" constraint). Not
  implemented in this pass: `static/index.html` had an unrelated,
  substantial, uncommitted settings-UI edit in flight from a concurrent
  session at research time, and touching the same file to add
  `<link rel="manifest">` risked colliding with that in-progress work
  (see `!startup.md`'s 2026-07-28 update). Follow-up once that lands.
- **(diagnosed) Everything else surveyed — file tree, in-browser editing,
  Git explorer, terminal, multi-project/session switching, per-tool
  toggles, MCP management — does not transfer**, because ConvoBox's web UI
  is deliberately a voice-session companion, not an IDE frontend, and each
  of those features either duplicates something the sandboxed backend
  agent already owns, or would widen the no-auth loopback trust model for
  no corresponding ConvoBox-specific need. This is a scope confirmation,
  not a gap.
- **(diagnosed) The one real analogue (tool/permission controls) already
  ships**, and arguably more rigorously (voice-gated, fail-closed) than
  the compared projects' own modal-toggle approach.
