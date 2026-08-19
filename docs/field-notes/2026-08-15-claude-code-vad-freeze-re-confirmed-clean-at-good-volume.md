---
title: claude-code re-tested against the unmodified 6-cycle stress harness with system output volume confirmed good (65%, was 25%) -- stays clean, matching codex's own re-confirmed-clean results; 34/36 utterances processed, zero gap-watcher hits, no genuine mic-layer freeze; completes this session's volume-confound re-verification across all three backends (codex: clean, opencode: real bug found+fixed, claude-code: clean)
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch docs/vad-freeze-volume-confound-2026-08-15 (off main), backend=claude-code, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), output volume confirmed 65% throughout, --mute
evidence:
  - Confirmed output volume via `osascript -e "get volume settings"` (25% at round start, matching the pattern seen every time volume was checked tonight before being deliberately raised), raised to 65%, re-confirmed after
  - Ran `_test_vad_freeze_macos.py 6` (this session's standard unmodified stress harness: pause -> 3x rapid safeword burst -> resume -> followup, x6 cycles) against a live claude-code session with `trace_silero_calls: true` and a gap-watcher armed (>=3s log-silence threshold)
  - Result: 34 `Processing audio` lines (36 expected; the 2-utterance shortfall matches a normal, expected outcome for this harness -- one cycle logged `dropped (no input, STT heard nothing recognizable)`, not a freeze), zero gap-watcher hits across the entire run, VAD Silero trace continuous throughout
  - The longest readline-adjacent stall was 75.5s on `_drain_stderr` -- this branch does not include the separate busy-state diagnostic fix from earlier tonight (a different branch, `fix/readline-stall-diagnostic-busy-state`), so `busy=` isn't visible in this run's log, but the absence of ANY gap-watcher hit (which tracks the full log file, not just one channel) is the decisive signal that nothing in the actual mic-processing pipeline stalled
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop's own queued low-effort next-step after both major threads closed out: "claude-code has not been re-tested with volume confirmed good since the volume-confound discovery")
    - Claude Code (Anthropic claude-sonnet-5) -- capture, monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T12:34:00-05:00
  revised: 2026-08-15T12:34:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# claude-code re-confirmed clean at good volume

**Context.** This session's rounds 13-14 discovered that macOS system
output volume was sitting at 25% -- too quiet for this session's
synthesized test audio to reliably cross the VAD's speech threshold --
and that re-running codex's original "severe" freeze scenarios with
volume confirmed good produced zero freezes across two separate
rounds. codex was reconfirmed clean; claude-code, which had only ever
been tested with ONE 6-cycle run (predating the volume-confound
discovery), was never re-verified either way. This was flagged as a
small, low-effort remaining open item once both of tonight's major
threads (force_kill(), the opencode freeze) closed out with validated
fixes.

## Result: clean, matching codex

6 full stress cycles, 34/36 utterances processed (the 2-utterance gap
matches a normal STT-heard-nothing drop, not a freeze -- explicitly
logged as such), zero gap-watcher hits across the entire run. This
brings claude-code in line with codex's own re-confirmed-clean status:
**of this session's three backends, codex and claude-code are both
now confirmed clean under proper test conditions, and opencode is the
one genuine bug this session found, root-caused, and mitigated.**

## Why this matters

This closes the last small open item from this session's own
volume-confound thread. It does not add a new finding on its own, but
it completes the picture: this session's original "VAD/mic-pipeline
freeze reproduces on macOS for codex... did not reproduce in one
6-cycle claude-code run" framing (from the very first round's brief)
is now fully resolved -- neither backend has a genuine freeze under
correct test conditions; the freeze this session eventually found and
fixed was specific to opencode.

## What transfers

- Nothing new methodologically -- this is a confirmatory re-run using
  already-established practice (volume-check-first, the standard
  stress harness, the gap-watcher). Included here for completeness
  rather than as a new lesson: a future reader of tonight's field
  notes should not need to wonder whether claude-code was ever properly
  re-checked. (validated-live)

## Not done here

- Nothing further planned on this specific sub-thread; both of
  tonight's substantive open threads are closed out with validated
  fixes (docs/field-notes/2026-08-15-force-kill-macos-pgrep-fallback-
  implemented-and-validated-15-of-15.md and docs/field-notes/2026-08-15-
  opencode-freeze-fix-validated-retry-cancel-turns-indefinite-hang-into-
  3s-bound.md), and this note closes the one remaining small item. Any
  further work is JP's call, pending review of the several pushed
  branches from tonight.
