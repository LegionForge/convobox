---
title: With vad.max_utterance_s unset, the VAD segmenter can lock up silently forever — mic capture keeps running, nothing is ever transcribed
status: validated-live
date: 2026-08-05
project: ConvoBox (github.com/LegionForge/convobox)
versions: feat/stt-hotwords-bias @ c59e117+; vad.threshold=0.65, min_speech_ms=300, min_silence_ms=500 (default, unset in convobox.yaml), max_utterance_s=None (default, unset); stt.temperature=0.0, device=cpu, compute_type=int8
evidence:
  - convobox-tui.log 2026-08-05 17:12:01-17:15:23 (last mic-side log activity, then silence until Quit)
  - src/convobox/vad/segmenter.py (UtteranceSegmenter: _triggered state machine)
  - src/convobox/config.py:41-49 (VADConfig.max_utterance_s, the existing but here-unset mitigation for this exact failure class)
  - scripts/run_convobox.py:2173-2233 (_mic_chunks: canceller.process()/AEC dump capture counter runs inside the same generator the segmenter consumes)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; hit this live, spoke into it for 3+ minutes with zero response before quitting)
    - Claude Code (Anthropic claude-sonnet-5) — live log investigation, code trace, writing
  org: https://legionforge.org
  created: 2026-08-05T17:22:49-05:00
  revised: 2026-08-05T17:22:49-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The VAD segmenter can lock up silently forever when max_utterance_s is unset

**Context for outsiders.** ConvoBox's voice pipeline is mic → AEC →
VAD segmenter (turns a raw audio stream into discrete "utterances") → STT
→ orchestrator → backend → TTS. The segmenter (Silero VAD, streamed
512-sample windows) is a small state machine: not-triggered → triggered
(speech in progress) → back to not-triggered once trailing silence
persists long enough. This note documents a session where that state
machine got stuck in "triggered" and never came back — the operator spoke
for over three minutes with zero transcription, zero errors, and zero log
output, until they gave up and quit.

## Problem

After a long stretch of live testing (pause/resume/hard-stop cycles,
resume-word attempts, hotwords testing), STT stopped producing anything
at all. Not mis-transcriptions, not "no input" drops — total silence.
The operator confirmed they were actively speaking and watching the TUI
when this was reported live; they quit via the web UI's Quit button after
~3 minutes of getting nothing back.

## Evidence

Last mic-side log line before the silence:

```
17:11:56,881 INFO dropped (no input, STT heard nothing recognizable) [ERROR-LADDER: tier 2]
17:12:01,008 INFO resumed listening (web UI)
17:12:01,955 INFO AEC dump progress: reference=5297 frames (53.0s)  capture=39574 frames (395.7s)  ...
```

Then nothing mic-related until the operator quit:

```
17:15:21,794 INFO AEC dump closed -- .aec-dumps\20260805-170517: reference 53.0s (5297 frames), capture 595.5s (59552 frames), session 603.8s.
17:15:23,462 INFO exiting
```

`capture` grew from 395.7s to 595.5s — **199.8s of capture in ~200s of
wall-clock time, real-time, no slowdown.** `canceller.process(chunk)`
(`scripts/run_convobox.py:2177`), which feeds that counter, runs inside
the same async generator (`_mic_chunks`) that `UtteranceSegmenter.segment()`
consumes chunk-by-chunk — so the mic loop was provably alive and iterating
the entire time. Nothing crashed, nothing hung at the process level
(confirmed separately: the web server's `/health` endpoint kept responding
`ok` throughout). The break is downstream, inside the segmenter itself,
which produced zero completed utterances (`Processing audio` in the log
only fires once one is yielded) for 3+ minutes of live speech.

## Mechanism

From `src/convobox/vad/segmenter.py`: once `_triggered` becomes `True`
(`_process_window`, line 187-203), there are exactly two paths back to
`False`:

1. **Natural end**: `_trailing_silence_windows >= min_silence_ms` worth
   of windows (line 219-220).
2. **Forced cap**: `len(self._speech) >= max_utterance_s`-derived window
   count (line 226-227) — but `VADConfig.max_utterance_s` defaults to
   `None` (`config.py:84-85`) and is **not set** in this session's
   `convobox.yaml` (`vad:` section only has `threshold`/`min_speech_ms`).

If trailing silence never cleanly accumulates `min_silence_ms` (500ms
default) worth of confidently-silent windows in a row, path 1 never
fires, and with path 2 disabled there is **no other exit**. Nothing else
in the app resets it — pause, hard-stop, and safeword all operate
downstream of the segmenter and never touch `UtteranceSegmenter` state.
Once stuck, `_process_window` keeps appending every incoming window to
`self._speech` (an unbounded, ever-growing buffer) and returns `None`
forever.

Two plausible, non-exclusive triggers for silence never accumulating,
neither confirmed (no per-window probability was logged during the
incident — `last_probability` exists as a read-only property but isn't
logged anywhere in the mic loop):

- **Hysteresis-band stall**: Silero's probability sits between
  `threshold - 0.15` and `threshold` (0.50-0.65 here) — "neither
  confidently speech nor confidently silence" — and by design
  (`segmenter.py:211-217`, a deliberate 2026-07 fix for exactly the
  opposite bug, premature silence-reset on ambiguous frames) neither
  counter moves in that band. If the noise floor sat there for an
  extended period, both exits stay frozen indefinitely.
- **Genuinely busy audio**: this stretch followed heavy pause/resume/
  hard-stop testing with utterances close together — if the operator's
  actual speech pattern never left 500ms of confident silence between
  attempts, the same lockup follows without needing the hysteresis-band
  explanation at all.

Either way, the underlying gap is the same: **`max_utterance_s` is this
project's own existing, tested mitigation for unbounded-buffer VAD
lockups** (`config.py:45-49` cites the original motivating incident, "a
30.5s single utterance whose transcript only arrived after it ended"),
and it's off by default.

Ruled out: STT/model-level hang (no `Processing audio` line ever
appeared, so the transcriber was never even invoked during the silent
stretch — this is upstream of STT entirely) and process crash/freeze
(`/health` kept responding, CPU showed the event loop still ticking).

## What transfers

- **An unbounded VAD buffer with no external reset path is a silent,
  total, zero-log failure mode** — worse than a crash, because nothing
  signals it happened. A caller watching only for exceptions or
  `ERROR`-level logs would never notice. (validated-live, symptom and
  mechanism both confirmed from code + log arithmetic)
- **Immediate mitigation, no code change needed**: set `vad.max_utterance_s`
  (e.g. `30`) in `convobox.yaml`. This forces `_finish_run(forced=True)`
  on window count alone, independent of whether silence ever confidently
  arrives — the escape valve already exists, it just needs enabling.
  (recommended, not yet applied or re-tested live)
- **The exact trigger for this specific incident is unconfirmed**
  (hysteresis-band stall vs. genuinely-busy audio) — logging
  `last_probability` periodically while `_triggered` is `True` and stuck
  past some duration would settle it next time. Not built. (hypothesis)
- **A defensive follow-up worth considering, not yet scoped**: even with
  `max_utterance_s` set, a `_triggered=True` state persisting past some
  threshold could itself be surfaced to the TUI/web status line (today's
  status vocabulary — listening/capturing/speaking/working/waiting/paused
  — has no "stuck" state), so an operator sees *something* instead of
  silence next time this class of bug recurs under a different trigger.
