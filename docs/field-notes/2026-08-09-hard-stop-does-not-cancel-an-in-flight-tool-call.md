---
title: Hard-stop (safeword/pause) reliably aborts ConvoBox's own turn state, but does not cancel an already-dispatched tool call on any of the three backends
status: validated-live
date: 2026-08-09
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 0021e02; codex-cli app-server (turn/interrupt); backend=codex, permission_mode=permissive, interrupt_preset=conversational
evidence:
  - Real UAT session, D:/LegionForge/session1-codex-live.log (not committed to the repo; timestamps quoted verbatim below), 2026-08-09 21:46-23:24, ~1h38m continuous, real AfterShokz OpenComm headset, real codex/gpt-5.6-terra backend
  - src/convobox/adapters/codex.py send_hard_stop() (turn/interrupt call site)
  - src/convobox/adapters/claude_code.py send_hard_stop() (control_request interrupt)
  - src/convobox/adapters/opencode.py send_hard_stop() (POST /api/session/{id}/interrupt)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran the live session, voice-tested pause/safeword mid-tool-call repeatedly, noticed the pattern and asked for it to be investigated and documented)
    - Claude Code (Anthropic claude-sonnet-5) -- live log analysis, adapter code reading across all three backends, timing extraction, writing
  org: https://legionforge.org
  created: 2026-08-09T23:32:05-05:00
  revised: 2026-08-09T23:32:05-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Hard-stop reliably aborts ConvoBox's own state, but does not cancel an already-dispatched tool call

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents: a spoken safeword or "pause listening" phrase is supposed
to abort whatever the backend agent is doing right now (`Orchestrator
.hard_stop()` -> each adapter's own `send_hard_stop()`). This note
documents a real, repeatedly-observed gap between "ConvoBox says it
stopped" and "the agent's tool call actually stopped."

## Problem

During a ~1h38m live voice UAT session (codex backend, real headset,
`interrupt_preset: conversational`), the operator deliberately triggered
the pause phrase and multiple safeword phrases while a `commandExecution`
tool call was in flight, specifically to test whether hard-stop actually
cancels a running tool, not just a spoken response. It does not, reliably.

## Evidence

Five separate live incidents, each showing the same shape: a hard-stop
signal is sent and logged as successfully matched, ConvoBox's own state
(pause/resume, safeword) transitions cleanly and immediately, but the
underlying tool call's `tool_result` arrives many seconds later,
unaffected.

**Incident A** (pause phrase, voice):
```
22:45:43,131 DEBUG backend event type=tool_call tool=commandExecution
22:45:47,077 INFO paused listening (matched 'stop listening') -- hard-stopped in-flight work
22:46:07,400 INFO resumed listening (resume word matched): 'resume listening'
22:46:24,055 DEBUG backend event type=tool_result tool=None
```
Hard-stop to tool_result: **37s**.

**Incident C** (pause phrase, voice):
```
22:50:37,636 DEBUG backend event type=tool_call tool=commandExecution
22:50:44,243 INFO paused listening (matched 'stop listening') -- hard-stopped in-flight work
22:50:52,409 INFO resumed listening (resume word matched): 'resume listening'
22:51:12,121 DEBUG backend event type=tool_result tool=None
```
Hard-stop to tool_result: **28s**.

**Incident D** (pause via the web UI's Stop-listening button, not voice --
rules out an STT-recognition-timing explanation):
```
22:51:15,994 DEBUG backend event type=tool_call tool=commandExecution
22:51:20,499 INFO paused listening (web UI) -- hard-stopped in-flight work
22:51:30,086 INFO resumed listening (web UI)
22:51:47,840 DEBUG backend event type=tool_result tool=None
```
Hard-stop to tool_result: **27s**.

**Incident E** (pause phrase, THEN two separate safewords stacked on top,
all while still waiting on the same tool call):
```
22:55:32,839 DEBUG backend event type=tool_call tool=commandExecution
22:55:37,021 INFO paused listening (matched 'stop listening')
22:55:41,588 INFO hard stop matched safeword 'stop stop stop'
22:55:45,403 INFO hard stop matched safeword 'brake brake brake'
22:55:48,598 INFO resumed listening (resume word matched): 'resume listening'
22:56:04,775 DEBUG backend event type=tool_result tool=None
```
First hard-stop signal to tool_result: **28s** -- despite THREE separate
abort signals (one pause, two distinct safeword phrases) landing during
the wait, none of them affected when the result actually arrived.

**Incident F** (three safewords in a row, no pause phrase this time):
```
22:59:03,975 DEBUG backend event type=tool_call tool=commandExecution
22:59:08,125 INFO hard stop matched safeword 'stop stop stop'
22:59:14,408 INFO hard stop matched safeword 'eject eject eject'
22:59:20,763 INFO hard stop matched safeword 'mayday mayday mayday'
22:59:56,136 DEBUG backend event type=tool_result tool=None
```
First safeword to tool_result: **48s**. Tool-call start to result: **52s**.
This is the starkest case: the operator said three of the four
configured hard-stop phrases in a row, all matched and logged
immediately and correctly, and the command kept running for another
36+ seconds after the last one.

**No RPC failures anywhere in the session.** `grep`-ing the full log for
`"codex turn/interrupt failed"` (the adapter's own warning for a failed
interrupt request) returns zero matches -- every `turn/interrupt` call
this session appears to have succeeded at the protocol level. The delay
is not an error being silently swallowed; the interrupt request itself
works exactly as designed.

## Mechanism

`codex.py`'s `send_hard_stop()` correctly calls `turn/interrupt
{threadId, turnId}` -- the adapter's own docstring (written 2026-07,
before this incident) describes this as canceling "for real (confirmed
live: interrupted turn emits `turn/completed`, and the same thread
serves subsequent turns fine)." That claim is not wrong, exactly, but it
describes a narrower guarantee than it reads as: it confirms the
*conversational turn* completes and the thread stays usable afterward --
it does not establish that an already-dispatched *shell subprocess*
`commandExecution` spawned gets killed. Tonight's evidence shows it does
not, at least not promptly: the real command keeps running to its own
natural completion, and codex reports the (by-then-unwanted) result only
once that happens.

This is structural, not codex-specific. All three adapters follow the
identical shape -- ask the agent's own session/orchestration layer to
stop, with no path to reach an already-spawned OS subprocess directly:

- `codex.py`: `turn/interrupt {threadId, turnId}` (JSON-RPC over stdio)
- `claude_code.py`: `{"type": "control_request", "request": {"subtype":
  "interrupt"}}` (same stdio protocol shape)
- `opencode.py`: `POST /api/session/{id}/interrupt` (confirmed real,
  not a no-op, per that adapter's own docstring correction -- see
  `OPENCODE_API_NOTES.md`)

None of these APIs is documented by its vendor to guarantee killing a
child process the agent spawned for a tool call; each only signals the
agent's own loop to stop generating/waiting. ConvoBox never has a
process handle on whatever subprocess the AGENT itself spawned
internally -- it only observes the eventual `tool_result` the agent
chooses to report. The only OS-level process handle ConvoBox actually
holds is `self._proc` in each adapter -- the CLI subprocess (`codex.cmd`,
`claude`, opencode's server) ConvoBox itself spawned, one level up from
whatever that CLI spawns internally for a shell command.

**Not the same category of problem as "Python cannot force-terminate a
thread"** (this repo's own established finding for the STT/AEC case,
`scripts/run_convobox.py`'s `_transcribe_with_timeout` docstring). A
Python thread genuinely cannot be safely killed from outside. An OS
process CAN always be force-killed (`proc.kill()` / Windows
`TerminateProcess`) -- ConvoBox already does this in each adapter's
`aclose()` on shutdown. The gap here isn't "impossible," it's "not
attempted": nothing currently escalates from the polite `turn/interrupt`
to a forceful process kill if the polite request doesn't produce a
result in time.

## What transfers

- **A hard-stop signal succeeding and being logged is not evidence the
  underlying tool call actually stopped.** ConvoBox's own state
  (pause/resume, safeword match, "resumed listening") can transition
  cleanly and immediately while a real shell command keeps running in
  the background for tens of seconds afterward. (validated-live, n=5
  incidents, one session, codex backend)
- **This reproduces the same way for both voice-triggered and web-UI-
  button-triggered hard-stops** (Incident D used the web Stop-listening
  button, not voice) -- rules out STT recognition delay as the
  explanation; the gap is in what happens after ConvoBox's own interrupt
  request is sent, not in how fast that request gets triggered.
  (validated-live)
- **Stacking multiple hard-stop signals (pause + several distinct
  safewords) does not shorten the wait** -- Incident E fired three
  separate abort signals during one tool call and the result still
  arrived on the underlying command's own schedule. (validated-live)
- **`codex.py`'s docstring claim that `turn/interrupt` "cancels for
  real" needs a caveat**, not a retraction -- it's accurate for the
  conversational turn itself (confirmed by the original 2026-07 probe)
  but does not extend to an in-flight `commandExecution`'s underlying
  process, which this session's evidence contradicts. Corrected in the
  same change as this note.
- **The structural gap (agent-level interrupt, no subprocess-level kill
  path) is shared by all three backend adapters**, not a codex quirk --
  inspected code, not yet independently live-tested against Claude Code
  or opencode specifically. (diagnosed, not validated-live, for the
  other two backends)
- **This is a solvable problem, unlike the STT-thread case** -- ConvoBox
  holds a real OS process handle on each backend's own CLI subprocess
  and already force-kills it cleanly on shutdown; extending that same
  capability into hard-stop (escalate to a process kill if the polite
  interrupt doesn't produce a result within a grace period) is a real
  option, at the real cost of losing the whole session/thread's context,
  not just the one aborted turn. Not yet designed or built -- discussed
  live with the operator (2026-08-09) as a deliberate follow-up
  decision, not something to build without scoping the tradeoff first.
  (hypothesis / design option, not yet implemented)

## Open question for a future session

Two independent follow-ups, not yet built:
1. **Honesty fix (small, safe)**: don't let the UI/status say "resumed
   listening" as if everything stopped when a hard-stop was sent but no
   corresponding tool_result/turn-completion has arrived yet -- track
   and surface that pending-cleanup state truthfully.
2. **Escalating force-kill (bigger, needs its own scoping/UAT pass)**:
   if no completion arrives within some grace period after
   `turn/interrupt`, escalate to killing and respawning the backend
   process. Trades session continuity for an actual guarantee -- should
   be an explicit, deliberate choice (config-gated?), not silently
   defaulted on.
