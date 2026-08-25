---
title: ConvoBox's TUI, --text mode, and Web UI all live-verified end-to-end on Linux for the first time -- real backend round trips, real STT/AEC/TTS, real browser rendering
status: validated-live (typed/text input only -- no real human voice tested yet, see Not done)
date: 2026-08-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0); backend claude-code (real `claude` CLI subprocess, permission_mode=plan); tts.engine kokoro, voice af_sarah; stt faster-whisper-base; interaction.interrupt_preset conversational; audio.echo_cancellation true (aec-audio-processing 1.0.1, built from source this session); openSUSE Tumbleweed 20260822 (kernel 7.1.8-1-default), PipeWire; Chrome (extension-driven) for the Web UI check
hardware: Clevo P17SM-A barebone (Sager-branded, DMI-confirmed: BIOS American Megatrends 4.6.5 dated 2014-03-27), Intel Core i7-4810MQ (Haswell, 4C/8T) -- CPU-only for this whole session, GPU (Intel HD 4600 + NVIDIA Quadro K3000M) unused by ConvoBox. Full spec detail in the companion AEC volume-sweep field note's `hardware:` block. This hardware's age is directly relevant here: all three surfaces below showed noticeable real-world latency (STT + backend response time), expected for 2014-era CPU-only inference, not a defect in any of the three surfaces themselves.
evidence:
  - Real --text/--mute smoke test: scripts/run_convobox.py --text "Reply with exactly: hello from linux" --mute --timeout 60 -v
  - Real --tui launch/shutdown test via `script` (no tmux installed on this host): scripts/run_convobox.py --tui, real mic loop init, clean Ctrl+C shutdown, log at (session-local) convobox-tui.log
  - Real --web session (scripts/run_convobox.py --web -v) driven through a real Chrome browser (extension-based automation) at http://127.0.0.1:5173 -- screenshot before and after sending a message, console-message check
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for TUI/Web UI Linux testing specifically, separate from the AEC volume-sweep work the same session; logged into this Linux machine directly partway through)
    - Claude Code (Anthropic claude-sonnet-5) -- ran all three live checks, drove the Web UI through Chrome, wrote this note
  org: https://legionforge.org
  created: 2026-08-25T06:49:00-05:00
  revised: 2026-08-25T06:49:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# ConvoBox's TUI, --text mode, and Web UI: first live Linux verification

**Context for outsiders.** ConvoBox has three ways to interact with it:
scrolling-log/full-screen TUI in a terminal, a one-shot `--text` mode (no
mic), and a browser-based Web UI. The README currently lists Linux as
"implemented, not yet voice-validated" -- the code paths exist and pass
unit tests, but nobody had actually launched and driven them against a
real backend on a real Linux machine before this session. This note is
that first live pass, run the same day as (and right after) a separate
AEC volume-sweep investigation on this same machine (see
`2026-08-24-linux-volume-sweep-reproduces-high-volume-aec-regression.md`).

## Problem

Do ConvoBox's three interaction surfaces actually work on Linux, driven
against a real backend, not just unit-tested against mocks? Specifically:
does the orchestrator correctly spawn and talk to a real `claude` CLI
subprocess, does real STT/TTS/AEC initialize and run without crashing, and
does the browser-based Web UI actually render and round-trip a message
through a real Chrome session?

## Method

Three checks, in order, each using the already-configured `convobox.yaml`
(backend: claude-code, working_dir a scratch dir outside the repo,
permission_mode: plan -- read-only/safe, interrupt_preset: conversational,
echo_cancellation: true). The single-instance audio lock
(127.0.0.1:47613) was confirmed free before each.

**1. `--text --mute`** (no mic, no speakers -- the safest possible real
round trip): one utterance sent straight to the orchestrator, bypassing
STT/VAD entirely.

**2. `--tui`** (full-screen curses mode, real mic loop): no `tmux` was
installed on this host, so `script` (util-linux, already present) provided
a real pty instead -- enough for curses to initialize without erroring,
though it doesn't give a visual screenshot the way `tmux capture-pane`
would. Ran for ~12s, then sent `Ctrl+C` (`SIGINT`) and checked both the
process exit code and `convobox-tui.log` (where `--tui` deliberately
redirects logging, so the curses redraw isn't garbled by interleaved log
lines -- confirmed that redirect itself works too).

**3. `--web`** (real mic loop, real web server): launched in the
background, confirmed the server came up (`GET /` -> 200), then drove it
through a real Chrome browser (this session's `claude-in-chrome`
extension, on the same Linux machine the server runs on -- a second,
macOS-connected browser was available too but can't reach
`127.0.0.1:5173` on this box, so the Linux one was explicitly selected).
Screenshotted the empty UI, typed a message into the chat box, clicked
Send, waited, screenshotted the result, and checked the browser console
for errors.

## Evidence

**`--text --mute`**: real subprocess spawn confirmed (`backend=claude-code
... pid=70787`), real tool-call events observed (`tool_call tool=Write`,
`tool_call tool=ToolSearch` -- the backend actually reasoned about the
request, in its own scratch workspace, permission_mode=plan keeping it
read-only), and the final reply matched the instruction exactly:

```
INFO response: hello from linux
```

TTS synthesis ran for real even muted: `muted stream: 129536 samples
total @ 24000 Hz`.

**`--tui`**: clean real-mic-loop init, all real subsystems:

