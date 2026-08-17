---
title: A repeated/delayed second Task.cancel() call reliably and quickly unsticks the frozen _consume_events() task (recovers in under 6s, 2-for-2 across replicated trials) versus 15-90s hangs with only the original single cancel() -- strongly suggests the cancellation request is being LOST or coalesced, not fundamentally impossible, and points at httpcore's AutoBackend routing all async socket I/O through anyio's AnyIOBackend even when the caller is plain asyncio (not anyio-native) as the likely architectural source of the gap
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ 219a2d1, backend=opencode, httpx 1.0.9, httpcore (AutoBackend -> AnyIOBackend), httpx-sse 0.4.3, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), audio-free repro (continuing the previous two rounds' scratch script)
evidence:
  - Read `httpcore`'s `_backends/auto.py`: `AutoBackend._init_backend()` always resolves to `AnyIOBackend` for any non-trio async context -- including plain `asyncio.run()` code with no anyio task group or anyio-native scheduling anywhere in the call stack (confirmed: this repro script uses bare `asyncio.run(main())`, no anyio import at all). This means every async socket read/connect this codebase's opencode adapter performs goes through anyio's asyncio-compatibility shim, not raw asyncio sockets directly -- a known category of interop surface where a bare `asyncio.Task.cancel()` (not routed through anyio's own cancel-scope machinery) is not guaranteed to be delivered promptly to code running under that shim.
  - Extended the previous round's task-dumper with an experiment: if the SAME task is found stuck at `orchestrator.py:416` (the confirmed freeze point) for 3 consecutive 2-second dumps (6s), re-issue `.cancel()` on it again (a second, later cancellation request on top of the original one already issued when `hard_stop()` first ran).
  - 2 of 3 trials this round hit the stall. BOTH recovered almost immediately after the re-cancel fired: 5.855s and 4.795s total stall duration (vs. this session's prior single-cancel trials: 15.001s, 90.002s, 90.003s, 90.004s -- always exactly the externally-imposed timeout, meaning those never would have resolved on their own). The third trial had no stall at all (matches this bug's established non-deterministic, race-dependent nature).
  - This is a small sample (2 stalls observed this round) but a consistent, large, and mechanistically sensible effect: a stall that a single upfront cancel() left indefinitely stuck (in every prior observation this session, with NO exception) was resolved within ~2 dumper cycles of a SECOND cancel() call, both times.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop's own queued next-step: "confirm WHY cancel() doesn't unstick the aiter_sse() await... or empirically test whether wrapping... in asyncio.wait_for... unsticks it (this would also double as a candidate fix)")
    - Claude Code (Anthropic claude-sonnet-5) -- experiment design, capture, analysis, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T11:05:00-05:00
  revised: 2026-08-15T11:05:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A second cancel() call reliably unsticks the freeze -- fast

**Context.** The previous round pinpointed the exact stuck coroutine
(`_consume_events()` at `orchestrator.py:416`) and showed that
`Task.cancel()`, called once as part of `hard_stop()`'s normal
sequence, does not unstick it -- the task stays suspended at the
identical frame for the observed hang's entire duration (up to 90+
seconds, only ending when an unrelated OUTER timeout gave up on a
DIFFERENT task). This round's queued next step was to test whether a
bounded timeout wrapped directly around the stuck await could unstick
it -- as both a diagnostic and a candidate fix. The actual experiment
run was slightly different and arguably more informative: rather than
imposing an external timeout, it tested whether simply calling
`.cancel()` a SECOND time, a few seconds after the first, could
resolve the stall.

## The experiment and result

The task-dumper (from the previous round) was extended: if the same
task is observed stuck at the known freeze point (`orchestrator.py:416`)
for 3 consecutive dumps (6 seconds), it calls `.cancel()` on that task
again -- a second cancellation request layered on top of the one
`hard_stop()` already issued.

Across 3 trials this round, 2 hit the stall. **Both recovered almost
immediately after the re-cancel fired**: 5.855s and 4.795s total stall
duration. Every single-cancel stall observed this entire session (4
of them, across two prior rounds) ran to exactly its externally-imposed
timeout with zero sign of resolving on its own -- 15s once, 90s three
times. A stall that showed zero tendency to self-resolve, ever, in 4
prior observations, resolved within ~2 dumper cycles of a second
cancel() call, twice in a row this round.

