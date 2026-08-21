# Permission model

**Canonical reference for `backend.permission_mode`.** This is the single
config key that decides what a coding agent driven by ConvoBox is allowed to
do. Other documents reference this one rather than re-explaining it.

ConvoBox assumes voice input is *less* trustworthy than keyboard input — STT
mishears, and an open mic picks up the room. The permission model is one of
the three mechanisms that follow from that assumption; the other two (the
spoken escalation ladder, and local-first data handling) are covered in
[SECURITY.md](SECURITY.md).

---

## The three modes

`backend.permission_mode` accepts exactly three values, validated at config
load. The default is `plan`.

| Value | Intent | What the agent may do |
|---|---|---|
| **`plan`** *(default)* | Read, explore, explain | No file edits, no commands. Investigation only. |
| **`approve`** | Act, but gated | Each risky call blocks on a spoken approval phrase before proceeding. |
| **`permissive`** | Act freely | **Bypasses all permission checks** on every tool call — Bash, WebFetch/WebSearch, MCP tools, file edits, everything. Opt-in and dangerous. |

`plan` is the default because a headless agent has no way to answer a
permission prompt at runtime. A mode that can act, with no channel to ask,
would be acting unsupervised by construction.

## How each mode reaches each backend

The three adapters have genuinely different permission machinery underneath.
ConvoBox maps one config key onto all three; the mapping is not obvious, so
it is spelled out here.

| Mode | Claude Code | Codex | OpenCode |
|---|---|---|---|
| `plan` | `--permission-mode plan` | `-c approval_policy=never`<br>`-c sandbox_mode=read-only` | *not applicable* |
| `approve` | `acceptEdits` **+** a ConvoBox-built `PreToolUse` hook and local IPC channel | `-c approval_policy=untrusted`<br>`-c sandbox_mode=workspace-write` | *not applicable* |
| `permissive` | `bypassPermissions` | `-c approval_policy=never`<br>`-c sandbox_mode=workspace-write` | *not applicable* |

**Claude Code** has no native per-tool-call approval channel in headless
mode, so ConvoBox builds one: a `PreToolUse` hook plus a loopback IPC channel
that blocks the call until a spoken decision arrives. See
`src/convobox/adapters/claude_code.py`'s module docstring for the mechanism
and its live verification.

**Codex** injects its overrides as `-c key=value` at spawn, which take
precedence over the user's own `~/.codex/config.toml` — so the posture is
ConvoBox's decision, not inherited from the user's Codex configuration. Codex
does have a real native approval channel (`execCommandApproval` /
`applyPatchApproval`), which `approve` mode drives.

**OpenCode has no tool-call approval concept at all.** Its permissions are
fixed by wherever `opencode serve` was launched, and ConvoBox does not pass
`permission_mode` to it. Setting the key for an opencode backend logs a
warning rather than silently pretending to apply. If you need to constrain an
opencode session, constrain it where you start the server.

---

## Two gotchas that have caused real problems

### 1. MCP tools are a separate gate on Claude Code

`permissive` does **not** cover MCP tool calls. Only an exact per-server
`permissions.allow` entry unlocks them — not a wildcard, and not
`--allowedTools`. And that grant is all-or-nothing: it grants the whole MCP
server at once, not individual tools within it.

This is a Claude Code behavior, not a ConvoBox one, and it is easy to
misdiagnose as a broken adapter. Live-verified in
[field-notes/2026-07-22-mcp-tool-calls-have-a-separate-permission-gate.md](field-notes/2026-07-22-mcp-tool-calls-have-a-separate-permission-gate.md).

### 2. `plan` blocks ConvoBox's own artifact-pane MCP tools

The web UI's artifact pane is driven by a small local MCP server that
ConvoBox hosts, giving the backend `show_document(path)` and
`get_shown_artifact()`. Under the default `plan` mode these are **reliably
blocked headless**, because `ExitPlanMode` doesn't work in `--print` mode —
so the agent can never leave plan mode to use them.

If you want the artifact pane, use `permissive` or `approve`. The design
intent recorded in [ARTIFACT-PANE-SCOPE.md](ARTIFACT-PANE-SCOPE.md) was that
these tools would be granted regardless of `permission_mode`; that intent is
not delivered under `plan`, and the tools should be treated as unavailable
there. No code fix is planned — see that document for the full reasoning.

---

## Guards that will stop you at startup

Three checks fail loudly rather than letting a safety control be quietly
wrong.

**Conflicting posture flags are a hard error.** `permission_mode` is the
single source of truth for the write/execute posture. If `backend.command`
also carries a flag that sets the same posture, ConvoBox refuses to start
rather than let two sources silently disagree (the failure case being
something like `permission_mode: plan` alongside
`--dangerously-skip-permissions`). Flags that trigger this:

- **claude-code:** `--permission-mode`, `--dangerously-skip-permissions`
- **codex:** `--sandbox`/`-s`, `--ask-for-approval`/`-a`,
  `--dangerously-bypass-approvals-and-sandbox`, and `-c` overrides of
  `approval_policy`, `sandbox_mode`, or `sandbox_permissions`

Tool-*scoping* flags (`--allowedTools`, `--disallowedTools`) are deliberately
**not** in that list — they are orthogonal to posture and compose fine with
any mode.

**`approve` without an approval phrase is a hard error.** Setting
`permission_mode: approve` for claude-code while `interaction.approval_phrase`
is unset wires the approval hook with nothing able to ever answer it. Left
unguarded, the first tool call hangs for its full 120s timeout behind a broken
spoken prompt, and every subsequent call is then silently auto-denied for the
rest of the session — with no log line either time. Set the phrase, or pick a
different mode.

**An unset working directory is a warning worth heeding.** With
`backend.working_dir` unset, the agent runs in ConvoBox's own directory and
can modify its source. ConvoBox warns at startup. Set it to an isolated
workspace. See [DESIGN-backend-sandboxing.md](DESIGN-backend-sandboxing.md).

---

## Choosing a mode

- **Learning the system, or any session you aren't watching:** `plan`.
- **Real work with a safety margin:** `approve`, with a distinctive
  multi-word `interaction.approval_phrase`. Plain "yes" is deliberately
  rejected as an approval phrase — it is far too easy to say accidentally or
  for STT to produce. Saying "no" denies; silence past
  `interaction.approval_timeout_s` (30s default) also denies, fail-closed.
  "Explain", "clarify", or "help" gets you a spoken explanation instead of a
  decision.
- **`permissive`:** only in a context you would trust an unsupervised agent
  with. Voice input can be misheard, and there is no per-action confirmation
  in this mode.

Whichever you pick, set `backend.working_dir` to an isolated workspace.
Permission mode limits what kinds of action the agent takes; the working
directory limits where those actions land. They are not substitutes.

---

## Related

- [SECURITY.md](SECURITY.md) — the full security and privacy model
- [DESIGN-backend-sandboxing.md](DESIGN-backend-sandboxing.md) — why
  `working_dir` matters and what sandboxing each backend offers
- [ARTIFACT-PANE-SCOPE.md](ARTIFACT-PANE-SCOPE.md) — the artifact-pane MCP
  tools and their `plan`-mode limitation
- [KNOWN-ISSUES.md](KNOWN-ISSUES.md) — including the `--text` mode +
  `approve` gap
- [field-notes/2026-08-11-permission-model-validation-claude-codex-opencode.md](field-notes/2026-08-11-permission-model-validation-claude-codex-opencode.md)
  — live validation of all three modes across all three backends
