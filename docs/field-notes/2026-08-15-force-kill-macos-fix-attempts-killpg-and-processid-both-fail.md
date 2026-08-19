---
title: Two candidate fixes for codex's macOS force_kill() gap both fail (os.killpg(), the reported processId); a real termination RPC exists but targets a different execution path than ConvoBox uses; opencode confirmed to share Windows' "dies anyway sometimes" pattern
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, codex-cli 0.147.0, opencode 1.18.15 (localhost:4096, OpenAI oauth + Inception api), macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Prototype scratch scripts (not committed, leading-underscore convention) monkeypatching CodexAdapter's spawn/kill paths to test start_new_session=True + os.killpg()
  - Direct JSON-RPC notification interception (_handle_notification monkeypatch) to inspect codex app-server's raw item/started commandExecution payloads, including the undocumented-in-ConvoBox processId field
  - os.getpgid()/os.getpid() process-group forensics, same discipline as this session's earlier field notes
  - A real opencode serve instance on this machine (authenticated, unlike the Windows session's opencode which had zero credentials configured) -- 15 runs across three scenarios
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked to keep testing "new combinations" and "solve" the macOS force_kill() gap after this session's first field note left os.killpg() as an untested candidate)
    - Claude Code (Anthropic claude-sonnet-5) -- prototyping, live process/protocol forensics, writing
  org: https://legionforge.org
  created: 2026-08-15T01:30:00-05:00
  revised: 2026-08-15T01:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Two candidate fixes for codex's macOS force_kill() gap both fail; opencode confirmed consistent with Windows

**Context.** This session's earlier field note
(`2026-08-15-force-kill-does-not-reach-real-tool-call-children-on-
macos.md`) found codex 0/10 on macOS and named `os.killpg()` as "a
strong untested candidate fix." This note tests it directly, tests a
second candidate (using codex's own protocol-reported PID), and fills
the opencode gap the earlier note also left open (that session's local
opencode had zero credentials configured; this machine's does).

## Candidate fix 1: `start_new_session=True` + `os.killpg()` -- FAILS

**Hypothesis**: spawn the codex app-server with `start_new_session=True`
(making it its own process-group leader), then on `force_kill()`, signal
the whole group via `os.killpg()` instead of just the top PID. If the
real shell child inherits the app-server's process group (the normal
POSIX fork() default when no one calls `setsid()`), this reaches it.

**Result: 0/5, unchanged from the original 0/10.** Root cause, confirmed
directly via `os.getpgid()` on both the app-server and the real spawned
child:

```
sandboxed (workspace-write):    app-server pgid=48055  child pgid=48727 (== child's own pid)
unsandboxed (danger-full-access): app-server pgid=48836  child pgid=49499 (== child's own pid)
```

**In both cases the real child is its own session leader** (`pgid`
equals its own `pid`), completely independent of the app-server's
process group -- confirmed true even with sandboxing OFF, which rules
out Apple Seatbelt as the mechanism. **This is codex's own process-
spawning behavior on macOS**, not a sandboxing artifact and not
something `os.killpg()` on ConvoBox's side can ever reach, regardless of
what flags ConvoBox passes to its own `create_subprocess_exec()` call --
the isolation happens on codex's side of the fork, after ConvoBox has
already lost control.

## Candidate fix 2: codex's own reported `processId` -- FAILS (mismatched PID, not a real kill target)

While inspecting raw JSON-RPC traffic to understand candidate 1's
failure, found something ConvoBox's adapter currently discards entirely:
`item/started` events for `commandExecution` items include a real-looking
field:

```json
{"type": "commandExecution", "processId": "59972", "source": "unifiedExecStartup",
 "command": "/bin/zsh -lc \"sh -c 'echo pidcheck1; sleep 5'\"", ...}
```

This looked like the missing piece -- codex telling ConvoBox exactly
which PID to target, sidestepping the process-group problem entirely.
**It is not.** Cross-checked the reported `processId` against the real
spawned process located independently via `pgrep -f <unique marker>` in
the same run: **they never matched** (e.g. reported `53175` vs. actual
`52011`). A direct kill attempt against the reported PID raised
`ProcessLookupError` (`ps -p <reported-pid>` found nothing at all) --
the reported PID does not correspond to any live process on this
machine by the time the tool call is actually running long enough to
matter. The `"source": "unifiedExecStartup"` field name suggests this is
an internal identifier from codex's own "unified exec" subsystem, likely
the PID of a short-lived spawner/wrapper stage that has already exited
and possibly been reused by the OS for something else entirely, not a
stable handle to the actual long-running leaf process. **Do not build
anything that trusts this field as a kill target without codex's own
documentation confirming what it actually identifies** -- this session
found it empirically unreliable, not just unused.

## opencode on macOS: confirms the Windows "dies anyway sometimes" pattern, not just the architectural expectation

Unlike the Windows session and this session's own earlier VAD-freeze
work (which found no local opencode credentials configured), this
machine's `opencode serve` is authenticated (OpenAI oauth + Inception
api) and directly testable. 15 runs across three scenarios (same
methodology as the codex/claude-code harnesses):

| scenario | clean | survivor |
|---|---|---|
| shell_sleep | 0/5 | 5/5 |
| file_write_progressive | 2/5 | 3/5 |
| web_fetch_slow | 3/4 (1 run incomplete, harness timeout) | 1/4 |

Architecturally expected: `OpenCodeAdapter.force_kill()` is the
`BackendAdapter` default (`aclose()` -- close the local HTTP/SSE
connection only), so the real remote process should survive every time.
**Confirmed NOT always true here either** -- at least one `web_fetch_slow`
run showed the process present before the kill attempt and gone
afterward, despite `force_kill()` doing the exact same local-connection-
close regardless of outcome (kill duration ~0.001-0.005s every time, no
correlation with outcome). This matches the Windows note's own 23/30
finding almost exactly in shape (mostly survives, unpredictably doesn't)
-- opencode's kill behavior is consistently unpredictable across both
platforms, for reasons this session did not investigate further (would
need opencode server-side internals/logs, same limitation the Windows
note already named).

## What transfers

- **A process-group id equal to its own pid is the single clearest
  signal that a process is its own session leader, independent of its
  apparent parent** -- worth checking directly via `os.getpgid()`
  whenever a kill mechanism seems to mysteriously not reach a real,
  confirmed-alive child. (validated-live)
- **A protocol field that looks like exactly the right data (a PID) is
  not automatically trustworthy** -- cross-check it against independent
  ground truth (here, `pgrep` on a unique marker) before building
  anything on it. This field's own name (`unifiedExecStartup`) was a
  hint worth taking more seriously before assuming it meant what it
  looked like. (validated-live)
- **An architecturally "should never work" result and an architecturally
  "should always work" result can both turn out to have the same
  unpredictable middle ground** -- opencode's macOS behavior (mostly
  survives, sometimes doesn't) is now confirmed on two separate
  platforms with two separate credential setups, making it much more
  likely a real, reproducible characteristic of opencode's own server
  rather than a fluke of either environment. (validated-live)

## A real cancellation RPC exists -- but for a different execution path than ConvoBox uses

Checked `codex app-server generate-json-schema --out <dir>` (not tried
in this investigation until now): codex's v2 protocol DOES define
`command/exec/terminate` (`CommandExecTerminateParams` ->
`CommandExecTerminateResponse`), a real, dedicated termination RPC --
not a process signal, an actual protocol-level cancel.

**It does not apply to ConvoBox's current usage as-is.** Reading
`CommandExecParams`'s own schema alongside it: `command/exec` is a
**standalone** RPC ("run a standalone command... without creating a
thread or turn") completely separate from the turn/agent-loop flow
ConvoBox actually drives (`turn/start` with a text prompt, letting
codex's own agent decide when to run shell commands as `commandExecution`
items mid-turn -- `"source": "unifiedExecStartup"` in the raw payload
above is this second, different path). `command/exec/terminate` requires
"the **client-supplied**, connection-scoped `processId` from the
original `command/exec` request" -- i.e. it only terminates a command
ConvoBox itself explicitly started via `command/exec`, with an id
ConvoBox itself chose. It has no defined relationship to the
`commandExecution` items that appear inside an ordinary agent turn,
which is the entire mechanism `force_kill()` is trying to reach.

**What this means for a real fix**: not a drop-in kill call. Using this
RPC would mean ConvoBox routing agent-initiated shell work through the
standalone `command/exec` surface instead of letting codex's own agent
loop dispatch it -- a materially different integration shape, likely
requiring the agent to be told to use this mechanism (if that's even
configurable) rather than its own default tool-calling behavior. Flagged
as a real, promising lead for a future redesign discussion, explicitly
NOT something to build unscoped tonight.

## Not done here / open

- No working fix for codex's macOS force_kill() gap has been found yet
  for the CURRENT integration shape (agent-initiated `commandExecution`
  items inside a turn). Remaining untested ideas: (a) whether
  `command/exec`'s standalone surface could be adopted for ConvoBox's own
  tool-call dispatch (see above -- a real, scoped design question, not a
  quick patch); (b) whether matching
  `ps`/`pgrep` output against the `command` field codex DOES report
  correctly (confirmed accurate in the raw payload above) could work as
  a last-resort, best-effort kill -- fragile (a startup-time race, and
  matching arbitrary shell text is inherently imprecise) but possibly
  better than nothing; (c) whether a newer/older codex-cli version
  reports a real, live PID (this session used 0.147.0 specifically).
- opencode's root cause for the unpredictable "dies anyway" pattern
  remains unexplained on both platforms now -- still needs opencode's
  own server internals or logs, not available to this investigation.
- Did not check whether claude-code's own protocol has an equivalent
  processId-shaped field, or whether it needs one at all given it
  already scores 10/10 without this kind of intervention.
