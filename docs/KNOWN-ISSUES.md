# Known issues

Diagnosed problems, with enough detail to pick up without
re-investigating. Most are deferred; a few are **fixed** and kept here
anyway, because the mechanism is subtle enough to be worth having on
hand if the symptom ever returns — those are marked `Fixed` in the index
below and in their own status line. [CHANGELOG.md](../CHANGELOG.md) is
the formal record of what shipped when; this file is the record of *why*
something broke.

Severity reflects consequence to a live session, not effort to fix.
**High** means it can affect whether the agent actually stops when told
to. Entries in [Safety-critical](#safety-critical) are the ones to read
before trusting a voice session with write access; see also
[PERMISSION-MODEL.md](PERMISSION-MODEL.md).

## Index

| Issue | Component | Platform | Status | Severity |
|---|---|---|---|---|
| **Safety-critical** | | | | |
| [A hard-stop (safeword or pause phrase) does not guarantee an in-flight tool call actually stops](#a-hard-stop-safeword-or-pause-phrase-does-not-guarantee-an-in-flight-tool-call-actually-stops) | Orchestrator | All | Validated-live | High |
| [`kill_phrase` ends the ConvoBox session cleanly on Windows, but does not reach an orphaned/detached child process it spawned](#kill_phrase-ends-the-convobox-session-cleanly-on-windows-but-does-not-reach-an-orphaneddetached-child-process-it-spawned) | Force-kill | Windows | Validated-live | High |
| [`--text` mode + `permission_mode: approve` abandons a pending approval instead of denying it](#--text-mode--permission_mode-approve-abandons-a-pending-approval-instead-of-denying-it----fixed) | Approvals | All | Fixed | — |
| [STT error-ladder rejection gates on language probability, not decode confidence](#stt-error-ladder-rejection-gates-on-language-probability-not-decode-confidence----a-low-confidence-hallucination-can-slip-through) | STT gating | All | Validated-live | Medium |
| ["halt halt halt" (a default hard-stop phrase) failed round-trip transcription 4/5 times; bare "Athena" (default resume word) failed 3/5](#halt-halt-halt-a-default-hard-stop-phrase-failed-round-trip-transcription-45-times-bare-athena-default-resume-word-failed-35) | Safeword STT | All | Validated-live | Medium |
| [A misheard safeword can land on the pause phrase instead of the safeword](#a-misheard-safeword-can-land-on-the-pause-phrase-instead-of-the-safeword----same-hard-stop-effect-different-resulting-state) | Safeword | All | Diagnosed | Low |
| [A safeword match in a transcript skips checking that same transcript for a pause phrase](#a-safeword-match-in-a-transcript-skips-checking-that-same-transcript-for-a-pause-phrase----fixed) | Safeword | All | Fixed | — |
| **Stability, freezes, and crashes** | | | | |
| [faster-whisper's native allocator can fail during a long session](#faster-whispers-native-allocator-can-fail-during-a-long-session) | STT | All (Windows-observed) | Mitigated | Medium |
| [VAD segmenter's per-window model call is synchronous with no offload/timeout](#vad-segmenters-per-window-model-call-is-synchronous-with-no-offloadtimeout----can-plausibly-freeze-the-whole-app) | VAD / mic loop | macOS, Windows, Linux | Mostly resolved; mic-layer-only variant still open | Medium |
| [Backend can go silently busy for minutes with zero output](#backend-can-go-silently-busy-for-minutes-with-zero-output----root-cause-unconfirmed) | Backend | All | Diagnosed | Medium |
| **Speech pipeline (STT / TTS / AEC)** | | | | |
| [Kokoro can't synthesize past ~510 phonemes](#kokoro-cant-synthesize-past-510-phonemes----hard-model-limit-not-a-configmode-issue) | TTS (Kokoro) | All | Diagnosed | Low |
| [WebRTC APM's noise suppression / auto gain control are unused (candidate, awaiting go-ahead)](#webrtc-apms-noise-suppression--auto-gain-control-are-unused-candidate-awaiting-go-ahead) | AEC | All | Candidate | — |
| **Platform and compatibility** | | | | |
| [WASAPI output plays speech an octave too high ("static chipmunk")](#wasapi-output-plays-speech-an-octave-too-high-static-chipmunk) | Audio output | Windows | Deferred | Medium |
| [AEC builds from source on macOS](#aec-builds-from-source-on-macos--pypi-just-doesnt-ship-a-wheel-for-it) | Install (AEC extra) | macOS | Verified | Low |
| [A Mac's front 3.5mm jack mutes the internal speaker at the hardware level, regardless of software output-device selection](#a-macs-front-35mm-jack-mutes-the-internal-speaker-at-the-hardware-level-regardless-of-software-output-device-selection) | Audio output | macOS | Verified | Low |
| **Backend integration (including upstream bugs)** | | | | |
| [opencode 1.18.3: session-level model pin silently never generates (upstream)](#opencode-1183-session-level-model-pin-silently-never-generates-upstream) | opencode (upstream) | All | Upstream, no fix | Medium |
| [Codex `permission_mode: approve` crashes on current codex-cli -- `approval_policy=untrusted` was removed upstream](#codex-permission_mode-approve-crashes-on-current-codex-cli----approval_policyuntrusted-was-removed-upstream) | codex (upstream) | All | Diagnosed, unfixed | High |
| **Web UI** | | | | |
| [Web UI: artifact pane gaps (0.3.0)](#web-ui-artifact-pane-gaps-030) | Web UI | All | Deferred | Low |
| [Web UI: a short CancelledError traceback can appear on quit/Ctrl+C](#web-ui-a-short-cancellederror-traceback-can-appear-on-quitctrlc) | Web UI | All | Mostly mitigated | Low |
| ["Open in editor" occasionally opens a different file than the one clicked](#open-in-editor-occasionally-opens-a-different-file-than-the-one-clicked----fixed) | Web UI | All | Fixed | — |
| **Cosmetic and diagnostic** | | | | |
| [A hard-stopped in-flight turn can show as a generic "error_during_execution" turn](#a-hard-stopped-in-flight-turn-can-show-as-a-generic-error_during_execution-turn----cosmetic-mislabel) | TUI / labels | All | Diagnosed | Low |
| [Settings TUI ignores real terminal size below 80x24, and never repaints on resize alone](#settings-tui-ignores-real-terminal-size-below-80x24-and-never-repaints-on-resize-alone) | Settings TUI | All | Diagnosed | Medium |
| [Settings TUI arrow keys silently did nothing](#settings-tui-arrow-keys-silently-did-nothing----root-caused-and-fixed-confirmed-live-via-key-by-key-debug-instrumentation) | Settings TUI | All | Fixed, live-confirmed | — |

---

## Safety-critical

Anything touching the hard-stop path, the kill phrase, or tool approval.
These are the entries to read before trusting a voice session with write
access.

### A hard-stop (safeword or pause phrase) does not guarantee an in-flight tool call actually stops

**Status:** validated-live, 2026-08-09. Option 1 (honesty fix) was built
shortly after. Option 2 (escalating force-kill) got a Phase 1 build
2026-08-14 -- see below; the "escalating" part (automatic, timeout-based)
was deliberately NOT what got built. **The Windows 90/90 result below does
NOT transfer to macOS -- codex is 0/10 there; see the macOS finding
further down before assuming this mechanism works cross-platform.** Full
evidence, exact timestamps, and
the mechanism writeup: `docs/field-notes/2026-08-09-hard-stop-does-not-
cancel-an-in-flight-tool-call.md`.

**Symptom.** During a real ~1h38m live voice UAT session (codex backend,
real headset), saying the pause phrase or a safeword while a
`commandExecution` tool call was in flight consistently produced this
sequence: the interrupt RPC (`turn/interrupt` for codex; the equivalent
`control_request interrupt` / `POST .../interrupt` for claude-code /
opencode) succeeds with no error, and ConvoBox's own state (pause/resume,
safeword-matched, "resumed listening") transitions cleanly and
immediately -- but the tool call's real `tool_result` doesn't arrive
until the underlying shell command finishes on its own schedule, **16 to
48+ seconds later**, across 5 separate incidents. Reproduces identically
whether triggered by voice or the web UI's Stop-listening button (rules
out an STT-timing explanation), and stacking multiple hard-stop signals
in a row during the same wait doesn't shorten it.

**Why:** all three backend adapters' `send_hard_stop()` only signal the
agent's own conversational/orchestration layer to stop -- none of the
three vendor APIs is documented to guarantee killing a shell subprocess
the agent already spawned for a tool call, and ConvoBox never has a
process handle on that subprocess (it only observes the eventual
`tool_result` the agent chooses to report). This directly relates to,
but is a different mechanism than, the entry above (a misheard safeword
landing on the pause phrase) -- that entry's "in-flight work is
cancelled either way" claim is about ConvoBox's OWN turn-level state,
which this finding doesn't contradict; the gap here is one level deeper,
at the tool call's own OS process.

**Unlike this repo's other "can't force-kill" findings (STT/AEC thread
offload), this one is solvable** -- an OS process (unlike an in-process
Python thread) can always be force-killed, and ConvoBox already holds a
real process handle on each backend's own CLI subprocess (used cleanly
in every adapter's `aclose()` on shutdown). The capability exists;
hard-stop just doesn't currently escalate to using it.

**Two follow-up options identified, neither built yet:**
1. **Honesty fix (small, low-risk):** don't let the UI say "resumed
   listening" as if everything stopped when a hard-stop was sent but no
   corresponding `tool_result`/turn-completion has arrived yet -- track
   and surface that pending-cleanup state truthfully instead of
   silently going quiet about it.
2. **Escalating force-kill (bigger, needs its own scoping/UAT pass):**
   if no completion arrives within a grace period after the polite
   interrupt, escalate to killing and respawning the backend process.
   Trades the whole session/thread's context for an actual guarantee --
   should be a deliberate, probably config-gated choice, not a silent
   default. Candidate follow-up test scenarios (from a same-session
   discussion with the codex backend itself, live-testing its own
   cancellation semantics): does the process actually die and stop
   performing side effects after an abort, or does aborting-then-
   restarting the same command produce duplicate/detached execution;
   how does a natural timeout compare to a manual abort; does a command
   with its own restart policy resist cancellation; what happens to
   output ordering (pre-delay vs. post-delay messages) when a delayed
   command is interrupted mid-stream.

**Option 2, Phase 1 built 2026-08-14** -- a *manual* escalation, not the
automatic timeout-based one described above. Motivated by three real
freeze incidents the same evening (`readline()` hung 65.5s+/236.7s+/
unresolved-until-killed) where `send_hard_stop()`'s own polite interrupt
rode the SAME channel that was stuck, so it couldn't reach the backend
either. `BackendAdapter.force_kill()` (default: delegates to `aclose()`;
`CodexAdapter`/`ClaudeCodeAdapter` override with a real OS-level
`terminate()` -> wait 5s -> `kill()`, no RPC round-trip attempted at
all) + `Orchestrator.force_kill()` + an opt-in `safeword.kill_phrase`
config field (must be one of `hard_stop_phrases`; unset by default) that
routes ONE specific configured safeword to this instead of the normal
polite `hard_stop()`. JP's own config: `kill_phrase: "eject eject
eject"`. Ends the whole ConvoBox session afterward (same as Quit) --
does NOT attempt to keep the session alive by respawning and
reconnecting.

**Not done in Phase 1, scoped for a possible Phase 2:** reconnecting a
freshly-spawned backend to the SAME conversation after a kill, instead of
ending the session. Checked whether this is even possible 2026-08-14 via
`codex.cmd app-server generate-json-schema`: codex's real protocol has a
`thread/resume` RPC (`{threadId: string, ...}` params) that could
reconnect to an existing server-side thread -- genuinely unverified
whether resuming a thread whose last turn was violently killed mid-flight
behaves cleanly, and `codex.py`'s adapter doesn't call it today
(`_ensure_thread()` always calls `thread/start`, a fresh thread, when
`self._thread_id is None`). `claude_code.py`'s adapter runs with
`--no-session-persistence` and has no equivalent capability to reconnect
to even in principle. `force_kill()`'s own docstring on both adapters
scaffolds this seam (deliberately doesn't clear any resumable identifier)
without implementing it.

**Also not done:** an automatic, timeout-based escalation (the original
"if no completion arrives within a grace period, escalate" framing above)
-- what got built is an explicit, separate, operator-triggered phrase,
not a fallback that fires on its own after `hard_stop()` seems to be
taking too long. That would need its own scoping (what grace period, does
it also default to ending the session) and hasn't happened.

**Mechanism verified live, 2026-08-14, 30/30 -- voice/STT trigger reliability still open.** Two
separable questions here: (1) once force_kill() runs, does it actually
kill the real spawned subprocess (not just the app-server's own
top-level process, potentially leaving a shell/tool-call child as an
orphan)? (2) does saying "eject eject eject" out loud reliably reach
force_kill() at all -- correct STT transcription, no false positives on
normal speech, no false negatives from a misheard phrase? Only (1) is
verified so far.

A scratch reliability harness (`_test_force_kill_stops_a_real_tool_call.py`,
not committed -- JP's own request: "test it at least 10 times... try
multiple types") drove a REAL codex CLI (not the fake app-server the unit
suite uses) through `adapter.force_kill()` directly, 10 times each across
three real long-running tool-call shapes: a plain shell sleep, a
shell loop progressively writing a file (to also confirm genuine
mid-flight interruption, not just process death after the work already
finished), and a real outbound web fetch (`httpbin.org/delay/N`). Each
run located the actual spawned OS process(es) via Windows/WMI
`CommandLine` matching (no psutil dependency), called `force_kill()`,
and confirmed via `Get-Process` that every spawned process -- not just
the codex app-server's own top-level PID -- was actually dead afterward.
**Result: 30/30 passed**, zero orphaned processes in any run (verified
via a full post-run process-tree sweep, not just the harness's own
per-iteration check). The file-write scenario's "confirm genuine
mid-flight interruption" check never actually caught partial progress in
any of the 10 runs -- force_kill() fired before the shell loop's first
write in every case (real spawn latency eating most of the harness's
wait window), so that specific piece of evidence is unconfirmed even
though the underlying PASS/FAIL (process death) is solid across all 30.

A fourth type JP asked about, an MCP tool call, wasn't separately built:
by mechanism, a stdio-based MCP call spawns a real child OS process (same
class already covered above); an HTTP-based MCP call makes no separate
process at all (same shape as the web-fetch case). Flagged rather than
silently skipped -- standing up a real/mock MCP server would be new
infrastructure with no different mechanism to actually observe.

**Extended to claude-code and opencode, same evening (JP's own
follow-up request).** claude-code: identical methodology, **30/30**,
same "never caught mid-write partial progress" limitation, plus one
useful outlier -- a single `shell_sleep` run took `force_kill()` 4.92s
(every other run: 0.01-0.48s), live confirmation the `terminate()`
-ignored-so-escalate-to-`kill()` fallback path actually fires for real,
not just in the unit suite's mocked-timeout test.

opencode is architecturally different -- `OpenCodeAdapter` never owns an
OS process at all (an HTTP client to an already-running `opencode
serve`), so its `force_kill()` is the `BackendAdapter` default
(`aclose()`: close the connection, nothing more). Expected the real
spawned process to survive every time. **Actual: 23/30 matched that
expectation; 7/30 the spawned process died anyway** (highest on
`web_fetch_slow`, 4/10) even though `force_kill()` did the exact same
thing (an instant, ~0.00s local connection close) in every single run.
Root cause not established -- flagged as a real open question, not
guessed at further; needs opencode's own server internals to explain.
**Net: opencode's kill behavior is unpredictable and must not be relied
upon** -- the architectural limitation this section already named is
confirmed real, but "the process always survives" is not the accurate
description of what happens; "sometimes, unpredictably, it doesn't" is.

Full data (all three backends, three scenario types, 10 runs each, raw
per-iteration results) in `docs/field-notes/2026-08-14-force-kill-
reliability-across-all-three-backends.md`.

**macOS did NOT reproduce the Windows result when first checked
(2026-08-15) -- codex 0/10, claude-code 10/10, opposite split -- but the
codex gap is now CLOSED (fixed 2026-08-18, re-verified live against
current main).** The Windows 90/90 result does not transfer:
`terminate()`/`kill()` both map to Windows' `TerminateProcess()`, but
map to genuinely different POSIX signals (`SIGTERM`/`SIGKILL`) on
macOS, which do not cascade to children by default. For codex, every
spawned shell child originally survived `force_kill()` -- root cause
isolated to two stacked issues: (1) codex's default sandboxing (Apple
Seatbelt) reparents the real leaf process to `launchd` almost
immediately, detaching it from the app-server's process tree before
`force_kill()` ever runs; (2) even with sandboxing disabled and the
child genuinely still a live child of the process tree, `force_kill()`
still didn't reach it, since signaling only the top-level PID doesn't
cascade on POSIX. **`os.killpg()` was tried as the obvious candidate
fix and tested live -- it FAILS**: `os.getpgid()` on the real spawned
child confirmed it is its own process-group leader (`pgid` equals its
own `pid`), independent of the app-server's process group, true even
with sandboxing off (ruling out Seatbelt as the mechanism) -- this is
codex's own process-spawning behavior on macOS, not something a
process-group signal from ConvoBox's side can ever reach. claude-code
did not show either failure mode -- 10/10 clean, same as its own
Windows result.

**The actual fix**: `CodexAdapter.force_kill()` now falls back to a
`ps`-based command-line match (quote-stripped substring comparison
against the live process table, since codex reports the shell-quoted
INVOCATION text but the real process's argv has already had that
quoting consumed by intermediate shells) plus a recursive
descendant-kill (a multi-statement shell script forks its later
commands as separate child processes -- killing only the matched
top-level match orphans them otherwise). Re-verified live,
2026-08-18, against current main: 20/20 clean across two real
scenarios (`shell_sleep`, `file_write_progressive`), correctly showing
the full process tree (wrapper + forked children) confirmed dead, not
just the matched PID. Fragile by construction (depends on codex
continuing to report accurate command text) but closes the practical
gap: `safeword.kill_phrase` against a codex backend on macOS now
reliably stops a runaway spawned tool-call child, not just the
top-level process.

**Still open:** the voice/STT-trigger reliability question -- does
saying "eject eject eject" live, through the real mic pipeline, actually
match and route to `force_kill()` reliably, and does normal conversation
ever false-positive on it? That needs a real mic session, not scriptable
the way the process-kill mechanism above was. Also open: whether the
same `ps`-based approach holds on Linux (architecturally expected to,
not yet independently verified there); root-causing WHY claude-code
never showed the same failure mode codex did.

---

### `kill_phrase` ends the ConvoBox session cleanly on Windows, but does not reach an orphaned/detached child process it spawned

**Status:** validated-live, 2026-08-19, Windows 11 (helios), codex
backend ONLY (claude-code/opencode not yet tested), real mic sessions,
reproduced 5/5 independently in the same evening -- 0 live successes.
An automated harness testing the identical scenario passed 8/8, a
confirmed and still-unexplained divergence (see "Final tally" below) --
do not treat the automated result as predictive for this gap. Not
fixed -- capture-and-diagnose only, with a recommended fix direction
(Windows Job Object wrapper) scoped below. This is the Windows
counterpart to the macOS gap documented just above, reached by a
different mechanism (detachment/orphaning, not codex's own
process-group behavior), and currently has **no mitigation at all on
Windows**: the `ps`-based fallback that closes the macOS gap is
explicitly excluded there (`codex.py:634-645` -- `signal.SIGKILL`/`ps`
don't exist on Windows, so `force_kill()` only ever does
`proc.terminate()`/`proc.kill()` against the single top-level process
handle).

**Symptom.** A CPU- and disk-heavy background process spawned by codex
(a PowerShell loop hashing an incrementing counter and appending
records to a file) kept running -- still burning CPU, still growing the
file -- for over two minutes after `kill_phrase` fired, the log said
`force-killing backend`, and the entire ConvoBox process had already
exited (confirmed: no `codex.exe`, no ConvoBox python process, anywhere
on the system). The spawned process was already an orphan by the time
it was checked (its own immediate parent process was already gone,
independent of and unaffected by the kill), meaning it had detached
from whatever process tree `force_kill()`'s single top-level kill could
ever reach. A companion test the same evening showed a *plain*
`Start-Sleep -Seconds 90` spawned by codex *does* die correctly within
seconds of the same kill -- so this is not "Windows never cascades a
kill," it's specifically that a detached/orphaned descendant is
invisible to it, same as the underlying class of bug the macOS fix
addressed, just reached by a different path here.

**Reproduction (repeatable "known failure" check).** Ask codex, in
natural language, something like:

> "Write a PowerShell script to a file called `writer.ps1` in this
> directory. It should loop continuously -- computing a SHA256 hash of
> an incrementing counter each iteration, and appending a line with the
> counter, the current UTC timestamp, and the hash to `hashlog.txt`,
> flushing to disk after every single write -- but stop itself
> automatically after 10 minutes OR if `hashlog.txt` exceeds 2 GB,
> whichever comes first, as a safety cap. Then run `writer.ps1` as a
> background process using `Start-Process pwsh -ArgumentList
> '-File','writer.ps1' -PassThru`, and tell me the resulting process
> ID."

Getting a working process this way has taken multiple attempts both
times this was tried (nested-quoting errors in an inline `-Command`
one-liner, a .NET method unavailable in this PowerShell's runtime) --
writing to a real `.ps1` file first and running it with `-File` avoids
the quoting failures specifically. Once codex reports a PID, confirm it
is real (`Get-Process`/`Get-CimInstance Win32_Process` showing a
growing `hashlog.txt`), say the kill phrase, then check the PID again:
if it is still alive and the file is still growing, the gap has
reproduced. A well-formed record is `<counter> <UTC timestamp>
<64-hex-char SHA256>` -- the counter enables detecting gaps/duplicates
from a torn write anywhere in the file, not just at the tail.

**Corruption check, done once (2026-08-19):** force-killing the
orphaned process directly (`Stop-Process -Force`, not `kill_phrase`,
since `kill_phrase` never reached it) while it was actively writing did
**not** produce a torn record -- the last line was complete and
well-formed even with the file growing at the instant of the kill. This
is a narrow result: each write is a single small (~93-byte)
`StreamWriter.WriteLine()` call with `AutoFlush`, which appears to
complete as one atomic syscall faster than `TerminateProcess` can land
mid-call. It does not establish that a larger single write (a
multi-KB buffered write, or a batch written without per-line flushing)
would survive the same kind of interruption equally cleanly -- that
variant has not been tried.

Full detail, exact timestamps, and the live log excerpts:
`docs/field-notes/2026-08-19-kill-phrase-windows-orphaned-descendant-
survives-force-kill.md`.

**All testing to date is codex-only.** `claude-code` and `opencode`
have not been tested against this specific gap at all -- unknown
whether either shares it, is immune (different sandboxing/spawn
model), or is worse.

**Final tally after a full evening's live + automated testing:** 5/5
live voice reproductions (codex, real STT/TUI pipeline, varied scripts
and phrasings), 0/5 live successes at killing a detached background
process. Against that, 8/8 automated attempts (a harness driving
`adapter.force_kill()` directly, bypassing voice) all succeeded --
dictated launch command, freeform codex-chosen command, short delay,
and long (75s) delay were each tried, and each time the automated kill
worked while the equivalent live attempt failed. Neither launch
method, elapsed time, nor "a prior failed attempt in the same session"
survived as a sufficient explanation once tested directly. The
strongest surviving candidate, untested as of this writing: the
automated harness has always run from inside Claude Code's own process
tree (via its Bash tool), and every live failure has always run from a
plain interactive terminal -- if Claude Code's own subprocess handling
happens to use a Windows Job Object, that could incidentally catch and
kill children that would otherwise escape, making the harness an
accidentally-safer environment than real usage. **Practical
consequence: do not treat this automated harness's pass rate as
predictive for this specific scenario -- it has been directly
contradicted by live testing every time tried.** It remains useful for
everything else it covers (synchronous commands, the no-corruption
data, cleanup logic).

One live round also surfaced a second, worse failure mode: codex's own
PID tracking got confused by the same detachment (it checked a PID
that had already exited while the real worker's log was still visibly
advancing), concluded the launch had failed, and **launched a second,
duplicate copy of the same background process** -- both survived the
kill phrase independently, both still writing to the same log file
with no coordination. Not just an unkillable process, but a doubled
one.

**Recommended fix direction, not yet built:** rather than continuing
to chase individual detachment patterns (an unbounded reactive game --
`Start-Process`, `Start-Job`, and whatever else an agent invents all
have to be handled separately under the current approach), wrap the
codex process in a Windows Job Object at spawn time
(`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, breakaway disabled). This is a
structural, one-time fix at the point ConvoBox creates the subprocess
-- it would force everything that process tree ever spawns, regardless
of how it detaches, to die when the job closes. Scoped as real
follow-up work, not built here.

**Still open:** why this session's `working_dir` reverted to the
source tree instead of the isolated scratch directory used
successfully in an earlier session the same evening; whether a
larger-single-write shape tears under a forced kill; the equivalent
scenario on claude-code and opencode backends, and on macOS/Linux for
this specific orphaning-not-process-group mechanism.

---

### `--text` mode + `permission_mode: approve` abandons a pending approval instead of denying it -- fixed

**Status:** diagnosed live 2026-08-11, macOS (Mac mini M4), both claude-code
and codex backends. Fixed 2026-08-27 -- always was fail-safe in practice
(nothing ever got written without a real answer), the fix makes the
mechanism honest: an explicit decline is now sent instead of a silent
disconnect.

**Symptom.** Ask either backend (in `--text` mode, `permission_mode: approve`)
to write a file: the approval prompt fires correctly
(`Approval needed to run Write. Say <phrase> to approve...` for claude-code;
`item/fileChange/requestApproval` for codex), then **exactly 120 seconds of
silence**, then `backend still busy after 120s; giving up the wait` and the
process exits. No file is ever created, on either backend, confirmed twice
for codex and once (to full resolution) for claude-code.

**Root cause.** `ApprovalPromptGate`'s own `approval_timeout_s` (default
30s), the thing that's supposed to auto-deny a silently-abandoned approval
prompt, is only ever ticked by `_working_watchdog` -- and
`scripts/run_convobox.py` only constructs `watchdog_task` in the mic-loop
setup path, well after `--text` mode's own early `return`. So in `--text`
mode, `approval_gate.observe_timeout()` is never called at all; the
approval just sits pending until an unrelated, generic 120s
"`backend still busy`" bail-out in `_drain_until_idle` gives up and the
script calls `adapter.aclose()`, disconnecting the backend without ever
sending an explicit decline.

**Why this matters even though nothing unsafe happens.** The net effect is
safe today (no destructive action executes without a real answer), but
what looks like "the system denied my request" is actually "the system
gave up waiting and disconnected" -- a real distinction if this approval
channel is ever built on further (e.g. surfaced to a caller who cares
*why* a request didn't go through, or a future mode where abandon and
deny should behave differently).

**Fix (2026-08-27):** took the second candidate -- `--text` mode's own
exit path now calls a new helper, `_deny_pending_approval_before_text_exit`
(`scripts/run_convobox.py`), right after `_drain_until_idle` gives up and
before `adapter.aclose()`. If `approval_gate.is_waiting`, it calls
`orchestrator.resolve_pending_approval(False)` explicitly (same "silence
is never consent" invariant the mic loop's own watchdog-driven timeout
already enforces) and clears the gate. Chose this over constructing a
lightweight watchdog in `--text` mode: it's a smaller, more targeted
change, and `--text` mode is inherently a single-shot request with no
further waiting to do once `_drain_until_idle` has already given up --
there is no "keep ticking a timer" case left to build for.

**Verified:** unit tests in `tests/test_approval_prompt_gate.py`
(`test_deny_pending_approval_*`, fake orchestrator/gate/web-forwarder,
covers "no gate," "not waiting," "denies and clears," "stale gate with
nothing pending to decline," "no web forwarder configured"), plus a live
before/after repro against a real `claude-code` subprocess
(`permission_mode: approve`, `--text "create a file called probe.txt..."`,
`--timeout 8` to shorten the wait): before the fix, the run ended with
only `backend still busy after 8s; giving up the wait` and no file
created; after the fix, the same run additionally logs `declined pending
backend approval before --text mode exit`, with the file still never
created either way (this was never a write-safety gap). Full test suite
(1561 passed, 1 skipped) green after the change. No live mic-loop
regression check was needed -- the mic loop's own watchdog-driven path is
untouched by this change.

**Also attempted, inconclusive (2026-08-11, still open):** the live mic-loop voice-approval flow
itself (the thing `--text` mode structurally can't exercise) -- 4 live
synthetic-injection attempts, blocked by real, loud ambient background
noise in the test room that session (not a code issue). Full detail,
plus the clean `plan`/`permissive` mode confirmations on both backends
(N=2 each) and a re-confirmation that opencode remains untestable
(0 configured credentials): `docs/field-notes/2026-08-11-permission-model-validation-claude-codex-opencode.md`.

---

### STT error-ladder rejection gates on language probability, not decode confidence -- a low-confidence hallucination can slip through

**Status:** validated-live, 2026-08-12, single instance -- not yet
confirmed as a systematic gap across more samples.

**Symptom.** JP was speaking live, deliberately only saying variations
on "stop listening"/"resume listening". The pipeline transcribed
`'mayday listening resume alpha bravo'` (`lang=en (0.62) dec=0.31`) --
words he never said, not a garbled version of what he did say (confirmed
by direct comparison against his own real-time report). It was not
rejected; it went to the backend as ordinary conversation.

**Why it wasn't caught.** The error ladder's low-confidence rejection
(`stt.min_language_probability`, 0.4 in this config) checks the
**language-detection probability**, not the separately-logged **decode
confidence** (`dec=...`). This hallucination's language probability
(0.62) was comfortably above threshold even though its decode confidence
(0.31) was lower than two other transcripts the SAME session correctly
rejected minutes earlier (`'stop brake'` lang=0.40, `'stop please'`
lang=0.37). "Confident this is English" and "confident these are the
right words" are different signals; only the first currently gates
rejection.

**Why this matters beyond STT accuracy in general.** The hallucinated
content -- `"alpha bravo"` -- is two of the three words in this session's
real `approval_phrase` (`"alpha bravo delta"`). It fell one word short
and nothing unsafe happened, but it's a genuine near-miss on a
security-relevant phrase, produced by hallucination rather than real
speech, on a gate that measured the wrong confidence signal.

**Not yet done:** checking whether adding `dec` as a second gate
condition would catch cases like this without materially increasing
false rejections on good transcripts -- needs real distribution data
across both accepted and rejected transcripts, not just this one sample.
Full evidence: `docs/field-notes/2026-08-12-stt-hallucination-bypasses-the-language-probability-gate-near-miss-on-approval-phrase.md`.

---

### "halt halt halt" (a default hard-stop phrase) failed round-trip transcription 4/5 times; bare "Athena" (default resume word) failed 3/5

**Status:** diagnosed live 2026-08-15, macOS, real Piper TTS -> faster-
whisper round-trip testing (`stt.model: base`). Not yet fixed or
decided -- see recommendations below.

**Symptom.** A safety-phrase reliability battery (23 hand-labeled test
cases, gibberish and foreign-language phrasing included) found zero
false positives anywhere, but two real false-negative gaps:
`"halt halt halt"` -- one of only three default `hard_stop_phrases` --
was misheard 4/5 times (`"Hold, hold, hold"` dominant, `"HOT POT POT"`
once), phonetically close enough to be a plausible genuine STT
confusion, not obviously a synthesis-only artifact. The default resume
word `"Athena"` said bare/alone (the simplest, most natural usage) was
misheard 3/5 times (`"patina"`, `"Adina"`, `"Aficino"`) -- notably worse
than the `resumeword/detector.py` module's own documented "5/5" claim,
which used varied multi-word phrasings; re-testing that same varied-
phrasing set here reproduced a comparable 4/5. `"stop stop stop"` and
`"abort abort abort"` were fully reliable (5/5 each).

**Why this matters.** `resumeword/detector.py` already documents a
round-trip verification discipline (`ROUNDTRIP_REJECTED_RESUME_WORDS`) --
applied once, when "Athena" was chosen 2026-07-13, but never repeated
for `hard_stop_phrases` when "abort"/"halt" were added 2026-08-09 (that
addition's own comment reasons about vocabulary collision with the
project's domain terms, not STT transcription reliability), and never
re-applied to the bare-word resume-word case specifically.

**Recommendations (not yet reviewed/decided by JP):**
1. Re-evaluate `"halt halt halt"` as a default -- drop it, keep it with
   a Settings-TUI warning (same shape as
   `ROUNDTRIP_REJECTED_RESUME_WORDS`), or verify against real human
   speech before deciding (this note's evidence is Piper-only).
2. Document that a resume word said WITH a little surrounding phrase is
   more reliable than said bare/alone.
3. Extend the not-yet-built setup-wizard "test-transcribe a few times"
   UX to hard-stop phrases too, not just the resume word.
4. This is NOT evidence for gating safewords/hotwords broadly behind an
   advanced-config warning -- the false-positive side is clean across
   this whole battery. The gap is narrow (one phrase, one usage
   pattern), not architectural.

Full data, methodology, and the false-positive-side results (all clean):
`docs/field-notes/2026-08-15-safety-phrase-reliability-battery-halt-and-
bare-athena-unreliable.md`.

---

### A misheard safeword can land on the pause phrase instead of the safeword -- same hard-stop effect, different resulting state

**Status:** diagnosed live 2026-08-01, not a safety gap, no fix planned
(STT-accuracy category, same underlying risk already noted for
`resume_word`/`pause_listening_phrases` in `docs/UAT-checklist.md`'s [P7]
enhancement idea). Documented so the distinction between "safe" and
"expected state" isn't lost.

**Symptom.** Live UAT, `convobox-UAT` @ `3d9d4b9`, `20:07:08`: an
utterance intended (per JP's own live report) as the safeword ("stop stop
stop") was transcribed by STT as `'Stop listening.'` instead --
```
20:07:08,019 Detected language 'en' with probability 0.97
20:07:08,570 paused listening (matched 'Stop listening.') -- hard-stopped
in-flight work; say 'Athena' to resume
```
Both `SafewordDetector` and `PauseListeningDetector` check the same raw
STT transcript (`docs/DESIGN-barge-in.md`, "Pause/resume listening" --
safeword checked first, then the pause phrase); when STT mishears one
configured phrase as a different, *also*-configured phrase, whichever one
the transcript actually matches is the one that fires. There's no gap
where the utterance is silently swallowed -- it always resolves to
whatever ConvoBox actually heard.

**Why this is not a safety gap.** The pause path calls the exact same
`send_hard_stop()` the safeword path does (see `scripts/run_convobox.py`'s
pause branch), so in-flight work is cancelled either way -- confirmed in
this same session, where the mis-heard "stop listening" correctly
hard-stopped the bogus in-flight "Stop listing." call from the entry
above. The real, user-visible difference is state, not safety: the
safeword returns to normal listening immediately, while landing on the
pause phrase instead leaves the session paused, requiring the resume word
before it hears anything else again -- an extra step someone reaching for
the emergency-stop phrase likely didn't intend.

**No fix proposed.** This is the same STT-reliability category already
tracked for `resume_word` (docs/UAT-checklist.md's [P7] entry: "STT is
unreliable enough live that one exact phrase can be hard to hit
reliably"), not a new problem this feature introduced. Worth keeping in
mind if `pause_listening_phrases`/`hard_stop_phrases` are ever tuned
closer together in pronunciation.

---

### A safeword match in a transcript skips checking that same transcript for a pause phrase -- fixed

**Status:** validated-live, 2026-08-12; fixed 2026-08-14, not yet
live-reproduced against the patched code (unit-level + full-suite
verification only so far -- see below). Never a safety gap (the
hard-stop itself always fired correctly regardless, before or after
this fix) -- this was a real, code-confirmed interaction gap between two
independent control mechanisms.

**Symptom.** JP spoke a long, rapid-fire safeword sequence live; STT
transcribed it as one continuous 11.8s utterance containing multiple
safewords AND the pause phrase: `'break break break cancel cancel
cancel ... abort abort abort stop listening cancel cancel cancel ...'`.
The hard-stop fired correctly on `'break break break'` (first match).
`'stop listening'`, present verbatim later in the same transcript, was
never separately evaluated -- the session never entered the paused
state from this utterance.

**Mechanism, confirmed in code** (`scripts/run_convobox.py:2507-2547`):
the entire pause/resume check (`listening_gate.observe(text)`) lived
inside `if not is_hard_stop:`. When a safeword matched a transcript,
that whole block -- including the pause check -- was skipped entirely
for that transcript, not just reordered after the hard-stop.
`PauseListeningDetector` itself was unaffected and would have found the
phrase if asked; the gap was in the caller never asking.

**Why this is realistic, not contrived:** this project already has a
documented hallucination pattern (2026-08-06) where a single STT segment
can span many seconds of repeated/garbled phrases -- exactly the shape
that lets two different trigger phrases land in one utterance. This
session hit it live.

**JP's decision (2026-08-14):** run the pause/resume check
unconditionally, even on a hard-stop transcript -- not because the two
should race, but because the spoken pause path already performs its own
full stop sequence as a side effect of registering the pause
(`gate_action == "pause"` at `scripts/run_convobox.py:2573-2609` calls
`player.stop()`/`tts.stop()`/`adapter.send_hard_stop()`, mirrored by
`WebListeningBridge.pause()` calling `Orchestrator.hard_stop()`
directly, `src/convobox/web/bridge.py:441`) -- so "stop listening"
already implies "hard stop" as one bundled action, on both the spoken
and web-button paths. The gap was that a safeword present in the same
utterance short-circuited past that pause branch entirely, losing the
paused-state transition even though an equivalent stop was about to
happen anyway via the hard-stop path.

**Fix:** `listening_gate.observe(text)` now also runs when
`is_hard_stop` is true, applying only the STATE change (pause/resume is
a pure state machine with no side effects of its own) -- not the
"pause" branch's own stop sequence (redundant, the hard-stop path
already stops everything) and not a `continue` (which would skip the
hard-stop path entirely, the one guarantee that must never change). The
existing safeword-hard-stop path is untouched: still checked first,
still falls through unconditionally, still "never swallowed." New log
lines ("also paused listening" / "also resumed listening") give live
signal when this branch actually fires. `ListeningGate`'s own docstring
updated to drop the now-stale "only call this when the safeword did NOT
match" claim.

**Verification so far:** full suite green (1412 passed / 2 pre-existing
unrelated Windows symlink-privilege failures, same as before this
change) and `mypy src/convobox scripts` clean. No unit test added --
`run()` itself has no existing test harness (real mic/segmenter
dependency; every fix to this exact function in this project's history
has been live-UAT-verified, not unit-tested, including the original
finding this fixes). **Not yet live-reproduced**: next real step is
repeating the same rapid-fire chained-safeword-plus-"stop listening"
utterance live and confirming both the hard-stop fires AND the session
actually ends up in the paused state afterward (observable via a
follow-up utterance being gated, or the new "also paused listening" log
line). Full original evidence:
`docs/field-notes/2026-08-12-safeword-and-pause-phrase-are-mutually-exclusive-within-one-utterance.md`.

---

## Stability, freezes, and crashes

Conditions where the process hangs, stalls, or dies. The VAD-freeze
entry is the long one; most of what it originally described turned out
to be measurement confounds, and it records which parts survived.

### faster-whisper's native allocator can fail during a long session

**Status:** mitigated (2026-07-14), root cause is upstream and unfixed.
`LocalTranscriber` (`src/convobox/stt/transcriber.py`) now catches this and
recovers automatically -- see below. This entry documents the underlying
cause for anyone debugging a recurrence or deciding whether to chase a real
upstream fix later.

**Symptom.** Live-confirmed 2026-07-14: a real ~13-minute UAT session
(claude-code backend, ~20 transcriptions in) crashed the whole
`run_convobox.py` process with an unhandled `RuntimeError: could not create
a memory object`, raised from inside `WhisperModel.transcribe()` ->
`detect_language()` -> `self.model.encode()`. Independently reproduced the
same failure class this same session while live-verifying a detector's
default vocabulary via a throwaway TTS->STT round-trip script: repeated
`transcribe()` calls in one long-lived process eventually failed with
`mkl_malloc: failed to allocate memory` (a different message, same
underlying allocator exhaustion), reproducible even in a fresh process
with system RAM never actually low (26GB free throughout, confirmed via
`Get-CimInstance Win32_OperatingSystem`) -- ruling out simple system-wide
memory pressure as the cause.

**Root cause: known, unresolved upstream issue, not a ConvoBox bug.**
ctranslate2's native (MKL on Windows) allocator leaks memory across
repeated inference calls in a long-lived process -- documented in
SYSTRAN/faster-whisper#660 ("Faster whisper holding memory not releasing
it, killing the flask server") and #390 ("Memory Leak investigation"),
both open/unresolved as of this writing. Not something Python-level
`gc.collect()` can fix, since the leaked memory is native (C++) heap, not
Python-managed objects.

**Mitigation shipped.** `LocalTranscriber.transcribe()` catches
`RuntimeError` around the model call, logs a warning with the real
exception and traceback (nothing silently swallowed), reloads the
`WhisperModel` (resets its allocator state -- the practical workaround for
this whole class of native-library leak), and returns an empty
`TranscriptResult` so the failed utterance is treated as unheard/dropped
by the normal low-confidence-transcript gate rather than crashing the
process. One lost utterance instead of a dead session. `model_factory` is
injectable for tests (`tests/test_transcriber.py`), so the recovery path
is unit-tested without needing to actually reproduce the native failure.

**Why not "actually fix" it.** The leak is inside ctranslate2's C++
runtime, several layers below anything ConvoBox's Python code controls --
not fixable here. Worth revisiting if a future ctranslate2/faster-whisper
release resolves the upstream issue, or if the reload mitigation itself
turns out to be insufficient (e.g. recurring often enough within a single
session to be disruptive) during a longer live-mic UAT pass than this
session's own testing has covered.

**Follow-up (2026-07-14): the reload used to make things worse under
load, now fixed.** Found live while investigating an unrelated UAT log
that surfaced an unexpected `huggingface.co` call: `WhisperModel(...)`
construction makes a real network request by default (a model-revision
freshness check) *even when the model is already fully cached* -- and
since every allocator-failure recovery above calls the exact same
construction path, a session recurring the native-allocator bug several
times would ALSO re-attempt that network call on every single recovery,
right when things are already degraded, with no guaranteed timeout.
`_build_whisper_model()` now tries `local_files_only=True` first,
falling back to the network only if nothing is cached yet (first-time
setup) -- every recovery after the first successful load is now fully
offline. See the commit message on the fix for verification details.

**Follow-up (2026-07-14): the recovery ITSELF could crash the process --
now fixed.** JP hit this live, mid-UAT, and reported it directly ("malloc
error... I thought I had enough memory"): the reload path
(`self._model = self._model_factory()`) was not itself wrapped in a
try/except. When the reload's OWN `WhisperModel` construction hit the
same native-allocator failure -- not a hypothetical, this is exactly
what happened in JP's session, a second, unhandled
`RuntimeError: mkl_malloc: failed to allocate memory` raised from
`ctranslate2.models.Whisper.__init__` -- it propagated all the way up
through `asyncio.run(run(args))` uncaught and killed the whole voice
loop, exactly the crash this whole mitigation exists to prevent.

Two changes:
1. `LocalTranscriber._reload_model()` now wraps the factory call in its
   own try/except. On success, `self._model` holds the new model as
   before. On failure, `self._model` is set to `None` (not left pointing
   at the old, still-broken instance) and the transcriber stays in a
   degraded-but-alive state; the NEXT `transcribe()` call detects
   `self._model is None` and retries the reload automatically -- no
   background timer, no permanent breakage, bounded by real utterances
   rather than a busy-retry loop.
2. The old model reference is dropped and `gc.collect()` is called
   **before** rebuilding, not after (or never). While `self._model`
   still pointed at the broken instance during the old reload code,
   calling the factory again meant asking the allocator to hold both the
   old and new model's native memory simultaneously -- exactly the wrong
   move when the allocator is already under enough pressure to be
   failing. This doesn't touch the underlying LEAK (still native C++
   heap, still not something Python GC reaches, per the existing
   explanation above) but it does reduce peak usage during the reload
   window itself, which is a real, distinct lever.

**Also added: a memory diagnostic in the failure log lines**
(`_memory_diagnostic()`), directly answering the question a tester asks
the moment they see "failed to allocate memory" -- Windows-only
(`ctypes` + `GlobalMemoryStatusEx`, no new dependency), reports real
available RAM, and if it's comfortably high, says outright that this
looks like the known allocator quirk rather than a real shortage
(matching this issue's own already-confirmed 26-28GB-free pattern from
earlier in the same session) -- no separate out-of-band check needed the
next time this recurs.

**Follow-up (2026-07-22): the same leak also surfaces as a bare numpy
`MemoryError`, which the `except RuntimeError` above did NOT catch --
now fixed.** Live-confirmed mid-UAT, testing Codex `approve` mode right
after PR #133 merged the full `permission_mode` design: an unhandled
`numpy._core._exceptions._ArrayMemoryError: Unable to allocate 1.15 MiB
for an array with shape (1, 376, 400)`, raised from `np.fft.rfft` inside
faster-whisper's own feature extractor (computing the mel spectrogram,
*before* ctranslate2's encode step ever runs) -- crashed the whole
session exactly like the original 2026-07-14 incidents this mitigation
exists to prevent. Confirmed via `_ArrayMemoryError.__mro__` that this
is a `MemoryError` subclass, not a `RuntimeError` -- an entirely
different exception hierarchy than what ctranslate2 itself raises for
the same underlying native-allocator pressure, so the existing catch
never had a chance of covering it. Both `LocalTranscriber.transcribe()`
and `_reload_model()` now catch `(RuntimeError, MemoryError)` together.
See `docs/field-notes/2026-07-22-native-allocator-leak-also-surfaces-as-numpy-memoryerror.md`
for the full writeup; `tests/test_transcriber.py::test_numpy_array_memory_error_is_recovered_not_raised`
covers it the same way the RuntimeError case already was.

**Follow-up (2026-08-02): the "recurring often enough to be disruptive"
condition this entry already flagged as worth revisiting -- now actually
hit live, on `large-v3`.** A `convobox-UAT` session (CPU fallback after a
separate CUDA-extra-not-installed gap, unrelated) hit the allocator
failure after ~9 successful transcriptions (~15 minutes) -- faster than
the original 2026-07-14 baseline (~20 transcriptions/13 minutes),
plausibly because `large-v3`'s much larger per-call native memory
footprint exhausts the leaking arena sooner than the smaller model that
first surfaced this bug. The mitigation above worked exactly as designed
(no crash, no unhandled traceback) -- but the reload it triggers **never
recovered**: every retry over the following 4+ minutes hit the identical
`mkl_malloc` failure, `self._model` stayed `None` for the rest of the
session, and every subsequent utterance was silently treated as unheard.
The mitigation's job was "don't crash," not "always recover," and it did
exactly that -- but this is the first live confirmation that once the
native allocator gets into this state, it can stay broken for the rest
of a session rather than self-healing on a later retry, which is worth
knowing before assuming "no crash" means "still working."

**Follow-up (2026-08-03, SOTA STT research pass): no upstream fix
exists, and this specific leak looks abandoned even though ctranslate2
itself is not.** ctranslate2's most recent GitHub release is **v4.8.1,
dated 2026-07-03** -- about a month old as of this research, one of five
releases in the preceding six months (v4.7.0 2026-02-03, v4.7.1
2026-02-04, v4.7.2 2026-05-19, v4.8.0 2026-06-06, v4.8.1 2026-07-03 --
the latest adding Gemma4 12B dense model support). The project itself is
actively maintained; what's missing across all of those releases is any
evidence of a fix for *this* leak specifically. faster-whisper issue
#390 is closed via PR #448, but that fix
could not be confirmed to specifically cover the MKL allocator leak
(vs. a narrower SageMaker-specific OOM); #660 shows no confirmed
resolution; a related, still-open issue (**#992**, "Memory on GPU not
cleared after transcription") suggests this is an ongoing pattern in the
library, not a one-off bug that got fixed. Community-circulated
workarounds (pinning to older ctranslate2 versions, e.g. 3.24.0 for
CUDA11/cuDNN8) were reported in the context of a *different* GPU-
allocation bug, not confirmed for this specific leak -- not a verified
fix. Practical implication: keep treating this as permanently unfixed
upstream rather than "unfixed for now" -- the reload mitigation (and
accepting that it can leave STT dead for the rest of a session once
triggered, per the follow-up above) is likely the durable state of
things, not a stopgap. The clean long-term fix, if this becomes worth
real effort, is moving off ctranslate2 entirely (see ROADMAP.md's
"Alternative local STT engines" -- the NVIDIA Parakeet TDT / `onnx-asr`
candidate runs on ONNX Runtime instead, sidestepping this whole class of
bug rather than working around it).

---

### VAD segmenter's per-window model call is synchronous with no offload/timeout -- can plausibly freeze the whole app

**Status:** narrowed and mostly resolved as of 2026-08-15 -- see the
"2026-08-15 investigation, final status" summary near the end of this
entry for the current, accurate picture before relying on anything
below it, which reflects earlier, less-informed hypotheses.
**Escalated 2026-08-12 -- likely two distinct bugs, not one.** PR #269
(2026-08-12) targeted this bug's then-leading hypothesis (thread-pool
contention) and did not fix it -- live re-tested the same day, three
clean reproductions, #269's own new stall diagnostic never fired once. A
same-day follow-up session then caught **two real short capture stalls
(1-4s, confirmed zero queue backlog -- not the "backlog piling up"
hypothesis, a genuine brief capture-callback hiccup, now directly
observable for the first time)**, and separately, **a 12+ minute freeze
that resisted every recovery path tried** (web resume, the hard-stop
API, even killing a hung backend subprocess that was itself stuck at the
time) -- only a full process kill ended it. CPU forensics during that
long freeze (target process pinned at a literal, sustained 0% CPU) point
at a genuine blocking wait with no timeout, most likely in
backend-subprocess I/O, not the VAD/capture layer at all. Full evidence
in both 2026-08-12 field notes linked below.

**Symptom, live-hit 2026-08-06/07, `stt.device: cpu`** (after a related
fix, PR #217, was already merged into the checkout): the app went
completely unresponsive -- multiple confirmed voice attempts produced
not even a `dropped (...)` log line, and the web UI's Stop-listening
button (a completely separate code path, an HTTP handler via uvicorn,
not the mic loop) also produced no log line and no effect. Zero
`Processing audio` lines appeared for the entire stuck window, meaning
the freeze happened *before* any utterance was ever handed to
`transcriber.transcribe()`.

**Why this is a different bug than the transcribe() freeze PR #217
already fixed:** that fix offloads `transcriber.transcribe()` to a
thread with an optional timeout -- it only helps once an utterance has
already been segmented. This incident's total absence of `Processing
audio` lines means the STT call was never reached at all.

**Root-cause candidate.** `UtteranceSegmenter._process_window()`
(`src/convobox/vad/segmenter.py`) calls `self._model(torch.from_numpy
(window), _SAMPLE_RATE).item()` -- a synchronous Silero VAD (ONNX)
inference call, made once per 512-sample (32ms) window, directly inside
`async def segment()`'s consumption loop (via `feed()`), with no thread
offload and no timeout. Same architectural shape as the transcribe()
bug PR #217 fixed -- a synchronous ML inference call that can freeze the
whole single-threaded event loop if it ever hangs -- just upstream of
it and far more frequent (~31 calls/second of audio vs. once per
completed utterance). `MicrophoneStream`'s own `blocksize: int = 512`
(`src/convobox/audio/capture.py`) matches `_WINDOW_SAMPLES` exactly, so
every mic chunk feeds exactly one VAD window through this same
synchronous path -- there's no batching that would reduce call
frequency at the chunk-consumption layer.

**Why not fixed yet, and the design wrinkle that makes this harder than
PR #217:** `transcribe()` is called once per completed utterance;
offloading it to `asyncio.to_thread()` per call is cheap relative to
its own cost. This model call happens ~31x/second -- offloading every
individual window call the same way would add real per-call thread-pool
overhead at that frequency, potentially comparable in magnitude to
Silero's own (very fast) inference time. The likely right fix is
offloading at the `feed()` (per-chunk) granularity rather than per-
window (the two are ~1:1 today given the blocksize match, but `feed()`
is the natural async/sync boundary `segment()`'s generator already
awaits at, and doesn't require reaching inside `_process_window()`) --
proposed, not yet built or benchmarked for the added-overhead tradeoff.

**Follow-up (2026-08-07, same day, later): recurred live a second time,
with real-time confirmation it blocks BOTH safety-relevant control
paths at once, and that it self-recovers.** JP hit this directly while
paused, following a runaway-repetition hard-stop (see the field note's
newest addendum for the full transcript): the "stop"/"eject" safeword
phrases had no effect, the web Stop button had no effect, and the web
Resume Listening button also had no effect -- reported live, in that
order, while it was happening. `convobox-tui.log` confirms genuine,
total silence for exactly 2m9.4s (18:41:50.939 -> 18:44:00.358), then a
`resumed listening (web UI)` line with no process restart in between.
**Two new findings this recurrence adds:**
1. Voice safeword, the web `/api/stop` handler, and the web
   `/api/listening` resume handler are THREE genuinely different code
   paths (a mic-loop hook and two separate HTTP routes) -- all three
   going unresponsive together is itself strong corroborating evidence
   for the shared-event-loop-blocked hypothesis above, gathered in real
   time while it was actively happening, not reconstructed from logs
   after the fact. Raises this from "diagnosed by reading the code" to
   "diagnosed by reading the code, with live behavioral confirmation
   matching the prediction."
2. **This instance was not permanent -- it self-recovered after
   2m9.4s with no kill/restart.** Operational guidance until this is
   actually fixed: waiting it out for a couple of minutes is a real,
   confirmed-working option, not just "kill the process" (killing is
   still reasonable if immediate control matters more than waiting on
   an unconfirmed recovery -- this is one data point, not a guarantee
   every recurrence resolves this fast).

**Priority raised** given both control paths that exist specifically
for safety (the safeword AND the web Stop button) failed simultaneously
in a real session -- worth prioritizing the `feed()`-granularity
offload fix proposed above over other STT/VAD polish work.

**Fix implemented 2026-08-07 (schema/unit-level; not yet live-validated
against a real recurrence).** New `UtteranceSegmenter.feed_async()`
(`src/convobox/vad/segmenter.py`) wraps the existing synchronous
`feed()` in `asyncio.to_thread()`, at exactly the `feed()`-granularity
proposed above. `segment()` (the mic loop's only real-time streaming
consumer) now awaits `feed_async()` instead of calling `feed()`
directly; `feed()` itself is unchanged and still synchronous, so every
existing caller (tests, any offline/non-realtime processing) keeps
identical behavior.

Deliberately **not** a timeout/abandon/invalidate mechanism like PR
#217's analogous STT fix: `transcribe()` is stateless per call, but
Silero's model carries sequential recurrent state across windows via
`reset_states()`, and abandoning an in-flight window while its
background thread still runs risks that thread's eventual completion
racing a fresh call against the same (not documented as thread-safe)
model object. Plain thread offload alone already addresses the
documented symptom -- other event-loop tasks (the web server's HTTP
routes, the watchdog, TUI redraw) stay responsive while a slow/stuck
window call runs in its own thread -- without introducing that new
race.

New test `test_feed_async_does_not_block_other_concurrent_work`
(`tests/test_vad_segmenter.py`) proves the mechanism the same way PR
#217's `test_timeout_does_not_block_other_concurrent_work` did: a
model call blocked via `time.sleep()` inside a worker thread does not
prevent concurrently-scheduled `asyncio.sleep()` ticks from firing on
the event loop. Full suite green (1273 passed), `ruff`/`mypy` clean on
the touched files.

**Still needed before this can be marked resolved**: live
re-verification against a real recurrence of the freeze (the same gap
PR #217's own field note flagged for its STT-side fix) -- this is
unit-proven-correct, not yet confirmed to actually prevent the next
live Stop/Resume-button lockup.

**Follow-up (2026-08-07, live UAT with this fix applied): the freeze
recurred, and the result is a genuine partial improvement, not a full
fix -- worth being precise about which part actually changed.** JP ran
a live voice UAT session on a branch combining this fix with PR #230's
STT changes, deliberately stress-testing pause/resume cycling. The
freeze recurred: real, active speech produced zero log activity
(`convobox-tui.log`, 20:57:40 -> 20:59:32, ~1m52s) -- confirmed live by
JP ("was hung for a few minutes... but had to manually resume
listening"; "during the gap, I was trying some utterances[,] but
stopped [trying] until a few minutes later"), i.e. this was not silence
being mistaken for a freeze, it was real speech the mic pipeline never
processed.

**What's different from the original incident, and why it matters:**
JP recovered by clicking the web UI's Resume Listening button, **and it
worked** -- in the original 2026-08-07 incident this follow-up's
sibling entry documents, all three recovery paths (voice safeword, web
Stop, web Resume) were simultaneously unresponsive for the same
2-minute-class duration. This time only the mic/voice path was stuck;
the web route stayed alive and functional. That is exactly what this
fix's own design claims -- offloading `feed()`'s Silero calls to a
worker thread keeps the *rest of the event loop* (HTTP routes, the
watchdog, TUI) responsive while a slow/stuck window call runs -- and
this live recurrence is the first real evidence that claim holds, not
just the unit test's proof of the mechanism in isolation.

**What the fix was never going to solve, and didn't:** `segment()`'s
own consumption of incoming mic chunks is still strictly sequential --
`await self.feed_async(chunk)` blocks that specific async generator
until the offloaded call returns, no matter which thread it runs in.
If one window's Silero call genuinely hangs, no *later* audio can be
processed until it returns, regardless of threading. This recurrence is
consistent with that being exactly what happened: the mic pipeline
itself stayed stuck for ~2 minutes while the rest of the app didn't.
**Net: this fix contains the blast radius (proven, live, this session)
but does not resolve the underlying hang (still reproduces, live, this
session) -- "partially validated," not "validated" or "insufficient."**

**Root cause of the underlying hang is still unconfirmed.** Not
determined this pass: whether it's genuinely Silero's own ONNX
inference stalling (OS scheduling, resource contention, a driver-level
stall), or something else entirely that this fix's instrumentation
can't currently distinguish from that. The immediate diagnostic gap:
there is no logging of when a `feed_async()` call starts, how long it
takes, or that it's still pending -- a future recurrence produces the
same "silence, then it's back" signature regardless of what's actually
stuck inside that await. Next concrete step, not done here: add
start/elapsed timing around the `asyncio.to_thread(self.feed, chunk)`
call in `feed_async()` (e.g. log a warning if a single call exceeds
some multiple of the ~1-3ms Silero normally takes), so the next
recurrence's own log distinguishes "one window call is still running,
N seconds in" from the current signature's total silence.

**JP's own real-time qualitative read, same session, worth recording
verbatim-close:** "stop and resume listening seem to be significantly
more reliable... assuming 1) I don't pound the paused client with lots
of hotwords, and 2) I don't spam the client while paused with lots of
conversation." Two things line up with this: the recurrence above
happened during a deliberate rapid-fire pause-overload stress test
(short repeated hotword-biased phrases, utterances arriving every few
seconds), and both this fix and PR #217's now both offload onto
`asyncio.to_thread()`'s shared default executor -- a real, untested
hypothesis for a follow-up session: sustained high-rate utterances
during a pause could be piling up VAD and/or STT thread submissions
faster than they drain, which would produce exactly this "fine under
normal use, hangs under rapid-fire stress" pattern without requiring
Silero itself to ever actually stall. Not confirmed -- a concrete lead
for whoever picks up the diagnostic-logging step above, not a
diagnosis.

**Follow-up (2026-08-12): PR #269 shipped for exactly this hypothesis,
same-day live re-test shows it did not fix the freeze, and its own new
diagnostic never fired.** PR #269 gave the thread-pool-contention theory
above a concrete mechanism (two indefinite blockers -- mic capture's
queue read and Piper's chunk pump -- competing with VAD/STT for the
shared default executor) and dedicated executors for both, plus a
queued-vs-running split added to `feed_async()`'s own stall warning
specifically so a recurrence would show which of the two was actually
happening.

Live re-tested the same day, immediately after merge: three clean
reproductions in ~15 minutes of the same rapid-fire-hotwords-while-paused
stress that produced the original incidents (durations 52.8s/72.7s/60.7s;
web UI Resume Listening recovered all three immediately, voice resume
did not work during any of them). `feed_async()`'s stall warning never
fired once. Since that warning is inside the exact code path #269's fix
targeted, its silence across three real occurrences is evidence the
stall isn't there -- not just an inconclusive result.

New leading candidate: `MicrophoneStream.stream()`'s own blocking
`queue.get()` (also given a dedicated executor by #269, but with no
equivalent stall diagnostic until this same pass). Under continuous
capture this call should return within about one blocksize (~32ms)
regardless of silence vs. speech, since the audio callback enqueues
chunks on a fixed hardware cadence -- so unlike a VAD/STT model call,
this one running long for real would be a genuinely abnormal, specific
signal (either real contention on its single-worker executor, or the
underlying sounddevice callback has stopped delivering chunks entirely).
Added the same queued-vs-running instrumentation here too, plus queue
backlog depth (to test JP's own live hypothesis that chunks might be
piling up behind a stalled consumer rather than capture itself
stopping) -- **not yet live-verified against a real recurrence.**

Full timing evidence, exact log excerpts, and reasoning:
`docs/field-notes/2026-08-12-vad-freeze-live-reproduced-three-times-pr269-did-not-fix-it.md`.

**Net: still an open, safety-relevant bug.** Not a release blocker in
the sense of a regression -- the web-side recovery path remains a real,
repeatable workaround -- but the underlying freeze itself is unresolved,
and the mechanism actually responsible is once again unconfirmed.

**Follow-up (2026-08-12, same day, later): a repeatable synthetic-speech
harness confirms the short stalls above are real (zero queue backlog
both times, ruling out the backlog-piling-up idea), then catches a
qualitatively worse, 12+ minute freeze that resisted every recovery path
tried -- web resume, the hard-stop API, and even killing a hung backend
subprocess that happened to be stuck at the same time. Only a full
process kill ended it.** The web UI's recovery path, 3-for-3 earlier the
same day, failed on this attempt -- don't treat it as a guaranteed
mitigation. CPU forensics (target process pinned at a literal, sustained
0% during the freeze) point toward a genuine blocking wait with no
timeout, likely in backend-subprocess I/O rather than the VAD/capture
layer -- a plausible, different mechanism from everything hypothesized
above, not yet confirmed. This may be two distinct bugs sharing a
symptom, not one. Full evidence:
`docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-and-a-12-minute-unrecoverable-one.md`.

**Correction + headline number, same evening, later still:** the session
above ran with Windows' own mic "Audio Enhancements" ON the whole time
(discovered live, disabled, fixed synthetic-audio pickup immediately) --
an unknown fraction of that session's "total silence" was this OS-level
setting suppressing the *test signal*, not ConvoBox's own pipeline
stuck. A second, unrelated bug in the test harness's own success
detection was also found and fixed (it was restarting visibly-healthy
sessions). With **both** confounds removed, a clean 10-cycle automated
batch still shows a real, frequent stall: **30% of cycles required a
full session restart** (near-total audio pickup silence), and clean
pause+resume success occurred in only 2/10 cycles. Resume-word matcher
logic itself was traced and confirmed correct (`resumeword/detector.py`,
`ListeningGate.observe()`) -- most "resume failed" readings are more
likely a downstream consequence of the pause phrase itself sometimes not
registering, not a matcher bug. **This 30% figure is the current
best-controlled estimate of how often this stress pattern produces a
real stall** -- treat it as the headline number for release discussions,
superseding the smaller/less-controlled samples above. Full evidence:
`docs/field-notes/2026-08-12-vad-freeze-exhaustive-batch-after-fixing-windows-enhancements-confound.md`.

**Instrumentation pass, 2026-08-14 (no fix yet -- diagnostic only).**
Implements the "not done this session" next step from the 12-minute-
freeze field note above: both backend adapters' `readline()` calls
(`codex.py`'s `_read_loop`, `claude_code.py`'s `_read_loop` and
`_drain_stderr`) had the exact shape the field note's leading candidate
pointed at -- an unbounded read with no timeout and no explicit check for
"the process died without the pipe EOF'ing" -- and neither had the
queued-vs-running stall diagnostic `capture.py`/`segmenter.py` already
have. Added a shared helper, `readline_with_stall_diagnostic()`
(`convobox/adapters/base.py`), same non-destructive `asyncio.wait()`
polling shape (never cancels the underlying read), used by all three
call sites; each stall warning now also logs `proc.returncode`. Also
added a DEBUG log line for `ListeningGate.observe()`'s `"pass"` outcome
(`run_convobox.py`, right after the `"pause"` branch) -- previously
silent, this is the exact ambiguity the exhaustive-batch note above had
to resolve by manual code-tracing (was a "resume" transcript ever
actually paused-state, or did the pause phrase never register in the
first place). Neither change fixes the freeze -- both exist purely so
the *next* recurrence produces real telemetry instead of the silence
every prior live repro has produced. All diagnosed on Windows; **not
yet reproduced or tested on macOS.** Next real step: reproduce with this
instrumentation live (Windows first, since that's where every prior
repro happened) and read what actually fired.

**That next step happened the same evening -- two more readline()
freezes (65.5s+, 236.7s) reproduced live with the new instrumentation
actually firing for the first time**, confirming the leading hypothesis
directly rather than by inference. Full evidence: `docs/field-notes/
2026-08-14-vad-freeze-harness-live-catches-two-more-readline-stalls-with-
real-telemetry.md`.

**A third, structurally DIFFERENT freeze also occurred the same evening**
-- zero readline() warnings at all (ruling out this same mechanism),
isolated for the first time via typed text (which bypasses the mic/VAD
pipeline entirely and reached a healthy, responsive backend while the
mic layer stayed dead) -- direct confirmation that the mic-freeze and
backend-readline-freeze are genuinely separate bugs, not inference. The
same session then entered an unnoticed **~41-minute** compound freeze
(three segments: ~14.1min, ~7.9min, then 18.8+ min never self-resolving)
discovered only in forensic log review afterward, triggered by ordinary
low-volume activity rather than a rapid-fire burst -- the first evidence
this freeze class isn't confirmed to require stress conditions at all.
Full evidence: `docs/field-notes/2026-08-14-mic-pipeline-silence-freeze-
isolated-from-backend-via-typed-text-then-a-41-minute-compound-freeze.md`.

**Reproduced live on macOS, 2026-08-15 -- NOT Windows-specific.** A
5-cycle synthetic-speech stress harness (same shape as the Windows
sessions above, Piper phrases through real speakers into a real mic)
caught both the short self-resolving `readline()` stalls (5.5s-30.7s,
same pattern) and, on the very first run, a severe freeze matching the
12-minute Windows case's own signature: 94.4s stuck `readline()`
(CPU forensics confirmed byte-identical process time across samples --
genuinely zero CPU, not descheduled), plus over 2 minutes of total
mic-pipeline silence (`Processing audio` never logged again) that
survived a dedicated safeword recovery attempt, killing the hung
subprocess directly, AND a fresh utterance played afterward. **One real
platform divergence found:** killing the hung subprocess DID unblock
the stuck `readline()` on macOS (`proc.returncode=-15`, immediate) --
the opposite of the Windows note's own finding that a `taskkill` did
NOT unblock the equivalent stuck read. The recovery still failed
overall on both platforms, just for different, now-distinguishable
reasons -- macOS's read woke up but something downstream (mic
capture/VAD layer) stayed silent anyway; Windows' read never woke up at
all. Full evidence: `docs/field-notes/2026-08-15-vad-mic-freeze-live-
reproduced-on-macos.md`.

**2026-08-15 investigation, final status: mostly false alarms and a
distinct, now-mitigated opencode bug -- one genuinely open variant
remains.** The same evening's continued macOS investigation
substantially reframed the picture above. In rough chronological order:
claude-code was repeatedly more resilient than codex under the same
stress harness (`docs/field-notes/2026-08-15-vad-freeze-claude-code-
backend-more-resilient-than-codex-on-macos.md`,
`docs/field-notes/2026-08-15-claude-code-vad-freeze-re-confirmed-clean-
at-good-volume.md`); a second independent severe freeze plus a
self-resolving 66.3s stall were caught, 2-for-2 batches tail-triggered
(`docs/field-notes/2026-08-15-vad-freeze-second-severe-instance-plus-a-
self-resolving-66s-stall.md`); and a **test-harness confound was found
and fixed -- the macOS stress harness had been running at 25% system
output volume**, and re-running both original repro conditions at
confirmed good volume came back clean
(`docs/field-notes/2026-08-15-vad-freeze-mic-layer-repro-was-a-test-
harness-confound-system-output-volume-at-25pct.md`,
`docs/field-notes/2026-08-15-vad-freeze-both-repro-conditions-clean-at-
confirmed-good-volume.md`).

With the volume confound removed and a **busy-state diagnostic** added
to distinguish a genuinely stuck `readline()`/`aiter` from one that's
simply idle (`docs/field-notes/2026-08-15-vad-freeze-readline-stalls-
often-just-idle-time-not-a-hang-busy-state-fix.md`), a definitive
idle-trigger re-run found a **335.6s `readline()` "stall" that was
entirely harmless idle time**, and separately, a real mic-layer freeze
caught twice
(`docs/field-notes/2026-08-15-vad-freeze-idle-trigger-re-run-with-busy-
diagnostic-readline-was-harmless-mic-layer-freeze-real.md`). A fresh
10-cycle stress batch with the busy-aware diagnostic then found **zero
genuine in-flight hangs**
(`docs/field-notes/2026-08-15-vad-freeze-re-run-with-busy-diagnostic-
zero-genuine-hangs-in-fresh-10-cycle-batch.md`). The backend-readline
freeze's root cause was finally pinned down via a live native stack
sample: **codex is blocked on its OWN stdin, not on anything in
ConvoBox's event loop** -- refuting the standing
thread-pool-contention/VAD-layer hypothesis this whole entry had been
built around since 2026-08-12
(`docs/field-notes/2026-08-15-vad-freeze-root-cause-codex-blocked-on-
own-stdin-not-convobox-event-loop.md`). The idle-trigger freeze was then
confirmed not to reproduce on claude-code
(`docs/field-notes/2026-08-15-vad-freeze-idle-trigger-does-not-
reproduce-on-claude-code.md`) or on opencode -- completing a 3-backend
comparison and including a self-caught false alarm
(`docs/field-notes/2026-08-15-vad-freeze-idle-trigger-does-not-
reproduce-on-opencode.md`) -- and separately reconfirmed that the severe
freeze doesn't require active stress conditions, a third independent
catch
(`docs/field-notes/2026-08-15-vad-freeze-idle-trigger-confirmed-no-
active-stress-needed.md`).

**Net effect on this entry's original claim:** the codex/backend-readline
freeze this entry escalated on 2026-08-12 is now understood (blocked on
codex's own stdin) and, on the orchestrator side, mitigated -- see the
opencode retry-cancel fix under the force-kill entry below and
`src/convobox/orchestrator/orchestrator.py`'s `stop_event_loop()`, which
bounds what used to be an indefinite hang to ~3 retries x 3s, validated
live against 143 automated hard-stops on Windows with zero timeouts
(`docs/field-notes/2026-08-15-opencode-retry-cancel-fix-holds-under-
automated-hardstop-storm-on-windows.md`). Most of what looked like a
widespread, safety-relevant freeze across this entry's history has
turned out to be either this one now-mitigated mechanism or harmless
idle time misread as a hang by diagnostics that didn't yet distinguish
the two.

**Still genuinely open, not a false alarm:** the same evening also
caught a **structurally new freeze variant -- mic-layer-only, no codex
subprocess involved at all, 6+ minutes, the first time this shape
self-resolved rather than requiring intervention**
(`docs/field-notes/2026-08-15-vad-freeze-new-variant-mic-layer-only-6-
minutes-self-resolved.md`). Root cause not established. Treat this
specific variant, not the broader entry above it, as the live open risk
going into any release -- rare (one occurrence to date, self-resolving),
not confirmed eliminated, and not yet reproduced deliberately rather
than caught incidentally.

**Second live occurrence, first on Linux (2026-08-30).** Caught during
Codex's second live-mic UAT pass (`docs/UAT-codex-smoke.md`,
`backend=codex`, `permission_mode=plan`) while re-testing hard-stop
responsiveness -- reported live by the operator as "still not
interrupting well" before the log was checked. `convobox-tui.log`
confirms genuine total silence in the mic/STT pipeline for **213.4s**
(00:53:40.683 -> 00:57:13.997, zero `Processing audio` lines), while the
codex adapter's own `_read_loop` kept logging its routine 5s idle-poll
warnings on schedule the entire time (`busy=False` throughout) --
identical shape to 2026-08-15's variant: the backend-adapter task stayed
demonstrably alive and idle, only the mic-capture/VAD side went dark.
Self-resolved with no restart, same as the original catch. This is now
**two occurrences, both `backend=codex`, one macOS (2026-08-15) and one
Linux (2026-08-30)** -- raises confidence this is platform-independent
and codex-adjacent rather than a macOS-specific fluke, though still not
proven backend-independent (both catches used codex). **Operational
impact confirmed live:** every hard-stop/kill-phrase attempt spoken
during the freeze window was not silently declined by the overlap gate
(that would still log a `dropped (...)` line) -- it was never heard at
all, no log line of any kind. Once the freeze lifted on its own, the very
next "stop stop stop" and "eject eject eject" attempts matched
instantly (see the UAT doc's own Findings log for that session) --
reinforcing that the safeword-matching logic itself is not the problem;
the mic-capture layer being unresponsive upstream of it is.

---

### Backend can go silently busy for minutes with zero output -- root cause unconfirmed

**Status:** diagnosed live 2026-07-31 (claude-code backend), root cause
**not** confirmed. Recorded now so it isn't lost, not because a fix is
ready -- the concrete next step is re-running with `--verbose` next time
this recurs (see below), not a code change.

**Symptom.** Live UAT session, `convobox-UAT` checkout @ `20181be`,
`backend.name: claude-code`, `--tui --aec-dump`, default (INFO) log
level. Three silent-busy stretches in one session, each ending in real
spoken output rather than a crash, error, or reconnect:
- 18:47:52 -> 19:01:29 (**822s / ~13.7 minutes**), resolved with audio at
  19:01:41.
- 19:02:12 -> 19:03:37 (90s), resolved with a fresh turn at 19:04:01.
- 19:04:01 -> 19:08:13 (270s), resolved with audio at 19:08:39.

All three immediately followed a plain-text (no-tool-call) response --
the live backend itself, mid-session, characterized its own stuck turn
as "both following a plain-text response with no tool call." No file in
the working tree changed timestamp during the worst stretch (checked via
`find . -newermt "2026-07-31 18:47:00" ! -newermt "2026-07-31 19:02:00"`,
zero matches outside the always-updating log/AEC-dump files) -- consistent
with either genuine extended "thinking" with no tool use, or a stuck
state producing nothing at all. Both are equally consistent with the
evidence gathered so far.

**Why root cause is unconfirmed.** `WorkingIndicator`
(`scripts/run_convobox.py`) only observes `adapter.is_busy()` and
`player.is_playing()` -- by design, it never times out or takes action
itself (the safeword is the intended abort path), so a long heartbeat is
not itself a bug, just a faithful report that `is_busy()` stayed `True`.
At the default INFO log level, individual backend stream events (tool
calls, thinking deltas) aren't logged, so a genuinely slow backend turn
and a ConvoBox-side state bug (`is_busy()` failing to clear after the
backend actually finished) look identical after the fact -- there's
currently no way to tell them apart from `convobox-tui.log` alone. No
native `claude` session transcript was found for this run either (the
project's own `~/.claude/projects/` entry for this working dir has no
`.jsonl` matching the session), so that avenue didn't help this time.

**Next step, not yet done:** re-run with `--verbose` (DEBUG logging)
next time a stall like this happens, so tool-call/thinking-level events
are actually captured during the stall. If a future occurrence shows
real backend events streaming the whole time, that confirms genuine
long-running backend work (not a ConvoBox bug, just a UX/observability
gap worth its own fix -- e.g. surfacing *what* the backend is doing, not
just how long). If a future occurrence shows zero backend-side events
for minutes at DEBUG level too, that would point at a real `is_busy()`
staleness bug and justify a code investigation this entry didn't have
enough evidence to start.

**Current guidance (JP, 2026-07-30):** Piper for long responses; Kokoro
is fine for short conversational replies where phoneme count won't
approach the limit. No code change proposed by this entry -- documenting
the finding so the ~500 number isn't re-diagnosed from scratch later.

**Sources:** Kokoro-82M-v1.0-ONNX context length / 510-phoneme limit --
[Hugging Face model card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX);
chunking workaround precedent --
[Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI/blob/master/README.md).

---

## Speech pipeline (STT / TTS / AEC)

Limits of the local speech stack itself, independent of any backend.

### Kokoro can't synthesize past ~510 phonemes -- hard model limit, not a config/mode issue

**Status:** diagnosed (root cause 2026-07-24; confirmed against upstream
docs 2026-07-30), unfixed. Workaround: use Piper for long responses.

**Symptom.** Live-confirmed 2026-07-30 (JP, manual A/B while testing
Piper): Piper reads long text fine; Kokoro reliably fails at around
~500 phonemes. This is the same mechanism already root-caused 2026-07-24
in `KokoroTTSEngine.synthesize_stream` (`src/convobox/tts/kokoro.py`):
kokoro-onnx's own `create_stream()` runs a detached background task with
no exception handling; text producing more than the model's phoneme
limit raises `IndexError` inside that task (`voice = voice[len(tokens)]`),
the task dies silently, and the consumer's `await queue.get()` hangs
forever at 0% CPU. ConvoBox bounds the hang with a 30s timeout
(`_CHUNK_TIMEOUT_S`) that turns it into a catchable `RuntimeError`
instead of an indefinite hang.

**Root cause: a confirmed hard architectural limit, not a runtime mode.**
Web-checked 2026-07-30 against Kokoro-82M's model card and the
kokoro-onnx source: the model's context length is 512 tokens, and with
mandatory pad tokens at the start and end, the effective max is **510
phoneme tokens per synthesis call** -- consistent with the ~500 JP
observed. This isn't a batching mode or config flag ConvoBox is missing;
projects that give Kokoro long-text support (e.g. Kokoro-FastAPI) do it
by pre-chunking text client-side into windows well under the limit (its
own defaults: ~175-250 target tokens, 450 absolute max) and stitching
the resulting audio, not by raising a limit on the model itself.

**Not yet built:** that pre-chunking layer. PR #175 (merged 2026-07-30)
makes the failure *visible* -- surfaces it as a logged error plus a
`BackendEvent(ERROR)` instead of a silent gap in the transcript -- but
its own scope note is explicit that it does not make Kokoro handle the
long text; a real fix means splitting text into safe-sized chunks
before each `synthesize_stream()` call, which needs a live mic session
to verify audio quality across chunk boundaries (naturalness/pacing at
the seam). Discussed and deliberately deferred (2026-07-30): a simpler,
lower-risk alternative if this becomes worth revisiting is auto-routing
by estimated phoneme/char count (Piper for long text, Kokoro for short)
rather than chunking Kokoro itself -- same benefit, none of the
audio-seam risk. Worth full chunking only if Piper's GPL-3.0 licensing
later becomes a reason to keep everything on the permissively-licensed
engine.

**Second live confirmation, different backend (2026-08-30):** reproduced
again during Codex's first live-mic UAT smoke test on Linux
(`docs/UAT-codex-smoke.md`), triggered by a long web-search-summary
response. Same signature (`RuntimeError: Kokoro synthesis stalled...`),
same graceful recovery (the 30s timeout caught it, ConvoBox logged it and
kept the session going normally on the next turn) -- no new root cause,
just evidence this isn't backend-specific. The operator never heard any
part of that response; no partial-audio or "answer too long" fallback
exists yet, which is the concrete cost of the still-missing pre-chunking
layer described above.

**Third live confirmation, new trigger -- short Arabic text (2026-08-30,
same day, later session):** fired three more times in a single session,
every time on a short (one-to-two-sentence) Arabic response -- nowhere
near the length that triggers this on English text. Arabic phonemization
appears to be far denser per character than English's, hitting the same
hard 510-token limit at a fraction of the visible text length; not
diagnosed further (would need inspecting the actual phonemizer's token
output for Arabic), but a real, actionable data point for anyone relying
on Kokoro for non-English responses. Also newly observed this time: a
`"Phonemes are too long, truncating to 510 phonemes"` warning from
kokoro-onnx itself immediately precedes the crash -- the library's own
truncation attempt appears to have an off-by-one (truncating to exactly
510, then indexing `voice[len(tokens)]` with `len(tokens) == 510` against
a 510-length array), consistent with rather than contradicting the root
cause above. Failure mode was milder than the English case: playback
started ~25ms after the error (some chunks had already synthesized
before the failing batch), so the operator heard a truncated response
rather than total silence for the full 30s stall timeout -- not a fix,
just a less bad outcome depending on where in the stream the failure
lands. Full write-up: `docs/field-notes/2026-08-30-conversational-mode-
plus-aec-first-live-codex-linux-session.md`.

---

### WebRTC APM's noise suppression / auto gain control are unused (candidate, awaiting go-ahead)

**Status:** candidate, not built. Offered to JP directly (2026-07-15
evening, in response to his live report that mic+speaker AEC is still
leaking despite the delay-hint fix); awaiting his go-ahead before
touching this. The original "why not now" reasoning below is stale (it
predated the extensive AEC investigation this session has since done)
and is kept for history, not as the current blocker.

**What's there but unused, with more real detail than previously
recorded.** `EchoCanceller.__init__` (`src/convobox/audio/aec.py`)
constructs `AudioProcessor(enable_aec=True, enable_ns=False,
enable_agc=False, enable_vad=False)`. Re-inspected the installed
package's real constructor signature (2026-07-16, `inspect.signature`,
not assumed): `AudioProcessor.__init__(self, enable_aec=True,
enable_ns=True, ns_level=2, enable_agc=True, agc_mode=1,
enable_vad=True)` -- the binding's OWN defaults have NS and AGC ON,
with real tunable aggressiveness parameters (`ns_level`, `agc_mode`)
neither previously documented here nor exposed anywhere in ConvoBox.
`aec.py` deliberately overrides both to off. This means a future PR
needs to pick real values for `ns_level`/`agc_mode`, not just flip two
booleans -- worth live-testing a couple of settings rather than
guessing at the "right" level, same discipline as everything else this
session has verified against real hardware before committing to it.

**Why this might matter, concretely, not speculatively.** AGC directly
targets an already-documented, real finding: PR #74's live hardware
smoke test (`probe_audio()`, Settings TUI) reported `"mic: ... very
quiet -- raise the input gain or move closer"` against this machine's
actual default mic -- the exact condition AGC exists to correct. More
recently (2026-07-15 evening), JP's own live mic+speaker UAT log showed
persistently erratic, often poor echo attenuation (0.5-12dB, swinging
response to response) even with a correct AEC delay hint -- a genuinely
hard open-air acoustic coupling problem, not a leftover config bug (see
`docs/UAT-checklist.md` **[E8]**/**[E9]**). NS/AGC won't fix delay
estimation, but AGC in particular could reduce how hot the mic signal
runs from close speaker proximity, which plausibly makes AEC3's own
adaptive filter's job easier -- untested, not asserted as a fix.

**Original "why not now" reasoning (2026-07-14, superseded, kept for
history).** This touches the exact same `AudioProcessor` construction
JP was then actively mid-assessment on for a different reason (his own
PR #78 `[L3]` finding: AEC produces artifacts and drops real barge-in
with a headset, "recorded for assessment," not yet decided at the
time). That assessment has since resolved through extensive live UAT
(`[L4]`-`[L6]`, `[E8]`, `[E9]`) -- the attribution-ambiguity concern
that justified waiting no longer applies. The live JP go-ahead question
is the only remaining gate now.

**What the NS/AGC actually are, confirmed by reading the binding
(2026-08-29).** `aec_audio_processing.AudioProcessor` is a SWIG-
generated wrapper (`audio_processing.py`, header says "Do not make
changes ... modify the SWIG interface file instead") around a compiled
`libwebrtc-audio-processing-2.{dylib,so,dll}` (`loader.py`) -- this is
the freedesktop.org `webrtc-audio-processing` fork of Google's own
WebRTC APM (the same engine PulseAudio's/PipeWire's `webrtc-echo-
cancel` module uses), not a from-scratch reimplementation. The Python
layer exposes only construction flags and stream I/O
(`process_stream`/`process_reverse_stream`/`has_voice`/etc.) -- no
docstrings or comments reference the algorithm beneath `ns_level`/
`agc_mode` specifically, because there's nothing to reference: those
ints map straight through to APM's own enums. Concretely, this means:
NS here is APM's classic **spectral-subtraction-style stationary noise
suppressor** (`ns_level` 0-3 aggressiveness) -- not a modern neural
denoiser (nothing like RNNoise/DTLN is in this binary), and AGC is
APM's **legacy AGC1 (`agc_mode` 0-3)**, an adaptive *input*-gain
controller that reacts to the mic signal after capture. Neither touches
what ConvoBox sends to the speaker before it plays -- both are reactive
input-side processing, not proactive output-side limiting. This is
useful context for the "why this might matter" section above (real,
but modest and reactive) and directly motivates the new idea below.

**New candidate, informed by this investigation + the 2026-08-29 AEC/
THD field-note campaign + a 2026-08-30 literature check on professional
AEC engineering practice: a proactive soft limiter on the render/TTS
signal, not just reactive NS/AGC on the mic.** This session's THD
measurements (`docs/field-notes/2026-08-29-thd-measurement-and-n20-
fixed-comparison-plus-front-jack-mutes-internal-speaker-gotcha.md`) and
the delay x volume grids (`docs/field-notes/2026-08-27-...md`,
`2026-08-28-...md`) all point the same direction: AEC3's false-barge
rate gets *worse* than AEC-off once the speaker itself is driven into
its own nonlinear distortion regime (confirmed on both the Mac mini's
internal driver and, at maxed own-gain, small external speakers too) --
because AEC3's adaptive filter assumes a roughly linear acoustic path
from far-end signal to echo, and a hard-clipping/distorting driver
breaks that assumption long before NS/AGC on the *input* side get a
chance to help.

**Correction (2026-08-30):** an earlier draft of this entry claimed
this is "known" Zoom/Teams practice -- a web research pass found no
public source describing either vendor's AEC internals, so that framing
was an unconfirmed inference and is retracted here. A real, citable
precedent was found instead: QSC's own **Q-SYS Acoustic Echo
Cancellation white paper** (a commercial pro-AEC vendor's published
engineering guidance, not marketing) explicitly recommends placing a
compressor/limiter on the *mixed reference signal* to avoid clipping,
with the threshold near 0dB -- and critically, placing it **before**
that signal forks to both the loudspeaker and the AEC's own reference
input, "so that the AEC reference input sees the same compressed or
limited signal that is sent to the loudspeakers... this prevents the
AEC from chasing gain changes." Separately, standard echo-cancellation
literature treats a nonlinear echo path as fundamentally uncancellable
by a linear adaptive filter (consistent with this project's own
finding above) -- reinforcing that this needs a fix ahead of the
speaker, not another mic-side reactive stage. WebRTC's own AGC1 (what
ConvoBox's binding uses) and the newer AGC2 were both checked and
confirmed to be capture-side only in every generation -- upgrading the
WebRTC binding would not add this on its own; it has to be new,
ConvoBox-side render-path code regardless.

Concretely for ConvoBox, per the Q-SYS placement detail: a limiter
stage applied to the mixed render signal in `AudioPlayer` (or wherever
`on_block_played`'s far-end reference is sourced from) BEFORE it forks
to (a) play out the real speaker and (b) feed the AEC reference input --
e.g. a simple soft-knee limiter with a threshold below the specific
driver's own distortion onset (internal speaker's own 4kHz THD climbs
sharply at 100% system volume per the N=3 sweep above; a real
implementation would need a proper per-device calibration step, not a
hardcoded threshold, since the onset level is clearly device-specific --
external speakers showed no such rise in their normal ~24-50%-gain
operating range but did distort badly once their own gain dial was
maxed). Feeding the AEC reference input the *same post-limit* signal,
not the pre-limit TTS PCM, is the detail Q-SYS calls out as the reason
this avoids the AEC "chasing" a gain change it never saw -- a real
implementation should get this ordering right, not just add a limiter
anywhere convenient in the render path.

**Not built.** A genuinely new idea from this session's research
discussion, distinct from (and complementary to -- not a replacement
for) the existing NS/AGC candidate above: NS/AGC are reactive, mic-
side, and already wired into the binding awaiting a go-ahead; this
limiter idea is proactive, output-side, and would need new code (no
existing knob in `aec-audio-processing` does this) plus a per-device
calibration step this session's THD script prototypes but doesn't
productize. Documented here as a candidate awaiting JP's go-ahead, same
convention as the rest of this entry -- not scoped or estimated further
this session. A follow-up prior-art/IP-strategy research pass is in
progress as of 2026-08-30 (see the field-notes/session log for
findings once complete) before any decision on whether this is worth
patenting, open-sourcing, or dual-licensing.

---

## Platform and compatibility

Issues that only appear on one OS or one audio API.

### A Mac's front 3.5mm jack mutes the internal speaker at the hardware level, regardless of software output-device selection

**Status:** verified live, 2026-08-29, Mac mini M4. Not a ConvoBox bug
-- a real hardware/OS behavior anyone testing multiple output devices
on similar hardware should know about, since it silently invalidates a
class of comparison the software has no way to detect.

**Symptom.** While running a controlled internal-vs-external-speaker
comparison (`docs/field-notes/2026-08-29-thd-measurement-and-n20-fixed-
comparison-plus-front-jack-mutes-internal-speaker-gotcha.md`), the
internal-speaker measurement came back suspiciously clean --
`raw_playback_rms` (the actual mic-captured playback level) was 0.0047,
a 20x drop from the same nominal setting's expected ~0.09, despite
`audio.output_device` being explicitly set to `"Mac mini Speakers"` (a
different device than the external speakers connected at the time) and
`sd.query_devices()` confirming that device was correctly selected in
software.

**Root cause.** Something physically plugged into the Mac mini's front
3.5mm analog jack attenuates/mutes the internal speaker at the hardware
level (a jack-sense circuit), and this does NOT appear to respect an
application-level output-device override -- selecting a different
device in Core Audio doesn't override the physical jack-sense behavior
for the internal driver specifically. Confirmed by physically unplugging
the external speakers and rerunning: `raw_playback_rms` returned to
0.0971, matching the pre-confound baseline almost exactly.

**Practical implication.** Any acoustic measurement of the internal
speaker taken while ANYTHING is plugged into the front port is suspect
-- checking `sd.query_devices()`/`audio.output_device` alone is not
enough to confirm which speaker is actually producing sound. Always
verify actual captured signal level (or just listen) matches
expectations before trusting a "device A vs. device B" comparison on
this class of hardware.

**Not built:** no code change is proposed here -- this is a testing-
methodology note, not a mitigation, since it's outside ConvoBox's own
control (a Core Audio / hardware jack-sense behavior, not something the
`sounddevice`/`aec-audio-processing` layer can detect or override).

---

### WASAPI output plays speech an octave too high ("static chipmunk")

**Status:** deferred (2026-07-12). Mitigation: use an **MME** output device.
WASAPI is documented as low-latency-but-finicky in
`scripts/audio_devices.py` and `docs/DESIGN-echo-and-barge-in.md`.

**Symptom.** With a WASAPI output device pinned (e.g.
`Headphones (Realtek(R) Audio), Windows WASAPI`), TTS playback is pitched up
about an octave with a static/gargle over it. The tester's exact
description across three UAT runs: *"the speech frequency is doubled but the
speech rate is right"* — i.e. **pitch up an octave, tempo correct.** MME and
DirectSound outputs on the same machine are clean.

**Two distinct causes — one fixed, one not.**

1. **Static at the seams — FIXED** (streaming resampler, this same work).
   Streaming playback resampled each TTS chunk in isolation, injecting a
   phase discontinuity at every chunk boundary. Inaudible at an integer
   device ratio (22050→44100, MME) but clicking at a non-integer ratio
   (22050→48000, any 48 kHz WASAPI device). Fixed by `_StreamResampler`
   (`src/convobox/audio/playback.py`): per-chunk RMS error vs a whole-buffer
   resample dropped from 0.024 to ~0 at 48000. This removed the *clicky*
   component but not the octave shift.

2. **Octave-up pitch — NOT FIXED.** Tempo-correct + pitch-doubled is the
   textbook signature of **mono audio mishandled on a stereo device** at the
   channel layer, inside PortAudio's WASAPI shared-mode conversion — below
   ConvoBox's Python. The player opens the stream `channels=1` and writes a
   mono buffer; the Realtek WASAPI endpoint's shared mix format is stereo
   48 kHz, and PortAudio's mono→stereo path appears to reinterpret rather
   than duplicate the samples on this driver.

**Evidence.**
- Offline frame-count tests show playback writes the *correct* number of
  frames at 48000 (implied duration == true duration), so it is **not** a
  sample-rate/resampling error — those change tempo, which is correct here.
- `AudioPlayer.play()` and `play_stream()` both produce correct-duration
  output numerically; the corruption is only audible from the physical DAC.
- Could not auto-measure the emitted pitch: this sounddevice build's
  `sd.WasapiSettings` has no `loopback` kwarg, so WASAPI loopback capture
  (which would confirm 440 Hz → ~880 Hz) is unavailable here. Diagnosis
  rests on the tempo-correct-pitch-doubled acoustic signature.

**Candidate fix (untried).** Open the output stream at the device's **native
channel count** and upmix mono→N ourselves (duplicate the sample across
channels) instead of relying on PortAudio's WASAPI mono conversion. Care
required: the AEC far-end reference (`AudioPlayer.on_block_played`) must stay
**mono** at the device rate — feed the canceller the pre-upmix mono block,
not the interleaved stereo one. Verify with the tester's ear (or a working
loopback capture) before trusting it, since the last three WASAPI fixes each
looked right offline and still needed a live listen.

**Why deferred.** MME output works cleanly today and 183 ms of output
latency is fine for the prototype. WASAPI's ~22 ms is an optimization, not a
blocker, and the fix touches the playback core plus the AEC reference — worth
doing carefully, not rushing mid-UAT.

---

### AEC builds from source on macOS — PyPI just doesn't ship a wheel for it

**Status:** verified 2026-07-16 on Apple Silicon (M4, macOS 26.5). Not a
bug — a gap in what was previously assumed. `aec.py`'s docstring and the
`aec` extra's comment in `pyproject.toml` both said the AEC package's
"wheels are Windows-only today," which reads like a platform limitation
of the underlying code. It isn't: `aec-audio-processing`'s sdist
(`setup.py`) already has full Darwin build support wired in — it builds
`webrtc-audio-processing` (the same WebRTC APM/AEC3 engine used on
Windows) via meson into a `.dylib`, with `-DWEBRTC_MAC` and ARM64 NEON
flags already set, correct macOS rpath handling for the built dylib, the
works. PyPI just only hosts prebuilt `win_amd64` wheels for it (1.0.0,
1.0.1); nobody has published a macOS wheel, so a plain `pip install`
silently falls back to failing rather than to a source build succeeding.

**Verified working, zero code changes to `EchoCanceller`.** Build
prerequisites (`meson`, `ninja`, `swig` — none installed by uv/pip)
installed via `brew install meson ninja swig`; Xcode CLT's `clang` was
already present. Then:

```
uv pip install --no-binary aec-audio-processing aec-audio-processing
```

builds cleanly in about 30s and produces a working extension —
`AudioProcessor(enable_aec=True, ...)` constructs, `process_reverse_stream`/
`process_stream` run, and all 13 existing `tests/test_aec.py` tests pass
against the real binding (previously these could only run on Windows).

**What this unblocks.** Signal-level AEC — and therefore live
mic+speaker attenuation UAT, analogous to JP's 2026-07-15 Windows run
(see the NS/AGC entry below and `docs/UAT-checklist.md` **[E8]**/**[E9]**)
— can now actually be exercised on macOS. Before this, macOS testing of
the barge-in/self-interruption problem was necessarily software-only
(overlap-gate, text-echo-filter), since `EchoCanceller.__init__` raised
immediately without the package installed.

**Follow-up (2026-08-10): the live mic+speaker attenuation measurement
this entry asked for has now been run, on this machine's real hardware
(AIRHUG 28 mic, Mac mini Speakers) -- different finding than expected.**
Ran `scripts/acoustic_calibration.py` (the repo's own unattended
real-room AEC/VAD calibration tool, previously only exercised on
Windows) twice independently, in a dedicated `git worktree` at
`convobox-UAT` (kept separate from this dev tree; see AGENTS.md's
"claim scope before editing" precedent) with a real Piper voice
(`en_US-lessac-medium`) actually played through the speakers and
captured back through the mic:

- Trial 1: `attenuation=2.49dB, ceiling=1.92dB` (auto-estimated delay
  238ms). Trial 2 (independent run): `attenuation=5.08dB,
  ceiling=0.69dB`. **Both readings sit at or below the tool's own
  "measurable ceiling"** -- per `EchoCanceller.measurable_ceiling_db()`'s
  own docstring, that means speaker echo at this mic barely rises above
  room ambient noise in the first place, not that AEC is failing to
  cancel it. `raw_playback_rms` (0.0047-0.0049) vs `ambient_rms`
  (0.0037-0.0040) confirms it directly: the un-cancelled echo is only
  marginally louder than the room's own noise floor.
- **Zero false barge-ins in either trial, with AEC on OR off**
  (`false_barge_ins: 0` for both `raw_vad` and `processed_vad`, both
  runs) -- the actual safety-relevant signal (would self-echo trip a
  spurious interrupt) reads clean even in the AEC-off condition on this
  hardware. Raw VAD did register 1-2 short utterances from the
  un-cancelled echo (once even peaking at `peak_vad_probability=0.997`),
  but never sustained long enough to cross `BargeInMonitor`'s own
  threshold -- the same distinction this repo's `[G1]`/`[G2]` UAT
  entries already draw between "VAD notices something" and "a real
  barge-in fires."
- **Reads as a genuinely different acoustic situation than the Windows
  finding below (erratic 0.5-12dB, clearly-above-ambient echo)**, not a
  contradiction of it -- plausibly this mic (AIRHUG 28) and/or the Mac
  mini Speakers' real-world coupling in this room is simply quieter
  relative to ambient noise than JP's Windows setup was. Only 2 trials,
  one room, one hardware pair -- not enough to generalize to "macOS is
  fine," just enough to say this specific machine's speaker-echo problem
  (if this repo ever needs to chase one on it) looks small relative to
  room noise, not that AEC itself is unusually strong or weak here.
- Full JSON reports + raw/AEC-processed WAV evidence live under the UAT
  worktree's own `uat-acoustic-calibration/` (gitignored scratch,
  per this project's own convention -- not copied into this repo).

**Follow-up (2026-08-11): first real human-speech demo on macOS —
safeword and barge-in both confirmed live, plus a real self-triggered
barge-in loop found and diagnosed.** JP demoed ConvoBox live to his son
(real speech, not synthetic injection). The safeword fired correctly 3
times (`stop stop stop` x2, `abort abort abort` x1); barge-in
(`interaction.interrupt_preset: conversational`) fired correctly on
the first two deliberate interrupts, then entered a real, sustained
self-triggered loop (20 barge-ins in ~90s, several firing with no one
present). Diagnosed live: 18 of 19 barge-in events showed
`UNDER-CANCELLING`, with attenuation staying close to this session's
steady-state baseline (6.54dB vs. 6.75dB) while the measured
echo-to-ambient ceiling spiked (14.22dB vs. ~0.53dB baseline) — rapid
back-to-back short turns (each cut short by the previous false
trigger) measurably increases the echo reaching the mic relative to
ambient, leaving proportionally more residual for a fixed amount of
real cancellation. `do-not-disturb` mode (this config's original
default) is not subject to this risk, since ordinary speech can't
trigger anything during playback there. No fix built or proposed this
pass — live characterization only. Full writeup:
`docs/field-notes/2026-08-11-macos-live-human-demo-safeword-bargein-and-self-echo-loop.md`.

**Follow-up (2026-08-11, same day): automated mitigation testing at the
exact demo volume (`tts.volume=4.0`, macOS system output 75%) —
a real, counterintuitive finding.** A 7-point AEC delay sweep found
**AEC-processed audio produced MORE false barge-ins than AEC-off, at
every single delay tested** (8-13 vs. 1) — the opposite of AEC's
intended effect, likely because residual-suppressor artifacts at this
volume are themselves speech-shaped enough to trip VAD more often than
the raw uncancelled echo. 400ms was the least-bad delay tested (8 vs.
10 for auto-238ms) but still far worse than AEC-off. A separate
4-point `barge_in_min_speech_ms` sweep (250/500/800/1200ms, N=1 each —
directional, not statistically robust) showed a real trend toward
1200ms converging to the AEC-off baseline (1 false trigger). Ranked
recommendation: lower the volume (biggest lever, matches this
session's whole volume-escalation arc), raise
`barge_in_min_speech_ms` if `conversational` mode must stay on at high
volume, set `aec_delay_ms: 400` explicitly as a smaller assist, or
fall back to `do-not-disturb`/headphones to sidestep the problem
entirely. Full writeup:
`docs/field-notes/2026-08-11-self-barge-in-mitigation-at-demo-volume.md`.

**Follow-up (2026-08-11, same day): combining both mitigations nearly
solves it, and a likely root cause was identified.** `aec_delay_ms=400`
+ `barge_in_min_speech_ms=1200` together, 4 real trials at the same
demo volume: mean 1.25 false barge-ins (2 of 4 trials hit zero), down
from 8-13 with no mitigation or either lever alone. **Likely root
cause, corroborated but not directly confirmed**: the Mac mini M4's
single built-in speaker (Apple's own spec lists it singular;
independent reviews describe it as prone to distortion at volume) may
be genuinely distorting acoustically at `tts.volume=4.0` + macOS
system volume 75% -- a linear AEC (WebRTC AEC3) structurally cannot
fully cancel a nonlinear/distorted acoustic path, which would explain
why AEC-processed audio measured worse than AEC-off at every delay
tested. No digital clipping found in the raw mic captures (peak
0.63-0.68/1.0), but that doesn't rule out acoustic distortion at the
speaker itself, a different phenomenon. **Also confirmed (JP directly
observed the LED)**: the AIRHUG 28 mic's own onboard "AI Noise
Reduction" DSP mode was OFF (green LED) throughout all testing this
session -- ruled out as a confound, not just assumed. Full writeup,
hardware specs, and sources:
`docs/field-notes/2026-08-11-self-barge-in-combined-mitigation-and-hardware-notes.md`.

**Follow-up (2026-08-11, same day): a full 119-trial volume sweep
(100%-20% system volume in 5% steps, N=7 per level, initial sweep +
3 corroborating up/down cycles) pins the transition zone precisely at
30-40% system output volume.** Above it, AEC consistently makes false
barge-ins worse than AEC-off (means of 8-13 vs. steady ~1); at and
below it, AEC flips back to normal (reducing false triggers below the
raw baseline). Also added a room RT60 measurement (exponential sine
sweep / Farina method): ~0.2s (T20) to ~0.4s (T30) in this session's
400 sq ft, hard-floored, open-plan test room -- shorter than the
room's "wet" subjective impression might suggest, plausibly because
being open on 3 sides lets reflected energy propagate away rather than
building up. Full raw data (every one of the 119 volume-sweep trials,
plus complete hardware/room specs) published for reuse:
`docs/field-notes/2026-08-11-full-volume-sweep-raw-data-and-room-rt60.md`.

**Follow-up (2026-08-11, same day): RT60 extended to N=50 repeat
measurements with ambient-noise logging.** T20 stayed tight and
reproducible (mean 0.2133s, sd 0.0138s, CV ~6.5%); T30 was noisier
(mean 0.4589s, sd 0.0573s, CV ~12.5%) -- confirms T20 is the more
trustworthy estimator here. Confirmed environmental state for the
whole batch: whole-house central AC running plus a box fan (Corsi-
Rosenthal configuration) on low, both throughout -- a real household
background-noise condition, not a silent-room ideal. A suggestive
N=10 pattern (lower ambient noise correlating with longer measured
RT60) held its direction at N=50 but was much weaker than it first
appeared (Pearson r=-0.243 for T20, r=-0.155 for T30) -- a real
example of a small sample overselling an effect size. Full 50-trial
raw data appended to the same field note:
`docs/field-notes/2026-08-11-full-volume-sweep-raw-data-and-room-rt60.md`.

**Follow-up (2026-08-27): the finding re-confirmed at N=10 across the
full standard delay-candidate set, not just `auto`.** 250 real trials
(5 delay candidates x 5 volume levels x N=10, up from N=1/N=7 in the
findings above) show every candidate -- not only auto-estimated delay
-- produces roughly 10x more AEC-processed false barge-ins than raw at
100%/75% volume (means ~9.8-9.9 vs. 1.0), tapering to AEC being a net
improvement at 35%/20%, consistent with the 30-40% crossover the
119-trial sweep found. This grid used the standard delay set (not
including the 400ms candidate the mitigation below found best) and left
`barge_in_min_speech_ms` at its unmitigated 250ms default throughout --
it characterizes the raw problem's scale, it does not re-test the known
mitigation. Full data: `docs/field-notes/2026-08-27-full-delay-x-volume-
grid-aec-processing-makes-self-barge-in-worse-at-high-volume.md`.

**Follow-up (2026-08-28): the known `barge_in_min_speech_ms=1200`
mitigation validated at N=10 across the full volume range.** 300 trials
(6 delay candidates, adding `400ms` to the standard set, x 5 volumes x
N=10) with the mitigation applied: complete elimination of AEC-caused
false barge-ins at 20-35% volume (0.00-0.25 mean, down from 1.28-1.80
unmitigated), 2.4x-6x fewer at 50-100% (still 2.6x-4x worse than
raw-AEC-off at the highest volumes). `aec_delay_ms=309` -- this repo's
long-standing historical recommendation, not the newly-tested 400ms --
turns out the most consistently strong paired delay choice across the
grid. Full data:
`docs/field-notes/2026-08-28-mitigation-grid-barge-in-threshold-1200ms-
plus-400ms-delay-validated-at-scale.md`.

**Follow-up (2026-08-28): real external speakers essentially eliminate
the problem at 75% and below -- the first direct evidence for the
distortion hypothesis, not just corroboration.** JP attached real
external powered speakers to the Mac mini's front port. Same grid as
the 2026-08-27 baseline (5 delays x 5 volumes x N=10, unmitigated
threshold): at 75%/50% volume, external speakers produced essentially
zero false barge-ins of any kind (0.00-0.02 mean AEC-processed, vs.
internal's 9.82/4.60 at the same volumes). 100% volume still shows a
real but far smaller effect (~1.5x AEC-vs-raw ratio, vs. internal's
~10x). Recommendation: for open-speaker use, swapping away from the
built-in speaker to almost any real external speaker looks like a more
complete fix than the threshold mitigation alone. Full data:
`docs/field-notes/2026-08-28-external-vs-internal-speaker-confirms-mac-
mini-built-in-speaker-is-the-driver.md`.

**Follow-up (2026-08-29): a tight N=20 fixed-setting comparison confirms
the grid finding exactly, and the first objective distortion
measurement (THD) backs it up.** Internal vs external at 100%
volume/`aec_delay_ms=309`/N=20: internal AEC-processed false-barges
9.90 vs. external's 3.15 -- matches the broader grid closely. A new THD
sweep (200/1000/4000Hz tones, SNR-gated after an initial ungated
version gave nonsensical noise-floor-dominated results) found a clean
signal at 4kHz: internal speaker's distortion peaks at 100% volume
(4.66% THD, vs. external's 1.02%) then drops -- the first genuinely
measured (not corroborated-by-citation-or-listening-report) evidence
for the distortion hypothesis. 200Hz/1000Hz results were noisier and
not fully explained (1kHz sat oddly flat at 14-20% across most volumes,
not a clean volume-dependent trend). Also found and documented a real
testing-methodology gotcha along the way (see this doc's own Platform
and compatibility section): something plugged into the Mac mini's
front jack mutes the internal speaker at the hardware level regardless
of software output-device selection. Full data:
`docs/field-notes/2026-08-29-thd-measurement-and-n20-fixed-comparison-
plus-front-jack-mutes-internal-speaker-gotcha.md`.

**Not done as part of this pass, deliberately:** publishing a macOS wheel
upstream, or vendoring/prebuilding one for this repo's CI — out of scope
for a documentation-only note; would need its own decision about where
built artifacts live and how they're kept in sync with the pinned
`aec-audio-processing` version.

**Follow-up (2026-08-30, Linux, different hardware): a 100-trial sweep
(10 volume levels x N=10, 100%-10% in 10% steps) pins the clean-floor
transition tighter than a live operator's own real-time estimate.** On
a 4th-gen Intel i7 laptop (openSUSE Tumbleweed) running `conversational`
+ AEC with Codex, **20% system volume was the only level with zero false
barge-ins across all 10 trials** -- 30% and 40% were a large improvement
over 50%+ (2 residual false-barges per 10 trials each, vs. 6-49 above)
but not clean, one step higher than the operator's own live guess of
"30% or 35%" based on ear alone during the same day's live session.
**A real anomaly, not yet corroborated**: 90% showed AEC-processed false
barge-ins (49) *exceeding* raw/no-AEC (33) in 9 of 10 trials -- the same
qualitative "AEC makes it worse than doing nothing" shape this entry's
macOS finding attributes to being *above* the transition zone, but here
appearing as an isolated spike at one level rather than a consistent
pattern across the whole upper range (100% itself shows AEC clearly
helping: 35 raw -> 14 processed). Single N=10 run at that level, not yet
re-run. Full table, methodology, and the same synthetic-vs-live-ear
caveat this entry already carries: `docs/field-notes/2026-08-30-linux-
100-trial-volume-sweep-self-barge-in-clean-floor-at-20-percent.md`.

---

### AEC builds from source on Linux too — but its `lib64` RPM convention breaks the sdist's own library search (upstream)

**Status:** verified 2026-08-24 on openSUSE Tumbleweed (RPM-based). Same
underlying gap as the macOS entry above (PyPI ships no Linux wheel
either, `pip install aec-audio-processing` falls back to a source
build), plus one Linux-specific bug on top: the sdist's `setup.py`
(`get_webrtc_library_path()`) globs only `install/lib/**` for the built
`libwebrtc-audio-processing*` shared object. On a Debian/Ubuntu-style
multiarch layout that's where meson's own `install` step puts it, so the
macOS/Windows-tested path works unchanged. On an RPM-based distro (this
machine), meson's install step follows the platform's own convention
and puts it under `install/lib64/` instead — the glob finds nothing, and
the build fails at the link step even though the shared object was
built successfully one directory over.

**Workaround used (not a durable fix):**

```
ln -s lib64 install/lib
```

run once inside the `uv` build cache's extracted sdist directory, after
the meson build step completes but before `setup.py` finishes linking.
Confirmed unblocks the same `EchoCanceller`/`AudioProcessor` path this
machine went on to run a 119-trial-scale volume sweep against (see
`docs/field-notes/2026-08-24-linux-volume-sweep-reproduces-high-volume-aec-regression.md`
and the other 2026-08-24/25 Linux field notes) — zero code changes to
ConvoBox itself, same as the macOS entry.

**Not done as part of this pass, deliberately:** no upstream bug/PR
filed against `aec-audio-processing` yet (the fix is small — glob
`install/lib64/**` too, or use `pkg-config`/meson's own introspection
instead of a hardcoded path — but is this repo's read of someone else's
build script, not confirmed against their intent); no Linux wheel
published or vendored for this repo's CI, same reasoning as the macOS
entry's deferral.

---

## Backend integration (including upstream bugs)

Problems in or at the boundary with a coding-agent CLI. Upstream entries
are not ours to fix; they are documented so the symptom is recognizable.

### opencode 1.18.3: session-level model pin silently never generates (upstream)

**Status:** diagnosed live 2026-07-18, upstream bug, no fix available
(1.18.3 is the latest release as of this entry). ConvoBox's
`backend.model` feature is effectively dead against this server version.

**Symptom.** With `backend.model` set (e.g. `openai/gpt-5.4-mini`), a
voice session creates its opencode session and POSTs the prompt (both
200 OK, prompt `admittedSeq` returned) but no assistant message is ever
created -- ConvoBox waits out its 120s busy window and gives up. No
error appears in the session's message list, the session object, or the
server's own console output; the session's `time.updated` never
advances past creation.

**Isolated with curl against a live 1.18.3 server** (ConvoBox not
involved), same prompt in all cases:

- unpinned session -> assistant reply in seconds (server default model)
- session pinned `{"providerID":"openai","id":"gpt-5.4-mini"}` -> never runs
- pinned to the Zen twin (`opencode/gpt-5.4-mini`) -> never runs
- pinned with explicit `"variant":"default"` and/or `"agent":"build"` -> never runs

So the pin MECHANISM is broken, not any one provider/credential. The
shape ConvoBox sends is still exactly what the server's own OpenAPI spec
(`GET /doc`) declares for `POST /api/session`.

**Also broken in 1.18.3:** the server ignores config-level default
models for API sessions. With `"model": "openai/gpt-5.4-mini"` (and even
`agent.build.model`) set in `~/.config/opencode/opencode.json`,
`opencode run` correctly uses gpt-5.4-mini, but API-created sessions
still answer with the built-in Zen default (`hy3-free`). CLI and server
resolve the default differently.

**The dedicated model-switch endpoint is broken the same way (found in
the same investigation).** The server exposes `POST
/api/session/{sessionID}/model` (body `{"model": ModelRef}` per its own
spec) -- the endpoint opencode's internal model chooser uses. It returns
204, the session object then genuinely shows the new model, a
`model-switched` marker lands in the message list -- and a subsequent
prompt still never generates. Worse: after prompting a switched session,
`GET .../message` for it stops responding entirely and the server needs
a restart (wedged twice, reproducibly). So all three routes to a
non-default model -- pin at creation, switch endpoint, config default --
are dead in 1.18.3's server, while `opencode run -m` works fine.

**Workaround for now:** leave `backend.model` unset (voice sessions run
on the server's own default) and treat model choice as pending an
upstream fix. Re-verify with the curl matrix above after any opencode
upgrade before re-adding a pin. A ConvoBox-side model chooser (Settings
TUI field fed from `GET /api/model`, the same source the internal
chooser reads) is the right shape once upstream generation works --
deliberately not built while every choosable value produces a dead
session.

**CORRECTION (2026-07-18 late, deeper investigation):** the pin mechanism
itself WORKS -- a session pinned to a model the server has actually
loaded (verified live: `opencode/grok-code`) generates normally. The real
bugs are narrower and nastier: (1) the server's API path never loads the
OAuth-credentialed `openai` provider -- `GET /api/model` lists only
api-key/config providers (Zen, inception, ollama-remote) even with
`"openai": {}` forced into config's provider block and a valid,
unexpired OAuth token; (2) pinning any model absent from that loaded
catalog (all `openai/*`, and Zen models the server build doesn't carry
like `opencode/gpt-5.4-mini`) hangs the session silently instead of
erroring -- that's what every earlier "pin is broken" observation
actually was; (3) the `opencode run`/TUI request path DOES load and use
the OAuth provider (verified: `opencode run -m openai/gpt-5.6-terra`
created its session on this same server and answered), but that lazy
load never becomes visible to API-created sessions -- retested
immediately after, still dead. Net: an API client (ConvoBox) cannot
reach ChatGPT-Plus-OAuth models in 1.18.3 at all; it CAN pin any model
in `GET /api/model` (the Zen catalog: grok-code, kimi-k2.5-free,
minimax-m3-free, qwen3.6-plus-free, ...). Config default `"model"` is
also ignored for API sessions (always Zen `hy3-free`).

**Follow-up (2026-08-07): a real upstream fix for the root cause
described above appears to exist, but this is diagnosed from opencode's
own changelog, NOT live-reverified against ConvoBox -- do not treat as
resolved without re-running the curl matrix above first.** opencode has
shipped 12 releases since 1.18.3 (up to v1.18.15 as of this check,
`gh release list -R sst/opencode`). Two changes in that range look like
they fix exactly this mechanism ("the server's API path never loads the
OAuth-credentialed provider"): **`fix(app): refresh V1 providers after
auth` (sst/opencode#38786, merged 2026-07-25)** -- its own root-cause
description: "V1 provider state is instance-cached... the refetch kept
returning the pre-auth connected-provider list," i.e. a newly
OAuth-authenticated provider's catalog never got rebuilt, which is the
same symptom as `GET /api/model` never listing `openai` here -- and
**`fix(app): refresh global provider state` (sst/opencode#39220, merged
2026-07-28)**, a closely related follow-up. Both predate the locally
installed v1.18.13 by several releases. **Not done, deliberately:** no
live opencode session was run to confirm `GET /api/model` now lists an
OAuth-authenticated provider, or that a `backend.model` pin against it
actually generates -- that would need a real API round-trip against a
live-authenticated provider, out of scope for an unattended research
pass. **Next step, concrete:** re-run the exact curl matrix this entry
already documents (pin `openai/*`, check `GET /api/model`, watch for a
generated reply) against the currently-installed opencode version before
re-enabling `backend.model` in any config -- if it passes, this whole
entry can move to a changelog/fixed note instead of KNOWN-ISSUES.md.

**Follow-up (2026-08-11): the curl matrix above was finally re-run live
(v1.18.15, macOS, real ChatGPT Plus/Pro OAuth credentials configured via
`opencode auth login`) -- STILL BROKEN, same symptom, now with the exact
mechanism identified, plus a more general bug found underneath it.**

`GET /api/model` / `GET /api/provider` on a fresh `opencode serve`
instance still list only the `opencode` (Zen) provider -- the
OAuth-authenticated `openai` provider never appears, exactly as
2026-07-18 found. `opencode run -m openai/gpt-5.4-mini "..."` (and
`gpt-5.4`, and `gpt-5.6-terra`) all answer correctly via the interactive
CLI in the same shell, same credentials -- confirming the split is still
serve-vs-CLI, not credential validity. So the two candidate upstream
fixes (`#38786`, `#39220`) either didn't land in 1.18.15 or don't
actually fix this specific symptom.

**The real mechanism, isolated with `--print-logs --log-level DEBUG`:**
pinning a session to `openai/gpt-5.4-mini` (or `gpt-5.4`, or
`gpt-5.6-terra` -- tried all three, identical) throws server-side:

```
ERROR message="Failed to drain Session" cause="SessionRunnerModel.ModelUnavailableError: Model unavailable: openai/gpt-5.4-mini ..."
```

**This error is logged and then silently discarded -- it never reaches
the API client in any form** (no SSE event, no session-state change, no
HTTP error). The client (ConvoBox, or a bare `curl` against the SSE
event stream, tested both ways) just waits forever with the prompt
sitting in `admitted`/`prompted` state.

**This turned out to be a more general opencode bug than "OAuth
provider not loaded," confirmed by triggering the identical hang three
different ways:**

1. **OAuth-credentialed model** (`openai/gpt-5.4-mini` et al, via `opencode
   auth login`): `SessionRunnerModel.ModelUnavailableError` (above).
2. **`opencode serve --pure`** (external plugins disabled, which is how
   the ChatGPT/Codex OAuth login is implemented): the *same* request
   instead fails with a clean `HTTP 401: Missing bearer or basic
   authentication in header` -- proving the plugin normally attaches
   the OAuth credential to outbound requests for the interactive CLI,
   but that attachment never happens for a `serve`-driven session.
3. **`opencode/hy3-free`** (opencode's own free Zen catalog, previously
   the verified-working model for ConvoBox per this entry's own
   `[L2]`/6d629be history): fails with `HTTP 402: "The account
   associated with the API Key is in arrears... top up the account"` --
   a billing suspension on **opencode's own infrastructure**, nothing
   to do with any credential configured on this machine, discovered
   only because the "known-good" free model was tried as a control and
   turned out not to be free/available right now either.

All three are different root causes at the provider layer -- but all
three produce the **exact same client-visible symptom**: `"Failed to
drain Session"` logged once, then permanent silence. **The actual bug
worth reporting upstream is this general one**: `opencode serve` never
propagates a provider/request failure back to the API caller, regardless
of *why* the provider call failed. `--text`-mode ConvoBox sessions
eventually give up via their own unrelated generic 120s "backend still
busy" bail-out (see the `--text`/`approve`-mode entry elsewhere in this
file for the identical shape of that same class of gap on the ConvoBox
side) -- but nothing ever tells the user *why* nothing happened.

**Not filed upstream yet.** Repro is clean and reproducible (3
independent trigger causes, identical symptom, `--log-level DEBUG`
output in hand) -- a good candidate for a real issue report on
`anomalyco/opencode` if this keeps mattering.

**Workaround found, real and confirmed end-to-end through ConvoBox
(2026-08-11, same session): a manually-declared custom provider in
`opencode.jsonc` sidesteps the whole bug.** All three failures above
share one thing in common -- every model tried came from opencode's
*built-in* provider-catalog/auth machinery (`opencode auth login` OAuth,
an `opencode auth login`-registered API key, or the built-in Zen
catalog). A provider declared directly in config, the same shape as
opencode's own `@ai-sdk/openai-compatible` custom-provider pattern
(seen independently in `anomalyco/opencode#12065`'s working example),
is a completely different code path and was NOT affected:

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "provider": {
    "ollama-local": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "qwen2.5-coder:7b": {} }
    }
  }
}
```

Pinning a session to `ollama-local/qwen2.5-coder:7b` (a local Ollama
instance, OpenAI-compatible endpoint) generated a real, complete
response through raw `curl` against `opencode serve` (`session.next.
text.ended` with actual text, clean `finish:"stop"`) -- and then through
**ConvoBox itself** (`--text` mode, real TTS spoken through the Mac mini
speakers). This is almost certainly the shape of what was running
successfully against opencode on Helios (Windows) earlier in this
project's history -- a manually-configured local/custom provider, not a
ChatGPT-Plus-OAuth or opencode-auth-registered API-key model.

**One separate, expected limitation surfaced by this same test, not a
bug -- verified, not just suspected:** `qwen2.5-coder:7b` returned the
requested tool call (`{"name": "write", "arguments": {...}}`) as plain
response TEXT instead of actually invoking it -- no file was created.
Confirmed this is a genuine model-capability gap, not an opencode/
ConvoBox wiring problem, by bypassing opencode's harness entirely:
called Ollama's own OpenAI-compatible `/v1/chat/completions` directly
with an explicit `tools` schema (the same shape opencode would send) --
identical result, `finish_reason: "stop"` with the call embedded as text
in `content`, no `tool_calls` array at all. This specific quantized
model just doesn't reliably emit native function-calling output despite
being handed a proper schema. A bigger/more agentic local model, or one
explicitly fine-tuned for tool use, would be the next thing to try if
local-model tool-calling through ConvoBox+opencode matters.

**A second, independent bug found while testing a real (Inception Labs)
API-key provider the same way: `{env:VAR}` substitution in
`opencode.jsonc` doesn't work, for ANY value, not just secrets.** Tried
`inception-direct` (`https://api.inceptionlabs.ai/v1`, an Inception Labs
API key) declared the same custom-provider way as the working Ollama
example above, with `"apiKey": "{env:INCEPTION_API_KEY}"` -- consistent
`HTTP 401: Incorrect API key provided`, even though (a) the key itself
was verified valid with a direct `curl` straight to Inception's API
(real `200`, real model list) and (b) the env var was confirmed present
in the `opencode serve` subprocess's actual environment via `ps eww`.
Read opencode's own substitution source
(`packages/opencode/src/config/variable.ts`'s `ConfigVariable.
substitute`, regex `/\{env:([^}]+)\}/g` against `process.env`) -- looks
correct and is applied to the whole raw config file text before JSON
parsing, so the mechanism should work in principle. **Isolated with a
clean control, no secret involved:** substituted `{env:OLLAMA_TEST_URL}`
(a harmless test value) into the *already-proven-working* Ollama
provider's `baseURL` field -- same `ModelUnavailableError` failure,
confirming this is general breakage of `{env:...}` for provider
`options` fields, not specific to API keys or to Inception. Hardcoding
the literal value directly in the file (both for the Inception key and
for the Ollama URL) works immediately every time. **Practical
consequence for anyone following the custom-provider workaround above:
`{env:VAR}` is not currently a safe way to keep a real API key out of
`opencode.jsonc` -- a working config today means the literal key sits
in that file in plaintext.** Not filed upstream yet; a good second
candidate alongside the `serve`-swallows-failures bug above.

**Follow-up, same session: Inception confirmed working end-to-end
through ConvoBox itself (not just raw curl), plus one more real bug --
a startup race, not a config problem.** With a fresh Inception key
hardcoded directly in `opencode.jsonc` (per the `{env:...}` bug above),
the *very first* request to a freshly-started `opencode serve` failed
with the same `ModelUnavailableError` seen throughout this
investigation -- but retrying the identical request against the
*same, now-warm* server succeeded immediately (`"banana"`, clean
`finish:"stop"`), and `scripts/run_convobox.py --text` against that
warm server produced a real spoken TTS response through the Mac mini
speakers. So there's a real startup race in `opencode serve`: a
provider/model can be correctly configured and still fail on the first
request after boot, before succeeding on every subsequent one. Anyone
hitting `ModelUnavailableError` on a custom provider should retry once
against an already-running server before concluding the config itself
is wrong.

**Closing finding: real tool-calling confirmed working end-to-end, not
just text generation.** Every earlier success this session (Ollama,
first Inception pass) only proved the model could generate text --
`qwen2.5-coder:7b` specifically could NOT invoke a real tool (see its
own entry above). Inception's `mercury-2` advertises
`"supported_features":["tools","json_mode","structured_outputs"]` in
its own `/v1/models` response, unlike the Ollama model tried -- worth
testing directly rather than assuming. Asked ConvoBox (`--text`,
`inception-direct/mercury-2`, warmed-up server) to create a file in the
sandbox: **the file was actually created, with the exact requested
content**, and ConvoBox spoke a real confirmation. First genuine
"the opencode agent actually did something" result in this entire
investigation, not just "opencode can talk."

**Practical state for ConvoBox today:** the opencode backend IS usable
via a manually-declared custom provider (confirmed working end-to-end
for actual text generation AND real tool-calling, both local/Ollama and
cloud/Inception, through ConvoBox itself); it remains unusable via
`opencode auth login` (OAuth or API-key) or the built-in Zen catalog,
for the reasons diagnosed above, and any custom-provider config that
needs a real credential currently has to hardcode it (the `{env:...}`
bug above) rather than reference an environment variable. A cold-start
retry may also be needed the first time a server starts. Full write-up:
`docs/field-notes/2026-08-11-permission-model-validation-claude-codex-opencode.md`.

---

### Codex `permission_mode: approve` crashes on current codex-cli -- `approval_policy=untrusted` was removed upstream

**Status:** diagnosed live 2026-08-30, unfixed. `backend.permission_mode:
approve` for the codex backend is currently unusable against codex-cli
0.149.1 (the version installed at diagnosis time).

**Symptom.** `_PERMISSION_CODEX_OVERRIDES` in
`src/convobox/adapters/codex.py` hardcodes `("untrusted",
"workspace-write")` for `approve` mode, injected as `-c
approval_policy=untrusted -c sandbox_mode=workspace-write` at spawn --
verified live against codex-cli as of 2026-07-20 (see the dict's own
comment). Against 0.149.1, the very first turn fails immediately with
`ConnectionError: codex app-server exited` (`codex.py`'s `_ensure_thread`
-> `_request` timing out because the pending future was resolved with
that error from `_read_loop`'s cleanup). Running the exact spawn command
by hand shows why: `codex -c approval_policy=untrusted -c
sandbox_mode=workspace-write app-server` -> `Error: approval_policy =
"untrusted" is no longer supported; remove this setting`. `plan`
(`never`/`read-only`) and `permissive` (`never`/`workspace-write`) are
unaffected -- only `approve`'s value has been deprecated upstream.

**Not a simple rename -- the underlying model changed, not just the
enum's spelling.** The CLI's own error for a truly-unknown value lists
the currently-valid `approval_policy` variants: `untrusted`,
`on-failure`, `on-request`, `granular`, `never` (`untrusted` is still a
*recognized* enum member, just explicitly rejected with a dedicated
error rather than accepted or reported as unknown -- a deliberate
deprecation, not a typo upstream). Swapping in `on-request` alone stops
the crash but is a **false fix, live-confirmed same session**: with
`sandbox_mode=workspace-write` still set, an in-workspace file-write
prompt completed and the file was created with **no approval prompt at
all** -- current codex-cli treats in-workspace writes as already
permitted by `workspace-write` regardless of `approval_policy`, so the
old "any write escalates to approval" behavior `approve` mode depends on
no longer exists at that sandbox setting. That combination was reverted
immediately rather than left in place, since it silently removes the
safety gate `approve` mode exists for instead of preserving it.

**Not yet built:** a real replacement mapping needs to be worked out and
live-verified against the actual approval RPCs (`item/fileChange/
requestApproval`, `item/commandExecution/requestApproval`) before
trusting it -- e.g. `sandbox_mode=read-only` paired with `on-request`/
`on-failure` (forcing every write to escalate, since the sandbox itself
disallows it, rather than relying on `approval_policy` to intercept
writes the sandbox already allows). Until then, `approve` mode fails
loudly and immediately (a crash, not a silent bypass) for the codex
backend specifically -- `plan` and `permissive` remain fine.

**How this was found:** while re-checking settings before a second live
Codex UAT pass (`docs/UAT-codex-smoke.md`), specifically to exercise the
still-untested "approval mid-flight" checklist item, which needs
`approve` mode to trigger a real approval RPC at all.

---

## Web UI

The optional browser companion (`--web`). Newest and least-hardened
subsystem.

### Web UI: artifact pane gaps (0.3.0)

**Status:** diagnosed/scoped, deferred. The web UI (docs/WEB-UI-USAGE.md)
is new in 0.3.0 -- these are known rough edges, not silently-missed bugs.

**PDF doesn't render inline in the artifact pane -- confirmed intentional
v1 design, not a bug (resolved as a non-issue, per the ConvoBox quickref's
PR #176 entry, 2026-07-29; this entry itself never got updated to say
so).** The original 2026-07-28 report observed a PDF opened via
`GET /api/artifacts/{path}` showing nothing inside the pane's frame.
Re-checked directly against current code: `src/convobox/adapters/base.py`'s
`ARTIFACT_MEDIA_TYPES` already maps `.pdf` -> `application/pdf`, so the
serving route (`src/convobox/web/artifacts.py`) sets the correct
`Content-Type` via `FileResponse` -- the backend was never the gap. The
frontend (`index.html`'s `renderArtifact()`) deliberately does NOT put
PDFs (or CSV/txt/md) in an `<iframe>`, by design: only
`_ARTIFACT_IMAGE_EXTENSIONS` get an `<img>` and `_ARTIFACT_HTML_EXTENSIONS`
get a sandboxed `<iframe>`; everything else in the allowlist renders a
plain "Download {filename}" link instead, exactly matching
`docs/ARTIFACT-PANE-SCOPE.md`'s own documented v1 rendering scope ("PDF/
CSV/plain text -> punt to a simple embed/pre fallback or a download link;
not worth [building rich viewers for] v1"). So today's real behavior is a
working download link, not a blank frame -- the "shows nothing" symptom
either predates this fallback-link code (same commit that shipped it,
`b40146e`, 2026-07-28) or was testing the raw API URL directly rather
than the real pane UI. No fix needed; a richer PDF/CSV viewer remains a
legitimate future v2 idea, not an open bug.

**codex now has the same `ARTIFACT` wiring, schema-verified but NOT yet
live-verified end-to-end.** (2026-08-07, `feat/codex-artifact-pane-wiring`
branch, PR #219.) `CodexAdapter._resolve_artifact_writes`
(`src/convobox/adapters/codex.py`) reads a completed `fileChange` item's
`changes: [{path, kind, diff}]` array (confirmed via `codex app-server
generate-json-schema`, codex-cli 0.146.1) and emits an `ARTIFACT` event
per renderable, in-`working_dir` path -- same `ARTIFACT_MEDIA_TYPES`
allowlist and `working_dir` fencing as `ClaudeCodeAdapter`. Unit-tested
against a fake app-server, but **no live session has confirmed the real
`codex app-server` actually reports paths in this shape at runtime** (the
schema bundle and the module's other live probes were done in separate
sessions) -- the first live codex+artifact-pane UAT pass should treat
this as the thing to specifically confirm, not assume-working. See
`docs/field-notes/2026-08-07-codex-artifact-pane-wiring.md`.

**opencode's `file.edited` event: the payload shape is now known, and it
turns out to be a bigger wiring job than "verify the format," not a
smaller one.** (2026-08-07, schema-checked against a real local
`opencode serve` instance, v1.18.13 -- `GET /doc`'s OpenAPI 3.1 spec
fetched live, no prompt sent, no LLM call made, zero cost.) Two real
findings:

1. **The payload is trivial**: `FileEdited`/`EventFileEdited`'s schema is
   just `{type: "file.edited", data: {file: <path string>}}` (or
   `properties: {file: ...}` on the older `/event` variant) -- no
   status/confirmation field at all, unlike codex's `fileChange` (which
   has `inProgress`/`completed`/`failed`/`declined`). If it arrived on
   the adapter's existing stream, wiring it would be close to trivial.
2. **It does NOT arrive on the adapter's existing stream, though --
   confirmed from the schema, not guessed.** `OpenCodeAdapter.events()`
   subscribes to `GET /api/session/{sessionID}/event`, whose SSE payload
   is typed `SessionDurableEvent` -- a 28-member union (`SessionNext*`
   only: prompted, step/tool/text/reasoning lifecycle, compaction,
   revert). `file.edited` is NOT one of those 28 members. It only
   appears in the broader `Event` (`/event`, 89 members) and `V2Event`
   (`/api/event`, 88 members) unions -- i.e. it's a **global,
   not session-scoped** event. Wiring it up means a SECOND, separate SSE
   subscription (`/api/event` most likely, matching the versioned `/api/`
   surface the rest of this adapter already uses) running alongside the
   existing session-scoped one, not just a new case in the current event
   parser. There's also a real correlation question the schema alone
   doesn't answer: `file.edited`'s `data` has no session ID, only a bare
   path, so multiple concurrent opencode sessions (if that's ever a real
   ConvoBox scenario) would be indistinguishable on this stream --
   `GlobalEvent`'s own envelope carries `directory`/`project`/`workspace`
   fields that might be enough to scope it to "this adapter's own
   server," but that's an architecture question, not confirmed here.

**Not done, deliberately:** no code was written for this. This is schema
evidence clarifying scope, the same discipline as the codex investigation
(PR #219). The follow-up design call this entry originally asked for is
now written up: `docs/DESIGN-opencode-artifact-pane-wiring.md` (a second
concurrent `/api/event` subscription, `working_dir` fencing as the
correlation mechanism in place of a session ID the payload doesn't carry,
sliced into a log-only step before a real `ARTIFACT`-emitting one) -- not
implemented, still a design note, not a blind port of the codex pattern.

---

### Web UI: a short CancelledError traceback can appear on quit/Ctrl+C

**Status:** mostly mitigated (2026-07-29), one small residual known and
accepted. `EventBroadcaster.close_all()` (`src/convobox/web/stream.py`)
eliminated the larger, more common source of this -- an open browser
tab's live-events SSE connection being force-cancelled at shutdown --
live-verified: zero "Exception in ASGI application" lines with a real
open SSE connection, versus several before.

**What's still possible.** uvicorn's own internal lifespan-handling task
(`starlette/routing.py`'s `lifespan()` -> `uvicorn/lifespan/on.py`'s
`receive()`) can still log a short `asyncio.exceptions.CancelledError`
traceback when the web server is torn down via `should_exit=True`
(`_stop_web_server`) rather than uvicorn's own normal signal-triggered
shutdown sequence. `run_convobox.py` has to drive shutdown this way
because it owns SIGINT/SIGTERM/SIGBREAK itself
(`_install_web_sigint_override` -- see that function's docstring for
why: `uvicorn.Server.serve()` steals those signals from Python's
default handler for as long as it's running, so ConvoBox has to
register its own handler after uvicorn's to reliably quit at all).

**Why not chased further.** This appears to be an inherent
characteristic of driving uvicorn's shutdown from outside its own
signal-handling flow, not a ConvoBox bug with an obvious fix --
resolving it fully would mean real surgery on uvicorn's own internal
lifespan-protocol driver, disproportionate to a cosmetic log line. The
process genuinely exits cleanly either way (live-confirmed: no
orphaned processes after either symptom).

**Mitigation:** `run_convobox.py`'s `main()` prints a plain console
reassurance ("ConvoBox exited cleanly...") right after a clean
--web quit/Ctrl+C, printed directly (not via `log.info`, which --tui
redirects to a file -- exactly where this wouldn't help) so it's visible
in the same place the traceback, if any, appeared.

---

### "Open in editor" occasionally opens a different file than the one clicked -- fixed

**Status:** fixed, 2026-08-11 (PR #260) -- a stale-fetch race in
`renderArtifact()`'s editor-uri lookup, live-reproduced on the real running
app, then closed with a staleness guard. See below for the full trail:
one hypothesis ruled out (2026-08-09), the real mechanism structurally
identified but unconfirmed (2026-08-10, PR #249), then live-reproduced and
fixed (2026-08-11).

**Symptom, live-hit 2026-08-09** (real codex UAT session): clicking
"Open in editor" on an artifact once brought VS Code to the foreground
showing an unrelated file rather than the one just clicked.

**Ruled out:** a backslash-vs-forward-slash URI-formatting bug in
`get_artifact_editor_uri()` (`web/artifacts.py` built `vscode://file/`
URIs via `Path.__str__()`, which uses native Windows backslashes -- not
a valid URI path separator per RFC 3986). This was the original
diagnosis and PR #249 fixed it (`Path.as_posix()` instead). **Directly
disproven the same night**: JP tested the exact same, still-running,
*unpatched* server process (confirmed via process uptime, never
restarted since before the fix existed) by clicking the real button for
a real artifact (`TestObjects.java`) -- it opened the correct file
correctly, in a new VS Code window, despite the backslash URI. VS Code
on Windows tolerates the malformed-per-RFC URI fine in practice. The
`as_posix()` change is kept as a reasonable portability improvement
(still correct per spec, may matter on non-Windows setups), but it does
not explain the original symptom.

**Leading hypothesis, 2026-08-10 (PR #249):** a real sequencing gap in
`index.html`'s `renderArtifact()`. The "Open in editor" link's `href` is
set via a fire-and-forget `fetch(...editor-uri)` with no staleness
guard. **Correction to that same writeup**: it claimed the main content
render "already tracks `artifactLoadCounter` specifically to prevent a
stale response from clobbering a newer one" -- rechecking the code,
that's not accurate. `artifactLoadCounter` is incremented once per
render and used only as a cache-busting query param on the body-content
URL (`?t=${Date.now()}_${artifactLoadCounter}`); it was never actually
compared against anywhere, so no staleness check existed for *either*
the body content or the editor link. The body content happens to be
race-safe anyway, but for a different reason: each render creates fresh
DOM nodes (a new `<img>`/`<iframe>`, or `<pre>`/`<code>` appended after
`artifactBodyEl.innerHTML` was cleared), so a slow, stale response from
an old render either overwrites an element no longer in the DOM or gets
replaced outright. `artifactEditorLink`, by contrast, is a single
persistent element reused across every render -- there is nothing
structural protecting it, which is exactly why it was vulnerable and the
body content wasn't. Structurally confirmed by code reading; a same-night
attempted timed reproduction (artificial `setTimeout` delays) was
inconclusive, dominated by real Chrome tab-throttling (a requested
~50ms gap actually took ~800ms in practice).

**Live-reproduced, 2026-08-11:** confirmed on the real running app
(PR #249's branch, `--web`, real codex backend, working dir
`_artifact-test-scratch`) by monkey-patching `window.fetch` in the live
page to artificially delay the *first* of two real `editor-uri` calls,
then driving two back-to-back real file edits through the web UI's text
composer. Final observed state: artifact pane title/content = `test.js`
(correct, most recent edit), but `artifactEditorLink.href` =
`vscode://file/.../test.md` (wrong, an older edit) -- the exact symptom
from the original report, reproduced with real fetches against the real
`/api/artifacts/*/editor-uri` endpoint, not a mock. Notably, the run
that reproduced it did so via a *second real, undelayed* ARTIFACT event
for `test.md` that fired naturally after the `test.js` edits, not
primarily through the injected delay -- confirming the race is reachable
under real backend/tool-call timing, not just a contrived artificial
ordering.

**Fix:** added the staleness guard that was believed to already exist.
`renderArtifact()` now captures `artifactLoadCounter` into a local
`loadId` at call start; the editor-uri fetch's resolution callback
checks `loadId !== artifactLoadCounter` and discards the response if a
newer render has started since the fetch was issued -- same pattern the
2026-08-10 writeup described, actually wired in this time. Verified live
by re-running the same reproduction harness against the patched code:
the stale response is now discarded and the href stays correct.

---

## Cosmetic and diagnostic

Real but low-consequence: wrong labels, noisy output, nothing
functionally broken.

### A hard-stopped in-flight turn can show as a generic "error_during_execution" turn -- cosmetic mislabel

**Status:** diagnosed (first noted 2026-08-01 during PR #191's live UAT),
unfixed. Cosmetic only -- never logged via this project's own logging,
never spoken, and doesn't affect the hard-stop itself, which works
correctly. Scoped fix identified, not built.

**Symptom.** Live-confirmed again 2026-08-01 (`convobox-UAT` @ `3d9d4b9`,
`backend.name: claude-code`, `--tui --web`): a `[TUI]` turn labeled
`error_during_execution` appears whenever a hard-stop (pause or safeword)
interrupts an in-flight `claude-code` CLI call. Concrete example from
this session's `convobox-tui.log`:
- `20:06:29,364 transcript='Stop listing.' ... busy=False` -- STT
  mis-transcribed "stop listening" as "Stop listing.", which matched
  neither the pause phrase nor the safeword, so it was sent to the
  backend as a real (nonsensical) query.
- `20:06:35 - 20:06:36` -- a second attempt correctly transcribed as
  `'Stop listening.'`, matched the pause phrase, and hard-stopped the
  still-busy "Stop listing." call via `send_hard_stop()`.
- The interrupted `claude-code` CLI process's own interrupt-confirmation
  text is what surfaces as the `error_during_execution` turn -- it's the
  CLI's own output, not a real ConvoBox error, and it's real behavior
  visible in the TUI turn history, not written to `convobox-tui.log` via
  this project's own `log.*()` calls at all (confirmed: the exact string
  `error_during_execution` does not appear anywhere in the text log for
  this session, only in the on-screen TUI transcript pane).

**Root cause.** `claude-code`'s headless-mode interrupt path (see
`src/convobox/adapters/claude_code.py`'s own module docstring on how this
adapter builds hard-stop since there's no native per-call channel) emits
its own confirmation output when a call is interrupted mid-execution.
`_on_backend_event` in `scripts/run_convobox.py` has no special case for
this and falls through to the generic ERROR system-turn tag ([U10]'s
convention for session-level events worth showing inline), the same
fallthrough noted for [T6]'s TTS-failure-in-`--tui` gap.

**Not yet decided:** whether to give this its own recognizable turn label
(distinguishing "backend confirms it was interrupted, as expected" from
"something actually errored") or leave it as-is since it's cosmetic and
never misleads about whether the hard-stop itself worked. Web UI behavior
not yet separately confirmed -- this session's evidence is TUI-only.

---

### Settings TUI ignores real terminal size below 80x24, and never repaints on resize alone

**Status:** diagnosed live 2026-08-30, unfixed.

**Symptom.** Reported live on Linux: "the settings tui isn't rendering
right either, and it isn't autosizing for the terminal size." Two
independent, compounding causes found by reading `scripts/settings_tui.py`
and confirmed by calling `render()` directly:

1. **Hardcoded minimum floor, live-confirmed by direct call:**
   `render(state, width, height)` opens with `width = max(width, 80)` and
   `height = max(height, 24)` -- calling it with a real, smaller terminal
   size (`width=60, height=20`) still produced 22 lines, each padded to
   exactly **80 columns**, completely ignoring the requested smaller
   size. On any real terminal narrower than 80 columns or shorter than 24
   rows (a common split-pane/tiled-window-manager size, not an edge
   case), every line this emits is wider than the actual terminal, so the
   terminal itself wraps each logical row into two or more visual rows --
   which then breaks `draw()`'s redraw scheme (`"\x1b[H"` cursor-home
   followed by one `"\x1b[K"`-cleared write per logical line): each
   repaint's cursor-home no longer lines up with the previous frame's
   actual row boundaries once wrapping is happening, producing the
   garbled/overlapping look reported as "not rendering right," not just
   clipped content.
2. **No live-resize repaint.** `run_tui()`'s main loop
   (`while running: draw(state); key = read_key(); ...`) only calls
   `draw()` once, then blocks inside `read_key()`'s raw-mode
   `sys.stdin.read(1)` until the next keypress -- there is no `SIGWINCH`
   handler and no timeout-based redraw. Resizing the terminal window
   while the TUI is idle (no key pressed) leaves the stale layout on
   screen until the very next keystroke, which is when `os.get_terminal_size()`
   is finally re-read inside `draw()`. This compounds (1): a session that
   starts in an 80+ column terminal and is then resized smaller keeps
   rendering as if nothing changed until the next key, then suddenly
   suffers the wrapping/misalignment above.

**Not yet built:** clamping `render()`'s layout to the real terminal size
(with a minimum-size message instead of a forced-80x24 layout when the
terminal is genuinely too small to render usefully) and either a
`SIGWINCH` handler or a short poll timeout in the main loop so a resize
repaints without waiting on a keypress.

---

### Settings TUI arrow keys silently did nothing -- root-caused and fixed, confirmed live via key-by-key debug instrumentation

**Status:** fixed and live-confirmed 2026-08-30. `tests/test_settings_tui.py`
156/156 green throughout every step below. Three attempts total, two
ruled out by direct live evidence before the real fix landed -- kept as
a record of the actual debugging path, not tidied into a straight line.

**Symptom, reported live on Linux:** arrow keys did nothing ("don't seem
to be navigating... tested twice. still not working"), `q` to quit
worked fine throughout.

**First hypothesis, tested and reasonably ruled out: SS3 vs. CSI escape
sequences.** `read_key()` originally recognized only the CSI form of
arrow keys (`"\x1b[A"` etc.); many terminals send the SS3 form instead
(`"\x1bOA"`) depending on cursor-key mode (DECCKM). This is a real gap
(fixed: both prefixes are now accepted, purely additive, no regression
risk) but **direct evidence says it wasn't this operator's actual
cause**: a minimal diagnostic script
(`key_probe.py`, blocking `read(1)` calls only, no escape-sequence
timing logic at all) was run live in the operator's real terminal and
showed perfectly standard, immediate CSI bytes for both arrows:
`'\x1b' '[' 'A'` (Up) and `'\x1b' '[' 'B'` (Down) -- exactly the form
`read_key()` already handled, both before and after the SS3 fix. The
SS3 fix stays (a real, separate robustness improvement for other
terminals/modes) but is not the explanation for this report.

**Second hypothesis, live-plausible, not yet confirmed: a too-tight
50ms `select()` timeout on slow hardware.** The operator's own machine
is a 4th-gen Intel i7 laptop, independently reported as noticeably
slower for this project's other work this same day (kokoro synthesis
timing, general responsiveness). `read_key()`'s escape-sequence path
gates on `select.select([sys.stdin], [], [], 0.05)` between reading the
leading `\x1b` and the rest of the sequence -- if more than 50ms elapses
(plausible under this app's own per-frame `draw()` cost between
keystrokes on slower hardware, not necessarily the terminal itself),
a genuine arrow-key sequence is misread as a bare `ESC` (a silent
no-op), with its remaining bytes then misread as literal keystrokes on
the *next* `read_key()` call. Notably, `key_probe.py` -- which has no
timeout/`select()` logic at all, just three unconditional blocking reads
-- worked cleanly on the very same terminal where the real app failed;
that contrast is the actual evidence behind this hypothesis, not
guesswork. First attempt: widened the timeout from 0.05s to 0.3s.
**Live-tested and still not enough** -- the operator's next live session
still needed multiple presses per field ("works better? sorta? but I
have to hit the arrow keys multiple times").

**Live-pty automated verification was attempted twice and both attempts
failed for environment reasons, not code reasons** -- worth recording so
the same dead end isn't repeated: a `pty.fork()`-spawned `settings_tui.py`
fed literal escape bytes was tried once while the operator had a live
`run_convobox.py --tui --web` session running (140-159% CPU from
real-time STT/TTS work) and never completed within 60s under that
contention; a second attempt after that session ended also hung, and
investigation found the FIRST attempt's own forked child had survived as
an orphan (`timeout` only kills its direct child, not a `pty.fork()`-
created grandchild) and was still holding real resources, confounding
the second attempt. Both were killed by exact PID once identified;
neither the operator's real sessions nor their config were touched by
any of this. **Automated pty verification of this specific app is
unreliable in this sandbox -- the fix that actually worked came from
different tooling entirely, below.**

**Root-caused and fixed via live key-by-key debug instrumentation, not
another guess.** Rather than propose a third hypothesis blind,
`run_tui()` gained an opt-in, zero-effect-unless-set debug trace
(`CONVOBOX_TUI_DEBUG_KEYS=<path>`) logging every raw `read_key()` result,
its wall-clock duration, and whether `selected_section`/`selected_field`
actually changed. The operator ran it live and the log gave a direct,
unambiguous answer: a single Right-arrow press was consistently split
into **three separate `read_key()` calls** -- `'ESC'` (this `select()`
timing out), then `'['` and `'C'` each read in ~0.0ms on the *next* two
calls (already sitting in the kernel's input buffer by then, arriving a
beat after the timeout fired) -- each landing as an independent no-op
instead of one `"RIGHT"` token. Exactly the "need to press multiple
times" symptom, now measured directly rather than inferred. The 0.3s
timeout from the first attempt was confirmed via this same log to still
be too tight (repeated splits with ~400-500ms between the pieces).

**Real fix: widened the timeout to 1.0s.** Still a non-issue for the one
legitimate standalone-`ESC` case (cancelling a modal) this timeout
exists to keep responsive -- no human perceives a <1s delay there as
broken -- while giving this hardware's apparent inter-byte latency a
much larger margin. **Confirmed live with a second debug-log run,
cleared and re-captured from scratch**: every arrow press in the new log
resolves into exactly one `'RIGHT'`/`'LEFT'`/`'UP'`/`'DOWN'` token and
moves the selection correctly every time; the only non-moving cases are
genuine boundaries (top of a field list, last tab) -- zero ESC/`[`/letter
splits anywhere in the confirming log. This is the first of the three
navigation hypotheses in this entry with real before/after log evidence
behind it, not inference from a probe script or code reading alone.

**The debug instrumentation was left in place**, opt-in and inert unless
`CONVOBOX_TUI_DEBUG_KEYS` is set -- useful if a different terminal or a
future regression needs the same kind of direct evidence again rather
than re-deriving this same debugging path from scratch.

**Not resolved by this entry:** the operator's earlier "another test
reveals a QC error" comment was never followed up with detail (raised
between the 0.3s and 1.0s attempts) -- if it names a real, separate
issue, it hasn't been captured anywhere and should be asked about
directly next time it comes up.
