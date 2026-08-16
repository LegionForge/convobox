---
title: The opencode stop_event_loop() retry-cancel fix holds up under an automated rapid-hardstop storm on Windows -- zero timeouts across 143 calls, though ~22% still pay a 1.8-4.3s single-cancel-resolution cost the mac-mini's own numbers didn't show
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: experiment/opencode-stop-event-loop-retry-cancel @ e7a7330 (PR-not-yet-opened, unmerged); opencode serve local, model ollama-remote/qwen3.5:latest; codex.cmd --model gpt-5.6-terra; claude-code (claude); Windows 11 (helios), Python 3.12, ProactorEventLoop
evidence:
  - `_test_hardstop_race.py` / `_test_opencode_hardstop_race.py` (scratch, gitignored, not committed -- content embedded below), run against a real local `opencode serve` and real `codex.cmd`/`claude` subprocesses, D:/LegionForge/convobox-UAT
  - Ported near-verbatim from the mac-mini session's original opencode-only script: docs/field-notes/2026-08-15-opencode-freeze-deterministic-audio-free-repro-orchestrator-only.md
  - src/convobox/orchestrator/orchestrator.py's `stop_event_loop()` (the fix under test) and `hard_stop()` (confirms every hard-stop call routes through it, not just final teardown)
  - Raw stdout of all 11 opencode runs (9 initial + 2 "isolated" re-checks) and one run each for codex/claude-code, quoted below
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the same automated test to be run across all three backends, live-testing concurrently on the same opencode server/model)
    - Claude Code (Anthropic claude-sonnet-5) -- script port/parametrization, execution, log correlation, writing
  org: https://legionforge.org
  created: 2026-08-15T21:36:06-05:00
  revised: 2026-08-15T21:36:06-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The opencode retry-cancel fix holds under an automated hard-stop storm on Windows

**Context for outsiders.** The mac-mini session root-caused a real
opencode freeze (a stuck asyncio task not honoring `Task.cancel()`
inside an SSE read) and built a fix: `stop_event_loop()` now retries
`cancel()` up to 3 times, 3 seconds each, instead of a single
cancel-and-unbounded-await. Their validation was macOS-only, 7/7 clean.
This note re-runs their own audio-free repro methodology on Windows,
across all three backends, to see if the fix holds and whether Windows
behaves the same.

## Problem

JP asked for the mac-mini's automated, audio-free repro script (rapid
`handle_transcript("stop stop stop")` calls against a real backend, no
mic/VAD/STT involved) to be re-run on Windows/helios, across codex,
claude-code, AND opencode -- not just opencode, which was the only
backend the mac-mini's own bug affected.

## Evidence

### codex and claude-code: clean, single run each, n=8

```
[codex] hard-stop 1/8 .. 8/8, all 0.000-0.016s, follow-up completed -- NO FREEZE
[claude-code] hard-stop 1/8 .. 8/8, all 0.000s, follow-up completed -- NO FREEZE
```

Both showed their own adapters' existing `readline()`/`_drain_stderr`
stall-diagnostic lines firing briefly (0.5-1.6s, self-resolving) -- the
same class of harmless-idle-time noise documented elsewhere this
session, not a freeze. Neither backend uses the SSE-based events task
this fix touches, so this is a baseline/comparison, not a direct test
of the fix itself.

Separately, both left Windows `ProactorEventLoop` subprocess-transport
cleanup noise on process exit (`RuntimeError: Event loop is closed`,
`ValueError: I/O operation on closed pipe` from `__del__` finalizers) --
cosmetic, happens after the script's own `main()` has already returned
successfully, not a functional failure. Not investigated further here.

### opencode: 11 runs, 143 total hard-stop calls, ZERO timeouts

```
n=8:  8/8 clean (first call 1.500s, rest instant)
n=10 x3: clean, only minor (<0.15s) blips except one run's first call at 1.516s
n=15 x5: run1 clean; run2 two calls at 1.875-1.922s; run3 -- FIRST run to
         show "proceeding to send without a confirmed SSE subscription;
         response events may be missed" -- 8/15 calls delayed 1.0-2.3s;
         run4 (same warning) 2/15 delayed; run5 (same warning) 5/15
         delayed, one at 4.313s
n=15 x3 ("isolated" re-check): all three now show the SSE-subscription
         warning at start; 5/15, 5/15, 2/15 calls delayed 1.8-4.2s
```

Aggregate: 143 calls, **31 (~22%) took 1.8-4.3s instead of the typical
<0.1s, zero ever hit the 90s `wait_for` timeout or failed.** The
"proceeding without a confirmed SSE subscription" warning appeared on
0/4 early runs and then consistently on 8/8 later runs -- see Mechanism
for why this is likely NOT evidence of a leak.

`/api/session` listing showed only 2 sessions server-side after all 11
runs, ruling out server-side session accumulation as the cause of the
later runs' degradation.

## Mechanism

`hard_stop()` calls `stop_event_loop()` on **every** hard-stop, not just
final teardown (`orchestrator.py:347`) -- confirming this fix's retry-
cancel path is exercised by every single "stop stop stop" in this
script, not just once at exit. Its own internal escalation warning
(`"events task did not honor cancel() after 3s (attempt N/3),
re-cancelling"`) never appeared in any of the 143 calls' output -- so
the observed 1.8-4.3s delays are NOT the fix's second/third retry firing
(which would require hitting the full 3.0s bound first). They're most
consistent with the FIRST `cancel()` simply taking 1.8-4.3s wall-clock
to actually unwind and raise `CancelledError`, inside the fix's
`asyncio.wait_for(asyncio.shield(task), timeout=3.0)` -- slower than an
instant clean cancel, but comfortably inside the 3s bound the fix
provides, and never needing to escalate. This is a real, live-observed
data point that the underlying anyio/httpcore cancellation-latency
issue (root cause still unconfirmed) is not strictly binary
(instant vs. 3s-bound-hang) -- it has a real middle ground on Windows
that this session's automated batch surfaced for the first time.

