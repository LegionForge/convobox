---
title: codex's app-server reports file changes in one shape ConvoBox can wire to the artifact pane, but nobody has watched it happen live yet
status: diagnosed
date: 2026-08-07
project: ConvoBox (github.com/LegionForge/convobox)
versions: codex-cli 0.146.1 (app-server JSON-RPC schema); ConvoBox main @ 20a9fa8
evidence:
  - codex app-server generate-json-schema (codex-cli 0.146.1) — FileChangeThreadItem schema
  - src/convobox/adapters/claude_code.py — existing ARTIFACT wiring this mirrors
  - docs/KNOWN-ISSUES.md, "Web UI: artifact pane gaps (0.3.0)" — the gap this closes half of
  - tests/test_codex_adapter.py, tests/fake_codex_appserver.py — unit coverage against a fake server
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, autonomous-loop session owner)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, implementation, writing
  org: https://legionforge.org
  created: 2026-08-07T06:30:00-05:00
  revised: 2026-08-07T06:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# codex's app-server reports file changes in one shape ConvoBox can wire to the artifact pane, but nobody has watched it happen live yet

**Context for outsiders:** ConvoBox is a voice interface that sits in front of
coding-agent CLIs (Claude Code, OpenCode, Codex) and, among other things,
renders any file the agent writes (an image, a plot, an HTML page) in a
web-UI side pane it calls the "artifact pane." Each backend CLI speaks its
own protocol, so each one needs its own adapter code that recognizes "a file
just got written" and turns it into ConvoBox's internal `ARTIFACT` event.

## Problem

`docs/KNOWN-ISSUES.md` has flagged since 0.3.0 that only the Claude Code
adapter (`src/convobox/adapters/claude_code.py`) actually does this —
opencode's event shape was unverified, and codex's app-server events "hasn't
been looked at" at all. A user driving ConvoBox against a codex backend gets
no artifact-pane behavior whatsoever, even when codex genuinely writes a
renderable file — this is the concrete case JP asked about directly ("tested
a few times with codex and wasn't working").

## Evidence

Running `codex app-server generate-json-schema` against the installed
codex-cli (0.146.1) and inspecting the bundle's `FileChangeThreadItem`
definition shows a completed file-change item arrives as:

```json
{
  "type": "fileChange",
  "id": "fc_1",
  "status": "completed",
  "changes": [
    {"path": "notes.md", "kind": "add", "diff": "+hi"}
  ]
}
```

delivered via the same `item/completed` JSON-RPC notification the adapter
already consumes for `TOOL_RESULT` events (`src/convobox/adapters/codex.py`).
`status` is one of `inProgress` / `completed` / `failed` / `declined` per the
schema — only `completed` items reflect a change that actually landed on
disk. Every changed file's **final** path/diff is present in that one
notification; there is no separate "did it actually get written" follow-up
message the way Claude Code's `Write`/`Edit` tool_use requires a matching
`tool_result` to confirm success.

This is schema evidence only — read from the JSON-RPC schema bundle codex-cli
ships, not observed from a real running `codex app-server` session. No live
codex session was driven to actually produce this notification shape during
this investigation.

## Mechanism

`CodexAdapter._resolve_artifact_writes` (added this session) hooks the
existing `item/completed` handling: when `item_type == "fileChange"` and
`item["status"] == "completed"`, it walks `changes`, keeps paths whose
extension is in `ARTIFACT_MEDIA_TYPES`, resolves each through
`_resolve_artifact_path` (same `working_dir`-fencing logic as
`ClaudeCodeAdapter._resolve_artifact_path` — reject anything that resolves
outside `working_dir`, independent defense-in-depth on top of the artifacts
route's own check), and emits one `BackendEvent(type=ARTIFACT, ...)` per
surviving path.

Unlike Claude Code's adapter, there's no staging-then-confirming across two
events — codex's `fileChange` item is self-contained and final by the time
`item/completed` fires, so the whole thing resolves in one pass. This is
architecturally simpler than the Claude Code path, not a shortcut; it follows
directly from codex's schema shape being different (batch of already-applied
changes) rather than an intent-then-result tool-call pattern.

Unit tests (`tests/test_codex_adapter.py`, fixtures added to
`tests/fake_codex_appserver.py`) cover: a completed change with a mix of
renderable/non-renderable/outside-`working_dir` paths (only the renderable
in-bounds one produces an `ARTIFACT`), a `failed`-status change (no event),
and no-`working_dir`-configured (no event) — all against a scripted fake
app-server, not a real `codex` binary.

## What transfers

- **diagnosed**: codex-cli 0.146.1's app-server schema shape for file
  changes is `FileChangeThreadItem{type, id, status, changes:[{path, kind,
  diff}]}`, delivered via `item/completed`.
- **diagnosed**: the wiring itself (extension allowlist + working_dir
  fencing) mirrors the already-live-verified Claude Code path closely enough
  to trust the code, but the connection from "codex-cli actually emits this
  shape at runtime" to "ConvoBox's parser handles it correctly" is
  unconfirmed — this is a **hypothesis under a schema-derived, unit-tested
  implementation**, not a live-verified fix.
- **Not done, deliberately**: no live codex session was run. The concrete
  next step for whoever picks this up for real UAT: start ConvoBox with a
  codex backend, ask it to write a renderable file (e.g. a PNG or an HTML
  page) into `working_dir`, and confirm the artifact pane actually opens it
  — specifically watching for whether real `codex app-server` output matches
  the schema-derived shape above, since generated schemas occasionally lag
  or diverge from what a given server build actually emits on the wire.
- opencode's half of this same gap (`file.edited` event shape) is still
  completely unaddressed — a separate investigation, not started here.
