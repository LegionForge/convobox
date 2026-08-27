---
title: Codex's process-kill fallback silently missed real processes on Linux -- two independent causes found and fixed while building the process-kill test matrix
status: validated-live (both fixes confirmed against real spawned process trees; tests/test_real_process_tree_kill.py)
date: 2026-08-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox test/cross-backend-regression-matrix-2026-08-25 @ b4e3323; codex-cli authenticated app-server; openSUSE Tumbleweed (procps ps), for comparison macOS 26.5 (BSD ps, prior sessions' field notes)
hardware: Clevo P17SM-A barebone (Sager-branded, 2014), Intel Core i7-4810MQ (Haswell) -- CPU-bound test harness only, not acoustically relevant here
evidence:
  - src/convobox/adapters/codex.py (_normalize_whitespace, _kill_by_command_text's env={**os.environ, "COLUMNS": "10000"})
  - tests/test_real_process_tree_kill.py (real forked process trees, no mocked ps)
  - tests/test_codex_adapter.py (test_kill_by_command_text_matches_a_multiline_command_on_linux, test_kill_by_command_text_requests_a_wide_ps_column_regardless_of_environment)
  - PR #341 (test/cross-backend-regression-matrix-2026-08-25)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for real process-tree kill verification across all three backends and all platforms, not just "nothing's running afterward")
    - Claude Code (Anthropic claude-sonnet-5) -- built the test harness, found both bugs live while writing it, implemented and verified the fixes
  org: https://legionforge.org
  created: 2026-08-26T22:15:28-05:00
  revised: 2026-08-26T22:15:28-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Codex's kill-phrase fallback silently missed real processes on Linux -- two independent causes

**Context for outsiders.** ConvoBox is a voice frontend for CLI coding
agents. Its `kill_phrase` ("eject eject eject" in this operator's config)
is a real, OS-level safety mechanism: saying it is supposed to guarantee
that any process the backend spawned actually dies, not just that
ConvoBox itself exits. For the Codex backend, `codex app-server` doesn't
expose a reliable "kill everything you started" RPC, so ConvoBox falls
back to `_kill_by_command_text()` -- it lists all live processes via `ps`,
substring-matches Codex's own reported command text against each
process's command line, and `SIGKILL`s every match plus its descendants.
This function had already been live-validated 20/20 on macOS across
several prior sessions. This was its first real test against a real
Linux process table, done while building an automated regression suite
so this kind of gap gets caught by CI next time instead of by a human
noticing a leftover process.

## Problem

Building `tests/test_real_process_tree_kill.py` meant forking real child
processes with known, deliberately-awkward `ps` command-line shapes (a
multi-statement shell script, a multi-line Python script) and confirming
`_kill_by_command_text()` actually finds and kills them on this Linux
machine -- not assuming the macOS result transfers. It didn't, cleanly:
the same real process, matched correctly under one calling context and
silently missed under another, with no error and no log line -- the
function's contract is "return an empty list" on a no-match, which is
indistinguishable from "there was genuinely nothing to kill."

## Evidence

**Bug 1 -- whitespace rendering divergence.** A multi-line `python3 -c`
script (embedded real newline bytes) is what Codex's own JSON-RPC
reports as the command text (a `\n` escape decodes to a real byte when
the JSON is parsed). macOS's BSD `ps` renders that same embedded newline
back as the literal four-character escape `\012`; this codebase already
had `_unescape_ps_octal()` to reverse exactly that, fixed 2026-08-23
(PR #338) after a 90-second write loop survived `force_kill()`
completely untouched on macOS. Linux's `procps` `ps` does something
different again: it renders the embedded newline as a plain space. Two
different real-world renderings of the same one logical byte, and
`_unescape_ps_octal()` alone only reverses one of them -- on Linux, the
octal-unescape is a no-op, and the reported command (with a real
newline) never equals the `ps`-observed command (with a space) under a
straight string comparison.

**Bug 2 -- `ps` column truncation.** Independent of bug 1: `ps -eo
pid,ppid,command`'s COMMAND column truncates to terminal width whenever
its own stdout isn't a wide/real tty. `subprocess.run(...,
capture_output=True)` always pipes `ps`'s stdout, so this was true on
*every* call this function ever made, on any platform -- it had just
never been noticed on macOS, plausibly because the test invocations
there happened to run under wide-enough terminal contexts. Confirmed
directly on this Linux machine: the identical real spawned process,
same code, same run, matched and was killed correctly when the calling
process had a wide terminal, and was silently missed (function returns
`[]`, no error) when run from a narrower context (a test runner, a
service manager) -- purely from ambient terminal-width auto-detection
this function never asked for or controlled.

Both were caught by comparing the real `ps` table against the real
process, not by any assertion the test itself made first -- the same
pattern as the macOS multi-statement-script bug two sessions earlier
(PR #337-era field notes): the automated survivor check's own blind
spot, found by looking at what was actually left running, not by
trusting a green test result.

## Mechanism

Neither bug is Codex-specific or Linux-specific in cause -- both are
`ps` implementation details that this function's design (a literal
substring match against a captured `ps` line) is inherently exposed to
on any POSIX system, unless explicitly guarded against. What changed
between macOS and Linux wasn't the *class* of risk, only which specific
manifestation actually triggered:

1. `_normalize_whitespace(text: str) -> str: return " ".join(text.split())`,
   applied to *both* sides of the comparison in `_kill_by_command_text`
   (the reported command and the `ps`-observed command line), collapses
   any run of whitespace -- real newlines, spaces, whatever a given
   `ps` implementation renders a control character as -- to single
   spaces before comparing. This subsumes `_unescape_ps_octal()` rather
   than replacing it: the octal-unescape still runs first (so a literal
   `\012` becomes a real newline), then normalization collapses
   whatever whitespace remains, on either platform's rendering.
2. `env={**os.environ, "COLUMNS": "10000"}` passed to the internal
   `ps` call pins a wide column width regardless of the calling
   process's actual terminal state, so the COMMAND column is never
   truncated by ambient context this function has no business
   depending on.

## What transfers

- **Validated-live**: both fixes, against real spawned process trees on
  this Linux machine (`tests/test_real_process_tree_kill.py`), plus
  regression tests with fixed mocked `ps` output covering each rendering
  (`tests/test_codex_adapter.py`).
- **Diagnosed, not yet re-confirmed on macOS since these fixes landed**:
  bug 2 (COLUMNS truncation) is architecturally platform-independent --
  worth a quick re-run of the macOS suite to confirm nothing regressed
  there, though nothing about `env=` construction is platform-specific
  enough to expect a difference.
- **The general lesson, not just this one function**: any code that
  parses `ps`, `ps aux`, or similar output for matching purposes should
  assume neither a fixed column width nor a single canonical rendering
  of embedded control characters -- both are real, silent, platform-
  and-context-dependent failure modes, not hypothetical edge cases.

## Not done here

No fuzzing or exhaustive enumeration of other possible `ps` rendering
quirks (e.g. other control characters, other `ps` implementations like
busybox's) -- these two were found because this specific test harness
happened to construct exactly the shapes that triggered them, not
because of a systematic survey of `ps` behavior across implementations.
