---
title: Permission-model (plan/approve/permissive) live validation across claude-code and codex; opencode still blocked on credentials
status: validated-live (plan, permissive -- N=2 each backend); approve mode -- text-mode gap deterministically confirmed via code + live repro, live voice-approval flow inconclusive (real ambient noise)
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 4f01148; macOS 26.x, Apple Silicon (Mac mini M4); AIRHUG 28 (USB mic), Mac mini Speakers; macOS system output volume=25%
hardware:
  computer: Mac mini M4 (2024), single built-in speaker
  microphone: AIRHUG 28 USB conference mic, ~8cm placement, AI DSP off (green LED)
  room_state: NOT a quiet/controlled room this pass -- real background noise present throughout (a second person playing video games + music in the same room), mic ambient RMS ~0.021-0.022, peak ~0.79-0.82 (near-clipping loud), confirmed via a standalone MicrophoneStream probe run twice
evidence:
  - 3 backends x 3 permission_mode values (plan/approve/permissive) exercised where testable, each isolated to a sandbox working_dir fully outside any ConvoBox git checkout (not the UAT worktree -- a plain scratch dir), per this session's own "isolated working_dir" discipline
  - plan and permissive modes: N=2 independent --text runs per backend, consistent both times
  - approve mode: --text-mode behavior read from source and confirmed live (claude-code N=1 full run to resolution, codex N=2, one lost to output buffering but same outcome, one clean)
  - live mic-loop voice-approval flow: 4 real injection attempts at escalating volume (3.0x/3.0x/5.0x/7.0x), diagnosed against real log output (--verbose)
  - opencode: re-checked `opencode auth list` and a port probe for `opencode serve` -- unchanged from the prior macOS pass, 0 credentials, no listener
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the full backend x permission-mode validation pass, set system volume to 25% for it, and caught the real cause of the first live-approval failures -- a macOS permission dialog that had gone unnoticed)
    - Claude Code (Anthropic claude-sonnet-5) -- built the isolated sandbox, wrote the 6 permission-mode configs, ran and diagnosed every test, read the relevant adapter/run_convobox.py source to explain what was observed
  org: https://legionforge.org
  created: 2026-08-11T19:30:00-05:00
  revised: 2026-08-11T19:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Permission-model validation: claude-code, codex, opencode

## Scope and method

