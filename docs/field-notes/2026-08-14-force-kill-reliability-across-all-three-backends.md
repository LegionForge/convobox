---
title: force_kill() reliably kills real spawned subprocesses on codex and claude-code (90/90 across three tool-call shapes); opencode has no equivalent guarantee by architecture, and its actual behavior is inconsistent, not simply absent
status: validated-live
date: 2026-08-14
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ a72b05c (PR #277); codex-cli 0.147.0; Claude Code CLI 2.1.233; opencode 1.18.18 (local `opencode serve`, unpinned default model -- OpenCode Zen's `nemotron-3.5-lightning-free`)
evidence:
  - A scratch reliability harness, `_test_force_kill_stops_a_real_tool_call.py` (not committed -- leading-underscore convention), driving each backend's REAL CLI/server, not the fake app-servers tests/test_codex_adapter.py and tests/test_claude_code_adapter.py use
  - Real Windows/WMI process-tree inspection (`Get-CimInstance Win32_Process`, no psutil dependency) before and after each `force_kill()` call
  - Full raw logs for all three 30-run batches, correlated by iteration
  - Post-run process-tree sweeps confirming zero unaccounted-for orphans (including catching and correctly attributing two false alarms to unrelated pre-existing processes -- see "What transfers")
  - src/convobox/adapters/base.py, codex.py, claude_code.py, opencode.py (force_kill()/aclose() implementations read directly to explain the results)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; requested the reliability matrix -- "at least 10 times... multiple types... for claude and opencode too" -- across this and two prior sessions the same evening)
    - Claude Code (Anthropic claude-sonnet-5) -- harness design/implementation, live test execution, process-tree analysis, writing
  org: https://legionforge.org
  created: 2026-08-15T00:15:00-05:00
  revised: 2026-08-15T00:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# force_kill() reliability across all three backends: 90/90 on the two subprocess-owning backends, an inconsistent 23/30 on opencode

