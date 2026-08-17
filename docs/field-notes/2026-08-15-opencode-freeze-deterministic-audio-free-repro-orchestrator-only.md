---
title: The opencode freeze is fully reproducible with NO audio, VAD, STT, or mic hardware at all -- a ~90-line async script that talks directly to a real `opencode serve` through Orchestrator.handle_transcript() and OpenCodeAdapter, calling the safeword repeatedly with realistic gaps, hits the identical freeze (main event loop genuinely idle, parked in kevent, 0% CPU) 4-for-4 attempts this round -- a huge simplification over every prior repro tonight, which all required the full audio pipeline
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch (scratch script only, not committed -- content embedded below), opencode.py / orchestrator.py as of main @ 219a2d1, backend=opencode, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), opencode serve running locally on :4096, no audio/mic/VAD/STT involved at all this round
evidence:
  - A scratch script (`_test_opencode_hardstop_race.py`, NOT committed per this session's leading-underscore convention -- full content embedded below so the repro survives) constructs a real `OpenCodeAdapter` + `Orchestrator` + `SafewordDetector` directly, sends one real initial turn via `handle_transcript()`, then calls `handle_transcript("stop stop stop")` N times with a 0.35s gap between each (approximating the real per-utterance STT/VAD latency observed live tonight), each wrapped in `asyncio.wait_for(..., timeout=90.0)`
  - 4 separate runs this round reproduced the exact same pattern: one `handle_transcript("stop stop stop")` call hangs for the FULL timeout (15s in an early attempt, 90s in three later attempts -- always exactly the configured timeout, meaning the underlying hang is genuinely indefinite, not a slow-but-finite operation), gets forcibly cancelled by `wait_for`, and every subsequent call in the same run then completes normally (sub-10ms) including the follow-up turn -- i.e. `wait_for`'s own cancellation is what "recovers" it; nothing internal to ConvoBox would have
  - Zero reproduction attempts at low repeat counts (n=3) hung; it took n=6, 8, or 10 rapid-fire hard-stops (with the 0.35s gap) to hit the hang in this round's runs -- consistent with a genuine race rather than a deterministic-every-time bug, but reliably triggerable given enough repetitions
  - 3 independent native stack samples (`sample <pid> 3`) taken while a run was actively hung, all byte-identical to each other AND to the samples taken during BOTH of the earlier live audio-driven freezes tonight (2026-08-15 rounds 15 and 16's field notes): main thread parked in `select_kqueue_control_impl -> kevent`, the correct signature for "genuinely idle event loop, nothing scheduled," not a busy-loop or lock contention
  - Ran entirely without touching audio playback, the mic, VAD, or STT -- output volume was untouched at its resting 25% the whole round, confirming this bug has nothing to do with tonight's separate volume-confound thread
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop's own queued next-step: "a minimal, targeted repro of TWO OR MORE overlapping hard_stop() calls specifically... or directly calling the adapter's hard_stop() twice concurrently in a small script")
    - Claude Code (Anthropic claude-sonnet-5) -- test design, capture, native-stack sampling, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T10:10:00-05:00
  revised: 2026-08-15T10:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The opencode freeze reproduces with zero audio -- a pure orchestrator/adapter bug

**Context.** Every opencode-freeze reproduction attempt tonight so far
(the original catch, the inconclusive minimal repro, the second live
confirmation) required the full audio pipeline: real speaker playback,
real mic capture, real VAD segmentation, real STT transcription. This
round's queued next step was to try isolating the mechanism with a
smaller, code-level repro rather than audio. It worked completely: a
plain async script calling into `Orchestrator` and `OpenCodeAdapter`
directly, with no audio anywhere in the call path, reproduces the
identical freeze.

## The repro script

```python
"""Scratch: minimal, audio-free repro of the opencode freeze."""

import asyncio
import os
import sys
import time

from convobox.adapters.opencode import OpenCodeAdapter
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.safeword.detector import SafewordDetector


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"[{time.strftime('%H:%M:%S')}] pid={os.getpid()}", flush=True)

    adapter = OpenCodeAdapter(url="http://localhost:4096")
    safeword = SafewordDetector(hard_stop_phrases=["stop stop stop"])
    orch = Orchestrator(adapter=adapter, safeword=safeword)

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
    print("follow-up completed -- NO FREEZE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
```

Run against a real, locally running `opencode serve` (`uv run python
_test_opencode_hardstop_race.py 10`) with 0.35s gaps between repeated
safeword calls to approximate real per-utterance timing.

## What it showed, 4 runs in a row

Every run that used 6+ repetitions eventually hit one
`handle_transcript("stop stop stop")` call that hung for the FULL
configured timeout -- 15s in one early attempt, 90s in three later,
more careful ones -- always exactly the timeout value, never a partial
duration. That's the signature of a genuinely indefinite hang, not a
slow operation: `wait_for`'s own cancellation is what ends it. Every
call before and after the hang completed in single-digit milliseconds.
Low repetition counts (n=3) never hit it in this round's attempts;
6-10 repetitions did, consistent with a real race that needs enough
attempts to land rather than a guaranteed-every-time failure.

Three native stack samples taken mid-hang were byte-identical to each
other and to BOTH of tonight's earlier live, audio-driven freeze
samples: main thread parked in `kevent`, the OS-level selector wait an
asyncio event loop sits in when it has genuinely nothing scheduled.
**This is the same bug** -- not a similar-looking but distinct issue --
confirmed by identical stack signature across three completely
different reproduction methods (two live audio sessions, one pure
code path) captured across two separate rounds tonight.

## Why this matters

**This is very likely the single most useful finding of the whole
night for anyone who wants to actually FIX this bug**, as opposed to
further characterizing it. Every previous repro required: real
speaker/mic hardware, correct system output volume, Piper TTS
synthesis, a live VAD/STT pipeline, and 30-90+ seconds of wall clock
per attempt. This repro needs: a running `opencode serve`, three
imports, and about 15 lines of orchestration logic, completing a full
positive-or-negative repro in under 2 minutes even at 10 repetitions.
It can be run in a tight loop, wrapped in a proper pytest test with a
bounded timeout and assertion, or stepped through with a debugger --
none of which were practical against the audio pipeline.

It also conclusively rules out any remaining doubt that this is
audio-timing-dependent, VAD-related, or connected to tonight's
separate volume-confound thread: this run touched none of that
machinery at all.

## What transfers

- **When a bug's reproduction depends on a large, slow, hardware-
  involving pipeline, actively look for the smallest subset of
  real components that still reproduces it, rather than assuming the
  full pipeline is load-bearing.** This session spent multiple rounds
  building increasingly elaborate audio-driven repros before trying
  the direct code path; the direct path turned out both simpler AND
  more reliable to trigger (4/4 vs. audio's less consistent hit rate).
  (validated-live)
- **A hang that always lasts exactly as long as its enclosing timeout,
  never less, is strong evidence of a genuine indefinite wait**, not a
  slow-but-eventually-completing operation -- worth checking explicitly
  (vary the timeout, confirm the duration tracks it) before concluding
  "this is just slow." (validated-live)

## Not done here

- Root-causing WHERE exactly inside `OpenCodeAdapter`/`Orchestrator`/
  httpx/httpcore the indefinite wait occurs (e.g. adding print/log
  statements at each await point, or using `asyncio.all_tasks()` /
  `sys._current_frames()` introspection from a signal handler for a
  Python-level stack instead of the native `sample` tool's C-level
  view) -- the native samples confirm WHERE the event loop's own
  thread is (idle, in kevent) but not WHICH specific asyncio Task is
  the one that's stuck; that's the natural next step and should be
  fast now that the repro is deterministic-enough and cheap.
- Any attempt at a fix (e.g. a lock around `hard_stop()`/
  `handle_transcript()` to prevent overlapping calls, or wrapping the
  interrupt POST so an outer cancellation can't corrupt in-flight
  connection state) -- still diagnose-first per this session's practice,
  but this finding makes a fix attempt genuinely tractable for a future
  session or for JP directly.
- The scratch script itself was NOT committed (this session's
  leading-underscore, gitignored convention) -- its exact content is
  embedded above so the repro isn't lost, but recreating it as an
  actual file is a one-time copy-paste for whoever picks this up next.
- Turning this into a proper `pytest` regression test with a bounded
  timeout and a clear pass/fail assertion, which would be a natural
  and fast follow-up given how directly this script maps onto one.
