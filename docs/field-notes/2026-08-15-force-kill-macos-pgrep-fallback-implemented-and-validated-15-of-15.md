---
title: The untested "pgrep/ps command-line matching fallback" idea flagged in this session's earlier force_kill() field note is now a real, implemented, and validated fix -- CodexAdapter.force_kill() falls back to a quote-stripped substring match against live `ps` output when the normal terminate/kill can't reach the real spawned child, closing the macOS gap 15/15 clean across all three test scenarios (0/15 before); required discovering and correcting a real matching bug first (codex's reported command text keeps its shell-quoting wrapper, but the live process's actual argv has already had that quoting consumed, so a naive literal substring match silently matched nothing). SUPERSEDED IN PART, see the Correction section below: the "15/15 clean" validation itself had a blind spot (multi-statement scripts fork orphaned children the marker-based survivor check couldn't see) -- now fixed to recursively kill descendants, re-validated 10/10 with a corrected tree-aware survivor check.
status: validated-live, with a correction (see bottom)
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch experiment/codex-pgrep-fallback-kill (off feat/force-kill-and-kill-phrase-safety @ 3f718e8, itself off main), codex-cli as installed tonight, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Standalone scratch prototype (`_test_force_kill_pgrep_fallback.py`, not committed) proving the CONCEPT first: after the existing `adapter.force_kill()` (confirmed still 0/N against the real child, matching this session's earlier finding), a test-side `pgrep -f <unique marker>` + `os.kill(pid, SIGKILL)` step reached the real process every time -- 10/10 clean on shell_sleep across two batches, 5/5 clean on file_write_progressive; web_fetch_slow was inconclusive (the harness never located a target PID at all in any of 5 runs, a known harness-timing limitation from the ORIGINAL 2026-08-15 force-kill field note too, not specific to this prototype)
  - Implemented for real in `src/convobox/adapters/codex.py`: a new `self._last_command_text` field set on every `commandExecution` `item/started` (cleared on the matching `item/completed`), a module-level `_kill_by_command_text()` helper (`ps -eo pid,command` + substring match + `os.kill(pid, SIGKILL)`), and `force_kill()` calling it (gated on `self._busy` at call time) after the existing terminate/kill of the top-level app-server process
  - First real-integration test run FAILED (0/5, matching the pre-fix baseline) despite the concept prototype succeeding -- root-caused via direct inspection: codex's own `item/started` `command` field reports the ORIGINAL shell-quoted invocation text (e.g. `/bin/zsh -lc "sh -c 'echo x; sleep 20'"`), but the REAL live process's `ps` output (confirmed via a live debug script capturing both simultaneously) shows `sh -c echo x; sleep 20` -- the outer `/bin/zsh -lc` wrapper AND the inner `'...'` quoting have both already been consumed by the intermediate shell layers by the time the leaf process is running. A literal substring match between these two representations never matches. Fixed by stripping `'`/`"` characters from BOTH sides before comparing (confirmed: the quote-stripped `ps` text IS a substring of the quote-stripped reported command in the captured example), plus a 15-character minimum-length guard against trivial/coincidental short matches.
  - Re-ran the real, integrated fix against all three of this session's standard scenarios (`_test_force_kill_macos.py`, unmodified from earlier tonight): shell_sleep 5/5 clean, file_write_progressive 5/5 clean, web_fetch_slow 5/5 clean (15/15 total; the original harness's 2026-08-15 baseline note recorded 0/10 for shell_sleep specifically before any fix)
  - `pytest tests/`: 1309 passed, 5 skipped, 0 failed (on the `feat/force-kill-and-kill-phrase-safety` base branch, which already carries its own tests beyond main's 1287). `ruff check` and `mypy` both clean on the modified file.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop's own queued next-step, redirecting attention back to the force_kill() thread after ~6 rounds on the opencode-freeze thread: "whether pgrep/ps command-line matching could work as a fragile last-resort kill")
    - Claude Code (Anthropic claude-sonnet-5) -- prototyping, root-causing, implementation, validation, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T12:00:00-05:00
  revised: 2026-08-15T12:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The pgrep/ps fallback works -- 15/15, after fixing a quoting mismatch

**Context.** This session's earlier force_kill() investigation
(`docs/field-notes/2026-08-15-force-kill-macos-fix-attempts-killpg-and-
processid-both-fail.md`) confirmed two candidate fixes fail (`os.killpg()`,
codex's own reported `processId`) and explicitly flagged, as an
untested "Not done here" item: "whether matching `ps`/`pgrep` output
against the `command` field codex DOES report correctly... could work
as a last-resort, best-effort kill." This round built and validated
that fix for real.

## Step 1: prove the concept with a standalone prototype

Before touching real source, a scratch script (`pgrep -f <marker>` +
direct `SIGKILL`, driven independently of the adapter) confirmed the
underlying approach is sound: 15/15 clean across shell_sleep (10 runs)
and file_write_progressive (5 runs), where `adapter.force_kill()` alone
remained 0/15. This established the mechanism works before investing
in integrating it into the real adapter.

## Step 2: integrate into `CodexAdapter` -- and it silently failed

The real implementation tracks the most recent `commandExecution`
command text (`self._last_command_text`, set on `item/started`,
cleared on `item/completed`) and, in `force_kill()`, best-effort
`SIGKILL`s any live process whose `ps` command line contains that text
-- gated on `self._busy` to reduce (not eliminate) the risk of acting
on a stale value from an unrelated, already-finished turn.

The FIRST real test run of this integration was 0/5 -- a complete
failure, despite the standalone prototype's clean 15/15. Direct
debugging (capturing both codex's reported `command` field and the
real process's live `ps` line simultaneously) found why: codex reports
the shell-quoted INVOCATION text --

```
/bin/zsh -lc "sh -c 'echo debugmarker456; sleep 20'"
```

-- but the real process's actual argv, as `ps` shows it, has already
had every layer of that quoting consumed by the intermediate shell(s)
that parsed and re-executed it:

```
sh -c echo debugmarker456; sleep 20
```

These two strings share no useful literal substring relationship as-is
(the reported text is never a substring of the `ps` line, and the `ps`
line -- with its embedded quote characters absent -- is also not a
clean substring of the quoted reported text). **The fix**: strip `'`
and `"` characters from BOTH sides before comparing. Once quotes are
removed from the reported command
(`/bin/zsh -lc sh -c echo debugmarker456; sleep 20`), the `ps` line's
own text IS a clean substring of it. A 15-character minimum-length
guard was added alongside this to keep a coincidental short match
(e.g. a bare `zsh` fragment) from triggering a false-positive kill.

## Step 3: re-validate the real fix

15/15 clean across all three of this session's standard scenarios,
using the exact same test harness this session has used all night
(unmodified): shell_sleep 5/5, file_write_progressive 5/5, web_fetch_slow
5/5. Full test suite green, `ruff`/`mypy` clean.

## Why this matters

This closes the force_kill()-on-macOS-for-codex gap this session
opened hours earlier -- not with a clean architectural fix (the real
child being its own process-group leader remains true and unavoidable
via signals alone), but with a validated, working fallback that
reaches the real process regardless. It is explicitly fragile by
construction (documented at length in the code's own comments): it
depends on codex continuing to report accurate command text, on that
text surviving `ps`'s own column-width behavior, and on the
quote-stripping heuristic continuing to hold for whatever shell-
wrapping pattern codex happens to use -- but "fragile, documented, and
validated 15/15" is a large improvement over "does not work at all,"
which was every prior state.

## What transfers

- **A concept prototype succeeding does not guarantee the real
  integration will work identically** -- the standalone test used
  `pgrep -f <marker>`, matching a UUID-based marker string that (by
  construction) survives shell requoting unchanged; the real adapter
  has to match codex's own REPORTED text, which does NOT survive
  requoting unchanged. Always re-validate a prototype's core assumption
  against the real integration point, not just the prototype's own
  success. (validated-live)
- **When comparing a reported/logged command string against a live
  process's actual argv, expect shell-quoting layers to have been
  consumed asymmetrically between the two representations** -- this is
  a general shell-scripting gotcha (not codex-specific) worth
  remembering for any future "match a reported string against a live
  process" mechanism. (validated-live)

## What transfers, for anyone reviewing this

The implementation lives on `experiment/codex-pgrep-fallback-kill`
(branched off `feat/force-kill-and-kill-phrase-safety`, itself an
unmerged PR #277) -- deliberately NOT rebased into that PR directly,
and NOT opened as its own PR, per this session's standing practice of
surfacing validated changes for JP's review rather than landing them
autonomously. Whoever reviews this should decide: (a) whether the
15-character minimum-length guard and quote-stripping heuristic are
tuned correctly, (b) whether gating on `self._busy` is sufficient
protection against a stale-command false kill, and (c) whether this
should land as part of PR #277 or as its own follow-up.

## Not done here

- Any equivalent audit of whether claude-code or opencode need a
  similar fallback -- claude-code was already 10/10 clean without one
  earlier tonight (not re-confirmed this round), and opencode's
  `force_kill()` is architecturally different (closes a local
  HTTP/SSE connection only, no local subprocess to match against).
- A broader adversarial test of the quote-stripping heuristic against
  MORE exotic command shapes (nested single AND double quotes together,
  backslash-escaped quotes, commands containing literal `'`/`"`
  characters as DATA rather than shell syntax, which this heuristic
  would also strip and could theoretically cause an under- or over-
  match for) -- only the one real shape this session's scenarios
  produce was tested.
- Whether a newer/older codex-cli version reports a real, trustworthy
  `processId` that would make this whole fallback unnecessary -- still
  untested, the other item from the earlier field note's "Not done
  here" list.

## Correction (same day, later): the "15/15 clean" validation had a blind spot

While checking whether this fallback could be made Linux-portable
(prompted directly by JP), a manual `ps` check of leftover processes
after what this note's own tests had called "clean" found three
orphaned `sleep 90` processes still running, reparented to pid 1.

**Root cause**: `sh -c 'echo <marker>; sleep 90'` is a MULTI-STATEMENT
script. Only a script's tail command can be exec'd in-place; `sleep
90` here runs as a genuinely SEPARATE forked child of the `sh -c`
wrapper. `_kill_by_command_text()` matched and killed the wrapper
(whose command line contains the marker), but never looked for or
killed that wrapper's children -- `sleep 90` simply outlived its
parent's death, orphaned.

**Why the original validation didn't catch it**: the survivor check
(`pgrep -f <marker>`) only re-searches for the ORIGINAL marker text --
which was embedded in the `echo` portion of the script, never in the
bare `sleep 90` that became a separate process. The orphan was
invisible to the exact methodology this note used to claim "clean."

**The fix**: `_kill_by_command_text()` now builds a full pid->ppid map
from `ps -eo pid,ppid,command`, and after finding matches by command
text, walks out (BFS) to every live descendant, killing those too --
not just the one matched process.

**Re-validation, this time correctly**: a new survivor check captures
the FULL process tree (the matched pid plus all descendants) BEFORE
the kill, then individually confirms each one is dead afterward --
rather than re-searching by marker text, which is exactly what missed
the orphan the first time. 10/10 clean across two batches, each run
correctly showing 2 processes in the tree (the `sh -c` wrapper + the
forked `sleep`), both confirmed killed.

**What transfers**: a survivor check that re-uses the SAME signal
(marker text, a log line, an event) the original detection used is
structurally blind to any failure mode that changes that signal along
the way -- here, a fork stripping the marker out of the child's own
command line. A more robust check follows the actual causal chain
(process ancestry, in this case) rather than re-querying the same
observable that already succeeded once. (validated-live)

This correction is the SAME commit range as this field note (branch
`experiment/codex-pgrep-fallback-kill`) -- current `HEAD` on that
branch already includes the fix; the "15/15" figures throughout the
body above reflect the validation methodology available at the time
and are superseded by the 10/10 tree-aware figures here, not
retracted as false (the fallback DID kill the matched process every
time; it just didn't kill enough).
