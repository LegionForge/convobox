---
title: kill_phrase correctly force-kills the codex backend and ends the ConvoBox session on Windows, but a detached/orphaned descendant process it spawned (a PowerShell CPU/disk-heavy write loop) survives completely untouched -- confirmed alive and still consuming resources 2+ minutes after the session had fully exited, stopping only via its own unrelated timer, never the kill
status: validated-live
date: 2026-08-19
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ dde7a4c (post-#314), backend=codex, Windows 11 (helios, 10.0.26200), real mic session (--tui --web --aec-dump -v), working_dir=D:\LegionForge\_uat-force-kill-scratch (isolated scratch, outside the product's own source tree)
evidence:
  - convobox-tui.log (D:\LegionForge\convobox-UAT), session pid=70148, started 2026-08-19 17:23:47
  - Live process inspection via PowerShell (Get-Process / Get-CimInstance Win32_Process), polled externally by Claude Code throughout the test
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; designed and ran the live destructive test -- asked specifically for "something destructive... pulling a lot of CPU" after the codex/sleep-90 test only exercised a passive, low-resource child; then asked for a rerun to verify, and for the orphaned process to be force-killed and cleaned up once the finding reproduced)
    - Claude Code (Anthropic claude-sonnet-5) -- watcher tooling, live process/file monitoring, root-cause analysis, writing
  org: https://legionforge.org
  created: 2026-08-19T17:40:00-05:00
  revised: 2026-08-19T19:58:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# kill_phrase ends the session but leaves an orphaned, resource-heavy child running on Windows

