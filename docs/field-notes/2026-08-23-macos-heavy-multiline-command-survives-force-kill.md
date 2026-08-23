---
title: A real, heavy, backgrounded-then-foreground multi-line commandExecution reveals two more force_kill() pgrep-fallback matching gaps on macOS -- one fixed live (ps's own octal-escaping of embedded newlines), one still open (wrapper-flag and escaped-quote survival mismatches in more complex generated commands)
status: validated-live
date: 2026-08-23
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ b2eee39 (post-#337, pre-this-note's-own-fix), backend=codex 0.149.0, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), real mic (AIRHUG 28), real Kokoro TTS through real speakers, --mute (TTS output suppressed, mic input real)
evidence:
  - Same live-voice methodology as the 2026-08-18 kill_phrase test (docs/field-notes/2026-08-18-kill-phrase-live-voice-test-finds-two-real-gaps.md): ConvoBox's own Kokoro TTS synthesized natural-language requests, played through real speakers into the real mic, no scripted API injection.
  - A real ConvoBox session (codex backend, sandbox_mode=workspace-write, permission_mode=permissive) asked via voice to run a CPU/disk-heavy SHA-256 hashing loop, mirroring the 2026-08-19 Windows destructive test (docs/field-notes/2026-08-19-kill-phrase-windows-orphaned-descendant-survives-force-kill.md) rather than a passive `sleep`.
  - Live process-tree inspection via `ps -eo pid,ppid,command -ww` before/after each kill attempt, plus a tight 0.3s-interval polling watcher to catch short-lived process appear/disappear events.
  - Temporary DEBUG instrumentation (added and removed within this session) to capture codex's exact raw `command` field text for direct comparison against the real process's `ps` output.
  - Direct code-level verification: reproduced each mismatch against the actual `_strip_shell_quotes`/`_kill_by_command_text` functions with the real captured strings, not just live process behavior.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; away helping family, asked Claude Code to "keep testing" autonomously and separately to investigate a recurring Full Disk Access permission issue on this Mac)
    - Claude Code (Anthropic claude-sonnet-5) -- test design, live capture, root-cause analysis, one fix built and live-verified, writing
  org: https://legionforge.org
  created: 2026-08-23T16:55:00-05:00
  revised: 2026-08-23T16:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A heavy multi-line command exposes two more macOS force_kill() matching gaps -- one fixed, one still open

**Context.** The 2026-08-18 live-voice kill_phrase test (the first ever
run through the real voice pipeline instead of scripted API injection)
found that `_kill_by_command_text()`'s 15-character minimum-length guard
silently excluded short legitimate commands like `sleep 90`. That gap
was fixed the same week (PR #309, merged). This round asked a harder
question, mirroring what the 2026-08-19 Windows test did after its own
passive `sleep` test looked reassuring: what happens against a REAL,
heavy, actively-writing process, not a passive one-liner?

## What happened

**Attempt 1 (backgrounded): the sandbox itself tears the child down,
nothing for force_kill() to reach.** Asked codex (via voice) to run a
SHA-256 hashing loop in the background (`nohup zsh -c '...' &`). A tight
polling watcher (0.3s interval) caught the exact moment: the worker
process appeared at 16:42:38.465 and disappeared at 16:42:39.444 -- both
it and its wrapper died on their own, ~1 second after spawning, well
before kill_phrase was ever spoken. This is macOS's Seatbelt sandbox
(`sandbox_mode=workspace-write`) tearing down detached descendants
automatically when the tool call completes -- the OPPOSITE of the
Windows finding (docs/field-notes/2026-08-19-kill-phrase-windows-
orphaned-descendant-survives-force-kill.md), where a detached process
survives untouched. Under default macOS sandboxing, there may be
nothing for this specific detachment technique to leave alive as a real
kill target at all.

**Attempt 2 (foreground, multi-line `python3 -c`): survived
force_kill() completely.** Asked codex to run the hashing loop directly
(not backgrounded), explicitly for 90 seconds. Codex produced a real,
heavy, multi-line Python script (`python3 -c "import hashlib, time\n
end = ...\n..."`), a direct child of the app-server. kill_phrase matched
correctly, force-killed the backend, and ConvoBox's own session exited
cleanly (`INFO exiting` logged -- the 2026-08-19 SIGINT fix, PR #310,
confirmed working live again here). **But the write-loop process
survived, orphaned to launchd (ppid 1), and ran its own independent 90s
timer to completion -- ~2.9GB written to disk before it exited on its
own**, well after the whole ConvoBox session was gone.

Root cause, confirmed at the code level: BSD `ps`'s COMMAND column
octal-escapes non-printable bytes (its own `strvis`-family encoding). A
real embedded newline (0x0A) inside the multi-line script renders as the
four literal ASCII characters `\012`, not an actual newline byte.
Codex's own reported `command` field (parsed from JSON, where `\n`
decodes to a real newline byte) contains the REAL byte. `_kill_by_
command_text()`'s substring match compares these two representations
directly -- they can never match, structurally, for ANY multi-line
command, completely independent of the 2026-08-18 length-guard fix
(which only helped short SINGLE-line commands like `sleep 90`).

```python
>>> _strip_shell_quotes(reported)   # from JSON, real newline byte
'python3 -c import hashlib, time\nend = time.monotonic() + 90\n...'
>>> _strip_shell_quotes(ps_line)    # from `ps`, octal-escaped
'python3 -c import hashlib, time\\012end = time.monotonic() + 90\\012...'
>>> stripped_ps in stripped_reported
False
```

**Fixed live, same session**: added `_unescape_ps_octal()` (a small
regex reversing `\NNN` octal escapes back to real bytes) and applied it
to the `ps`-sourced side before comparison, in `src/convobox/adapters/
codex.py`. Re-verified directly against the real captured strings from
this exact incident -- the match succeeds after the fix. Live-re-ran
the same voice test end-to-end afterward to confirm the fix holds in a
real session, not just a unit test.

**Attempt 3 (foreground, single-line `zsh -c` with nested quoting):
ALSO survived force_kill(), through a DIFFERENT, still-unfixed
mechanism.** Re-running the identical voice request produced a
differently-shaped command this time (codex doesn't produce the same
shell invocation twice for the same natural-language request) --
a single-line `zsh -c '...'` loop using `printf`/`shasum`/`awk` with
nested quoting. This one has no embedded newlines at all, so the
octal-escape fix above doesn't apply -- and it survived force_kill()
too, orphaned to launchd, for a THIRD distinct reason. Captured via
temporary debug instrumentation (added and removed within this
session) and compared directly against the real `ps` output:

```
reported (codex's own field): /bin/zsh -lc '...printf '"'%s' \""'$counter"...
ps (the real running process): /bin/zsh -c counter=0; ...printf '%s' "$counter"...
```

Two separate mismatches visible in that one comparison: (1) the
reported wrapper is `/bin/zsh -lc` (with the login-shell `-l` flag) but
the real process's own argv, as `ps` shows it, is `/bin/zsh -c` (no
`-l`) -- a substring match anchored at the start of the wrapper text can
never succeed across that asymmetry; (2) an escaped `\"` sequence in
the reported text survives `_strip_shell_quotes`' quote-stripping as a
literal backslash character that has no counterpart in the real
process's argv (the shell already consumed that escaping when parsing
its own `-c` argument, so the running process never has a backslash
there at all).

