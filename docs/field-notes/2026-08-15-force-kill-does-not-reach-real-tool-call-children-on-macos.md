---
title: force_kill() does NOT reliably kill real spawned tool-call children on macOS -- codex 0/10, claude-code 10/10, opposite of each backend's own Windows result
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3ad3a03 (PR #277); codex-cli 0.147.0; Claude Code CLI 2.1.224; macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - A scratch reliability harness, `_test_force_kill_macos.py` (not committed -- leading-underscore convention, matches the 2026-08-14 Windows harness), driving each backend's REAL CLI, not the fake app-servers tests/test_codex_adapter.py and tests/test_claude_code_adapter.py use
  - Real macOS process-tree inspection (`pgrep -f`, `ps -o pid,ppid,command`) before and after each force_kill() call -- no psutil dependency, same discipline as the Windows note
  - Two one-off diagnostic scripts isolating sandbox_mode as a variable (kept in /tmp, not committed)
  - src/convobox/adapters/base.py, codex.py, claude_code.py (force_kill()/_terminate_and_kill_process() implementations read directly to explain the results)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; this session's explicit handoff ask from the prior Windows session -- "reproduce force_kill() process-tree-kill reliability on macOS... the Windows 60/60 result does NOT establish this transfers")
    - Claude Code (Anthropic claude-sonnet-5) -- harness design/implementation, live test execution, process-tree analysis, writing
  org: https://legionforge.org
  created: 2026-08-15T00:45:00-05:00
  revised: 2026-08-15T00:45:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# force_kill() does NOT reach real tool-call children on macOS for codex; claude-code stays reliable

**Context for outsiders.** ConvoBox drives one of three coding-agent CLIs
(codex, Claude Code) or a locally-run HTTP server (opencode) to do real
work, including dispatching real shell commands. `force_kill()` (PR #277)
is a genuine OS-level `terminate()`/`kill()` on the backend's own process,
built after live incidents where the existing polite `send_hard_stop()`
got stuck riding the same channel as a wedged backend. A prior Windows
session (2026-08-14, `docs/field-notes/2026-08-14-force-kill-reliability-
across-all-three-backends.md`) found this reached real spawned children
90/90 across codex and claude-code. This session's explicit handoff ask
was whether that transfers to macOS, since Windows'
`terminate()`/`kill()` both map to the same `TerminateProcess()` call,
while POSIX `SIGTERM`/`SIGKILL` are genuinely different signals with no
built-in cascade to children -- and ConvoBox kills a single PID, not a
process group (`os.killpg()` appears nowhere in the adapters).

**It does not transfer, and not for the reason expected going in.** The
answer differs by backend, and by mechanism, not just by number:

## codex: 0/10 clean (0/5 shell_sleep, 0/5 file_write_progressive)

Every run's spawned shell child survived `force_kill()`. The mechanism
is NOT a slow/failed kill -- `force_kill()` still returned in
0.004-0.008s every time, identical to the clean Windows runs. The child
process was simply never reachable from the codex app-server's PID by
the time force_kill() (or even the harness's own pre-kill inspection)
ran:

```
PID  PPID STARTED                      COMMAND
4030     1 Sat Aug 15 00:29:23 2026     sh -c echo fkmac_...; sleep 90
```

`PPID=1` (launchd) -- already reparented away from codex, before
`force_kill()` fired at all. Isolated this to `sandbox_mode`: codex's
default `workspace-write`/`danger-full-access` sandboxing on macOS wraps
shell tool calls through Apple Seatbelt (`sandbox-exec`), and whatever
that wrapper does detaches the real leaf process from the app-server's
process tree essentially immediately -- confirmed this is real detachment,
not "it already finished" (the file-write scenario's 90-line
progressive-write file had 87/90 lines when force_kill() ran, so the
child was genuinely still alive and doing real work, just already
orphaned).

**Re-tested with sandboxing disabled** (`sandbox_mode=danger-full-access`,
`approval_policy=never`, same one-off diagnostic script) to isolate
whether sandboxing was the sole cause. It is not the sole cause:

```
PID  PPID COMMAND
23183 22501 sh -c echo fkmac_...; sleep 90
```

Here the child IS a real, live child of an intermediate codex process
(PPID 22501, not launchd) -- yet it **still survived `force_kill()`**.
This is the more fundamental finding: `force_kill()`'s
`proc.terminate()`/`proc.kill()` only signals the single top-level PID
`asyncio.create_subprocess_exec()` handed back. On POSIX, `SIGTERM`/
`SIGKILL` do not cascade to a process's children automatically -- that
requires either a process-group signal (`os.killpg()`) or the parent
itself forwarding the signal before it dies. codex's app-server does
neither. So even in the best case (sandboxing off, child still directly
parented), macOS's signal semantics alone are enough to leave the real
work running.

## claude-code: 10/10 clean (5/5 shell_sleep, 5/5 file_write_progressive)

Identical harness, identical scenarios, opposite result -- every spawned
child was confirmed dead ~1s after `force_kill()` returned (0.4-0.6s
kill duration, consistent with the Windows note's terminate-then-wait
path, not the escalate-to-kill fallback). Root cause of the difference
not fully isolated (would need reading claude-code's own signal-handling
source, out of scope here), but the process-tree evidence is consistent
with claude-code either not sandboxing shell tool calls the same way, or
itself forwarding `SIGTERM` to its own children before exiting -- either
of which would explain why the same `force_kill()` call reaches real
work on this backend but not on codex's.

## web_fetch_slow: inconclusive on both backends, same limitation as Windows

Both backends' `curl` calls to `httpbin.org/delay/9` frequently completed
or were never located before the harness's own polling window closed
(`pids_before=[]` in most runs) -- the same "real spawn latency eats the
harness's wait window" limitation the Windows note already named for its
file-write scenario. Not usable evidence either way; not recorded as a
pass or fail.

## Mechanism, summarized

| | sandboxed (codex default) | unsandboxed (codex, tested manually) | claude-code |
|---|---|---|---|
| Child still a real child of the process tree? | No -- reparented to launchd almost immediately | Yes | Yes |
| Survives `force_kill()`? | Yes (trivially -- nothing to signal) | **Yes** | No |

The unsandboxed row is the one that matters: this rules out "sandboxing
is the whole story." Even with a real, live, correctly-parented child,
signaling only the top-level PID is insufficient on macOS. Windows'
90/90 result cannot be explained by "the same code reaches the whole
tree on any platform" -- something about Windows' process/job semantics
(not investigated further here) made that true there; POSIX signal
semantics make it false here by default.

## What transfers

- **A platform-specific reliability claim does not generalize from one
  platform's process model to another's, even when the exact same code
  path is exercised** -- `force_kill()` is unchanged between the Windows
  and macOS runs; only the OS's process semantics differ, and that alone
  flips the result from 90/90 to 0/10 for the same backend
  (codex). (validated-live)
- **"The child got orphaned" and "the child stayed a child but the
  signal didn't reach it" are two distinct failure modes that look
  identical from the caller's side (`force_kill()` returns fast,
  process survives) but need different fixes** -- disabling sandboxing
  fixes the first, not the second. (validated-live)
- **A fast `force_kill()` return time is not evidence of success** --
  every failing run here returned in the same ~0.005s as the passing
  Windows runs; only the post-kill process-tree check distinguishes
  them. Any future dashboard/log surfacing `force_kill()` timing alone
  would be misleading. (validated-live)

## Not done here

- Confirming `os.killpg()` (process-group kill) as a fix -- attempted
  live via a one-off script but the diagnostic run stalled on an
  unrelated harness issue (stale leftover PIDs from a prior failed
  attempt sharing the same hardcoded marker, polluting the pgrep match)
  before producing a clean result; not re-attempted given the time
  budget for this session. The theory is strong (POSIX process-group
  kill is the standard fix for exactly this shape of problem) but
  UNCONFIRMED -- do not treat it as validated.
- Root-causing WHY claude-code's children stay reachable while codex's
  do not (sandboxing difference vs. signal-forwarding difference vs.
  something else) -- flagged as a real open question, not guessed at
  further.
- The VAD/mic freeze reproduction on macOS -- this session's other
  explicit ask, not started; see `!startup.md` for the standing request.
- Any live voice/STT-trigger testing of `kill_phrase` itself -- same
  standing gap as the Windows note.
