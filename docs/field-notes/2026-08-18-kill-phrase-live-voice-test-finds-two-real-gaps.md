---
title: The first-ever live, real-voice test of kill_phrase (not scripted API injection) finds TWO real, previously-invisible gaps -- the just-merged pgrep fallback (#306) has a 15-char minimum-length guard that silently excludes legitimate short commands like "sleep 90" from protection, and kill_phrase's own "ends this session" claim doesn't hold: the self-signal SIGINT never reaches asyncio.run()'s top-level handler, leaving the process alive and still listening
status: validated-live
date: 2026-08-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ c8db010 (post-#306), backend=codex, macOS Darwin, real mic (AIRHUG 28), real Kokoro TTS synthesis through real speakers, --mute (TTS output suppressed to avoid self-barge-in, mic input real)
evidence:
  - This is the FIRST time kill_phrase has ever been tested through the actual voice pipeline (mic -> VAD -> STT -> Orchestrator -> force_kill()) rather than scripted `adapter.force_kill()` calls or `send_text()` API injection -- every prior validation (this session's own #306 work, the 2026-08-15 overnight session, the 2026-08-17/18 Windows UAT) tested the mechanism, never the trigger path end to end.
  - Config: `safeword.kill_phrase: "stop stop stop"` (also in `hard_stop_phrases`), codex backend, real ConvoBox mic session (`--mute` to suppress TTS output only, mic input unaffected -- avoids the self-barge-in confound found 2026-08-15).
  - Synthesized a natural-language request via ConvoBox's own Kokoro TTS engine ("Please run a shell command that echoes the words zebra tango foxtrot kilo and then sleeps for ninety seconds...") played through real speakers into the real mic. codex issued two separate `commandExecution` tool calls in response (an `echo` and a standalone `sleep 90` -- NOT wrapped together in one `sh -c '...'` compound the way every prior scripted test's prompt explicitly dictated). Confirmed live: `ps -eo pid,ppid,command` showed `50564 49230 sleep 90` -- a bare 8-character command line, direct child of the codex app-server (49230), no shell wrapper.
  - Played the kill phrase ("Stop, stop, stop.") via the same real TTS-to-speaker-to-mic path. STT correctly transcribed it (`transcript='Stop, stop, stop.' ... [HARD STOP]`) and the orchestrator correctly matched it (`kill phrase matched 'stop stop stop' -- force-killing backend`).
  - **Gap 1**: `ps -p 50564` showed the process still alive immediately after the match. Captured the full process tree before the kill (codex app-server 49230 + 4 children including 50564). All OTHER children of 49230 (the node_repl, mcp-remote, codex-code-mode-host helper processes) died correctly. Only `sleep 90` survived -- and it survived specifically until its OWN natural 90-second timer expired (confirmed via wall-clock: started ~22:39:14, still alive at 22:41:07, which is past its natural ~22:40:44 expiry only by coincidence of when it was checked -- the decisive evidence is it was ALIVE well past the kill at 22:39:54 and only gone by the time its own timer would have run out). `len("sleep 90") == 8`, under `_kill_by_command_text()`'s 15-character minimum-length guard (added 2026-08-15 specifically to avoid coincidental short-string false positives) -- the guard silently excluded a completely legitimate, real target from ever being matched.
  - **Gap 2**: `scripts/run_convobox.py`'s own process (pid 46179) was still alive and actively processing mic audio (confirmed via `sample`: CoreAudio IOThread callback actively invoking the Python audio callback, ~3.6% CPU, NOT the 0%-CPU-stuck-in-kevent signature of a genuine freeze) 1.5+ minutes after the kill phrase matched and logged. `grep -i "exiting"` on the full session log found nothing -- the `except KeyboardInterrupt: log.info("exiting")` handler in `main()` (scripts/run_convobox.py, ~line 3248) was never reached. `_self_signal_interrupt()`'s `os.kill(os.getpid(), signal.SIGINT)` (the POSIX branch) was called (confirmed via code read: `Orchestrator.handle_transcript()` calls `self._on_kill_phrase()` synchronously right after `await self.force_kill()` returns, and the mic loop's continued operation proves `handle_transcript()` did return, meaning this call site was reached) but the resulting SIGINT never propagated to `asyncio.run()`'s top-level exception handler. Process was killed manually (`kill -9`) to end the test.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked "more tests? anything left?" prompting this round to close the single most safety-relevant untested gap: kill_phrase through the real voice pipeline)
    - Claude Code (Anthropic claude-sonnet-5) -- test design, live capture, root-cause analysis, writing
  org: https://legionforge.org
  created: 2026-08-18T00:00:00-05:00
  revised: 2026-08-18T00:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The first real-voice kill_phrase test finds two real gaps, both previously invisible to scripted testing

**Context.** Every kill_phrase/force_kill() validation this project has
ever done -- the 2026-08-15 overnight investigation, this session's own
#306 fix, the 2026-08-17/18 Windows UAT round -- tested the underlying
kill MECHANISM directly (calling `adapter.force_kill()` from a script,
or injecting text via `send_text()`), never the actual trigger path a
real user experiences: speaking the phrase, having STT transcribe it,
having the safeword matcher recognize it, and having the orchestrator
route to `force_kill()` from there. This round closed that gap for the
first time, using ConvoBox's own real TTS engine (Kokoro) to synthesize
speech through real speakers into the real mic -- the same acoustically
real path this whole session's earlier VAD-freeze investigation used,
applied here to the safety-critical kill path specifically.

## What happened

A natural-language request ("please run a shell command that echoes X
and sleeps for 90 seconds") produced two separate `commandExecution`
tool calls from codex, not the single `sh -c 'echo X; sleep 90'`
compound every prior scripted test explicitly dictated in its prompt
text. The `sleep 90` tool call ran as a bare, unwrapped 8-character
command (`sleep 90`), a direct child of the codex app-server process.

The kill phrase, spoken and correctly recognized through the real
pipeline, triggered `force_kill()` as expected -- but `sleep 90`
survived. Root cause: `_kill_by_command_text()`'s 15-character minimum-
length guard (added 2026-08-15 specifically to prevent a coincidental
short substring like "zsh" from matching an unrelated process) treats
`"sleep 90"` identically to that kind of coincidental noise, because it
is also short. The guard cannot distinguish "too short to trust" from
"a real, legitimate, short command" -- and a real voice interaction,
phrased naturally rather than dictated as an explicit shell one-liner,
produces exactly this shape far more easily than any of this session's
own scripted tests ever did.

Separately, and likely more significant: the whole ConvoBox process
never actually exited. `kill_phrase`'s own log line
(`"kill phrase %r configured -- force-kills the backend process and
ends this session"`) states plainly that the session ends. It didn't.
The mic loop kept running, kept processing real audio, for as long as
the process was left alive (killed manually after ~90+ seconds to end
the test). The self-signal-interrupt mechanism that's supposed to
deliver this (`_self_signal_interrupt()`, a real `os.kill(pid,
SIGINT)` to itself) appears to have been called -- the code path is
unconditional and synchronous right after `force_kill()` returns, and
the mic loop's own continued operation proves `handle_transcript()`
itself returned normally -- but the resulting signal never reached
`asyncio.run()`'s top-level `except KeyboardInterrupt` handler. No
"exiting" log line, no crash, no traceback -- the interrupt simply
never arrived where it needed to.

## Why this matters

**kill_phrase is this project's explicitly-designed "ejector seat" --
the emergency mechanism for when the polite hard-stop path is itself
wedged.** An ejector seat that only reaches part of its target (kills
the wrapper but not a short direct-child command) and doesn't actually
end the session it claims to end is a real, safety-relevant gap, not a
cosmetic one -- and one that was completely invisible to this session's
entire scripted validation history, because every scripted test always
produced compound, longer commands and always measured "did the
process die," not "did the whole session actually end."

This is also a direct, concrete demonstration of a broader lesson this
whole night's work has repeatedly surfaced: **the mechanism working in
isolation is not the same claim as the mechanism working end-to-end
through the real trigger path a user actually exercises.** Every other
finding tonight (#306's own orphaned-child bug, #302's CSRF-masked
confound) was caught the same way -- by testing one level closer to
reality than the existing test suite reaches.

## What transfers

- **A length-based (or any purely syntactic) heuristic guard against
  false positives should be checked against the shortest REALISTIC
  legitimate input, not just against what a test's own prompts happen
  to produce.** `sleep 90`, `ls -la`, `pwd`, `whoami` are all common,
  entirely legitimate real commands under 15 characters -- the guard as
  shipped silently fails to protect against exactly this class of
  command, and no scripted test in this project's history would ever
  have revealed that, because every scripted test dictated longer
  compound commands explicitly. (validated-live)
- **A feature's own log message asserting a guarantee ("ends this
  session") is not evidence that the guarantee holds** -- it's a claim
  that itself needs the same live verification as any other behavior.
  This log line was written when the feature was built and never
  independently re-checked against a real end-to-end run until now.
  (validated-live)
- **Natural, conversational phrasing produces meaningfully different
  tool-call shapes than an explicit, dictated shell command.** Every
  prior test in this project's history (tonight's and the 2026-08-15
  session's alike) told the backend exactly what shell syntax to run;
  a real user asking naturally lets the backend choose its own command
  structure, which can differ in ways that matter (single compound vs.
  multiple separate commandExecutions) for anything that depends on
  the resulting process shape. (validated-live)

## Not done here

- No fix attempted for either gap -- this round is capture-and-diagnose,
  matching this session's established practice, but both findings are
  concrete enough that fixes are tractable next steps:
  - Gap 1: the length guard needs a smarter heuristic than a blanket
    minimum -- e.g. excluding only specific known-generic binary names
    (`sh`, `zsh`, `bash` alone) rather than any short string, or
    requiring the match to be the FULL `ps` command line (not just
    "long enough"), which would correctly accept `sleep 90` while still
    rejecting a bare `zsh` fragment.
  - Gap 2: needs direct investigation into why `os.kill(self, SIGINT)`
    isn't reaching `asyncio.run()`'s handler in this runtime context --
    candidates not yet checked: whether being launched via `nohup`
    changes signal delivery/disposition in a way an interactive
    terminal session wouldn't hit, whether some other code path resets
    SIGINT's handler between the interrupt firing and the main task's
    await point, or whether the signal fires correctly but something in
    the shutdown sequence itself hangs before reaching the log line.
- Did not re-test with a DIFFERENT phrasing that would produce a single
  compound `sh -c '...'` command (which would likely have hidden Gap 1
  again, matching every prior scripted test) -- the natural-language
  phrasing that surfaced it was not deliberately engineered to do so,
  it's simply what came out of describing the task conversationally.
- Did not test kill_phrase against claude-code or opencode through the
  real voice pipeline -- only codex, matching this session's overall
  focus.