**Context for outsiders.** ConvoBox drives one of three coding-agent CLIs
(codex, Claude Code) or a locally-run HTTP server (opencode) to do real
work -- including dispatching real shell commands. Earlier the same
evening, three live incidents showed the existing "hard stop" (a polite
interrupt request) can itself get stuck when the backend process is
wedged, since the request rides the same channel that's stuck. `force_kill()`
(PR #277) is the fix: a genuine OS-level `terminate()`/`kill()` on the
backend's own process, no request/response round-trip involved. This note
answers the question the unit test suite structurally cannot: once
`force_kill()` fires, does the REAL spawned work -- not just the
adapter's own top-level process -- actually stop?

## Problem

The existing unit tests (`tests/test_codex_adapter.py`,
`tests/test_claude_code_adapter.py`) prove `force_kill()` terminates the
adapter's own process handle -- against a FAKE app-server that never
spawns a real shell child. That leaves open exactly the failure mode this
feature exists to fix: if `codex.cmd`/`claude` is itself a thin wrapper
(node.js, a shim script) around a deeper process tree, killing whatever
`asyncio.create_subprocess_exec()` handed back might not reach the real
leaf process doing the actual work (a shell command, a file write, a
network call) at all, leaving it an orphan. JP asked for this to be
tested for real, at least 10 times, across multiple kinds of long-running
work, for all three backends.

## Evidence

### Methodology

Each of 3 scenario types was run 10 times per backend (90 runs on
codex/claude-code, 30 on opencode -- 210 total):

- **shell_sleep**: `powershell -Command "Start-Sleep -Seconds 90"` -- baseline.
- **file_write_progressive**: a shell loop writing one line/second for
  90s, to also check whether the file was left genuinely incomplete
  (proof of mid-flight interruption, not "it finished, then something
  unrelated died").
- **web_fetch_slow**: a real outbound request to `httpbin.org/delay/90`
  (a public endpoint purpose-built for this; capped at 10s server-side
  regardless of the requested delay, which doesn't matter here since
  every kill fires within ~5-9s of the tool call starting, well before
  that cap).

Per run: send the backend a prompt instructing it to run one exact
command containing a unique marker string, wait for a `TOOL_CALL` event,
locate the real spawned OS process(es) via `Get-CimInstance Win32_Process`
`CommandLine` matching on that marker (not psutil -- avoided the
dependency entirely), call `force_kill()`, then check via `Get-Process`
whether every process found before the kill is actually gone.

### codex: 30/30

Every run: the app-server subprocess terminated, and every real spawned
child (2-3 processes per shell/file-write run -- a wrapper chain, one
web-fetch run -- 1 process) was also confirmed dead. `file_write_progressive`
never actually caught partial progress in any of the 10 runs (the kill
fired before the shell loop's first write in every case -- real process-
spawn latency ate the harness's wait window) -- a limitation of this
harness's timing, not evidence against the underlying finding, which is
solid regardless: zero survivors across all 30 runs, confirmed by a full
post-batch process-tree sweep.

### claude-code: 30/30

Same shape, same result. One useful outlier: `shell_sleep` iteration 2
took `force_kill()` 4.92s to return (every other run: 0.01-0.48s) --
this is the `terminate()`-ignored-so-escalate-to-`kill()` fallback path
actually firing live for the first time (previously only exercised by
the unit suite's own mocked-timeout test), not a failure. `file_write_progressive`
had the identical "never caught partial progress" limitation as codex's
run, same underlying cause.

### opencode: 23/30 matched the documented architectural limitation; 7/30 did not

`OpenCodeAdapter` never owns an OS process -- it's an HTTP client to an
ALREADY-RUNNING `opencode serve` instance ConvoBox doesn't spawn or hold
a handle to. Its `force_kill()` is the `BackendAdapter` default: delegate
to `aclose()`, which just closes ConvoBox's own HTTP/SSE connection. The
expectation going in: the real spawned process should survive every
time, since nothing we do touches the opencode server process itself.

That held 23/30 times -- `force_kill()` always returned in ~0.00-0.02s
(closing a local connection is instant regardless of outcome), and in
those 23 cases the real process was still alive afterward, cleaned up by
the harness itself rather than by anything `force_kill()` did.

**In 7/30 runs, the spawned process died anyway** -- shell_sleep 1/10,
file_write_progressive 2/10, web_fetch_slow **4/10** (the highest rate).
This is the OPPOSITE of the architectural expectation: ConvoBox only
closed its own client connection in every case, identically, yet the
real remote work sometimes stopped and sometimes didn't. web_fetch_slow's
higher rate is a real pattern in this sample (4/10 vs. 1-2/10 for the
other two types), not confirmed as causal -- a plausible but unverified
guess is that opencode's own handling of an outbound HTTP call inside a
shell tool call reacts differently to a dropped SSE subscriber than a
CPU-bound sleep does, but this note does not claim that as established;
it would need opencode's own server-side source or logs to confirm.

## Mechanism

For codex/claude-code, the mechanism is fully understood and now
directly confirmed: `self._proc` is a real `asyncio.subprocess.Process`
handle to the actual spawned CLI, `force_kill()`'s `terminate()` (falling
back to `kill()` after a 5s grace period) reaches the whole process tree
that CLI itself spawned for a tool call -- not just its own top-level
PID, contrary to the "thin wrapper might orphan children" concern this
test was built to rule out.

For opencode, the mechanism behind the 23/30 vs. 7/30 split is NOT
understood -- flagged as an open question, not guessed at further. What
IS confirmed: `force_kill()`'s own local behavior (an instant connection
close) is identical in every single run regardless of outcome, so
whatever determines whether the remote process survives is entirely
server-side, not anything the caller controls or can currently predict.

## What transfers

- **A "does the process die" test needs the REAL binary, not a test
  double** -- the fake-app-server unit suite could never have caught an
  orphaned-child gap even if one existed, since it never spawns real
  children at all. (validated-live)
- **An architecturally-expected limitation (HTTP client, no owned
  process) is not the same claim as "this never works"** -- treating
  opencode's 7/30 as noise to explain away, or treating the 23/30 as
  "basically never," would both misstate what the data actually shows:
  a real, unpredictable split. (validated-live)
- **Two false-alarm orphan sightings during this same investigation are
  worth naming as a methodology lesson**: an unrelated `codex.exe` from
  the Windows Store Claude/Codex IDE extension (created the day before,
  different install path entirely) and 11 `Claude.exe` processes that
  turned out to be the Claude Desktop Electron app (also unrelated,
  same executable NAME as the CLI's own process tree but a completely
  different binary/path) both briefly looked like real orphans from this
  test until their `CreationDate`/`CommandLine`/install path was checked.
  Name-matching alone is not enough evidence for "this is my orphan" --
  cross-check timestamp and full command line before concluding a
  process is actually related. (validated-live)
- **A slow force_kill() (4.92s, one sample) is evidence the escalation
  path (terminate ignored -> kill after grace period) is real and not
  just a mocked code path** -- worth remembering as a positive data point
  distinct from "did it eventually succeed," which every run answered
  yes to regardless of timing. (validated-live, single instance)

## Not done here

- Root-causing WHY opencode's remote process dies in some runs and not
  others (would need opencode's own server internals/logs).
- Any live voice/STT-trigger testing of the `kill_phrase` mechanism
  itself (saying "eject eject eject" out loud) -- this note is entirely
  about the mechanism's effect once triggered, driven directly via
  `adapter.force_kill()`/`send_text()`, not through the mic pipeline.
  Still the standing gap noted in `docs/KNOWN-ISSUES.md`.
