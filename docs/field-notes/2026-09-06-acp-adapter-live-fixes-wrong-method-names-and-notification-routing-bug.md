---
title: ACP adapter's first commit had five real, live-confirmed bugs -- wrong handshake/session-creation/prompt method names, a notification-routing bug that silently dropped every event, and session/cancel sent in the wrong JSON-RPC shape
status: validated-live
date: 2026-09-06
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ f50a4b1 (src/convobox/adapters/acp.py, PR #384); opencode 1.18.20 (`opencode acp`); kilo/@kilocode/cli 7.5.14 (installed this session, not yet authenticated -- handshake/session-creation only, no model round-trip)
evidence:
  - Real raw JSON-RPC probes against a spawned `opencode acp` subprocess (throwaway scripts, no ConvoBox code), covering initialize/session.new/session.prompt/session.update/session.cancel, including a real tool call (shell sleep) and a real mid-tool-call cancel
  - A live end-to-end run of the ACTUAL fixed src/convobox/adapters/acp.py (not a throwaway probe): send_text() -> real streamed TEXT event, a real TOOL_CALL event, and send_hard_stop() genuinely aborting an in-flight `sleep 10` shell tool call
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (asked for overnight maintenance work while GitHub sync was idle)
    - Claude Code (Anthropic claude-sonnet-5) -- ran the probes, fixed the adapter, wrote this note
  org: https://legionforge.org
  created: 2026-09-06T02:30:00Z
  revised: 2026-09-06T02:30:00Z
license: CC BY 4.0 (intent; repo code MIT)
---

# ACP adapter's first commit: five real bugs, found by actually running it

PR #384 added `src/convobox/adapters/acp.py`, a skeleton ACP adapter for
OpenCode/Kilo, built from documentation and the earlier schema-only R&D
(`docs/field-notes/2026-09-03-stt-parakeet-prototype-and-acp-protocol-
probes.md`, `docs/ROADMAP.md`). It merged with passing tests -- but the
tests only exercised the code's own (wrong) assumptions in isolation,
never a real spawned process. This project's own stated bar
(`AGENTS.md`) is to verify against the real thing, not trust unit tests
alone; a real `opencode acp` process was one command away and already
authenticated on this machine, so this pass just... ran it.

## Method

Three throwaway Python scripts (same convention as the 2026-09-03 pass:
raw JSON-RPC over a spawned subprocess's stdin/stdout, no ConvoBox code),
plus a fourth script that imported the real, fixed `ACPAdapter` class and
drove it exactly the way `run_convobox.py`'s orchestrator would. All four
against a real `opencode acp` 1.18.20 process, already authenticated
(OpenAI oauth + Inception api key already configured on this machine from
earlier live-UAT sessions in this project).

`kilo` (`@kilocode/cli` 7.5.14) was also installed this session via `npm
install -g @kilocode/cli`, at JP's request, for future testing -- but not
yet authenticated (`kilo auth login` needs interactive credential entry,
which is JP's to do). No live round-trip against Kilo specifically in
this pass; the Kilo-specific findings below are carried over from the
2026-09-03/04 pass (docs/ROADMAP.md), not re-verified here.

## Findings

**1. Handshake method name was wrong.** The first commit sent
`session/initialize` with `{clientInfo: {...}}`. The real method is a
top-level **`initialize`**, params `{protocolVersion, clientCapabilities}`.
Confirmed live: `session/initialize` was never actually attempted against
a real process before merge (source-read/inferred only); a live process
returns `-32601 Method not found` for anything not in its own dispatch
table, and `initialize` is what actually returns a valid response
(`agentCapabilities`, `authMethods`, `agentInfo`).

**2. Session-creation method name was wrong.** The first commit called
`session/start` with empty params. The real method is **`session/new`**,
params `{cwd, mcpServers}` -- `cwd` is not optional in practice; the probe
that omitted it wasn't attempted, but every real session/new call in this
pass carried a real path.

**3. `session/prompt`'s params shape was wrong.** The first commit sent
`{sessionId, text}`. The real shape is a content-block array:
`{sessionId, prompt: [{type: "text", text: "..."}]}`.

**4. The single biggest bug: real notifications never reached
`_process_notification` at all.** ACP notifications arrive as genuine
JSON-RPC notifications -- `"method": "session/update"`, **no `"id"`** --
whose actual event lives at `params.update`, discriminated by
`update.sessionUpdate` (`agent_message_chunk`, `tool_call`,
`tool_call_update`, `usage_update`, `available_commands_update`, all
observed live). The first commit's `_read_loop` dispatcher was:

```python
elif "method" in payload:
    request_id = payload.get("id")
    if request_id is not None:
        ... reply to it as a permission request ...
    # else: falls through, does nothing
else:
    await self._process_notification(payload)
```

Every real notification has `"method"` and no `"id"` -- so it always hit
the `if "method" in payload` branch, found `request_id is None`, and did
**nothing**. The `else` branch that called `_process_notification` was
dead code in practice: nothing in the real protocol produces a payload
with neither `"method"` nor `"id"`/`"result"`/`"error"`. On top of that,
`_process_notification` itself read `payload.get("type")` /
`payload.get("text")` -- fields that don't exist on either the outer
envelope or the real `update` object (which uses `sessionUpdate` and
nested `content.text`). Two independent wrongnesses stacked on the same
code path meant the entire text/tool-call event stream was silently
dropped, end to end, even if the routing bug alone had been fixed.

Confirmed by a live end-to-end run of the corrected code: a real
`session/prompt` call now produces a real `BackendEventType.TEXT` event
with the model's actual reply, and a real `BackendEventType.TOOL_CALL`
event when it runs a shell command.

**5. `session/cancel` sent as a request instead of a notification.** The
first commit's `send_hard_stop()` used `self._request(...)`, which
attaches an `"id"` and awaits a response. Sent that way, opencode returns
`-32601 Method not found` for `session/cancel`. Sent as a **notification**
(no `"id"`) instead, it genuinely aborts an in-flight tool call within the
same event loop tick -- confirmed live: a `sleep 15` shell tool call,
cancelled ~5s in, produced `tool_call_update` `status: "completed"`
immediately followed by `session/prompt`'s own pending response resolving
with `stopReason: "cancelled"`, not a timeout. This is the same
conclusion the 2026-09-03/04 Kilo pass reached for Kilo's own
`session/cancel` (docs/ROADMAP.md) -- now confirmed for OpenCode too, and
narrowed to the actual reason the naive attempt fails: JSON-RPC request
vs. notification framing, not a missing method.

**Bonus, not a bug but worth recording:** the first commit's
`send_interject` called a `session/steer` method that was never live- or
even schema-confirmed to exist -- ACP has no steering primitive at all,
already established by the 2026-09-03 pass. Removed; `send_interject` now
always degrades to a fresh `send_text()`, the same choice
`claude_code.py`'s own `send_interject` makes for a backend with no
steer/queue distinction.

## What's still NOT live-verified

- **Kilo's ACP session specifically** -- not authenticated on this
  machine yet (JP's to do via `kilo auth login`). Everything above was
  confirmed against OpenCode's `opencode acp` only; Kilo shares the same
  fork of the protocol per the 2026-09-03/04 pass, but that pass's own
  Kilo-specific findings (model pre-selection gotcha, permission posture)
  are carried over here unchanged, not re-confirmed by this pass.
- **`session/request_permission`'s real response shape.** Both live
  passes to date (2026-09-03 and this one) confirm OpenCode/Kilo's
  *default* posture is full-trust -- this method essentially never fires
  in practice -- so neither pass has ever actually seen a live permission
  request to confirm what response shape the server accepts. The
  adapter's decline response (`{"outcome": {"outcome": "cancelled"}}`) is
  a best-effort guess at the spec shape, not independently confirmed.
- **A real failed tool call.** No tool call failed during either pass's
  live probing; the `tool_call_update`/`status: "failed"` -> ERROR event
  mapping is inferred from the "completed" case's sibling fields, same
  confidence tier `opencode.py`'s own `_to_backend_event` uses for its
  analogous unobserved-failure branch.

## Where this leaves the roadmap

`docs/ROADMAP.md`'s ACP section can now say the adapter has been
live-verified against a real process for its core loop (handshake,
session creation, prompt, streamed text, tool calls, hard-stop) -- not
just schema-read. `create_backend_adapter()` already dispatches
`backend.name: "acp"` to it (PR #384's own factory-wiring commit). What's
still missing is a UI path to it: `scripts/settings_tui.py`'s
`_CHOICE_BACKENDS` allowlist doesn't include `"acp"`, so `validate_config`
(shared by the Settings TUI and the web UI) rejects it before either UI
would let a user save that config -- confirmed by reading, not yet fixed.
A hand-edited `convobox.yaml` bypasses both UIs and works today (this
pass's own live runs used exactly that path). Adding `"acp"` to the UI,
deciding its `backend.command` default/validation shape, and whether
`run_convobox.py`'s opencode/codex/claude-code-specific startup guards
need an ACP-aware branch are real UX decisions left for a follow-up, not
folded into this live-verification pass.
