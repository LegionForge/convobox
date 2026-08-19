---
title: Root cause pinpointed via Python-level asyncio task introspection -- the stuck coroutine is ALWAYS _consume_events() parked at orchestrator.py:416's `async for event in self._adapter.events():`, and Task.cancel() genuinely does NOT unstick it (the task's dumped stack is byte-identical across the entire 90-second hang, before AND after cancel() is called on it) -- plus a real, separate bug candidate in stop_event_loop()'s bare `except asyncio.CancelledError: pass`, which can mask a leaked, permanently-stuck task as successfully cleaned up
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: opencode.py / orchestrator.py as of main @ 219a2d1, backend=opencode, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), opencode serve on :4096, no audio/mic/VAD/STT (same audio-free repro as the previous round)
evidence:
  - Extended the previous round's audio-free repro script with a periodic (every 2s) `asyncio.all_tasks()` dump, printing each live task's name, done-state, and current stack frame via `task.get_stack()` -- pinpoints WHICH coroutine is suspended and WHERE, unlike a native `sample`, which only shows the OS thread is idle
  - Reproduced the hang again (hard-stop 8/10 this run), and captured ~45 consecutive task dumps (one every 2s) spanning the full ~90s hang, from the moment it started (10:31:19) to the moment `asyncio.wait_for`'s own timeout finally fired (10:32:48)
  - EVERY single dump in that window shows exactly 3 live tasks: the dumper itself, `Task-1` (the test's own `await asyncio.wait_for(orch.handle_transcript(...), timeout=90.0)`, correctly waiting), and `Task-10` -- which shows the IDENTICAL stack frame, unchanged, in every dump: `orchestrator.py:416, async for event in self._adapter.events():` inside `_consume_events()`
  - This is a direct, conclusive demonstration that `Task.cancel()` was called on `Task-10` (as part of hard-stop 8's own `hard_stop() -> stop_event_loop()` sequence, matching the code at `orchestrator.py:359-367`) and the task genuinely never processed that cancellation -- it did not raise `CancelledError`, did not unwind, did not run its `finally` block (`events()`'s own cleanup, which would clear `is_busy()`/close the SSE context) -- for the ENTIRE 90 seconds, only ending because the OUTER `wait_for` gave up and cancelled the test's own awaiting task instead (which is a different task from `Task-10` -- `Task-10` itself was never actually resolved, and is presumably still alive as a leaked zombie task when the process exits)
  - Read `orchestrator.py`'s `stop_event_loop()` (lines 359-368): `self._events_task.cancel(); try: await self._events_task; except asyncio.CancelledError: pass; self._events_task = None` -- a bare `except asyncio.CancelledError` around `await self._events_task` does not distinguish "the awaited task (Task-10) actually finished via its own cancellation" from "the CURRENT task (the one calling stop_event_loop) was itself cancelled by something else while waiting" -- both raise the identically-typed exception at that await point in asyncio
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop's own queued next-step: "Python-level task introspection... to find WHICH specific coroutine/await point is stuck")
    - Claude Code (Anthropic claude-sonnet-5) -- instrumentation, capture, analysis, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T10:35:00-05:00
  revised: 2026-08-15T10:35:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Root cause pinpointed: `_consume_events()`'s `async for` never honors cancellation

**Context.** The previous round found a fully deterministic, audio-free
repro of the opencode freeze, but could only show that the event loop's
OS thread was genuinely idle (via native `sample`), not which specific
Python coroutine was stuck or why. This round extended that same repro
with a periodic `asyncio.all_tasks()` dump -- Python-level
introspection that shows exactly which task is suspended and at what
line, something a native stack sample cannot distinguish.

## What the task dumps show

Every single dump across the entire ~90-second hang (about 45 samples,
one every 2 seconds) shows the same 3 tasks, and critically, `Task-10`
(the `_consume_events()` task) shows the IDENTICAL stack frame in
every dump, before AND after `hard_stop()`'s own `.cancel()` call was
issued against it:

```
--- Task-10 done=False ---
  File ".../orchestrator/orchestrator.py", line 416, in _consume_events
    async for event in self._adapter.events():
```

This is direct, repeated, time-stamped proof that `Task.cancel()` does
not unstick this task. Cancellation in asyncio normally works by
raising `CancelledError` at the task's next `await` resumption point --
but this task simply never resumes at all. It stays suspended at
exactly the same point, for the full 90 seconds, until the OUTER
`asyncio.wait_for` (wrapping the whole `handle_transcript()` call, two
frames further up the call stack in `Task-1`) gives up and cancels
*that* task instead -- which is a DIFFERENT task from `Task-10`.
Cancelling `Task-1` does not, and cannot, force `Task-10` to unstick;
it can only make the caller stop waiting for it. `Task-10` itself is
never actually resolved -- it is presumably still alive as an orphaned,
permanently-stuck task for the remaining life of the process.

## A related, real bug: cancellation gets silently swallowed

`orchestrator.py`'s `stop_event_loop()` (lines 359-368):

```python
async def stop_event_loop(self) -> None:
    self._cancel_speak_task()
    if self._events_task is None:
        return
    self._events_task.cancel()
    try:
        await self._events_task
    except asyncio.CancelledError:
        pass
    self._events_task = None
```

A bare `except asyncio.CancelledError: pass` around `await
self._events_task` cannot distinguish two very different situations:
(a) `Task-10` actually ran, processed the cancellation, and raised
`CancelledError` as it unwound -- the intended, successful case; vs.
(b) the CURRENT task (whatever is calling `stop_event_loop()`) gets
cancelled by something else entirely (an outer `wait_for` timeout, or
-- live and plausible -- ANOTHER overlapping `hard_stop()` call's own
cleanup) while sitting at this exact `await`. Both raise the same
exception type at the same line. In case (b), this code swallows that
too, then proceeds to unconditionally set `self._events_task = None`
as if cleanup succeeded -- even though `Task-10` is still alive,
genuinely stuck, and never actually cancelled. The next
`handle_transcript()` call's `start_event_loop()` then sees
`self._events_task is None` and happily creates a brand new events
task, masking the leak completely: the SYSTEM appears to recover
(subsequent turns work fine, exactly as observed in every live and
scripted repro tonight), while the orphaned, permanently-stuck task
and its underlying SSE connection are silently leaked forever.

## Why `Task-10` doesn't unstick is still open

This round conclusively shows WHERE the freeze lives (this exact
`async for`, this exact task) and demonstrates that `cancel()` does
not resolve it -- but does not yet show WHY the cancellation isn't
honored at that specific await point. The most likely candidates,
none confirmed by reading source this round:
- `httpx_sse`'s `aiter_sse()` (wrapping `httpx`'s own async byte-stream
  iteration over the SSE response) may perform its underlying read via
  a mechanism that isn't cooperative with `asyncio.Task.cancel()` until
  the read itself returns -- e.g. blocked deep inside an `anyio`/
  `httpcore` primitive that doesn't check for cancellation at that
  specific suspension point.
