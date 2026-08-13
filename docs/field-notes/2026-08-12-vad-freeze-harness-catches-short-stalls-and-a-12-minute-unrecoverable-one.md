---
title: A repeatable synthetic-speech harness confirms real short capture stalls, then catches a 12+ minute freeze that resisted every recovery path -- likely a second, distinct bug
status: validated-live
date: 2026-08-12
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #271 branch (docs/vad-freeze-live-repro-2026-08-12) @ cc2fcea; stt.device=cpu, stt.model=base; backend=codex, permission_mode=permissive
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, --tui --web -v, real codex backend, working_dir _artifact-test-scratch
  - A scratch synthetic-speech stress harness (Piper-synthesized phrases played through real speakers into the real mic) and a psutil-based CPU sampler, both ad-hoc scripts, not committed to the repo
  - convobox-tui.log timestamps quoted verbatim below
  - docs/field-notes/2026-08-12-vad-freeze-live-reproduced-three-times-pr269-did-not-fix-it.md (same-day predecessor session)
  - docs/KNOWN-ISSUES.md "A hard-stop... does not guarantee an in-flight tool call actually stops" entry
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; confirmed audio output device routing, made the go/no-go calls on recovery attempts)
    - Claude Code (Anthropic claude-sonnet-5) -- harness and CPU-sampler design/implementation, live process forensics during the freeze, writing
  org: https://legionforge.org
  created: 2026-08-12T19:55:00-05:00
  revised: 2026-08-12T19:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A repeatable harness confirms short capture stalls, then catches a 12+ minute freeze that resisted every recovery path

**Context for outsiders.** Same investigation as this session's earlier
field note: a real, safety-relevant freeze in ConvoBox's mic pipeline
that PR #269 did not fix. This note covers a follow-up pass using a
repeatable synthetic-speech harness (so the stress doesn't depend on a
human talking) plus CPU-contention instrumentation -- and an unplanned,
much more severe recurrence that surfaced along the way.

## Setup

A scratch harness pre-synthesizes the same hotword/safeword phrases used
in the earlier live session via Piper TTS, then plays them through the
real speakers (into the real mic of a separately-running ConvoBox
session) in repeatable pause -> rapid-fire-burst -> resume-attempt
cycles. A separate `psutil`-based sampler logs system-wide and
per-process CPU% at ~5Hz for correlation. Both scripts are diagnostic
scratch tools, not part of the shipped repo.

**One real setup bug caught before any useful data**: the harness's
first run used `sounddevice`'s default output device, which was the
operator's headset, not the room speakers -- so the synthetic speech
never reached the mic at all. Not a finding about the app; just a
harness bug, fixed by confirming the correct default output device
before the real run.

## Evidence

### Short stalls: real, now visible, still not backlog-related

Even during the misconfigured (headset-only) run, the target session's
own mic loop hit two brief real stalls, caught for the first time by
this session's new `MicrophoneStream.stream()` diagnostic (PR #271):

```
19:17:28.511  still running after 0.5s -- 0 chunk(s) backlogged
19:17:31.608  finally returned after 3.6s total (0 chunk(s) still backlogged)
19:17:34.605  still running after 0.5s -- 0 chunk(s) backlogged
19:17:35.540  finally returned after 1.5s total (0 chunk(s) still backlogged)
```

Two more of the same shape recurred later, unprompted, during otherwise
normal operation:

```
19:32:20.170  still running after 0.5s -- 0 chunk(s) backlogged
19:32:20.881  finally returned after 1.2s total (0 chunk(s) still backlogged)
```

**Zero backlog in every case** directly answers the operator's own live
hypothesis from earlier in the session (chunks piling up behind a
stalled consumer): that's not what's happening. The queue is empty the
whole time, meaning the capture callback itself briefly stops delivering
new chunks -- a real, if usually minor (1-4s), capture-layer hiccup, now
directly observable for the first time.

### The corrected run: real reproduction, then a 12+ minute unrecoverable freeze

With output correctly routed to the speakers, the harness ran 5 stress
cycles. Cycles 1-3 completed in ~40s each, matching earlier timings.
Partway through cycle 2, the pause phrase only partially transcribed
(`transcript='listening'`, not the full "stop listening"), so the
session never entered the simple paused state. A rapid-fire safeword
utterance then hard-stopped a turn that was **already busy**:

```
19:39:08.722  transcript='brake brake brake' ... busy=True [HARD STOP]
19:39:08.722  hard stop matched safeword 'brake brake brake'
19:39:08.732  hard-stop interrupted a turn that was still busy -- if it
              included a tool call, the underlying process is not
              guaranteed to have stopped; any result it eventually
              produces will be discarded, not spoken
```

