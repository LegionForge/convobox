---
title: Claude Code's MCP tool permission is a separate gate from --permission-mode — permissive mode doesn't cover it without an explicit grant
status: validated-live
date: 2026-07-22
project: ConvoBox (github.com/LegionForge/convobox)
versions: claude (Claude Code CLI) headless --print mode, real account with 7 configured MCP servers (5 claude.ai-account connectors + 2 locally-configured)
evidence:
  - Live UAT session, 2026-07-22 11:41-11:43 (convobox-tui.log) — a real voice session stuck asking the user to "authorize the Obsidian MCP tools", user said "Authorized", nothing changed (no mechanism exists to grant this at runtime)
  - Live probe: `--permission-mode acceptEdits` alone still rejects an MCP tool call with "Claude requested permissions to use mcp__obsidian__get_workspace_files, but you haven't granted it yet"
  - Live probe: `--allowedTools mcp__obsidian__get_workspace_files` does NOT grant it either
  - Live probe: `--settings '{"permissions":{"allow":["mcp__*"]}}'` (bare wildcard) does NOT grant it
  - Live probe: `--settings '{"permissions":{"allow":["mcp__obsidian"]}}'` (exact server name, no tool suffix) DOES grant it — and grants every tool on that server, not just one
  - Live probe: `claude mcp list` enumerates all configured servers including claude.ai-account-level connectors (Gmail, Drive, Calendar, two NetSuite-backed connectors) that live outside any local config file, alongside locally-configured servers (a local Obsidian bridge, a local browser-automation server)
  - Live probe: OAuth-gated connectors ("Needs authentication") report needing the OAuth flow specifically, distinct from the plain "you haven't granted it yet" gate — permissions.allow cannot help those regardless
  - convobox src/convobox/adapters/claude_code.py's _enumerate_mcp_server_names / _ensure_mcp_permissions_settings_file
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT, reported the stuck session and the permissive-mode expectation gap)
    - Claude Code (Anthropic claude-sonnet-5) — diagnosis, fix, live verification, writing
  org: https://legionforge.org
  created: 2026-07-22T12:15:00-05:00
  revised: 2026-07-22T12:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# MCP tool calls have a separate permission gate from --permission-mode

**Context for outsiders.** ConvoBox drives Claude Code CLI headless, and
the CLI supports MCP (Model Context Protocol) servers — external tool
providers a session can call into (a personal notes vault, cloud
services, a browser automation bridge). This note is about a
Claude-Code-specific permission architecture surprise: `--permission-mode
acceptEdits` (or ConvoBox's own "permissive" — acts without asking)
turned out not to cover MCP tools at all, silently.

## Problem

A live voice session asked Claude Code (running under
`--permission-mode acceptEdits`, ConvoBox's "permissive" backend config)
to use its configured Obsidian MCP server. Claude Code replied asking
for permission; the user said "Authorized" out loud; nothing changed —
there is no runtime mechanism, voice or otherwise, to grant this in a
headless session. The turn itself didn't hang (a real, speakable result
came back: "the permission request is still pending... you'll need to
approve access... through your Claude Code settings or interface"), but
the operator's actual goal (read the vault) never happened, and
"permissive" mode's own definition ("acts without asking") silently
didn't hold for this class of tool.

## Evidence

```
$ claude --print ... --permission-mode acceptEdits < prompt-asking-for-obsidian-tool
TOOL_RESULT: Claude requested permissions to use mcp__obsidian__get_workspace_files,
             but you haven't granted it yet.
```

Tried and confirmed NOT sufficient:
- `--permission-mode acceptEdits` / `bypassPermissions`-adjacent modes alone
- `--allowedTools mcp__obsidian__get_workspace_files` (the specific tool name)
- `--settings '{"permissions":{"allow":["mcp__*"]}}'` (bare wildcard)

Confirmed sufficient:
```
$ claude --print ... --settings '{"permissions":{"allow":["mcp__obsidian"]}}' < same prompt
TOOL_USE: mcp__obsidian__get_workspace_files
TOOL_RESULT: <57KB of real vault content>
```

The exact server name (as `claude mcp list` reports it), with no tool
suffix, granting every tool on that server at once. Confirmed live
through the real adapter (not just a raw CLI probe) afterward.

`claude mcp list` (health-checks every server, ~3s on a real 7-server
account) is the only complete enumeration — it includes
account-level connectors configured through claude.ai itself (Gmail,
Drive, Calendar, two NetSuite-backed connectors on this account), which
live outside any local settings file this adapter could read directly,
alongside locally-configured servers (`~/.claude/settings.json`'s own
`mcpServers` key had only one of the seven).

Separately confirmed: the account-level connectors marked "Needs
authentication" in that listing require a real OAuth flow — the
`permissions.allow` grant is a no-op for those specifically; they were
never going to be fixed by this mechanism, and aren't.

## Mechanism

Best understanding, not confirmed against Claude Code's own source:
MCP tool authorization is tracked as a property of the tool/server
identity itself (an allow-list check), independent of the general
`--permission-mode` posture that governs built-in tools (Bash, Write,
Edit). The two systems don't compose — a mode that's fully permissive
for built-in tools has no bearing on MCP tools at all without a
separate, explicit grant naming the server.

## What transfers

- **`--permission-mode`/`acceptEdits`/`bypassPermissions` do not imply
  MCP tool access.** Anyone building a headless Claude Code integration
  that expects "permissive" to mean "everything works without asking"
  needs a separate `--settings` `permissions.allow` grant per MCP server
  name — checked and confirmed necessary specifically, not assumed.
- **The exact server name, not a wildcard, not the specific tool name.**
  `mcp__<server>` (no tool suffix) is what actually works; `mcp__*` does
  not, and naming individual tools via `--allowedTools` does not either.
- **Enumerate via `claude mcp list`, not a config file.** Account-level
  connectors have no local file representation this kind of integration
  can read directly; the CLI's own listing is the only complete source,
  at the cost of a real multi-second health-check round trip per
  server — worth caching once per session rather than re-running per
  spawned subprocess.
- **OAuth-gated servers are a genuinely different, unfixable-this-way
  gate** — don't spend effort trying to pre-grant those; the fix there
  is the real OAuth flow, out of band, once.