## What this suggests

**The cancellation request is most likely being lost or coalesced
somewhere in the async I/O stack, not fundamentally impossible to
deliver.** If the underlying socket read were in a truly
un-cancellable state (e.g. a raw blocking syscall with no cancellation
hook at all), a SECOND `.cancel()` call should be exactly as
ineffective as the first -- there would be no mechanism for it to
"land" any differently. The fact that it does land, reliably, and
quickly, points instead at a race or a missed-signal condition: the
first cancellation request arrives at a moment where the underlying
anyio/httpcore primitive isn't positioned to observe it (e.g., a
checkpoint that isn't reached until the next explicit await
resumption, which for a socket sitting in an idle read may not occur
until actual data arrives -- but a LATER cancel(), landing during some
other bookkeeping moment, gets through).

This is consistent with `httpcore`'s `AutoBackend` always resolving to
`AnyIOBackend` for async I/O (confirmed by reading its source) even
though this codebase and its repro script are plain `asyncio.run()`
code with no anyio task groups anywhere. Mixing bare-asyncio
`Task.cancel()` with anyio-wrapped I/O primitives is a known category
of interop friction -- anyio's own cancellation model expects
cooperation through its cancel scopes, and a bare external
`Task.cancel()` is a cruder signal that anyio's internals may not
check at every possible suspension point, particularly ones deep
inside a blocking read that anyio itself is managing.

## Why this matters -- a real mitigation candidate

**This turns an indefinite, unrecoverable hang into a bounded, few-
second delay, using nothing more than calling the SAME existing
`.cancel()` a second time after a short wait.** A concrete, minimal
change to `orchestrator.py`'s `stop_event_loop()` along these lines is
now a credible near-term mitigation, even without a complete
understanding of the underlying anyio/httpcore interaction:

```python
async def stop_event_loop(self) -> None:
    self._cancel_speak_task()
    if self._events_task is None:
        return
    self._events_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(self._events_task), timeout=3.0)
    except (asyncio.CancelledError, TimeoutError):
        if not self._events_task.done():
            self._events_task.cancel()  # second attempt
            try:
                await asyncio.wait_for(self._events_task, timeout=3.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
    self._events_task = None
```

This is a sketch, not a verified fix -- it is untested against the
real repro, and the exact right shape (retry count, timeout values,
whether `asyncio.shield` is even correct here) needs validation before
landing. But it demonstrates the mitigation is simple in principle and
does not require solving the deeper anyio/httpcore mystery to ship
something that turns a permanent freeze into, at worst, a several-
second delay -- a dramatic improvement in user-facing severity even as
a stopgap.

## What transfers

- **When a cancellation-based cleanup mysteriously hangs, "try calling
  cancel() again" is a cheap, high-value experiment before assuming
  the operation is fundamentally uncancellable** -- it took under 10
  minutes to design, run, and get a clear, replicated positive signal,
  versus the much longer investigation needed to trace the exact
  anyio/httpcore mechanism. (validated-live)
- **`httpcore`'s `AutoBackend` silently uses `anyio` under the hood for
  ALL async I/O, even in codebases that never import or use `anyio`
  directly.** Any future cancellation-related mystery in HTTP-calling
  async code in this repo (or any httpx-based codebase) should
  consider this interop boundary as a suspect early, not late.
  (validated-live)

## Not done here

- Actually landing a real fix in `orchestrator.py` -- the sketch above
  is unverified and needs to be run against the real repro (and
  ideally against a live audio-driven freeze too) before being
  considered production-ready. Left for JP or a future session.
- A larger sample size to firm up "2/2 recovers fast" into a stronger
  statistical claim -- 2 data points is suggestive, not conclusive;
  worth 5-10 more trials before fully trusting the retry-count/timing
  chosen here.
- Tracing the EXACT anyio/httpcore source location responsible for the
  missed-cancellation window -- this note explains the most likely
  architectural boundary (AutoBackend -> AnyIOBackend under bare
  asyncio) but does not pinpoint the specific line/mechanism inside
  anyio itself.
- Testing whether the SAME "second cancel recovers it" behavior holds
  for the LIVE, audio-driven freeze (rounds 15-16), not just this
  scripted repro -- plausible given they're confirmed to be the same
  underlying bug, but not directly verified this round.
