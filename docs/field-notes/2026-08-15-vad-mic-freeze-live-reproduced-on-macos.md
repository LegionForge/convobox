---
title: The VAD/mic-pipeline freeze reproduces live on macOS -- 94.4s readline() stall plus 2+ minutes of total mic-pipeline silence that survived a safeword, a subprocess kill, and a fresh utterance
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ 4ee0428, backend=codex, permission_mode=permissive, stt.device=cpu, stt.model=base, audio.echo_cancellation=false (AEC extra not installed on this machine), macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), codex-cli 0.147.0
evidence:
  - A real, separately-running ConvoBox session (`scripts/run_convobox.py -v --permission-mode permissive`, no --tui -- that flag requires a real controlling TTY, unavailable to a backgrounded process), real codex backend, real mic (AIRHUG 28) and speakers (Mac mini Speakers)
  - A scratch synthetic-speech stress harness `_test_vad_freeze_macos.py` (not committed, matches the Windows session's convention), Piper-synthesized phrases played through real speakers into the real mic in pause -> rapid-fire-burst -> resume cycles
  - `ps` process-state/CPU-time sampling of the hung codex app-server subprocess (no psutil dependency)
  - Full raw session log, timestamps quoted verbatim below (copy preserved at /tmp/convobox_session_freeze_evidence_*.log on this machine, not committed)
  - docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-and-a-12-minute-unrecoverable-one.md, docs/field-notes/2026-08-14-vad-freeze-harness-live-catches-two-more-readline-stalls-with-real-telemetry.md (Windows predecessors, same methodology)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; explicit handoff ask from the 2026-08-14 Windows session -- "does the VAD/mic freeze reproduce on macOS at all"; approved running the audio-injection harness live, checked audibility from sleeping-adjacent rooms first)
    - Claude Code (Anthropic claude-sonnet-5) -- harness design/implementation, live session operation, process forensics during the freeze, writing
  org: https://legionforge.org
  created: 2026-08-15T00:55:00-05:00
  revised: 2026-08-15T00:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The VAD/mic-pipeline freeze reproduces live on macOS

**Context for outsiders.** ConvoBox has a real, safety-relevant freeze
where its mic pipeline can go totally unresponsive -- first documented
2026-08-05, live-reproduced multiple times on Windows through
2026-08-14 (short 1-4s capture hiccups plus at least one severe
12+ minute freeze that resisted every recovery path). This session's
explicit handoff ask was whether that reproduces on macOS at all, or is
Windows-specific. **It reproduces.** This session caught both the short
readline() stalls (already expected) and, unprompted, a genuine severe
freeze matching the Windows write-up's own signature -- within one
5-cycle stress run, no multi-hour wait required.

## Setup

Ran a real ConvoBox session in the background (`-v`, no `--tui` -- that
mode requires a real controlling TTY and crashed with
`termios.error: (102, 'Operation not supported on socket')` under a
backgrounded/nohup process; plain scrolling-log mode works fine and was
used instead), `audio.echo_cancellation: false` (the AEC extra needs a
manual `brew install meson ninja swig` + source build on macOS, not
done this session -- see the standing gap already noted in `!startup.md`).
A scratch harness (`_test_vad_freeze_macos.py`, not committed) played
Piper-synthesized phrases through the real Mac mini speakers into the
real AIRHUG 28 mic: `pause_phrase` ("stop listening") -> 3x rapid-fire
`safeword_burst` ("stop stop stop") -> `resume_word` ("Athena") ->
`followup_utterance`, repeated for 5 cycles.

**Volume/audibility checked live before running anything**, per JP's
own request while putting his son to bed: system output volume 25/100,
confirmed via a direct mic-loopback RMS measurement (-33dBFS RMS, 0.71
peak -- a clean, easily-detectable signal) that the mic picks it up
reliably at that volume, and JP confirmed live it was inaudible from the
rooms where people were sleeping. This is a real methodology point worth
naming: audibility-to-a-human and mic-detectability-by-the-app are
different questions, and both were checked independently rather than
assuming one implies the other.