That log line is this project's own existing, already-documented finding
("A hard-stop... does not guarantee an in-flight tool call actually
stops," validated-live 2026-08-09). What happened next was new: **total
silence for over 12 minutes**, and every recovery path tried failed:

1. **Web `/api/listening` resume** (direct API call, correct
   `X-ConvoBox-Client` header, HTTP 200, `{"is_paused": false}`) --
   succeeded as an API call, produced **zero** new mic-pipeline activity.
   (Consistent with the session never having actually been in the
   simple paused state -- there was nothing for "resume" to undo.)
2. **Process forensics**: the actual backend subprocess for that turn
   (`codex.exe`, spawned 19:39:06.7, confirmed via the process tree
   under the target's own `cmd.exe` child) was still alive 5+ minutes
   later. Two `KernelModeTime`/`UserModeTime` samples 3 seconds apart
   were byte-identical -- **zero CPU consumed**, i.e. genuinely hung,
   not slow.
3. **Killing that subprocess directly** (`taskkill /F`) -- the target
   process's own memory dropped noticeably afterward (931MB -> 631MB,
   suggesting it did notice the pipe/connection dying at some level),
   but **still zero new mic-pipeline log activity**.
4. **`/api/stop` (the hard-stop route)** -- returned `{"stopped": true}`,
   HTTP 200, confirming `safeword_bridge.trigger()` completed without
   hanging. **Still zero new activity.**

This is the first time in this whole investigation (three clean repros
earlier the same day, plus this session's two short stalls) that the web
UI's recovery path -- previously 3-for-3 -- failed to recover anything.
Only killing the whole target process ended it, after 12+ minutes.

## Mechanism

**This looks like it may be two different bugs converging, not one.**

The short stalls (1-4s, zero backlog) are consistent with the
capture-callback hypothesis from earlier in the day: a brief,
now-visible hiccup in chunk delivery.

The 12-minute freeze does not fit that shape at all. CPU evidence is the
key new data point: the target process's own CPU usage was a **flat,
literal zero** for the entire stuck window (350+ consecutive ~0.2s
samples showing 0.0%), while system-wide peak-core usage stayed high
(70-100%) throughout -- both during the freeze and during normal
operation before it, so system load alone doesn't cleanly predict freeze
timing. A process that's merely *starved* of scheduler time under
contention would still show occasional nonzero slivers of CPU as the OS
eventually gives it a turn; a clean, sustained zero across that many
samples is much more consistent with a **genuine blocking wait with no
timeout** -- not descheduling, an actual synchronous stop.

That a hung backend subprocess died (via `taskkill`) without unblocking
anything is the strongest single clue: if some code path is doing a
blocking read on that subprocess's stdout/stderr pipe with no timeout
and no handling for "the far end died," killing the process might not
promptly surface an EOF/error on that read either -- especially if
another handle on the same pipe is still open, or the read primitive
used doesn't reliably wake on process death on Windows. This is a
plausible, testable hypothesis, not a confirmed diagnosis: it would
explain every observation (zero CPU, unaffected by pause/resume/hard-stop
since none of those touch this specific I/O wait, and survives the
subprocess's own death) that the VAD/capture-layer hypothesis does not.

## What transfers

- **A repeatable synthetic-injection harness is worth building even
  before its subject bug is understood** -- it caught real short stalls
  immediately (the backlog-vs-empty-queue question got answered on the
  very first, still-misconfigured run) and then caught a qualitatively
  different, far more severe failure mode within one corrected run,
  neither of which required a human to sustain 12+ minutes of live
  speech. (validated-live)
- **"The web UI reliably recovers this" was true 3-for-3 earlier the
  same day and false on attempt 4.** Don't generalize a recovery
  workaround from a small sample, even a clean one -- keep testing it as
  its own variable, not just the underlying bug. (validated-live)
- **A process pinned at a sustained, literal 0% CPU during a hang is a
  meaningfully different signal than one merely running slow or
  showing reduced-but-nonzero usage under contention** -- worth checking
  explicitly (two `cpu_percent()`/kernel-time samples a few seconds apart)
  before assuming a "starvation" explanation for an unresponsive process.
  (validated-live)
- **Killing a hung subprocess is not guaranteed to unblock whatever in
  the parent was waiting on it** -- test this explicitly rather than
  assuming a force-kill is sufficient recovery once you've identified a
  hung child process. (validated-live)

## Next steps, not done this session

1. Audit every blocking read/wait on backend-subprocess I/O (stdout/
   stderr pipes, any JSON-RPC framing loop) for a missing timeout or
   missing "process died" handling -- the leading candidate for the
   12-minute variant specifically.
2. Decide whether the short capture-callback stalls (1-4s, now visible
   via PR #271's diagnostic) warrant their own fix, or are acceptable
   background noise given they're brief and self-resolving.
3. Re-run the harness (now proven to work correctly) multiple more times
   to build a real sample size on how often each variant recurs, ideally
   overnight/unattended now that the output-device bug is fixed.