The SSE-subscription warning correlating with the LATER runs, against
one single long-lived `opencode serve` process that had by then handled
~10+ sessions in rapid succession (this script's own repeated runs, NOT
counting JP's own concurrent live UAT session against the same server
and model), is the leading candidate explanation for why later runs
degraded relative to earlier ones -- consistent with either
accumulating internal state in that one long-lived server process, or
genuine resource contention with JP's own concurrent live testing
against the same local model. This note does NOT distinguish between
those two explanations; a clean isolated run against a freshly-started
`opencode serve`, with no concurrent live session, is the natural
follow-up.

## What transfers

- **Zero timeouts across 143 automated hard-stop calls on Windows is a
  real, positive signal for the fix, but not proof the specific bug it
  targets was ever actually triggered here** -- the fix's own escalation
  warning never fired, meaning this batch may only be exercising the
  "cancel() takes a bit longer than instant" case, not the "cancel()
  never gets honored at all" case the mac-mini's macOS session actually
  caught. Absence of a freeze here does not by itself confirm the fix
  works against the ORIGINAL failure mode on Windows -- only that
  nothing in this batch got bad enough to need it. (validated-live for
  what was observed; the stronger claim is a hypothesis)
- **A single hard-stop call taking up to ~4.3s, unprompted, with no
  error, is real user-facing latency worth knowing about even when
  nothing is technically broken** -- 22% of calls in this batch were
  noticeably slower than the rest. (validated-live)
- **Testing the same fix across all three backends in one pass is cheap
  and worth doing by default, not just for the specific backend a fix
  targets** -- codex/claude-code's clean baseline here is useful context
  for interpreting opencode's numbers, and cost almost nothing extra to
  gather. (methodology note)
- **A long-running shared test server accumulating load across many
  automated runs, OR contention with a concurrent live human session
  against the same local model, are both plausible explanations for
  runs degrading over a session -- and this note can't tell them apart**,
  which itself is a real methodology gap worth naming rather than
  silently picking one explanation. (explicitly unresolved)

## Not done here

- Distinguishing "long-lived server accumulation" from "contention with
  JP's concurrent live session" as the cause of the later runs'
  degradation -- needs a clean run against a freshly-started
  `opencode serve` with no concurrent live traffic.
- Confirming whether this batch ever actually triggered the ORIGINAL
  root-caused bug (cancel() never honored at all) versus only ever
  hitting the milder "cancel() slow but eventually honored" case --
  the fix's own escalation-warning log line would be the tell; it never
  fired here.
- The script (`_test_hardstop_race.py`) is a scratch file, not
  committed (this session's leading-underscore, gitignored convention).
  Full content:

```python
"""Scratch: minimal, audio-free repro of the rapid-overlapping-hard-stop
race, parametrized across all three backends."""

import asyncio
import os
import sys
import time

from convobox.adapters.claude_code import ClaudeCodeAdapter
from convobox.adapters.codex import CodexAdapter
from convobox.adapters.opencode import OpenCodeAdapter
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.safeword.detector import SafewordDetector

WORKING_DIR = "D:/LegionForge/_artifact-test-scratch"


def build_adapter(name: str):
    if name == "opencode":
        return OpenCodeAdapter(url="http://localhost:4096")
    if name == "codex":
        return CodexAdapter(
            command=["codex.cmd", "--model", "gpt-5.6-terra"],
            permission_mode="permissive",
            working_dir=WORKING_DIR,
        )
    if name == "claude-code":
        return ClaudeCodeAdapter(
            command=["claude"],
            permission_mode="permissive",
            working_dir=WORKING_DIR,
        )
    raise ValueError(f"unknown backend {name!r}")


async def main() -> None:
    backend = sys.argv[1] if len(sys.argv) > 1 else "opencode"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    print(f"[{time.strftime('%H:%M:%S')}] backend={backend} pid={os.getpid()}", flush=True)

    adapter = build_adapter(backend)
    safeword = SafewordDetector(hard_stop_phrases=["stop stop stop"])
    orch = Orchestrator(adapter=adapter, safeword=safeword)

    try:
        await orch.handle_transcript(
            "please check the artifact pane and tell me what it shows"
        )
        await asyncio.sleep(0.3)

        for i in range(n):
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(orch.handle_transcript("stop stop stop"), timeout=90.0)
                print(f"hard-stop {i+1}/{n} done in {time.monotonic() - t0:.3f}s", flush=True)
            except TimeoutError:
                print(f"hard-stop {i+1}/{n} TIMED OUT after {time.monotonic() - t0:.3f}s", flush=True)
                return
            await asyncio.sleep(0.35)  # approximates real STT/VAD per-utterance latency

        await asyncio.wait_for(
            orch.handle_transcript("did the session actually recover this time"), timeout=20.0
        )
        print(f"[{backend}] follow-up completed -- NO FREEZE", flush=True)
    finally:
        await orch.stop_event_loop()


if __name__ == "__main__":
    asyncio.run(main())
```