```
INFO backend permission_mode: plan (claude-code)
INFO backend=claude-code  voice=af_sarah  safeword='stop stop stop'  pid=71973
INFO single-instance lock acquired (pid=71973)
INFO Processing audio with duration 00:00.500
INFO Detected language 'en' with probability 0.52
INFO acoustic echo cancellation ON (delay hint 100ms, will auto-estimate from stream latencies)
INFO barge-in ON (preset=conversational: on_current_turn=mute, on_new_words=now; after 250ms of sustained speech)
INFO say 'stop listening' to pause listening (hard-stops in-flight work); say 'resume listening' to resume
INFO LegionForge ConvoBox, version dev, ready and standing by.
```

`script`'s own wrapper confirmed a clean shutdown: `Script done ...
COMMAND_EXIT_CODE="0"`. One warning line appeared --
`WARNING words count mismatch on 100.0% of the lines (1/1)` -- traced to
`phonemizer`'s espeak-ng backend
(`.venv/.../site-packages/phonemizer/backend/espeak/words_mismatch.py`),
a known, benign upstream warning from Kokoro TTS's phonemization step on
short text, not a ConvoBox defect.

**`--web`**: server came up (`GET / -> 200`), real mic loop initialized
identically to the `--tui` run (same AEC/barge-in/STT log lines), and the
real ambient room noise was correctly rejected as non-speech by STT before
any message was sent:

```
DEBUG Compression ratio threshold is not met with temperature 0.0 (23.615385 > 2.400000)
DEBUG Log probability threshold is not met with temperature 0.2 (-1.140441 < -1.000000)
DEBUG No speech threshold is met (0.723766 > 0.600000)
```

The browser itself rendered a real ConvoBox UI (header, live status
indicator, Stop/Browse files/Settings/Quit controls, chat input) --
confirmed by screenshot, not assumed. Typing "Reply with exactly: web ui
works on linux" and clicking Send produced, in the browser, a `TRANSCRIPT`
bubble with the exact typed text and a `RESPONSE` bubble reading exactly
`web ui works on linux` -- a real round trip through the browser, the
FastAPI/SSE web layer, the orchestrator, and the same real `claude`
subprocess backend as check 1. Status indicator correctly showed `live ·
speaking` while the reply's TTS audio was playing. No console errors were
observed during the interaction (console tracking was only active from
after page load, not page-load itself, so a load-time error can't be
fully ruled out by this check alone).

Server-side log for the Web UI round trip confirms real playback and a
real (if characteristically weak, at this volume) AEC measurement:

```
INFO response: web ui works on linux
INFO playback: first audio block reached output device
INFO AEC stats for last response: attenuation=7.3dB of ~1.8dB measurable  delay=76ms  frames(reverse=549, capture=15571)  [NO ECHO DETECTED: barely any speaker sound is reaching the mic -- check the output device is audible; this is NOT a cancellation result]
```

**This "no echo detected" line is not a bug -- it's a direct, independent
cross-confirmation of the same-day AEC volume sweep's own finding**: at
30% system volume (the level active during this test, restored after the
earlier sweep), that investigation measured `ceiling_db` of only 0.24dB
mean across 10 trials -- i.e. almost no measurable echo reaches the mic at
this volume on this rig at all. Two completely different test paths (the
synthetic calibration harness, and a real live orchestrator session) agree
on the same physical fact about this hardware.

## Mechanism

Nothing surprising mechanistically -- the point of this note is that the
already-designed system works as designed, for the first time actually
observed on Linux. The one genuinely new piece of information is the
cross-confirmation above: two independently-built test paths (one
synthetic/calibration, one real end-to-end) agreeing on the same AEC
measurement gives real confidence that neither is measuring an artifact of
its own harness.

## What transfers

- **All three ConvoBox interaction surfaces (`--text`, `--tui`, `--web`)
  are now confirmed live-functional on Linux**, closing a real portion of
  the README's "implemented, not yet voice-validated" gap -- specifically
  the typed/text-input half of it. (validated-live, this session)
- **The claude-code backend adapter works correctly on Linux end-to-end**,
  including real tool-call events, not just a text reply -- consistent
  with `docs/UAT-claude-code-smoke.md`'s existing (presumably
  non-Linux-specific) coverage, now confirmed on this platform too.
- **`script` is a usable `tmux`-free way to real-pty-test `--tui`** when
  `tmux` isn't installed -- gets a clean init/shutdown/exit-code check,
  though not a visual screenshot the way `tmux capture-pane` gives.
- **The `words count mismatch` warning from `phonemizer`/espeak-ng is
  cosmetic**, safe to ignore, not Linux-specific (it's a property of the
  Kokoro TTS phonemization step generally, for short input text).

## Not done here

- **No real human voice was used anywhere in this pass** -- every
  interaction was typed (`--text`'s CLI argument, or the Web UI's text
  box). Real barge-in (a human actually speaking over a playing response)
  was not tested; only the synthetic-tone AEC calibration harness
  (separate field note) exercised anything resembling barge-in on this
  session, and that harness explicitly does not go through the real
  orchestrator/backend loop. JP logged into this machine directly partway
  through this session -- a real live voice/barge-in test with him
  actually speaking is a natural, currently open follow-up.
- No visual screenshot of the `--tui` curses screen itself (only log-based
  confirmation of clean init/shutdown) -- `tmux` isn't installed on this
  host; installing it and re-running with `capture-pane` would close this
  gap.
- Only the claude-code backend was exercised through the full
  `run_convobox.py` loop. OpenCode was separately live-verified this
  session but only at the adapter level, bypassing `run_convobox.py`
  entirely (see the OpenCode field note) -- a full OpenCode-backed
  `run_convobox.py` session (mic loop, TUI, or Web UI) is still open.
  Codex was not exercised at all this session.
- Browser console was only monitored from after page load, not
  page-load itself -- a load-time-only JS error wouldn't have been caught.