**Context for outsiders.** ConvoBox is a voice interface that lets a
user talk to a coding agent (here, OpenAI's `codex` CLI) and hear it
respond. `kill_phrase` is its emergency "ejector seat": a spoken phrase
that is supposed to force-kill the backend and end the whole session
immediately, for when the polite hard-stop path is itself stuck. This
project has an established history of finding that "ends the session"
and "kills everything" are claims that need direct, live verification
-- a 2026-08-15 investigation found the same class of gap on macOS (a
spawned shell child surviving because it becomes its own process-group
leader), and a 2026-08-18 live-voice test found the Windows/POSIX
pgrep-fallback built for that macOS gap has its own guard bug and that
the session-exit claim didn't hold on macOS either. This note is the
first live test of `kill_phrase` against a CPU/disk-heavy, genuinely
runaway process on Windows specifically.

## Problem

Earlier the same session, a passive `Start-Sleep -Seconds 90` spawned
by codex was killed cleanly end-to-end by `kill_phrase` (process gone
37.9s after being detected, well before its natural 90s completion --
see the session transcript, not a separate field note). That result
looked reassuring, but a `Start-Sleep` process is nearly the easiest
possible target: single process, minimal resource use, spawned and
waited on synchronously within codex's own command-execution tree. The
operator asked for a harder case: something destructive, CPU-heavy, and
actively writing to disk, to see whether `kill_phrase` still reaches it
and whether an interrupted write leaves a corrupted file.

## Evidence

Session `pid=70148` started cleanly in an isolated working directory
(no source-tree warning, unlike two earlier sessions in the same log):

```
2026-08-19 17:23:47,827 INFO backend=codex  voice=en_GB-alba-medium  safeword='stop stop stop'  pid=70148
2026-08-19 17:23:47,827 INFO kill phrase 'eject eject eject' configured -- force-kills the backend process and ends this session, instead of the normal hard-stop
```

Getting codex to actually start a working write-loop took several
attempts -- an initial `Start-Process -ArgumentList ...` invocation had
mangled nested quoting and errored before ever running, and a follow-up
used `[Convert]::ToHexString()`, unavailable in this PowerShell's
.NET runtime, and had to be rewritten to `[BitConverter]::ToString()`.
The eventual working command ran a `pwsh.exe -Command` loop that hashes
an incrementing counter with SHA256 and appends `<UTC timestamp> <hash>`
lines to `hashlog.txt`, flushed after every write, for up to 3 minutes:

```
$writer = [IO.StreamWriter]::new($path, $true, $utf8); $writer.AutoFlush = $true
while ([DateTime]::UtcNow -lt $end) { ... $writer.WriteLine(...) }
```

Confirmed running: PID 17328, started 17:29:06. The file grew fast --
478,593,663 bytes after ~48s, 997,020,918 bytes by 17:31, over 1 GB
(1,076,737,603 bytes) by 17:31:14 -- genuinely CPU- and disk-heavy, as
requested.

The kill phrase was spoken partway through:

```
2026-08-19 17:29:47,734 INFO transcript='eject eject eject' lang=en (0.62) dec=0.89 busy=False  [HARD STOP]
2026-08-19 17:29:47,734 WARNING kill phrase matched 'eject eject eject' -- force-killing backend
2026-08-19 17:29:50,008 INFO StreamableHTTP session manager shutting down
2026-08-19 17:29:50,030 INFO exiting
```

The whole ConvoBox process (`pid=70148`) and its entire codex tree were
confirmed gone within 2.3s of the match. But PID 17328 was checked
directly, twice, after that exit:

```
17:30:25 -- watcher armed, PID 17328 not yet checked directly
17:31:14 -- Get-Item hashlog.txt: Length=1,076,737,603  LastWriteTime=17:31:14 (still growing)
17:31:xx -- Get-Process -Id 17328: ALIVE, CPU=163.296875s
```

Its parent chain at that point:

```
ProcessId ParentProcessId
17328     56928
  parent[0]: PID=56928 (no longer exists / not found)
```

PID 17328 was already an orphan -- its immediate parent was gone,
independent of and unaffected by the `kill_phrase` event, which had
already torn down the entire live codex/ConvoBox tree (confirmed: no
`codex.exe` process, no `pid=70148` python process, found anywhere on
the system at this point).

PID 17328 kept running and kept writing for **another 2 minutes and 20
seconds** after the session had fully exited, finally stopping only
because its own script had a 3-minute self-timer (started 17:29:06,
ended 17:32:07):

```
17:32:07.749 PROCESS DIED (self-timer, not a kill)
Final file size: 1,494,117,633 bytes
Last non-empty line: 2026-08-19T22:32:07.5934040Z 2E3FABD4...48229AC (93 chars, well-formed, trailing newline present)
```

Because it stopped gracefully on its own schedule rather than being cut
off mid-write, the file shows no corruption -- which means this run
answers "did `kill_phrase` reach it" (no) but not "what does a real
forced interruption do to the file" (not tested; nothing here actually
interrupted the write).

## Reproduction (round 2) and the corruption question

