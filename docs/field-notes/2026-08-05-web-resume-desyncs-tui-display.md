---
title: A web-triggered resume unblocks the mic loop but never tells the TUI — a "hung" session that wasn't
status: validated-live
date: 2026-08-05
project: ConvoBox (github.com/LegionForge/convobox)
versions: incident session on feat/stt-hotwords-bias (#204 unmerged), faster-whisper base, device=cpu, compute_type=int8, stt.hotwords enabled, backend=codex; follow-up session on fix/web-listening-bridge-tui-desync @ 5f4e629 (branched off main, no stt.hotwords support -- config key present in convobox.yaml but silently ignored, no field on STTConfig)
evidence:
  - convobox-tui.log 2026-08-05 14:41:52-14:43:10 (original incident, pause/resume sequence)
  - convobox-tui.log 2026-08-05 15:36:22-15:43:21 (follow-up session, three pause/resume cycles + a second false-positive pause)
  - src/convobox/web/bridge.py:276-288 (WebListeningBridge.resume()/pause(), pre-fix)
  - scripts/run_convobox.py:2411-2483 (mic-loop pause/resume gate, listening_gate)
  - src/convobox/tui/state.py (turn log is the TUI's only pause/resume indicator)
  - convobox.yaml (session config: stt.hotwords includes "Athena")
  - PR #212 (the fix)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; hit the false hang live, resolved it via the web button, ran the follow-up UAT session)
    - Claude Code (Anthropic claude-sonnet-5) — live log/code investigation, writing, fix (PR #212)
  org: https://legionforge.org
  created: 2026-08-05T14:56:31-05:00
  revised: 2026-08-05T15:48:17-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A web-triggered resume unblocks the mic loop but never tells the TUI

**Context for outsiders.** ConvoBox is a local voice frontend for coding-agent
CLIs: mic → VAD → STT → orchestrator → agent backend → TTS, with a spoken
"pause/resume listening" mechanism (say a pause phrase to hard-stop and mute
the mic; say a resume word to un-mute) mirrored by a web UI Stop/Resume
button hitting the same backend state. This note documents a live session
where the operator believed the app had hung waiting for the resume word,
when the mic loop had in fact already resumed — the TUI just never found
out — plus a same-day follow-up session that reproduced the underlying
false-positive-pause category a second time and supplied a real (if
uncontrolled) baseline for resume-attempt variance.

## Problem

Mid-session, the operator's speech was mistaken for the pause command and
listening paused. Saying the configured resume word ("Athena") repeatedly
failed to un-pause it. The operator eventually clicked "Resume listening" in
the web UI, which worked — but the terminal UI kept displaying "paused
listening — say 'Athena' to resume" with no indication anything had changed,
making the whole session look stuck even after it was fixed.

## Evidence

Three distinct events in one incident, all in `convobox-tui.log`:

**1. False-positive pause trigger** — the operator was talking *about* the
pause phrase, not invoking it:

```
14:41:52,708 INFO paused listening (matched 'No I tried to say the stop
listening phrase') -- hard-stopped in-flight work; say 'Athena' to resume
```

**2. Three consecutive failed resume attempts**, despite `stt.hotwords:
Athena stop break brake eject mayday Whiskey Tango Foxtrot` already being
configured for this session (`convobox.yaml`, this is the branch shipping
#204):

```
14:42:22,944 INFO dropped (paused, not the resume word): 'Pina'
14:42:26,594 INFO dropped (no input, STT heard nothing recognizable) [ERROR-LADDER: tier 1]
14:42:30,278 INFO dropped (no input, STT heard nothing recognizable) [ERROR-LADDER: tier 2]
```

**3. A silent resume** — the operator clicked the web UI's "Resume
listening" button. This is logged only as an unlabeled playback event (the
ack tone), with no line stating what happened:

```
14:43:10,114 INFO playback: first audio block reached output device
14:43:10,781 INFO AEC stats for last response: attenuation=10.4dB ... [FLOOR-LIMITED: success]
```

No `resumed listening` line ever appears. Confirmed live afterward: `GET
/api/listening` on the running instance returned `{"is_paused": false}` —
the backend was genuinely unpaused. The TUI's own turn log, checked at the
same time, still showed the stale "paused listening" system turn with
nothing appended after it.

**4. Follow-up session, same day (15:36:22-15:43:21), a second independent
false-positive pause** -- run against the fix itself, still voice-only
(the web button wasn't clicked this session, so PR #212's own display
change went unexercised):

```
15:38:09,477 INFO paused listening (matched 'but I just said stop
listening.') -- hard-stopped in-flight work; say 'Athena' to resume
```

Same shape as finding #1 -- the operator describing the phrase, not
invoking it, still trips the bare substring match. Two independent
instances in one day is enough to call this a reliably reproducing
category, not a one-off.

The same session also gives a second, unrelated data point worth
recording precisely rather than by impression: three pause/resume cycles,
resumed in 1, 2, and 2 spoken attempts respectively (vs. the original
incident's 3-for-3 total misses). This ran on `fix/web-listening-bridge-
tui-desync` (branched off `main`), which has **no** `stt.hotwords` support
-- `convobox.yaml`'s `hotwords:` key is silently dropped by pydantic's
default `extra="ignore"` on `STTConfig`, confirmed by grep (no `hotwords`
identifier anywhere under `src/convobox/`). So the improved rate is real
but **not attributable to #204** -- most likely session-to-session
variance (mic technique, room noise, speaking pace) rather than any code
change, since the code that would explain it wasn't even loaded.

## Mechanism

Two independent code paths mutate the same `ListeningGate.is_paused` flag:

- **Voice path** (`scripts/run_convobox.py:2411-2483`, inside the mic loop):
  on pause or resume, calls `log.info(...)` with the matched text *and*
  `tui_state.add_turn("system", ...)`. Both the log and the TUI screen
  reflect the change.
- **Web path** (`src/convobox/web/bridge.py:276-288`,
  `WebListeningBridge.pause()`/`.resume()`): sets `self._gate.is_paused`
  directly, plays the same ack tone (`pause_resume_ack == "tone"`), and
  returns a bool to the HTTP caller — but calls neither `log.info(...)` nor
  `tui_state.add_turn(...)`. The only externally visible trace is the ack
  tone's own generic `playback: first audio block reached output device`
  line, indistinguishable from any other tone or response.

`tui/state.py` has no independent notion of pause state — the "paused
listening" banner the operator sees is nothing but the last system turn
appended to that log. Since the web path never appends one, a web-triggered
resume is invisible to the TUI forever (until the *next* voice-triggered
pause/resume event overwrites it), even though the underlying gate — and
therefore the actual mic loop's behavior — is completely correct and live.

Ruled out: STT/CUDA hang (the earlier vault note's "stuck-paused sessions
look like STT silently stopped" concern from 2026-08-02, addressed by
adding the `dropped (paused, not the resume word)` log line) — that fix is
working correctly here; the ambiguity in this incident is a display bug one
layer up, not a recognition-pipeline stall. Also ruled out: a second
competing ConvoBox instance (only one `say 'stop listening' to pause
listening` startup banner exists for this session, `.aec-dumps
\20260805-143527`, so there was exactly one mic loop, not two racing on the
same log file).

## What transfers

- **Any secondary control surface that mutates state a primary UI also
  displays must update that UI's own state, not just the shared object.**
  Two writers to `is_paused`, only one of which tells the display layer, is
  enough to make a fully-working session look permanently hung. (validated-live)
- **Deterministic substring-match pause/resume detectors will false-fire on
  a sentence that mentions the trigger phrase without invoking it** — "I
  tried to say the stop listening phrase" / "but I just said stop
  listening." both contain "stop listening" as a literal substring. Two
  independent instances in one day, same shape as this project's earlier
  documented near-misses on the safeword/confirmword family. (validated-live,
  n=2; the general fix — requiring more than bare substring containment —
  is still an open design question, not attempted here)
- **`stt.hotwords` bias does not guarantee a short proper-noun resume word
  transcribes correctly.** Three consecutive attempts at the configured
  resume word ("Athena") failed in a row — one heard as "Pina," two heard
  as nothing at all — in the original incident, where `hotwords` was
  configured but the branch running it (unclear at the time) may not have
  had it wired end-to-end either. n=1, not a controlled test; still a
  (hypothesis), and now specifically NOT corroborated by the follow-up
  session's better rate, since that session is confirmed to have run
  without hotwords support at all (see Evidence #4) — the two data points
  don't actually compare the same thing. A real read on #204 needs a
  session confirmed to be on `feat/stt-hotwords-bias` (or later, `main`
  once merged), deliberately labeled as such.
- **Session-to-session resume-attempt variance is high even with zero code
  changes.** 3-for-3 misses in one session, 1-2 tries per success in the
  next, both on plain `base`/cpu/int8 STT with no hotwords. Worth keeping
  in mind before crediting any future hotwords-branch improvement to the
  feature itself — a single better-feeling session isn't enough on its own
  either way. (validated-live)
- **The concrete, scoped fix**: have `WebListeningBridge.pause()`/
  `.resume()` (`src/convobox/web/bridge.py:260-288`) call the same
  `log.info` pattern the voice path uses and append the same
  `tui_state.add_turn("system", ...)` line, parameterized by which surface
  triggered it (e.g. "paused listening (web)" / "resumed listening (web)").
  **Built**: PR #212 (open, unmerged), unit-tested (4 new tests, 1234
  passed overall). Still open: a live UAT actually clicking the web
  Resume/Stop-listening button and confirming the `(web)`-tagged turn
  appears in the TUI transcript pane — every resume so far, in both
  sessions, went through voice, not the button.
