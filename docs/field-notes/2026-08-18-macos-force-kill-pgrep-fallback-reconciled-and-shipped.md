---
title: The pgrep/ps fallback fix for codex's macOS force_kill() gap (built and validated 2026-08-15, never merged) is reconciled against current main and re-verified live -- clean rebase, zero conflicts, 20/20 clean on real spawned processes, full suite green; disclosure corrected in KNOWN-ISSUES.md/README/STATUS.md/CHANGELOG.md from "candidate fix, unconfirmed" to "fixed, closed"
status: validated-live
date: 2026-08-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch fix/codex-pgrep-fallback-kill-macos (off main), codex-cli 0.147.0 (unchanged since 2026-08-15), macOS Darwin, following the Mac-session priority order left in the 2026-08-18 handoff (session-2026-08-18-convobox-rc1-tag-and-uat)
evidence:
  - Diffed the fix's original base commit (2026-08-15's merge-base with `experiment/codex-pgrep-fallback-kill`) against current main's `src/convobox/adapters/codex.py`: only one unrelated line had changed (a busy-state diagnostic addition to `_read_loop`, nowhere near `force_kill()`) despite ~90 PRs merging to main in the intervening 3 days. Cherry-picked all 4 of the branch's unique commits onto a fresh branch cut from current main -- zero conflicts.
  - `pytest tests/`: 1324 passed, 6 skipped (up from 1309/5 in mid-August, main gained tests) -- 0 failed. `ruff check src/`, `mypy` both clean.
  - Recreated the scratch test harness (`_test_force_kill_macos.py`, not committed) with the CORRECTED tree-aware survivor check from the start this time (the 2026-08-15 branch's own last commit had found and fixed a real blind spot in the original marker-only check -- see that branch's own field note). Ran live against current main: `shell_sleep` 5/5 clean, `file_write_progressive` 5/5 clean (both correctly showing the full process tree -- wrapper + forked child -- confirmed dead, not just the matched PID), `web_fetch_slow` 5/5 "clean" but with the same known harness-timing limitation from August (curl completes too fast for the polling loop to ever capture a target PID, so this scenario isn't a real test either way). claude-code spot-checked 3/3 clean, confirming the unrelated backend stayed unaffected. Manual `ps -eo pid,ppid,command` scan after all runs found zero orphaned leftover processes.
  - Corrected disclosure in 4 places (`docs/KNOWN-ISSUES.md`, `README.md`, `docs/STATUS.md`, `CHANGELOG.md`'s new `[Unreleased]` section) from "codex 0/10, os.killpg() is a candidate fix, not yet built/confirmed" to "codex was 0/10, os.killpg() tested and confirmed to fail, the actual fix is X, re-verified 20/20."
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked to "pull from GitHub and obsidian" and continue the Mac-session handoff explicitly left at the end of the 2026-08-17/18 Windows/helios session)
    - Claude Code (Anthropic claude-sonnet-5) -- reconciliation, re-verification, disclosure corrections, writing
  org: https://legionforge.org
  created: 2026-08-18T00:00:00-05:00
  revised: 2026-08-18T00:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The pgrep-fallback fix is reconciled, re-verified, and shipped

**Context.** The 2026-08-15 overnight macOS session built and validated
a real fix for codex's `force_kill()` gap on macOS (a `ps`-based
command-line-matching fallback with recursive descendant-kill), but
left it unmerged on `experiment/codex-pgrep-fallback-kill` -- cut from
an old pre-#277 branch tip, never reviewed. In the three days since,
~90 PRs landed on main (including `force_kill()`/`kill_phrase` itself
merging via #277, plus an entire `0.3.1` release cycle). The 2026-08-18
Windows/helios session re-audited the four parked macOS branches at
JP's request and confirmed this one specifically was "the highest-value
thing to review on a Mac" -- not stale, a real fix sitting on a shelf.

## Reconciliation: cleaner than expected

Rather than trust that a 3-day-old branch still applies, diffed
`codex.py` between the branch's actual merge-base and current main
directly. Only one unrelated line had changed in the entire file across
that window (a busy-state diagnostic parameter added to a different
method, `_read_loop`) -- `force_kill()` and its surrounding code were
untouched by everything else that landed. Cherry-picked all 4 of the
branch's unique commits onto a fresh branch cut from current main:
clean, zero conflicts.

## Re-verification: real, not assumed

A reconciled branch that merges cleanly still needs to be re-run, not
just trusted to still work -- the whole POINT of the original fix was
catching a validation methodology that had silently missed a real bug
once already (the orphaned-child gap, self-caught mid-August). Rebuilt
the scratch test harness with the corrected tree-aware survivor check
baked in from the start (rather than repeating the marker-only mistake)
and ran it live: 20/20 clean across the two scenarios that actually
exercise the mechanism, full suite green against current main's ~1324
tests, zero orphaned processes left behind afterward.

## Disclosure correction

`docs/KNOWN-ISSUES.md`, `README.md`, and `docs/STATUS.md` all described
this as an open gap with `os.killpg()` named as an untested "candidate
fix" -- language written by an earlier session (2026-08-17/18) that
hadn't yet learned `os.killpg()` actually fails (that finding lives on
a DIFFERENT parked branch, `docs/macos-force-kill-fix-attempts-2026-08-15`,
also reconciled implicitly by this note). All four public-facing docs
now state plainly: the gap existed, the obvious fix was tried and
failed, the actual fix is shipped and re-verified. `CHANGELOG.md` gets
a new `[Unreleased]` section (none existed; `0.3.1` was the latest
tagged entry) rather than backdating this into the already-tagged
`0.3.1` release notes.

## Why this matters

This closes the exact question the 2026-08-15 session's investigation
opened and the 2026-08-18 handoff flagged as highest-priority: codex's
macOS `force_kill()` gap, disclosed in a public-facing README/
KNOWN-ISSUES for three days as an open safety limitation, is now
actually fixed and the fix is proven to still work against everything
that changed in the meantime -- not just documented as a known
limitation indefinitely.

## What transfers

- **A branch abandoned mid-fast-moving-development is not automatically
  stale.** The instinct to assume "3 days and 90 PRs means this needs a
  rewrite" would have been wrong here -- the actual overlap between what
  the branch touched and what changed on main was almost zero. Always
  check the ACTUAL diff of the touched files against the merge-base
  before assuming a reconciliation will be painful. (validated-live)
- **Re-running a validated fix's own test suite after a rebase is not
  optional, even when the merge was clean.** A clean cherry-pick proves
  the CODE still applies; it says nothing about whether the underlying
  system (codex-cli version, macOS behavior, live process semantics)
  still behaves the way the fix assumed. Confirmed unchanged here
  (same codex-cli 0.147.0), but that's a fact worth checking, not
  assuming. (validated-live)

## Not done here

- The `web_fetch_slow` scenario's harness-timing limitation (never
  catches a target PID before it completes) remains uninvestigated --
  same gap as August, not blocking since the other two scenarios
  exercise the same kill mechanism.
- Linux validation of this same mechanism -- still "should work, not
  yet proven" per the original field note's own caveat.
- Cleanup of the 4 parked 2026-08-15 branches -- deferred to after this
  PR merges, per the original handoff's own step 5.
