---
title: Root cause finally captured via a live native stack sample -- codex's own subprocess is blocked reading its OWN stdin during the readline() freeze, and ConvoBox's entire Python process (event loop, STT thread pool, everything) is genuinely idle at that exact moment, directly refuting the standing "synchronous audio-pipeline call blocks the event loop" hypothesis
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=codex, codex-cli 0.147.0, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 7. macOS's built-in `sample` profiler (no sudo needed for own-user processes; `py-spy` was tried first but requires sudo on macOS, not used) captured live during a real, in-progress readline() stall on both the ConvoBox Python process AND the hung codex subprocess simultaneously
  - An automated watcher script (`_watch_and_sample.sh`, not committed) armed against a real 10-cycle VAD stress batch, triggering the moment a `readline() still pending after 1Xs` warning appeared -- the same trigger every prior severe-freeze catch this session used, but this time with a live sampler already armed
  - Full raw sample output (`/tmp/freeze_samples/{main,codex}_readline_stall.txt`, not committed -- key excerpts quoted below)
  - src/convobox/adapters/codex.py's own `_write()` method, read directly to check for a plausible ConvoBox-side write bug
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness/watcher design, live capture, analysis, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T05:10:00-05:00
  revised: 2026-08-15T05:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Root cause captured: codex is blocked on its OWN stdin, not ConvoBox's event loop

**Context.** Every prior field note in this session's investigation
(and the Windows session before it) diagnosed the readline() freeze by
correlation -- what conditions make it more/less likely -- never by
directly observing what the stuck process is actually doing at the
moment it's stuck. This round finally captures that, using macOS's
built-in `sample` profiler (no `sudo` needed for a user's own
processes; `py-spy` was tried first and requires `sudo` on macOS, ruled
out for an unattended session).

## Method

Built a small watcher script that tails the ConvoBox session log and,
the instant a `readline() still pending after 1Xs` warning appears
(the same signal every prior catch this session used), immediately
fires `sample <pid> 3` against BOTH the ConvoBox Python process and the
hung codex subprocess -- capturing a live, 3-second native stack trace
of every thread in each process at that exact moment. Armed this
against a real 10-cycle stress batch (the same shape that's reliably
produced stalls all night).

## Result: ConvoBox's Python process is completely idle; codex is blocked reading its own stdin

**The codex subprocess.** Of its ~20 threads, every single one is
parked in a normal idle wait (`parking_lot::condvar::wait`,
`_dispatch_sema4_wait`, `notify-rs fsevents` in a quiet `CFRunLoopRun`,
8 `sqlx-sqlite-worker` threads all asleep on a semaphore) --
**except one**:

```
Thread_15736480: tokio-rt-worker
  ...
  std::sys::io::stdio::Stdin::Read::read
    read  (in libsystem_kernel.dylib)
```

A tokio worker thread inside codex's own process is blocked in a raw
`read()` syscall on **its own stdin** -- waiting for more input from
ConvoBox that isn't arriving. This is codex's side of the same pipe
ConvoBox's `readline()` is stuck reading from the other end of.

**ConvoBox's Python process.** Every thread is idle at the moment of
the freeze: the main event-loop thread is parked in
`select_kqueue_control_impl` (2123 of 2146 samples -- a normal idle
`kqueue` wait, not a busy-loop or a blocking call), all `ctranslate2`
(faster-whisper) thread-pool workers are asleep on a condition
variable (`BS::thread_pool::worker` -> `condition_variable::wait`, zero
STT work in flight), the `os.waitpid()` reaper thread is in its normal
idle wait, and the CoreAudio I/O thread shows nothing abnormal. **There
is no synchronous call blocking the event loop anywhere in this
sample.**

## This directly refutes the standing "audio-pipeline blocks the event loop" hypothesis

Every field note in this investigation, going back to the 2026-08-12
Windows sessions, has carried some version of the hypothesis that a
synchronous VAD/STT/audio-capture call sharing the single-threaded
asyncio event loop with backend I/O is the likely root cause -- most
recently restated in this session's own harness-confound note ("a
synchronous VAD/audio-capture call sharing the same single-threaded
event loop... is a plausible root cause"). **This sample shows that
hypothesis is wrong, at least for this specific freeze instance**: the
event loop is genuinely idle, correctly waiting on `kqueue` for the
next I/O event, at the exact moment `readline()` is stuck. Nothing on
ConvoBox's Python side is preventing that read from completing.

The actual bottleneck is on the OTHER end of the pipe: codex's own
process is waiting for more bytes on stdin that ConvoBox either hasn't
sent yet, or sent in a form codex's own parser doesn't yet consider a
complete message.

## Checked ConvoBox's own write path for a plausible bug -- found nothing obviously wrong

`CodexAdapter._write()` (`src/convobox/adapters/codex.py`) is a plain,
correctly-shaped line-delimited write: `stdin.write(json.dumps(payload)
+ b"\n")` followed by `await stdin.drain()`. `asyncio.StreamWriter
.write()` is synchronous (buffers immediately, no yield point), so even
without an explicit lock around `_write()`, two concurrent callers
can't interleave partial writes mid-message -- each call's `write()`
fully completes before any `await` inside that call could hand control
to another task. Nothing here looks like an obvious ConvoBox-side
framing bug. This doesn't rule out a real bug on this side entirely
(a genuinely raced write from two call sites is still conceivable if
one call is mid-`await` elsewhere while unrelated code runs
`self._proc.stdin.write()` directly -- not confirmed either way here),
but the simple, already-correct shape of this method makes it a less
likely culprit than codex's own stdin-parsing logic.

## What transfers

- **Correlation-based freeze investigation (this whole session's prior
  work) and direct observation can point to genuinely different root
  causes.** Every earlier note's "consistent with, not proof of" caveat
  about the event-loop-contention hypothesis was the right level of
  confidence to hold -- and this capture is exactly why: the actual
  answer turned out to be different from the leading guess.
  (validated-live)
- **`sample` (built-in, no sudo, no extra install) is a genuinely
  usable live-forensics tool for this kind of investigation on macOS**,
  once you know `py-spy` needs elevated permissions here and isn't a
  drop-in substitute. Worth keeping as a standard tool for any future
  live-freeze capture on this platform. (validated-live)
- **The fix, if this diagnosis holds, likely lives on codex's own side
  (or in exactly what ConvoBox sends it, at the protocol level, not the
  event-loop level)** -- this reframes where to look next entirely, away
  from "make the audio pipeline async-friendly" (this session's and the
  Windows session's standing assumption) and toward "find what specific
  written payload/timing causes codex's own stdin parser to stop
  consuming."

## Not done here

- Capturing what ConvoBox ACTUALLY wrote to codex's stdin in the
  seconds immediately before this freeze began -- the sample shows
  codex is waiting, not what it's waiting FOR. Correlating this with
  the exact JSON-RPC messages sent (would need adding temporary logging
  of every `_write()` call, or a `strace`/`dtruss`-equivalent capture
  of the actual bytes on the pipe) is the natural next step to close
  the loop on this diagnosis.
- Confirming this same mechanism (codex-blocked-on-own-stdin) explains
  EVERY severe freeze this session caught, not just this one instance --
  this is a single sample, not a systematic survey.
- Investigating whether this is a known/reported codex-cli issue
  upstream, or specific to some interaction with how ConvoBox drives
  the `app-server` protocol.
- The separate mic-layer-only freeze variant (no codex subprocess
  involved, documented in the immediately prior round's note) was NOT
  sampled this round -- this capture only explains the readline()-stall
  variant, not that one.
