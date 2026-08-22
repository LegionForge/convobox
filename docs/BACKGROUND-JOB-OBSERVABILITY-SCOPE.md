# Background-job observability scope

This doc scopes a fix for the disclosed Windows `kill_phrase` gap
(`docs/KNOWN-ISSUES.md`: "`kill_phrase` ends the ConvoBox session cleanly
on Windows, but does not reach an orphaned/detached child process it
spawned") — written as a design pass before code, same reasoning as
`docs/SETTINGS-TUI-SCOPE.md` and `docs/ADVANCED-SETTINGS-SCOPE.md`.

Two research passes fed this doc: a schema/API audit of all three backend
protocols (Codex `app-server`, Claude Code `stream-json`, OpenCode's HTTP
API — live-probed, not just read), and a prior-art + critique pass. Nothing
here is built or committed; this is the plan the code should follow.

## Origin, and the pivot that reframes the whole problem

The original recommended fix (still sitting in KNOWN-ISSUES.md) was: wrap
the backend process in a Windows Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` at spawn time, so anything it ever
spawns — including a deliberately detached descendant — dies when the job
closes.

Working through this with JP surfaced a real problem with that fix, before
any code was written: **it isn't just an engineering shortcut, it's
probably the wrong default.** A user who asks the agent to background a
dev server and leave it running would be legitimately upset if an
unrelated safety phrase later killed it. Promising guaranteed termination
of every spawned/detached process is overpromising a "regular user"
almost certainly doesn't expect from a phrase that means "eject me from
this conversation" — and industry precedent backs this up directly:
**systemd's `KillUserProcesses=` shipped defaulting to "kill everything on
logout," then flipped to "leave it running" after sustained user backlash**
(`loginctl enable-linger` / `systemd-run --scope` are the explicit opt-in
that resulted). This isn't a hypothetical concern; it's an experiment the
ecosystem already ran and reversed.

**The pivot: stop trying to guarantee termination. Guarantee honest
visibility instead.** Track what ConvoBox can actually observe, tell the
user the truth, and let them decide — rather than silently doing nothing,
or silently killing something they wanted kept alive.

## What each backend's protocol actually exposes today

Audited directly against each backend's real protocol (not the backend's
own direct-user UX) — `codex app-server generate-json-schema`, a live
`claude` stream-json probe (N=4 runs), and a live `opencode serve`'s
fetched OpenAPI spec:

| | Job list | Kill-one-job RPC | OS PID | Exit code |
|---|---|---|---|---|
| **Codex** | ✗ | ✗ | ✗ (`processId` is a connection-scoped string handle, not an OS pid — schema confirms the existing macOS field note's finding) | ✓, foreground calls only (`CommandExecutionThreadItem.exitCode`/`durationMs`) |
| **Claude Code** | **✓, pushed unsolicited** (`system/background_tasks_changed`, `task_started`, `task_updated`, `task_notification`) | **✓ `stop_task` — live-verified** (`control_request {subtype: stop_task, task_id}` → `control_response success` → `task_updated {status: killed}`) | ✗ | ✓, but **31–79s late**, and the reported `end_time` is the *report* instant, not the real exit instant |
| **OpenCode** | ✗ (bash tool has no background mode at all — confirmed absent from the shipped binary) | ✗ (`POST /experimental/session/{id}/background` is session-scoped, no handles, no enumeration) | One real PID field exists (`Pty.pid`), but PTYs are client-created terminals, not agent tool calls — `GET /pty` on a live agent session returns `[]` | ✗ |

**The corrected headline:** no backend gives ConvoBox an OS PID for an
agent-spawned process — that part of the original assumption holds, and
it's why the macOS fix had to fall back to `ps` command-line matching
instead of asking the agent directly. **But Claude Code already gives a
real, live, protocol-level job list and a working kill RPC, over the exact
channel `ClaudeCodeAdapter` already reads and writes — and the current
adapter uses neither.**

## Recommended architecture

Two **defaulted, non-abstract** methods on `BackendAdapter`
(`src/convobox/adapters/base.py`), modeled on the ABC's existing pattern
(`wait_listening`, `resolve_pending_approval` already ship as
default-no-op-override-where-real) — not a new `@abstractmethod` (breaks
every adapter for an optional capability) and not a separate mixin/Protocol
(pushes `isinstance` checks into orchestrator code, which the ABC exists to
avoid):

```python
class JobState(str, Enum):
    RUNNING = "running"; EXITED = "exited"; UNKNOWN = "unknown"

@dataclass(frozen=True)
class BackgroundJob:
    id: str              # adapter-scoped handle (codex item id / CC task_id / opencode callID)
    state: JobState
    label: str            # speakable/renderable; NEVER raw command text
    command: str | None   # raw text; only populated when the adapter has it AND verbose is on
    pid: int | None       # a real OS pid where one genuinely exists (none today; Job Object later)
    exit_code: int | None
    started_at: float
    observed_at: float    # when this state was last CONFIRMED -- load-bearing, see Staleness below
    source: str           # "protocol" | "os-scan" | "inferred"

class BackendAdapter(ABC):
    ...
    def background_jobs(self) -> Sequence[BackgroundJob]:
        return ()  # synchronous, no I/O -- returns an already-observed snapshot
    async def stop_background_job(self, job_id: str) -> bool:
        return False  # deliberately separate from force_kill() -- see Eject below
```

`background_jobs()` is synchronous and does no I/O on purpose: it's called
from the quit path and potentially the eject path, and an adapter that
blocks here reintroduces the exact "the control path rides the stuck
channel" failure mode `force_kill()`'s own docstring already exists to
avoid.

Per-adapter population, all from events already parsed today — zero new
I/O:

- **`ClaudeCodeAdapter`**: wire up the `background_tasks_changed` /
  `task_started` / `task_updated` / `task_notification` events already
  arriving in `_to_backend_events`. `source="protocol"`.
- **`CodexAdapter`**: generalize the existing `_last_command_text` into a
  small dict keyed by item id, and start keeping the `exitCode`/
  `durationMs`/`status` fields `_handle_notification` currently discards on
  `item/completed`. Anything the command detaches stays `UNKNOWN` — never
  claimed as `EXITED`.
- **`OpenCodeAdapter`**: `session.next.tool.called/success/failed` gives
  callID + status. `source="protocol"`.

**New module `src/convobox/adapters/process_scan.py`** — hoist
`_strip_shell_quotes` / `_is_bare_generic_shell` / the `ps`-matching logic
out of `codex.py` (where it's macOS-only today) and split them into two
halves that were never actually the same concern:

- `find_by_command_text(text) -> list[ProcInfo]` — **observation**,
  cross-platform (`ps -eo pid,ppid,command` on POSIX; `Get-CimInstance
  Win32_Process` on Windows — the existing Windows harness already proved
  WMI `CommandLine` matching works with no new dependency).
- `kill_pids(pids)` — **action**, unchanged from today's POSIX-only fix.

Today the macOS fix is buried inside `CodexAdapter`; `ClaudeCodeAdapter`
gets none of it; a future 4th adapter would have to copy-paste it.
Splitting observe from act also makes the Windows half tractable, because
the Windows half is *only* the observe half (see next section).

**The single strongest technical recommendation here: a Windows Job
Object for observation only — no `KILL_ON_JOB_CLOSE`.**
`CreateJobObjectW` + `AssignProcessToJobObject` at spawn, then
`QueryInformationJobObject(JobObjectBasicProcessIdList)` enumerates
**every PID in the job, including detached descendants** —
`Start-Process` does not break out of a job unless
`CREATE_BREAKAWAY_FROM_JOB` is used and the job explicitly permits it. Same
primitive KNOWN-ISSUES.md already recommends, with exactly one flag
removed: the visibility comes for free, and killing stays a separate,
explicit decision instead of an automatic spawn-time policy. ~60 lines of
`ctypes`, no new dependency, same shape as the existing
`_memory_diagnostic()` precedent. (Windows 8+ allows nested jobs, so being
inside someone else's job — Claude Code's own Bash tool, VS Code, a CI
runner — no longer blocks assigning our own.)

Ownership: not `Orchestrator` — its docstrings already refuse to own "what
ending the session means" (`_on_kill_phrase` is a caller callback,
deliberately not composed in). A thin `Orchestrator.background_jobs()`
pass-through, so `web/bridge.py` and the TUI don't reach past it into the
adapter. Web surface: a new event type on the existing `EventBroadcaster`,
same shape as `APPROVAL_REQUEST`/`ARTIFACT` — no new endpoint, no polling.

**4th-backend test:** an adapter implementing neither method gets `()` /
`False` — the gate correctly says nothing, the panel correctly shows
*nothing observed* (not "nothing is running"). A protocol job list is one
method override; OS-level observation is `import process_scan`. That bar
is met by the design above.

## Eject (`kill_phrase`) must NOT be gated — corrected from the original framing

The original proposal considered gating both quit *and* eject on
confirmed-still-running jobs. **This is wrong and needs to be corrected
before any code exists.** `kill_phrase` is an emergency stop whose entire
documented purpose is reaching a wedged backend when nothing else works —
and this repo already has a validated-live Known Issue (the hard-stop
entry) where *both* hard-stop paths and the web Stop button went
simultaneously unresponsive at once. Putting a confirmation prompt in
front of the one lever that still works in a wedged state is a safety
regression dressed up as an honesty improvement.

**The honest move for eject is a post-hoc report, not a pre-hoc gate**: on
the way out, one line naming what ConvoBox could and couldn't account for,
carried on the existing `session_ended` event the web UI already handles.
Zero added friction, zero new failure mode, and it completes the "ends the
session" promise precisely instead of half-blocking it.

**Quit** (graceful, no wedged backend implied) is the only path that gets
an actual confirm gate — and it should reuse the existing arm/confirm
mechanism the web Quit button already has, not invent a new one.

## The Windows scenario this exists for would produce zero warnings from a protocol-only tracker

This is the load-bearing correction to the whole design: the Windows gap
is *definitionally* the case where a job left ConvoBox's field of view —
the descendant orphaned itself before anything could observe it. A
protocol-level tracker (Claude Code's job list, Codex's exit codes) knows
nothing about it, by construction. **On codex/Windows — the exact
backend+platform combination where the gap is validated-live 5/5 — a
protocol-only implementation produces honest silence, not a warning.**
The Windows Job Object observation step is the only piece of this design
that makes the motivating scenario visible at all; everything else is
honest bookkeeping for cases that were never actually the problem.

## Staleness — a real, measured gap in the proposal

Claude Code reports a backgrounded task's natural completion **31–79
seconds after it actually exited** (measured directly: `sleep 10` reported
79s late, `sleep 5` reported 31s late), and stamps `end_time` at report
time, not real exit time. At the moment of Quit, "confirmed still running"
can be wrong — in the false-alarm direction — by up to a minute, on the
single most common case (a short backgrounded task). Left unhandled, this
reproduces the exact alert-fatigue problem the design is trying to avoid,
just via a different mechanism.

**Required, not optional:** `BackgroundJob.observed_at` must gate
freshness. Either downgrade a `RUNNING` observation older than some
threshold to `UNKNOWN` (→ silent, per the untracked rule below), or
re-confirm via the OS scan at gate time rather than trusting the last
push.

## The two-state, not three-state, toggle — and why `basic` must be the default

The earlier `off`/`basic`/`verbose` idea, modeled on
`interaction.approval_explanation_mode`, conflates two things that
`approval_explanation_mode` doesn't have to distinguish: *detail level* and
*whether the gate fires at all*. `approval_explanation_mode` is a pure
detail knob with no behavioral component, so the analogy only carries
partway.

**Decision:** `off` means no gate and no panel, full stop — not "gate with
less detail." `basic`/`verbose` differ only in whether raw command text is
shown; gate behavior is identical between them.

**`basic`, not `off`, must be the default**, for a specific reason: the
worst real incident in the Windows field note produced a **doubled**
runaway process (codex's own PID tracking got confused by the detachment
and launched a second copy of the same background job). Both instances
were untracked at the time. A silent-on-unknown rule (below) combined with
`off`-by-default would mean the worst incident to date produces the
quietest possible output. That's only defensible if the passive layer is
actually on.

Per this repo's own convention (`ADVANCED-SETTINGS-SCOPE.md`: every
privacy/security-relevant knob defaults to the safe posture), `verbose`
specifically — which can surface raw command text, including a
command-line-embedded secret (see Privacy below) — is the one value that
should live behind the Advanced-section warning treatment once that
section exists, not a plain three-way picker.

## Untracked / genuinely unknown state

If a job's status can't be confirmed either way (never observed, or a
stale `RUNNING` downgraded per Staleness above): **stay silent on the
gate**, matching Kubernetes' `Unknown` pod phase and Docker's `dead`
state — both treat "we can't tell" as a real, displayed-but-not-acted-on
state rather than either "assume alive" or "assume dead." The passive
panel still shows it honestly as `unknown` for anyone who looks; the gate
just doesn't fire on it, to avoid training the user to dismiss warnings
that fire on mere uncertainty.

## Privacy

`docs/SECURITY.md` already names command-line-embedded credentials as an
always-private category (its own worked example is a `curl -H
"Authorization: Bearer <api-key>"` call) — this isn't a new risk
*category*, but the design moves it into weaker protection if not handled
explicitly:

- **The panel is persistent for the whole session**, unlike a tool-call
  event that scrolls past — different shoulder-surf/screenshot exposure
  than existing event types. Worth a line in user-facing docs, not a
  blocker.
- **Job records must not be persisted** if `web.history_tracking_enabled`
  is on — they're ephemeral session state, not history. A command line
  with a secret must never land in the gitignored-but-still-real SQLite
  history file. This needs to be explicit in the implementation, not
  assumed.
- **Command text must never reach the TTS path, independent of the config
  tier.** This is a voice product; a gate that reads a `curl -H
  "Authorization: Bearer sk-..."` out loud is a new leak channel with no
  precedent in `SECURITY.md`. Hard rule, not a `verbose`-only concern.

## Force-kill vs. graceful quit need different bookkeeping — four concrete divergences

1. A `force_kill()` that wraps in a Job Object kills in-job descendants —
   `RUNNING` records go stale **because of ConvoBox's own action**. The
   post-kill report must re-query current state, not replay the pre-kill
   snapshot, or it will confidently name processes it just killed.
2. `CodexAdapter.force_kill()` deliberately preserves `_thread_id` but
   nulls `self._proc`. Any tracker keyed off adapter state must survive
   that, or the post-kill report comes back empty.
3. `ClaudeCodeAdapter.force_kill()` explicitly skips the rest of teardown,
   expecting a full normal shutdown to follow. Wire the gate/report into
   **one caller** (`run_convobox.py`'s shutdown path), not into an adapter
   method, or it fires twice or not at all.
4. On the `kill_phrase` path, `Orchestrator.force_kill()` calls
   `stop_event_loop()`, so no further adapter events are consumed and job
   state freezes at the last observation — exactly the staleness case
   above, arriving through a different door.

## Voice interaction is currently unscoped, and shouldn't default to a spoken gate

The design above is described entirely in visual terms (web panel, quit
dialog). What happens if the user says "quit" by voice with a job running
is an open question, and this repo has hard evidence that a new required
safety *phrase* is expensive to get right: `"halt halt halt"` failed
round-trip transcription 4/5 times, bare `"Athena"` failed 3/5, and any
new phrase needs the same round-trip verification discipline
`resumeword/detector.py` already documents. **Default scope: the gate is
web/TUI-only**, confirmed with a keypress/click, not a new spoken phrase.
Extending it to voice is a separate, later decision that needs its own
phrase-reliability pass if it's ever pursued.

## What counts as a "job" — the predicate that keeps this from becoming noise

Claude Code's own protocol implies multiple task types
(`task_type: "local_bash"` among them) and separately reports
`subagent_stats` on every result. **A running subagent is not something
the user backgrounded** — warning about it at quit would be pure noise
with no actionable consequence. The predicate for "worth tracking" is **"an
OS-level side effect that outlives the session,"** not "anything the
backend's protocol calls a task" — matching VS Code's own
`confirmOnExit: hasChildProcesses` design (prompt because there's a real
child process beyond the idle shell, not because a terminal merely
exists).

## Ship now / later / never

**Now — completes the existing `kill_phrase` promise, all low-risk:**
- Docs + Settings-TUI help text update: say plainly what `kill_phrase`
  does and doesn't reach today. Zero code.
- `session_ended` payload + final log line on eject: what was observed,
  what couldn't be accounted for. Post-hoc, no gate, no new failure mode.
- Windows Job Object, **observation only** — the one piece that makes the
  motivating Windows scenario visible at all.
- The `BackgroundJob`/`background_jobs()` seam and per-adapter bookkeeping
  above (all from events already parsed).

**Later — real, but each needs its own verification pass:**
- Graceful-Quit gate (Quit path only, never eject), on the existing
  arm/confirm mechanism.
- The passive "background jobs" panel in the web UI.
- `stop_background_job()` wired to Claude Code's live-verified `stop_task`
  RPC — as a **user-initiated panel action only**, never automatic, never
  reachable from the `kill_phrase` path.

**Never, or not as originally specified:**
- Any automatic killing of detached descendants as a default (the pivot
  this whole doc is built on).
- A "wait for it to finish" option on quit — no indefinite blocking on an
  external process with no defined end.
- Command text (or any job detail) ever reaching the TTS path or the
  persisted history store, regardless of config tier.

## Decisions made

- Guarantee visibility, not termination. The Job Object (or any future
  mechanism) exists to observe, never to auto-kill on session end.
- `kill_phrase`/eject is never gated. Quit is the only gated path.
- Two behavioral states (gate on/off), not three — detail level
  (`basic`/`verbose`) is orthogonal to whether the gate fires.
- `basic` is the default, not `off`.
- Untracked/unknown jobs never trigger the gate; they're shown honestly in
  the passive panel instead.
- No command text on the TTS path, ever. No job-record persistence to the
  history store, ever.
- The gate is web/TUI-only for now; a voice-triggered version is a
  separate, later decision requiring its own phrase-reliability pass.

## Open questions

- Exact staleness threshold for downgrading a `RUNNING` observation to
  `UNKNOWN` — needs real numbers per backend, not just Claude Code's
  measured 31–79s.
- Whether `stop_background_job()` should be exposed for Codex/OpenCode at
  all once/if either ever grows a real per-job kill RPC, or whether it
  stays Claude-Code-only indefinitely.
- Whether the passive panel should show historical (already-exited) jobs
  for the current session, or only currently-live ones — a UI-scope
  question deferred to whoever builds the panel.
- Live-verification bar for this whole feature, stated up front, before
  anyone claims it's done: the motivating gap has a documented
  8/8-automated-pass vs. 0/5-live-pass divergence, with a standing
  instruction not to treat the automated harness as predictive for this
  specific scenario. Any acceptance test for this work must include a
  real live run reproducing the original Windows incident, not just a
  scripted harness.