## Evidence

### Cycles 1-4: short stalls, all self-resolving (5.5s-30.7s)

Every cycle produced at least one `readline() still pending` warning on
the codex app-server's `_read_loop`, growing in the same 5s-interval
pattern the diagnostic logs at (`readline_with_stall_diagnostic`,
PR #274). All of these resolved on their own:

```
00:45:22  still pending after 5.5s
00:45:27  still pending after 10.5s
00:45:32  still pending after 15.5s
00:45:37  still pending after 20.5s (cont. through cycle transitions)
00:45:47  finally returned after 30.7s total
```

One STT hallucination caught along the way: "stop listening" (the
pause phrase) transcribed as `"I'm still still."` -- didn't match any
pause/safeword phrase, so the session never actually entered the paused
state for that attempt, consistent with the exact class of failure the
2026-08-06 hallucination field note already documented on Windows.
Separately, `"Stop, stop."` (NOT the full triple-repeat safeword) still
triggered a hard-stop response ("Stopped.") -- worth a closer look in a
future session; not chased further here.

### Cycle 5: the severe freeze, live-caught in real time

Last normal mic-pipeline activity: `00:45:58,089 DEBUG backend event
type=done`. Cycle 5's own audio started playing 1 second later
(`00:45:59`, per the harness's own timestamped log) -- **and the mic
pipeline never produced another `Processing audio` line for the rest of
the session**, despite the harness continuing to play its full cycle 5
(pause, 3x safeword burst, resume, followup -- all injected normally,
confirmed via the harness's own successful `sd.play()`/`sd.wait()`
completions) and two additional manual recovery attempts described
below.

Meanwhile the same `readline()` diagnostic kept firing, now without
resolving:

```
00:45:58  still pending after 0.5s
00:46:03  still pending after 5.5s
...       (every 5s, uninterrupted)
00:47:28  still pending after 90.5s
00:47:32  readline() finally returned after 94.4s total (proc.returncode=-15)
```

**CPU forensics, same discipline as the Windows note**: two `ps`
samples of the hung codex app-server subprocess (PID 28426), 3 seconds
apart, showed `TIME 0:00.78` both times -- byte-identical, genuinely
zero CPU consumed, not merely descheduled under load. Same signature as
Windows' 12-minute freeze.

**Recovery attempts, in order, all failed to produce ANY new mic-pipeline
log activity:**

1. **A dedicated safeword recovery attempt** (fresh "stop stop stop"
   played at 00:47:19, ~80s into the stall) -- zero effect.
2. **Killing the hung subprocess directly** (`kill -TERM` on the codex
   app-server PID, 00:47:32) -- this DID unblock the `readline()` call
   itself (`proc.returncode=-15`, i.e. died to SIGTERM, read returned
   immediately). **This is the opposite of the Windows note's own
   finding**, where `taskkill /F`-ing the equivalent hung process did
   NOT unblock the parent's read. A real, useful platform divergence:
   on macOS, at minimum for this specific stuck-`readline()` shape,
   killing the far end of the pipe DOES wake the blocking read (POSIX
   pipe semantics -- the read end sees EOF once the write end's last
   fd closes, matching ordinary Unix pipe behavior). Still: this did
   NOT recover the app. See below.
3. **A fresh, ordinary utterance** ("can you hear me now", played
   00:47:54, 22s after the subprocess kill) -- zero new mic-pipeline
   log activity. The ConvoBox process itself (PID 28355) was
   confirmed still alive and NOT CPU-pinned (`ps` showed 3.5-4.6%,
   sleeping state `S`, 20 threads -- consistent with idle background
   STT/audio threads, not a hard spin), so this is not the same "whole
   process wedged" shape as the readline() stall -- something
   downstream of subprocess death, likely in the mic capture or VAD
   segmenter layer itself, stopped accepting new audio and never
   resumed within the ~2 minutes this session watched it.

Session ended by directly `kill -TERM`-ing the ConvoBox process itself
(PID 28355) -- which worked immediately, unlike the subprocess-level
freeze, confirming the top-level process was still signal-responsive
throughout; it was specifically the mic-pipeline task, not the whole
process, that was stuck.

## Mechanism

**This looks like the same two-part shape the Windows 2026-08-12 note
already proposed**, now confirmed cross-platform: (1) a genuine blocking
wait with no timeout on backend-subprocess I/O (the `readline()` stall,
now instrumented and directly observable via PR #274's diagnostic,
confirmed zero-CPU via process forensics), and (2) a separate,
downstream mic-pipeline stoppage that outlives the subprocess entirely
-- surviving both a safeword utterance and the subprocess's own death.

**What's new here, not previously established on either platform:**
killing the hung subprocess DOES unblock the specific `readline()` call
waiting on it, on macOS -- a real, mechanism-level platform difference
from the Windows finding, not just a difference in overall recovery
outcome (both platforms end up NOT fully recovering, but for
different, now-distinguishable reasons: Windows' read stayed stuck even
after the kill; macOS's read woke up immediately but something else
downstream stayed stuck anyway).

## What transfers

- **The freeze is not Windows-specific.** JP's own standing question,
  answered: this reproduces on macOS with the same harness shape, within
  one 5-cycle run -- no multi-hour wait needed to catch a severe
  instance. (validated-live)
- **"Killing the hung process unblocks the parent" is platform-dependent,
  not a universal recovery guarantee** -- true on macOS for this
  specific stuck-read shape, false on Windows for the Windows note's own
  12-minute case. Do not assume a fix validated on one platform's
  process/pipe semantics transfers to the other. (validated-live)
- **A live process that is NOT CPU-pinned can still be functionally
  frozen** -- PID 28355 showed normal-looking idle CPU (3.5-4.6%,
  sleeping state) throughout the mic-pipeline silence, which would look
  healthy on a naive "is it burning CPU" check. The actual signal that
  mattered was the *absence* of `Processing audio` log lines despite
  confirmed audio being played, not the process's resource usage.
  (validated-live)
- **Audibility-to-a-human and mic-detectability-by-the-app are
  different measurements** -- checked independently this session
  (RMS/dBFS loopback measurement vs. JP's own ears) rather than assuming
  a quiet-sounding test signal is also a weak signal for the app, or
  vice versa. (validated-live)

## Not done here

- Root-causing WHY the mic-pipeline/VAD layer specifically stops
  accepting audio after the backend subprocess dies -- this note
  establishes THAT it happens and confirms it survives subprocess death,
  not the specific blocking call responsible. Would need the same kind
  of audit the 2026-08-12 Windows note already scoped ("audit every
  blocking read/wait on backend-subprocess I/O... for a missing timeout
  or missing 'process died' handling") extended to the capture/VAD layer
  specifically.
- Confirming whether `Orchestrator.force_kill()` (PR #277, the
  mechanism this session's other field note evaluated) would have
  recovered this specific freeze -- the manual subprocess kill tested
  here is the same underlying OS action `force_kill()` performs on
  codex, but `force_kill()` itself, and the `kill_phrase` voice trigger,
  were not exercised against this exact live freeze.
- Confirming whether this specific freeze shape (subprocess dies cleanly,
  mic pipeline stays silent) recurs consistently, or was a one-off this
  session happened to catch on the first 5-cycle run. Windows' own
  09-08-12 note found the severe variant on attempt 4 of that day, not
  every run -- sample size here is 1.
- Any live voice/STT-trigger testing beyond what this harness already
  covers (safeword/pause/resume phrases) -- no human speech was used,
  synthetic Piper audio only, per the standing distinction this repo's
  other freeze notes already draw between synthetic and human-speech
  testing.