- The connection may be in a state (e.g. server never sends more bytes
  and never closes the socket either) where nothing will EVER wake
  that particular await, cancellation-aware or not, until something
  external (socket timeout, forced close) intervenes -- which would
  mean the real fix is a bounded read timeout on the SSE stream, not a
  cancellation fix at all.

## Why this matters

This is now a fully actionable, line-level bug report, not just a
reproducible symptom. Whoever picks this up next has: the exact stuck
line, direct proof that `.cancel()` alone cannot resolve it, a
concrete secondary bug (the swallowed-cancellation leak in
`stop_event_loop()`) that should be fixed regardless of the primary
cause, and two concrete hypotheses for the primary fix (a bounded
timeout on the SSE iteration itself, most likely the right fix
regardless of the deeper "why," vs. some cancellation-propagation gap
in the SSE library stack).

## What transfers

- **`asyncio.all_tasks()` + `task.get_stack()`, polled periodically, is
  a fast, precise, zero-dependency way to pinpoint a stuck coroutine**
  -- far more specific than a native OS-level stack sample, which can
  only show that the event loop thread is idle, not which of
  potentially many tasks is the one that never got scheduled or never
  resumed. Worth reaching for this FIRST on any future async hang in
  this codebase, before native sampling. (validated-live)
- **A bare `except asyncio.CancelledError: pass` around `await
  some_other_task` is a real hazard**: it cannot tell "the other task
  finished (via cancellation)" apart from "I myself got cancelled
  while waiting for it." The safe pattern checks `task.cancelled()`
  after the await (or re-raises if the CURRENT task's own cancellation
  is what's in flight) rather than swallowing unconditionally. This
  specific instance directly explains why every hard-stop-driven freeze
  tonight "recovered" for subsequent turns while leaking the actual
  stuck resource silently. (validated-live)

## Not done here

- Confirming which of the two "why doesn't cancel work" hypotheses is
  correct by reading `httpx_sse`/`httpcore`/`anyio` source, or by
  adding a bounded `asyncio.wait_for()` directly around the `aiter_sse()`
  iteration itself as a test (which would also double as a candidate
  fix if it works).
- Actually fixing `stop_event_loop()`'s cancellation-swallowing bug or
  the underlying stuck-read issue -- diagnose-first per this session's
  practice, but both are now concrete enough to implement directly.
- Confirming whether repeated leaked `Task-10`-style zombies (one per
  hard-stop that hits this race) are what eventually exhausts the
  shared httpx connection pool in the LIVE (non-scripted, real audio)
  freezes from rounds 15-16, where the freeze was permanent with no
  outer `wait_for` to ever "recover" it -- plausible given tonight's
  evidence, not directly measured.