The operator asked for a rerun to verify this wasn't a one-off, this
time with the write-loop's self-timer removed entirely -- so
`kill_phrase` would be the *only* thing that could ever stop it, and a
real race between "still writing" and "being killed" would actually
happen (round 1's process happened to hit its own 3-minute cap first).

Getting a second working write-loop running took a few tries again
(one attempt's candidate PIDs turned out to already be dead by the time
they were checked; a live STT hallucination of the same "brake" pattern
documented in `2026-08-18-brake-safeword-stt-hallucination-false-
positive-hard-stop.md` also hit mid-launch and hard-stopped an in-flight
tool call -- see log below). The eventual working process (PID 35192,
`pwsh.exe -File writer.ps1`, no time limit) landed in
`D:\LegionForge\convobox-UAT` rather than the intended isolated scratch
directory -- the session's `working_dir` reverted to the source tree
for this run rather than the earlier `_uat-force-kill-scratch`; not
investigated further here, and the resulting files were deleted after
the test (see below) so nothing was left in the tracked tree.

```
2026-08-19 17:57:23,229 transcript='brake brake...' (x~75) lang=en (0.61) dec=0.86 busy=True  [HARD STOP]
2026-08-19 17:57:23,229 hard stop matched safeword 'brake brake brake'
2026-08-19 17:57:23,263 hard-stop interrupted a turn that was still busy -- if it included a tool call,
                          the underlying process is not guaranteed to have stopped; any result it
                          eventually produces will be discarded, not spoken
```

PID 35192 survived that interruption and kept running regardless --
consistent with this whole note's finding that a hard-stop/kill event
not reaching a spawned process is the default, not the exception, on
this platform.

```
17:58:22,696 transcript='eject eject eject' ... [HARD STOP]
17:58:22,696 kill phrase matched 'eject eject eject' -- force-killing backend
17:58:27,633 exiting
```

Checked directly afterward: PID 35192 alive, 150.6 CPU-seconds burned,
file at 1,924,494,895 bytes and still growing, more than a minute after
the session had fully exited. Because this run had no self-timer, it
would have kept running (and kept growing the file) indefinitely --
stronger evidence than round 1, where the process's own 3-minute cap
left some ambiguity about how long it would otherwise have survived.

With the finding re-confirmed, the operator asked to force-kill the
process directly and clean up:

```
PRE-KILL  size: 3,188,637,095 bytes at 18:01:26.253
Stop-Process -Id 35192 -Force  issued at 18:01:26.257
POST-KILL size: 3,189,214,790 bytes (grew ~578KB in the gap between snapshots --
                the write loop was still actively running at the moment of the kill)
Last line: 2026-08-19T23:01:26.2612210Z e4c525ea8e3de4c0b484d905129828785aedc02057360b9d8670e72801403f45
           (93 chars: well-formed timestamp + complete 64-hex-char SHA256, trailing newline present)
```

**No corruption, even under a genuinely forced, immediate termination
of a process that was writing at the instant of the kill.** This
answers round 1's open question, but as a negative result specific to
this test's write shape: each record is a single small (~93-byte)
`StreamWriter.WriteLine()` call with `AutoFlush`, which on NTFS appears
to complete as one atomic `WriteFile` faster than `TerminateProcess`
can land mid-call. This does not establish that torn writes are
impossible in general -- a larger single write (e.g. writing a whole
buffer/multi-KB record at once) would be a meaningfully different test
and was not tried here.

Both files (`hashlog.txt`, ~3.19 GB; `writer.ps1`) were deleted after
the test; `git status` confirmed the checkout was never tracking them.

## Mechanism -- CORRECTION: the leading hypothesis below did NOT survive automated testing

The `Start-Process`/job-object explanation originally written here (still
shown below for the record) turned out to be wrong, or at least
incomplete, once tested under controlled conditions. An automated harness
(`_test_force_kill_stops_a_real_tool_call.py`, extended 2026-08-19) drove
the exact same launch method -- `Start-Process powershell -ArgumentList
'-Command', "..." -PassThru` -- deterministically, both with a short
(~7-9s) and a long (75s, chosen to match this note's own 83s live gap)
delay before calling `force_kill()`. **All 4 automated attempts (2 short-
delay, 2 long-delay) had `force_kill()` kill the detached process
successfully, in 2.08-2.38s each time.** Against that, both live attempts
in this note (PID 17328, PID 35192) survived. Same launch method, same
elapsed-time range covered, opposite outcome -- the hypothesis that
`Start-Process` detachment alone explains the survival is therefore
**not confirmed, and probably incomplete**.

Two real differences between the live and automated cases remain
untested as the actual differentiator:
- The live successful process was launched via `-File writer.ps1`
  (running a script file) rather than `-Command "..."` (a command
  string) -- the automated harness has only tried the latter.
- The live session had multiple failed tool-call attempts (mangled
  quoting, an unavailable .NET method) before the successful spawn --
  the automated harness issues one clean command with no prior retries
  or accumulated session state.

Next diagnostic step: add a `-File`-based automated variant, and/or one
that deliberately fails once before the real attempt, to isolate which
(if either) of these is the actual cause. Until then, treat this note's
finding (the orphaning itself) as validated-live and unaffected by this
correction -- only the proposed MECHANISM behind it is now open again.

### Third live reproduction (2026-08-19, same evening) -- strengthens the "prior failed attempt" candidate

A third live voice round, using an intentionally open-ended prompt ("write
a script that keeps hashing data and appending timestamped entries to a
log file, run it as a background process, don't wait for it, tell me the
PID") reproduced the exact same finding a third time: PID 67440
(`powershell.exe -File hash-log-worker.ps1`), spawned 19:33:13, its own
direct launcher (`pwsh.exe`, PID 70612) already dead 1.4s later
(19:33:14.775) -- orphaned while still running, same shape as both prior
occurrences. Kill phrase matched 19:33:43.832, session fully exited by
19:33:46.197; the worker was independently confirmed still alive and its
log still growing (15,580 -> 31,255 bytes, timestamps to 19:34:39) a full
minute after the kill.

Notably, this session ALSO had a failed first attempt immediately before
the successful one, in the same codex session: PID 64584 (an earlier
`powershell.exe -File hash-log-worker.ps1`) died 1.4s after spawning,
codex reporting "incompatible hash function" before retrying. **All
three live reproductions of this finding, across two different sessions
and two different specific scripts, have now had a failed prior attempt
in the same session immediately before the successful orphaned spawn.**
None of the 8 automated attempts (4 dictated, 4 freeform launch method,
short and long delay) ever had this condition -- the harness always
succeeds cold, first try. This is now the strongest surviving candidate
for the real differentiator, ahead of launch-method choice or elapsed
time (both directly tested and ruled out as sufficient on their own).
Still not confirmed as causal -- next step is an automated variant that
deliberately induces one failed attempt before the real one.

### Fourth live reproduction -- the "prior failed attempt" hypothesis did NOT survive this one

A fourth live round, same evening, used a clean single-shot sequence with
no retry: `tool_call tool=fileChange` (writing `wait-90-seconds.ps1`) at
19:39:13, then `tool_call tool=commandExecution` (launching it via
`Start-Process -FilePath 'powershell.exe' -ArgumentList @(...,'-File',
$scriptPath) -WindowStyle Hidden -PassThru`) at 19:39:21 -- no error, no
retry, nothing resembling the prior three rounds' failed-first-attempt
shape. PID 44140 (`powershell.exe -File wait-90-seconds.ps1`, created
19:39:23) still survived: kill phrase matched 19:39:35.187, session
exited 19:39:37.814, PID confirmed alive directly (`Get-Process`) several
minutes later. **This rules out "a prior failed attempt in the same
session" as a necessary condition** -- it correlated with 3 of 4 live
occurrences but wasn't present in the 4th, which still failed.

Across all 12 trials to date (4 live, all failed; 8 automated, all
passed), the one variable that has never been varied on either side: the
automated harness runs from inside Claude Code's own process tree
(spawned via its Bash tool); every live failure has run from a plain
interactive PowerShell/Windows Terminal session launched directly by the
operator. This is now the strongest surviving candidate by elimination,
not by direct evidence -- next step is running the harness itself from a
plain terminal, outside Claude Code entirely, to test it directly.

### Fifth live reproduction -- codex's own PID-tracking confusion caused a DUPLICATE orphaned process

A fifth live round (same evening) surfaced a new, worse variant. codex
launched the worker (`hash-worker.ps1`, an infinite `while ($true)` loop
hashing data and appending to a log every second, no self-timer), then
lost track of which PID it actually was -- its own verification checked
`Get-Process -Id 8420` (already dead, almost certainly the intermediate
`Start-Process` launcher, not the real worker) and got `Running: false`,
despite the log demonstrably still advancing (`iteration=56`) at the
same moment. Concluding the launch had failed, codex **relaunched a
second instance of the same script**. Both ended up running
concurrently: PID 48144 and PID 31132, both `pwsh.exe -NoLogo -NoProfile
-NonInteractive -ExecutionPolicy Bypass -File hash-worker.ps1`, both
independently confirmed alive via direct `Get-Process` before the kill
phrase was said. After `eject eject eject` matched (19:57:13.765) and
the session fully exited (19:57:19.009), **both PIDs were confirmed
still alive** -- not one orphan, but two duplicate ones, both still
appending to the same log file with no coordination between them.

This is a materially worse failure mode than plain orphaning: it is not
just "the kill didn't reach the process," it is "the agent's own
confusion about whether its first attempt succeeded caused it to spawn
a second, redundant copy of a process that then ALSO couldn't be
stopped." A real user relying on "say the kill phrase and everything
stops" would in this scenario be left with two uncoordinated background
writers instead of zero.

(Watcher note: this round's orphaned PID was initially missed by a live
process-diff watcher built earlier the same session -- its ancestry walk
required every intermediate ancestor to still be alive at poll time, so
a launcher dying between polls caused the real descendant to be silently
un-tracked. The process was still confirmed independently via a direct
`Get-Process`/`Get-CimInstance` check. Watcher logic fixed after this
round to cache any PID once confirmed a descendant, rather than
re-deriving the full ancestry chain on every poll.)

### Original hypothesis (kept for the record, not confirmed)

`CodexAdapter.force_kill()` (`src/convobox/adapters/codex.py:599-645`)
does two things: `_terminate_and_kill_process()` on `self._proc` (the
top-level codex app-server handle), and -- **POSIX only** --
`_kill_by_command_text()`, a `ps`-based best-effort fallback that
matches and kills real descendants by command-line text. That fallback
is unconditionally excluded on Windows (`sys.platform != "win32"` at
the call site, `codex.py:644`), because `signal.SIGKILL` and `ps` don't
exist there. On Windows, `force_kill()` is only ever
`proc.terminate()`/`proc.kill()` against the single top-level handle.

The earlier `sleep 90` test in the same session showed this single
top-level kill *can* cascade through a whole live descendant tree on
Windows (6 levels deep: `codex.exe` -> `node.exe` -> `cmd.exe` ->
`codex-command-runner.exe` -> `pwsh.exe` -> nested `pwsh.exe` running
`Start-Sleep`), all dying within ~2.4s of the top-level kill -- almost
certainly because that whole chain was still a live, attached
descendant tree (likely under a Windows Job Object) at the moment of
the kill. This test's process did not share that shape: by the time it
was checked, its own immediate parent (PID 56928) was already gone,
meaning it had detached from whatever tree `force_kill()` could reach
-- plausibly because the multiple `Start-Process`-based launch attempts
codex went through to get a working command (see Evidence) left the
eventually-successful process parented to an intermediate launcher that
itself exited early, orphaning PID 17328 from the tree `force_kill()`
walks. This was not deliberately engineered -- it's simply what codex
produced when asked, in natural language, for a CPU-heavy background
write loop, the same way the 2026-08-18 field note found that natural
phrasing (vs. an explicit dictated one-liner) produces meaningfully
different process shapes than scripted tests anticipate.

## Automated follow-up (2026-08-20): four more hypotheses tested, all ruled out

Extending `_test_force_kill_stops_a_real_tool_call.py`, four more candidate
differentiators between the 8/8-passing automated harness and this note's
5/5-failing live reproductions were each isolated as the SOLE variable
against an already-passing baseline (`detached_background_sleep_freeform_
long_delay`, 2/2 pass) and tested. All four passed again -- each is now
ruled out as the mechanism on its own:

- **Process-tree location (inside vs. outside Claude Code's own process
  tree).** The prior correction above (round dated 2026-08-19) flagged
  this as the strongest surviving candidate by elimination -- every
  automated pass to that point had run from inside Claude Code's own Bash
  tool; every live failure ran from a plain interactive terminal. Re-run
  identically (`detached_background_sleep`, `_long_delay`,
  `_freeform`, `_freeform_long_delay`, 2 iterations each = 8 runs) from a
  plain PowerShell window the operator opened directly, no Claude Code
  involved at all. **8/8 passed.** Ruled out.
- **`permission_mode` mismatch.** Before assuming the harness's hardcoded
  `permission_mode="permissive"` matched what live UAT actually ran under,
  this was checked directly rather than assumed: `convobox-UAT/
  convobox-tui.log` shows every live session on 2026-08-19, including
  every failing one, logged `backend permission_mode: permissive (codex)`.
  Separately, loading the harness's own config confirmed its
  `make_adapter()` hardcodes `permission_mode="permissive"` for codex
  regardless of whatever `convobox.yaml` happens to be loaded (the config
  file's own `backend.permission_mode` field, which resolved to `"plan"`
  in the dev repo's `convobox.yaml`, is silently ignored for this path) --
  resolving to the same `-c approval_policy=never -c
  sandbox_mode=workspace-write` overrides either way. Confirmed equivalent,
  not just assumed. Ruled out before it cost more test cycles.
- **Session depth / turn count.** Every prior automated run created a
  brand-new `CodexAdapter` (and therefore a fresh codex session) per
  iteration, so the real prompt was always turn 1. Every live failure
  happened deep into an evening-long session. New scenario
  `detached_background_sleep_freeform_warm_session`: 5 unrelated filler
  turns sent through the SAME adapter/session before the real prompt (turn
  6, not turn 1). This generalizes the earlier "prior failed attempt"
  candidate (round 3 vs. round 4 above) into turn-depth itself, not just
  one specific failure-shaped prior turn. **3/3 passed.** Ruled out.
- **Bypassing `Orchestrator` entirely.** The harness always called
  `CodexAdapter.send_text()`/`force_kill()` directly, with a one-shot event
  consumer that stops reading `adapter.events()` the instant it sees
  `TOOL_CALL` -- reading nothing for the rest of `kill_delay_seconds`. The
  real app instead calls `Orchestrator.handle_transcript()`
  (`orchestrator.py:234`), which runs `_consume_events()` as a background
  task that drains `adapter.events()` CONTINUOUSLY for the whole session,
  and only reaches `force_kill()` via `SafewordDetector` matching the kill
  phrase inside that same method -- a structurally different I/O and
  call-path shape, and plausibly relevant given this harness's own runs
  already log recurring `readline() still pending` warnings (cross-
  referenced to docs/KNOWN-ISSUES.md's VAD-freeze entry) that suggest the
  app-server's stdout pipe is sensitive to how continuously it gets read.
  New scenario `detached_background_sleep_freeform_via_orchestrator`:
  constructs a real `Orchestrator` (same `SafewordDetector`,
  `kill_phrase="eject eject eject"`) and routes BOTH the prompt and the
  kill through `handle_transcript()`, exactly as `run_convobox.py` does,
  instead of calling the adapter directly. The real log line `kill phrase
  matched 'eject eject eject' -- force-killing backend` confirmed the real
  code path fired, not a silent bypass. **3/3 passed.** Ruled out.

A fifth candidate was added and tested the same day:

- **CPU contention from real-time STT inference.** Every automated run to
  this point had an essentially idle CPU; every live failure ran with
  real-time Whisper transcription competing for cycles on the same
  machine, and Windows process creation/detachment timing is exactly the
  kind of thing that can differ under real contention. New scenario
  `detached_background_sleep_freeform_cpu_load`: spawns `os.cpu_count()`
  CPU-bound worker processes (repeated SHA-256 hashing, no I/O -- genuine
  multi-core saturation, not a token load) for the scenario's whole
  duration, simulating the load real Whisper CPU inference puts on the
  machine, no mic required. **3/3 passed.** `force_kill()`'s own elapsed
  time did visibly increase under load (0.14s/0.47s/0.20s vs. the usual
  ~0.05s baseline) -- a real, measurable effect of the contention -- but
  not enough to flip the outcome. Ruled out.

Running tally after this round: **25/25 automated passes, 5/5 live
failures.** Every cheaply-scriptable difference identified by reading the
code, including a genuine CPU-saturation stress test, has now been tested
and closed. What remains untested is structurally different from anything
above and not further scriptable: the real audio/STT/VAD pipeline (not
just prompt text but real concurrent mic capture, VAD segmentation, and
Whisper inference timing/scheduling interacting with process creation in
ways a synthetic CPU load doesn't replicate) and real TTS/audio playback
active concurrently (the harness passes `tts=None, player=None` to
`Orchestrator`; live sessions run a real Piper engine). At this point the
honest read is that closing this gap needs an actual live-voice re-run,
not another scripted variant -- five independently-varied hypotheses
(process-tree location, permission_mode, session depth, event-draining
path, CPU contention) have each been ruled out in isolation without
finding the mechanism.

### Sixth live reproduction (2026-08-20, next day, real-time independently verified)

The requested live re-run (the honest conclusion of the automated follow-up
above) happened the same day it was written, on a real voice session:
`run_convobox.py --tui --web --aec-dump -v --working-dir d:\LegionForge\
_uat-force-kill-scratch`, backend codex, `permission_mode: permissive`
(confirmed from the session's own startup log line). The operator asked,
in natural language: "Write a small script that keeps hashing some data
and appending timestamped entries to a log file, and run it as a
background process that keeps going after you respond -- don't wait for
it to finish, just tell me the process ID once it's running." Codex's own
report inside the session named PID 39972 as current, after first
reporting and then replacing an earlier PID 46960 -- the same PID-
confusion flavor as the fifth reproduction above, though this time
resolving to a single correct final process rather than a duplicate.

Unlike every prior round in this note, this one was checked with an
independent observer running the whole time (this session's own AI
assistant, polling `Get-Process`/`Get-CimInstance` directly against the
real PIDs -- not trusting codex's in-session report, and not the automated
harness):

- **Before the kill phrase**: PID 39972 confirmed real
  (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  D:\LegionForge\_uat-force-kill-scratch\hash-loop.ps1`, created
  17:35:22) -- and already orphaned from its own launcher at that point:
  its parent (PID 22816) was already dead before the kill phrase was even
  said, same detachment shape as every prior round. PID 46960 (the
  earlier, replaced PID) independently confirmed already dead.
- **Kill phrase fired** ("killed," operator's own words, matching this
  note's `eject eject eject`). Independently confirmed: no `python.exe`
  or `codex.exe` process remained anywhere on the system immediately
  after -- the ConvoBox session itself, and codex's app-server, both
  genuinely exited, exactly as every prior round found.
- **PID 39972 survived.** Confirmed alive 4+ seconds after the kill via a
  direct `Get-Process` check, CPU time climbing (0.61s -> 0.64s), and
  `hash-loop.log` actively growing (5,120 -> 7,620 bytes) with fresh
  timestamps (`17:36:31`, `17:36:32`) -- unambiguous proof it was still
  live and working, not a stale handle.
- Cleaned up afterward: `Stop-Process -Id 39972 -Force`, confirmed dead,
  scratch files removed.

This is the first reproduction in this note verified by an independent
observer polling the real OS process/file state directly, in real time,
rather than relying on the operator's or the agent's own report of what
happened. Same mechanism, same outcome, a full day after the original
5/5 -- **6/6 live failures to date**, and the first one to happen after
25/25 automated passes had already ruled out five separate hypotheses
without finding what differs. Whatever the automated harness still isn't
replicating, it isn't something that stopped reproducing over time.

## Why this matters

This is the Windows counterpart to the documented macOS gap, reached by
a different path (detachment/orphaning vs. macOS's own-process-group
behavior), and currently has **no mitigation at all on Windows** -- the
fallback built for the macOS case is explicitly platform-gated away
from ever running here. A user who says the kill phrase expecting
"everything stops now" can be left with a CPU- and disk-consuming
runaway process for as long as that process's own logic keeps it alive
-- in this test, over two more minutes, and only because the test
script happened to have a self-imposed cap. A less considerate spawned
process would not have one.

## What transfers

- **A single top-level `proc.kill()` reaching a live descendant tree in
  one test does not establish it reaches every descendant tree** --
  whether it cascades depends on whether the target is still attached
  to that tree at the moment of the kill, which in turn depends on
  exactly how it was spawned. Two processes from the same session, same
  backend, same platform, produced opposite outcomes. (validated-live)
- **A feature's own "ends this session" framing describes the
  ConvoBox process, not necessarily every process it caused to exist**
  -- the session genuinely did end (confirmed: process gone, log says
  "exiting"), but a user experiencing "the whole thing stopped" would
  reasonably assume everything it spawned stopped too. That gap between
  what actually happened and what a plain reading of the log/behavior
  implies is itself worth flagging. (validated-live)
- **The orphaning finding reproduces independently, not a one-off.** A
  second run, with the write-loop's self-timer removed so `kill_phrase`
  was the only possible stop condition, showed the identical outcome:
  session exits cleanly, spawned process keeps running and writing
  indefinitely. (validated-live)
- **A small (~93-byte), `AutoFlush`-per-line write survives even a hard
  forced termination (`Stop-Process -Force`) without corruption** --
  each `WriteLine()` call appears to complete as one atomic syscall
  faster than `TerminateProcess` can interrupt it. This is a real,
  observed result, but it is specific to this write shape: it does not
  establish that a larger single write (multi-KB buffer, unflushed
  batch) would survive the same kind of interruption equally cleanly.
  (validated-live, narrow scope)

## Not done here

- No fix attempted -- capture-and-diagnose only, matching this
  project's established practice for a freshly found gap.
- Did not determine exactly which of the several `Start-Process`/nested
  `pwsh` launch attempts is what caused the orphaning, or whether a
  differently-launched (but equally CPU/disk-heavy) process would have
  stayed attached to the tree the way `sleep 90` did. A controlled
  rerun, deliberately varying how the write-loop is launched, would
  narrow this down.
- Did not test a larger-single-write shape (a multi-KB buffered write
  rather than a small `AutoFlush`-per-line one) against a forced kill --
  round 2's no-corruption result may not hold for that case.
- Did not determine why round 2's session `working_dir` reverted to the
  source tree (`convobox-UAT`) instead of the isolated scratch
  directory used successfully in round 1's session.
- Did not test the equivalent scenario on claude-code or opencode
  backends, or on macOS/Linux.
- (2026-08-20) Did not yet test the real audio/STT/VAD pipeline in the
  loop -- every automated scenario to date, including all four in the
  follow-up section above, sends the prompt as plain text
  (`send_text()`/`handle_transcript()` called directly with a Python
  string), never through real mic capture, VAD segmentation, or Whisper
  transcription. This is the largest remaining untested difference from
  every live failure and may require an actual live voice re-run to
  close, not further scripting.
- (2026-08-20) Did not yet test with a real TTS engine/player wired into
  `Orchestrator` -- every scenario so far passes `tts=None, player=None`,
  while live sessions run a real Piper engine speaking responses
  concurrently with the backend's tool call.
- (2026-08-20) Five automated hypotheses (process-tree location,
  permission_mode, session depth, Orchestrator event-draining path, CPU
  contention) were each individually ruled out -- 25/25 automated passes
  vs. 5/5 live failures. No further scripted variant is planned; next
  step is an actual live-voice re-run of the original reproduction (see
  the "Automated follow-up (2026-08-20)" section above), since the
  remaining candidates (real audio/STT/VAD pipeline, real TTS/player
  concurrently active) are not meaningfully testable without one.
