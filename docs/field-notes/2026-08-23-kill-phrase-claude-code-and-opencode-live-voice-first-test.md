---
title: The first-ever live-voice kill_phrase test against claude-code and opencode (codex was the only backend tested this way before tonight) -- claude-code force-kills a real heavy foreground process 3/3, matching its historical 10/10; opencode's own kill behavior stays confirmed-unpredictable (the remote process survives 2/2), while the SIGINT/session-exit fix holds for both backends
status: validated-live
date: 2026-08-23
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ 8008c9e (post-#338), backends=claude-code (2.1.231) and opencode (1.18.20, localhost:4096), macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), real mic (AIRHUG 28), real Kokoro TTS through real speakers, --mute (TTS output suppressed, mic input real)
evidence:
  - Same live-voice methodology as the 2026-08-18/23 kill_phrase tests (Kokoro TTS synthesized natural-language requests played through real speakers into the real mic, no scripted API injection) -- extended tonight to claude-code and opencode, the two backends every prior live-voice kill_phrase test explicitly left untested (see docs/field-notes/2026-08-18-kill-phrase-live-voice-test-finds-two-real-gaps.md's own "not done here" section, still open as of tonight's start).
  - claude-code, permission_mode=permissive, real `claude` CLI subprocess -- 3 independent trials, same heavy SHA-256-hashing foreground write loop as the 2026-08-23 codex test earlier tonight (docs/field-notes/2026-08-23-macos-heavy-multiline-command-survives-force-kill.md).
  - opencode, real `opencode serve` (localhost:4096, authenticated OpenAI oauth + Inception api) -- 2 independent trials, same test.
  - Live process-tree inspection via `ps -eo pid,ppid,command -ww` before/after each kill attempt.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked to "keep looping" and continue testing overnight, explicitly welcomed spawning opus subagents for a separate marketing research thread run in parallel)
    - Claude Code (Anthropic claude-sonnet-5) -- test design, live capture, writing
  org: https://legionforge.org
  created: 2026-08-23T23:55:00-05:00
  revised: 2026-08-23T23:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# claude-code closes the live-voice testing gap clean; opencode's own gap stays exactly as documented

**Context.** Every live-voice kill_phrase test this project has ever run
-- 2026-08-18's first-ever live-voice test, and tonight's earlier codex
testing (docs/field-notes/2026-08-23-macos-heavy-multiline-command-
survives-force-kill.md) -- used the codex backend exclusively. The
2026-08-18 field note explicitly named this as unfinished: "Did not test
kill_phrase against claude-code or opencode through the real voice
pipeline -- only codex." This note closes that gap for both remaining
backends, using the same real heavy write-loop target (a foreground
SHA-256 hashing loop, no passive `sleep`) that surfaced real bugs for
codex earlier tonight.

## claude-code: 3/3 clean

Each trial: voice request for the heavy write loop, confirmed the real
subprocess was alive and the output file actively growing, then spoke
"Stop, stop, stop." All three times:

- The write-loop child process was gone immediately after the kill
  (confirmed via `ps -p <pid>` returning nothing).
- The whole ConvoBox session exited cleanly (`INFO exiting` logged --
  the 2026-08-19 SIGINT/backgrounded-session fix, PR #310, holding for
  this backend too).

`ClaudeCodeAdapter.force_kill()` has no pgrep-style fallback at all --
it only ever terminates/kills its own `claude` subprocess directly
(`src/convobox/adapters/claude_code.py`'s `_terminate_and_kill_process`).
That plain terminate()/kill() reliably reached the real tool-call child
every time, matching the 2026-08-15 macOS validation's historical 10/10
result -- now confirmed for the first time against a genuinely heavy,
actively-writing target instead of a passive `sleep`/echo.

## opencode: confirmed-unpredictable, exactly as already documented

Each trial: same voice request, confirmed a real write-loop process
spawned as a direct child of the `opencode serve` process itself (not
of ConvoBox), then spoke the kill phrase. Both times:

- The whole ConvoBox session exited cleanly (`INFO exiting` logged --
  same SIGINT fix holding for opencode too, first confirmation for this
  backend).
- **The remote write-loop process survived both times**, needing a
  manual `kill -9` to clean up. One trial's loop had its own 90s natural
  timer (still running, real disk I/O, well past the kill); the other
  produced an unbounded `while :; do ...; done` loop with no timer at
  all -- would have run forever unattended.

`OpenCodeAdapter` has no `force_kill()` override -- it uses `BackendAdapter`'s
default, which only closes ConvoBox's own local HTTP/SSE connection.
The actual tool-call process is owned entirely by the remote `opencode
serve` process, architecturally outside anything ConvoBox's own
`force_kill()` can reach. This matches `docs/KNOWN-ISSUES.md`'s existing
disclosure (confirmed live 2026-08-15: "opencode's kill behavior is
unpredictable and must not be relied upon") exactly -- tonight's result
(2/2 survived) is consistent with, not a new finding beyond, that
existing characterization. No fix attempted or expected here; this is
architecturally out of ConvoBox's control without opencode's own
server-side cooperation.

## Why this matters

**The live-voice testing gap named in the very first live-voice kill_phrase
test (2026-08-18) is now closed for all three backends.** codex, claude-code,
and opencode have each been tested through the real trigger path (mic ->
STT -> orchestrator -> force_kill()) against a genuinely heavy target, not
just a scripted API call or a passive `sleep`. The results diverge exactly
as the architecture predicts: a backend where ConvoBox owns the real OS
process (claude-code, and codex after tonight's earlier fix) can be made
reliable; a backend where the real process lives on a remote server
ConvoBox doesn't own (opencode) fundamentally cannot be, by force_kill()
alone.

## What transfers

- **A backend adapter's architecture (does ConvoBox own the real OS
  process, or only a connection to something that owns it) determines
  the ceiling of what force_kill() can ever guarantee** -- no amount of
  fallback-matching cleverness (codex's pgrep approach) can close a gap
  that's structural to a different backend's own transport shape.
  (validated-live)
- **"3/3" and "2/2" are small samples, consistent with, not independent
  proof beyond, the same finding's larger prior samples** (claude-code's
  historical 10/10, opencode's historical 2-3/5-per-scenario
  unpredictability) -- worth stating plainly rather than overclaiming a
  small night's sample as a fresh, larger-n result.

## Not done here

- Did not attempt to fix or further characterize opencode's remote-kill
  gap -- already disclosed, already understood to be out of ConvoBox's
  direct control; not this session's scope.
- Did not test claude-code/opencode against the SPECIFIC matching-fragility
  gaps found for codex tonight (the ps-octal-escape fix, or the still-open
  wrapper-flag/escaped-backslash gap) -- those are codex-specific
  (`_kill_by_command_text()` doesn't exist for the other two adapters).
- Did not test with real Dylan-audible volume tonight -- Dylan was asleep
  in the house; tests ran at reduced volume (still audible enough to
  trigger VAD reliably, confirmed via successful transcripts each trial)
  and stopped once informed he'd gone to sleep specifically to avoid
  disturbing him.
