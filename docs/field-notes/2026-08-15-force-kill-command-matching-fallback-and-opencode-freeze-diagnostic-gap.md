---
title: A fragile pgrep/command-matching fallback works as a last-resort kill for codex on macOS; opencode survived a 6-cycle VAD stress run but has zero stall diagnostic on its own SSE read path, unlike codex/claude-code
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, codex-cli 0.147.0 (confirmed current via `brew outdated`), opencode 1.18.15 (localhost:4096), macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Continuation of this session's ongoing force_kill()/VAD-freeze macOS investigation, autonomous /loop round 1
  - Prototype scratch scripts (not committed) cross-checking codex's reported commandExecution "command" field against real spawned processes via pgrep
  - A real opencode serve + ConvoBox session run through the same 6-cycle synthetic-speech stress harness used for codex/claude-code
  - Direct source read of src/convobox/adapters/opencode.py's events() SSE loop
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up an autonomous /loop to continue this investigation across the night)
    - Claude Code (Anthropic claude-sonnet-5) -- prototyping, live testing, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T02:10:00-05:00
  revised: 2026-08-15T02:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A fragile command-matching fallback works for codex; opencode has zero freeze diagnostic on its SSE path

**Context.** First autonomous `/loop` round of this session's ongoing
macOS force_kill()/VAD-freeze investigation (see the four other
2026-08-15 field notes for the full thread). This round: checked for a
newer codex-cli (none available), prototyped a last-resort kill fallback,
and filled the one backend/scenario combination not yet tested for the
VAD freeze itself (opencode).

## codex-cli version check: 0.147.0 is already current

`brew outdated codex` reports nothing to update -- the version used
throughout this entire investigation (0.147.0) is the latest available
via this machine's install method. The "does a newer version report a
real PID" question from the prior round's open-items list is answered:
not testable right now, nothing newer exists to try.

## A pgrep/command-matching fallback DOES work, with real caveats

Following up on the prior round's dead end (codex's own reported
`processId` field doesn't match the real process): codex's
`commandExecution` items also report a `command` field, confirmed
accurate in a prior round's raw payload
(`"/bin/zsh -lc \"sh -c 'echo pidcheck1; sleep 5'\""`). Tested whether
`pgrep -f` against a substring of that reported text can locate the real
process, as a fallback for when `force_kill()`'s clean mechanisms all
fail.

**Result: yes, with an important nuance.** `pgrep -f` against the FULL
reported command string (even correctly shell-escaped) found NOTHING --
because the real process's actual argv (`sh -c 'echo ...; sleep 90'`)
is only the INNER portion of what codex reports; the outer
`/bin/zsh -lc "..."` wrapper layer has already exec'd through by the
time the real long-running work is happening, so its own argv no longer
exists to match against. **Matching a substring of the INNER command
text works reliably** -- confirmed via a direct pgrep on the actual
command text extracted from the reported field.

**Real caveats, why this is "last resort" not "the fix":**
- Requires correctly parsing/stripping the reported command's outer
  shell-wrapper layer to extract the inner text -- a string-parsing
  problem with edge cases (nested quoting, multiple wrapper layers for
  more complex sandbox configurations) not fully characterized here.
- `pgrep -f` matches ANY process whose command line contains the
  substring, not just the one codex spawned -- a real false-positive
  risk if the same shell text happens to appear in an unrelated
  concurrent process (unlikely for arbitrary generated commands, but not
  impossible, especially for short/generic commands).
- Timing-sensitive: the real process must exist by the time the search
  runs; a fast-failing or not-yet-spawned command could be missed
  entirely (same limitation the very first Windows harness noted about
  file-write scenarios never catching "before first write").
- Not tested here: what happens if TWO tool calls are in flight
  concurrently with similar command text -- a real scenario ConvoBox
  should handle correctly, unexplored.

**Assessment:** viable as an opt-in, clearly-labeled best-effort
fallback (e.g. only triggered after the clean `terminate()` path is
confirmed to have left a survivor), not as the primary mechanism. Worth
scoping as a real Phase 2 addition to `force_kill()`, not urgent enough
to build unscoped in an autonomous loop.

## opencode: 6/6 clean cycles, but a genuine diagnostic blind spot found

Ran the same synthetic-speech VAD stress harness (6 cycles) against a
real ConvoBox session on the opencode backend for the first time this
session (prior rounds only tested opencode's `force_kill()` behavior
directly, not its resistance to this stress pattern). **No freeze --
all 6 cycles completed with normal responses.** Small sample, same
caveat as every other backend comparison this session.

**While investigating, found a real structural gap**: `OpenCodeAdapter
.events()`'s SSE read (`aiter_sse()`) uses `timeout=httpx.Timeout(5.0,
read=None)` -- **no read timeout at all**, a deliberate, well-reasoned
choice (the adapter's own comment: a real multi-step tool call can
legitimately go 5+ seconds between SSE frames, and a shorter timeout
killed long responses live in an earlier investigation). But unlike
`codex.py`'s and `claude_code.py`'s `_read_loop`/`_drain_stderr` (which
got `readline_with_stall_diagnostic()` in PR #274, specifically to give
"the next recurrence real telemetry instead of the silence every prior
live repro produced"), **opencode's SSE loop has no equivalent
instrumentation at all.** If this connection ever genuinely hangs (no
more events, ever, mid-response), ConvoBox would show total silence
with zero warning -- exactly the failure shape PR #274 was built to
stop happening on the other two backends, but never extended here.

## What transfers

- **A protocol field that fails as a direct kill target can still be
  useful for pattern-matching, if you understand which LAYER of the
  process tree it actually describes** -- the "processId" field failed
  outright (prior round), but the "command" field's text, correctly
  parsed for the wrapper-vs-real-process distinction, is usable.
  (validated-live)
- **An instrumentation pass scoped to "the backends known to have caused
  incidents" can leave an equally-plausible failure surface completely
  dark** -- PR #274 was scoped to codex/claude-code specifically because
  those were the backends with live freeze incidents; opencode's
  architecturally-different SSE path was never audited for the same gap,
  and turns out to have it. Worth treating "we instrumented the known
  problem spots" as distinct from "we audited every blocking wait in the
  codebase" -- the KNOWN-ISSUES.md entry this whole investigation traces
  back to explicitly named the latter as unfinished work. (validated-
  live)

## Not done here

- No PR/code change built for either finding -- the command-matching
  fallback needs real scoping (concurrent-tool-call handling, wrapper-
  parsing robustness) before it's safe to ship; the opencode SSE stall
  diagnostic is a small, well-scoped addition (mirror
  `readline_with_stall_diagnostic()`'s shape for an async-generator loop
  instead of a `StreamReader`) that could reasonably be built next round
  if this loop continues.
- Did not attempt to actually trigger an opencode SSE hang to confirm
  the diagnostic gap matters in practice (vs. being a theoretical-only
  concern) -- this note establishes the gap exists in the code, not that
  it has caused or will cause a real incident.
- Did not retest claude-code beyond what the prior round already
  covered (10/10 force_kill, 6/6 clean VAD cycles) -- no new claude-code-
  specific work this round.