## Why this matters

**The pattern holds across every round of testing this mechanism has
had**: `_kill_by_command_text()`'s whole strategy (transform two text
representations with a few known fixups, then substring-compare them)
is fundamentally reactive to whatever specific mismatch the last live
test happened to surface. The 2026-08-15 fix handled quote-character
removal. The 2026-08-18 fix handled a length guard that was too broad.
This round's fix handles one specific control-character-escaping
asymmetry. This round ALSO found a second, different asymmetry
(wrapper-flag + backslash-escape survival) that is NOT fixed here --
each fix closes the exact gap that was found, not the general class of
"codex's reported invocation text and the real process's `ps` argv can
diverge in many different, individually-discoverable ways."

**This is exactly the "fragile by construction" characterization the
function's own docstring already carries** -- this round confirms that
description is accurate, and arguably still understated: real,
LLM-generated shell commands for the same plain-English request are NOT
consistent in shape run to run (a background `nohup` loop one time, a
`python3 -c` multi-line script another, a `zsh -c` with nested
`printf`/`awk` quoting a third) -- there is no fixed, enumerable set of
quoting patterns to chase indefinitely.

## What transfers

- **A control-character escaping convention used by ONE tool in a
  comparison pipeline (here, `ps`'s own octal-escaping) can silently
  break a text-matching heuristic that assumes both sides use the same
  representation** -- worth checking explicitly whenever comparing a
  live process's reported command line against text captured from a
  different source (a protocol field, a log line, a config value).
  (validated-live)
- **The same natural-language voice request can produce meaningfully
  different generated shell command SHAPES on different turns** -- not
  just different content, but different wrapping/quoting strategies
  entirely (backgrounded vs. foreground; `python3 -c` vs. `zsh -c`;
  simple vs. nested quoting). A fix validated against one shape is not
  validated against the next one the same request might produce.
  (validated-live)
- **macOS's Seatbelt sandbox appears to auto-terminate backgrounded/
  detached descendants of a sandboxed shell invocation when the tool
  call itself completes** -- a real, live-observed platform behavior
  (not yet independently confirmed against `codex`'s other sandbox
  modes, or documented by Apple in a way this investigation found), and
  the direct opposite of Windows' `Start-Process` detachment surviving.
  Foreground/synchronous heavy commands are NOT protected by this and
  remain a real target `force_kill()` must reach. (validated-live,
  n=1 for the sandbox-teardown-timing specifically)

## Not done here

- The wrapper-flag (`-lc` vs `-c`) and escaped-backslash-survival
  mismatches found in Attempt 3 are NOT fixed -- diagnosed and
  documented, matching this session's own judgment that patching each
  newly-discovered quoting permutation one at a time, blind, in an
  unattended cycle is the wrong shape of fix for what this evidence
  shows is a structurally recurring problem. A real fix likely needs a
  fundamentally different matching strategy (e.g., normalizing BOTH
  sides through a shared canonicalization pass more thorough than
  today's quote-stripping + one-off octal-unescaping, or abandoning
  text-substring matching for something structural), scoped as its own
  deliberate piece of work rather than built reactively here.
- Did not attempt to characterize the macOS Seatbelt sandbox-teardown
  timing further (exact mechanism, whether it's specific to
  `workspace-write` vs. other sandbox modes, whether `CREATE_BREAKAWAY`-
  equivalent options exist on macOS the way Windows Job Objects expose
  one) -- established that it happens and roughly how fast, not why or
  whether it's tunable.
- Did not test against claude-code or opencode -- codex only, matching
  the 2026-08-18 note's own scope and this round's continuation of it.
- Did not re-run Attempt 1's sandbox-teardown finding a second time to
  confirm it's not itself timing-sensitive (n=1) -- Attempts 2 and 3
  (the foreground cases) were each independently reproduced/re-verified
  though (Attempt 2 twice: once finding the bug, once confirming the
  fix; Attempt 3 once, diagnosed but not re-tested after Attempt 2's
  fix, since it's a genuinely separate, unfixed mechanism).
