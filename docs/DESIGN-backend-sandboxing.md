# Design: backend sandboxing & workspace isolation

Status: living design. Origin: a live UAT session (2026-07-18/19) in which
a voice conversation drove the Codex backend to **silently modify
ConvoBox's own source** while the operator was testing ConvoBox. This
document is the threat model and the layered mitigation plan.

## The problem

ConvoBox is a voice frontend for coding-agent CLIs. Its whole purpose is
to let an agent *do things* — and a coding agent's core capabilities are
**writing files and running shell commands**. That makes every backend
root-adjacent by default (per the LegionForge security lessons: any tool
that can write files AND execute on your host is root-adjacent until
proven otherwise).

Three properties made the incident dangerous, and generalize to any voice
frontend for a coding agent:

1. **The agent inherited ConvoBox's own working directory.** The adapter
   spawned Codex with no `cwd`, so it operated in — and edited — the
   product's source tree.
2. **It happened silently and by default.** No approval prompt, no notice;
   the operator discovered it via `git status`.
3. **It happened during the test that was validating that source.** A
   backend rewriting the safeword / approval / AEC code mid-UAT invalidates
   the test and can alter safety-critical behavior with no gate.

Prompt injection amplifies all three: a backend reads files and web
content, and injected instructions can steer edits.

## The mitigation, in layers (defense in depth)

No single control is sufficient; they compose.

### Layer 1 — Workspace isolation (WHERE the agent operates) — shipped

`backend.working_dir` (config) + `--working-dir`/`-d` (CLI) set the
directory the spawned agent runs and edits in, passed as the subprocess
`cwd`. Point it at an **isolated workspace** separate from any repo you
care about, and the agent's edits land there, not on your source. A
startup check errors on a nonexistent directory and warns loudly on the
dangerous cases (unset, a directory inside ConvoBox's own repo, or set for
opencode where it has no effect). Shipped in PR #102.

This fixes the **accident** — "it edited the source because that's where
it happened to be launched." It does **not**, by itself, stop a determined
or prompt-injected agent from using `../` or absolute paths to climb back
out. That needs Layer 2.

### Layer 2 — Sandbox write posture (WHETHER the agent may write) — per-agent config

Whether the agent can write at all, and where, is governed by the agent's
own sandbox:

- **Codex**: its sandbox/approval policy (`~/.codex/config.toml` / launch
  flags). A read-only posture blocks writes outright; an "ask on every
  write" posture makes ConvoBox's adapter auto-decline (see below) actually
  engage. Recommended default for conversational/UAT use: read-only.
- **Claude Code**: ConvoBox already defaults to `--permission-mode plan`
  (nothing executes/writes) unless the caller overrides it, and
  `--disallowedTools` can remove Bash/Write/Edit entirely — see
  `claude_code.py` and `docs/DESIGN-0.3.0-interaction-and-safety.md`.
- **opencode**: its directory and permissions are fixed by where
  `opencode serve` was launched; ConvoBox does not spawn it.

Key nuance learned live: **ConvoBox's approval auto-decline is a backstop
that only fires when the agent ASKS.** Codex's adapter declines
`requestApproval`/`applyPatchApproval`, but under a write-enabled sandbox
Codex edits the workspace *without asking*, so the backstop never triggers.
The sandbox posture, not the decline, is what makes read-only real.

#### A unified write-posture control (recommended shape)

Each backend expresses "can it write?" differently (Codex `--sandbox`,
Claude Code `--permission-mode`, opencode server-side permissions). Rather
than three separate flags, ConvoBox should expose **one backend-agnostic
control** and translate it per-backend:

- **Config:** `backend.allow_edits: false` (the safe default).
- **CLI:** a matching `--allow-edits` opt-in (and/or `-ro`/`--read-only`
  for symmetry / explicitness).
- **Translation, at spawn time:**
  - Codex → inject the read-only sandbox flag/policy unless `allow_edits`.
  - Claude Code → already defaults to `--permission-mode plan` (read-only);
    `allow_edits` would relax it. No new mechanism needed.
  - opencode → cannot be enforced per session (its permissions are fixed at
    `opencode serve` launch); warn rather than pretend.