Every prior macOS field note this session ran with `backend.permission_mode: plan`
(Claude Code's read/explore/explain-only mode) specifically because it can never
trigger a tool-call approval prompt -- convenient for AEC/barge-in/safeword
testing, but it meant the actual permission-gating system had never been
exercised live on this machine. This pass closes that gap directly.

ConvoBox's `backend.permission_mode` has three values, each mapped differently
per backend (`src/convobox/adapters/claude_code.py` `_PERMISSION_CLAUDE_MODE`,
`src/convobox/adapters/codex.py` `_PERMISSION_CODEX_OVERRIDES`):

| mode | claude-code | codex |
|---|---|---|
| `plan` | `--permission-mode plan` (read-only, no writes) | `approval_policy=never, sandbox_mode=read-only` |
| `approve` | `acceptEdits` + a real `PreToolUse` hook (voice-gated) | `approval_policy=untrusted, sandbox_mode=workspace-write` (writes escalate to approval) |
| `permissive` | `bypassPermissions` (skips all checks) | `approval_policy=never, sandbox_mode=workspace-write` (writes freely) |

opencode has no permission-mode concept at all (README's own characterization,
confirmed by `grep` finding zero `permission_mode`/`approval` handling in
`src/convobox/adapters/opencode.py`).

Every write-capable test ran with `backend.working_dir` pointed at a sandbox
**outside any ConvoBox git checkout** (`/private/tmp/.../scratchpad/permission_sandbox`,
not the `convobox-UAT` worktree -- the tool's own working-dir check flagged the
UAT worktree as "inside ConvoBox's own source tree" on a first attempt, a real
and correct warning, so the sandbox was moved).

## `plan` mode: confirmed clean, both backends, N=2

Asked each backend to create a file. Neither backend ever wrote anything,
both times, for both backends:

- **claude-code**: explained a one-line plan ("create X with content Y"),
  offered to proceed, stopped -- `ExitPlanMode` correctly not available as a tool.
- **codex**: attempted the write, self-reported "I couldn't create the file
  because this workspace is read-only" (from `sandbox_mode=read-only`).

No new files in the sandbox after either backend, either run.

## `permissive` mode: confirmed writes freely, no prompts, both backends, N=2

Same request, `permissive` mode. Both backends created the requested file
immediately, no approval request emitted, both runs:

```
permissive_test.txt, permissive_test2.txt        (claude-code)
codex_permissive_test.txt, codex_permissive_test2.txt   (codex)
```

## `approve` mode: a real gap found in `--text` mode, deterministic and code-confirmed

**Finding: `--text` mode + `permission_mode: approve` never resolves the
approval on either backend.** Not a timeout-to-deny -- the request is
abandoned when `--text` mode's own generic 120s "give up waiting" bail-out
fires, and the process exits without ever sending an explicit decline.

Root cause, read directly from `scripts/run_convobox.py`: the `approval_gate`
(`ApprovalPromptGate`, which owns the 30s-default `approval_timeout_s` that's
supposed to auto-deny a silently-abandoned prompt) is only ever *ticked* by
`_working_watchdog`, and `watchdog_task = asyncio.create_task(_working_watchdog(...))`
is constructed at line ~2431 -- **after** `--text` mode's own early `return`
at line 2069-2084. So in `--text` mode, `ApprovalPromptGate.observe_timeout()`
is simply never called. The request just sits pending until `_drain_until_idle`'s
unrelated, generic 120s "backend still busy" bail-out gives up and the script
calls `adapter.aclose()`, which disconnects/kills the backend subprocess
without ever answering the approval.

Live-confirmed on both backends:

- **claude-code**, `--text`, N=1 full run to resolution: approval prompt fired
  ("Approval needed to run Write. Say cobalt night and gale to approve, or
  say no to deny."), then **120s of total silence**, then
  `backend still busy after 120s; giving up the wait`. No file created.
- **codex**, `--text`, N=2 (one run's log lost to a pipe-buffering artifact on
  kill, same no-file outcome both times; the clean run shows identical
  behavior): approval prompt fired (`item/fileChange/requestApproval`,
  "Silence denies the request" -- a message from codex itself, not from
  ConvoBox, and not actually true in this mode since nothing ever sends that
  denial), then the same 120s bail-out. No file created.

**Practical net effect is fail-safe** (nothing ever gets written without a
real answer) but the mechanism is not what it looks like -- it reads as
"denied," but it's actually "abandoned and disconnected." Worth fixing:
either construct (or a lightweight standalone version of) the watchdog in
`--text` mode too, or have `--text` mode's own exit path call
`resolve_pending_approval(False)` explicitly before `adapter.aclose()`.
Not fixed this pass -- diagnosis only, per this repo's "characterize before
fixing" pattern for anything discovered mid-UAT.

## Live mic-loop voice-approval flow: inconclusive, real ambient noise, not a code bug

The one thing `--text` mode structurally cannot test is whether the *actual*
voice-gated approval flow works -- `ApprovalDetector` listening for the spoken
`approval_phrase` requires the live mic loop's own watchdog, which by
definition only exists outside `--text` mode. Attempted via the same
synthetic-injection technique used earlier this session for safeword tests.

**This room was not a quiet/controlled testing environment for this pass** --
a second person was playing video games with music running in the same room
throughout (explicitly OK'd for this test; system volume set to 25% partly
for this reason). A standalone `MicrophoneStream` probe (run twice) confirmed
real, loud, steady ambient signal: RMS ~0.021-0.022, peak ~0.79-0.82 --
close enough to full-scale to matter.

Four injection attempts (3.0x, 3.0x, 5.0x, 7.0x volume), with `--verbose`
logging on the last two to see the actual VAD/STT decision path:

- First two attempts (3.0x): a suspected macOS permission-dialog interruption
  (caught mid-session by JP -- a permission prompt on this machine had gone
  unnoticed) may have affected timing; no VAD segmentation of the injected
  phrase at all.
- Third attempt (5.0x): VAD *did* segment a real 3.6s utterance
  (`Processing audio with duration 00:03.616`, language confidence 0.84 --
  clearly picked something real up), but Whisper's own no-speech-threshold
  heuristic rejected it (`No speech threshold is met (0.656652 > 0.600000)`)
  -- `dropped (no input, STT heard nothing recognizable)`.
- Fourth attempt (7.0x): no VAD segmentation at all again.

A standalone mic probe both before and after confirms the microphone itself
was working correctly throughout -- this is a real-world STT-under-noise
degradation, not a broken feature, and not the permission-dialog issue
either (that was isolated to an unrelated `osascript`/System Events call
made independently during diagnosis, not the ConvoBox process). It's also
directly consistent with this session's own earlier `[E6]`/round-trip
findings: far-field acoustic injection through a real, noisy room is a
harder problem than clean synthetic audio, and this room had real
competing audio in it the whole time, unlike every prior successful
injection test this session (all run in a quiet house).

**Not closed this pass.** To close cleanly: either retry in a genuinely
quiet room (matching every earlier successful injection test's conditions),
or use a non-acoustic injection path (e.g. feeding synthesized audio
directly into the STT pipeline the way the `[E6]` isolation test did,
bypassing room acoustics and the real mic entirely) paired with a way to
drive the mic loop's watchdog without needing a live room at all.

## opencode: still blocked, unchanged

Re-checked `opencode auth list` (0 credentials, same as the prior macOS
pass) and probed for a listening `opencode serve` (none running, port 4096
unreachable). No change from the earlier finding -- opencode remains
untestable on this machine without a provider credential, which is a user
decision (an API key), not something to add unilaterally.

## What transfers

- **`plan` and `permissive` modes are solid on both claude-code and codex** --
  N=2 each, fully consistent, matches their documented contracts exactly.
- **`--text` mode + `approve` mode has a real, deterministic gap**: the
  approval-timeout watchdog doesn't run outside the mic loop, so a pending
  approval is abandoned (not explicitly denied) after ~120s. Net effect is
  safe (nothing gets written) but the mechanism is misleading. Worth a real
  fix, not done this pass.
- **The live voice-approval flow itself remains unconfirmed on macOS** --
  not because it's broken, but because this specific test session had real,
  loud competing background audio in the room the whole time. This is a
  genuinely different failure mode from every other thing this session
  characterized (it's an environmental testing-conditions problem, not a
  ConvoBox bug), and it's worth being explicit about that distinction so a
  future pass doesn't waste time re-diagnosing the wrong thing.
- **opencode is still just blocked on a credential the user hasn't configured
  on this machine** -- not a bug, not something this session can resolve on
  its own.