**Recommended default: read-only.** A voice conversation should not be able
to mutate anything unless the operator explicitly opted into an editing
session — and that editing session is where the approval phrase and the
visible work-mode indicator belong. This is a deliberate default-behavior
decision (it changes what an existing user's session can do), so it is
flagged as the operator's call, not something a refactor decides silently.

One binary (read-only vs allow-edits) is enough for the safety-relevant
line; Codex's finer sandbox taxonomy (read-only / workspace-write / danger)
can be surfaced later if a real need appears, but exposing it by default
would trade the clarity of "can it write, yes or no" for configuration
sprawl.

### Layer 3 — Process sandbox (blast radius) — future

For real isolation of a backend with shell access, run it in a container
or a constrained user account: workspace mount only, no credential stores
(`~/.ssh`, `~/.codex`/`~/.claude` creds beyond what it needs, the Obsidian
vault), egress only where needed, cap-drop, non-root. This is the
LegionForge "container-first for agents with shell access" pattern. Turns
"the agent can reach my whole machine" into "the agent can reach a box."

## The development pipeline (isolation + version control + human gate)

The intended workflow that these layers enable:

```
  [ ConvoBox voice session ]
            |
            v  (backend.working_dir = an isolated workspace)
  [ Codex/agent edits in  ../convobox-UAT  ]   <- disposable, version-controlled
            |
            v  (review + promote, human-gated)
  [ Claude reviews the diff, opens a PR to the real repo ]
            |
            v  (CODEOWNERS -> JP approves)
  [ LegionForge/convobox main ]
```

- The agent develops in an **isolated, version-controlled workspace**,
  never the canonical source.
- **Promotion to the real repo is human-gated** — reviewed and PR'd, never
  a direct write. (This is the same collective-work discipline as
  `AGENTS.md` rule 10, with an autonomous agent as one participant.)
- The two are **separate directories under separate version control**, so a
  bad edit in the workspace is disposable and can never silently reach the
  product.

## Mode separation (future)

A first-class ConvoBox mode, surfaced visibly (like the WAITING-FOR-YOU
status):

- **converse / read-only** (default): the backend cannot mutate anything.
  For normal conversation and UAT.
- **work / allow-edits** (explicit opt-in, gated by the approval phrase):
  the backend may edit its workspace. Never the default.

Paired with a **backend action audit** — every file write / command the
backend performs surfaced live and out loud, never silent. "The pile
appeared" should have been "Codex wants to edit `run_convobox.py` —
approve?" spoken aloud. The approval machinery (Whiskey-Tango-Foxtrot) is
part of this answer, extended to gate **file writes**, not just shell.

## Recommended UAT setup (today)

1. Keep the source repo (`.../ConvoBox`) as canonical; never launch UAT
   from it.
2. Create a scratch workspace, e.g. `../convobox-UAT` (its own git repo, or
   at least a directory you don't mind the agent rewriting).
3. Set `backend.working_dir: ../convobox-UAT` (or pass `--working-dir`).
4. Set the agent's own sandbox to read-only for pure conversation/UAT;
   switch to write only when you deliberately want it developing, and keep
   promotion human-gated.

## Status

| Control | Layer | State |
|---|---|---|
| `backend.working_dir` / `--working-dir` | 1 (location) | shipped (PR #102) |
| Startup warnings for dangerous working dirs | 1 | shipped (PR #102) |
| Claude Code `--permission-mode plan` default | 2 (write posture) | shipped |
| Unified `backend.allow_edits` + `--allow-edits`/`-ro` control | 2 | planned (recommended shape above) |
| Codex read-only sandbox default | 2 | operator config; ConvoBox-side default TBD |
| ConvoBox sets agent sandbox posture explicitly | 2 | planned |
| Process sandbox (container/constrained user) | 3 | planned |
| converse/work mode separation | — | planned |
| Backend action audit (spoken/logged writes) | — | planned |
