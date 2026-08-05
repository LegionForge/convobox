---
title: The VAD segmenter can go silent for extended stretches even with vad.max_utterance_s set — mic capture keeps running, nothing is transcribed, no log line
status: validated-live
date: 2026-08-05
project: ConvoBox (github.com/LegionForge/convobox)
versions: feat/stt-hotwords-bias @ c59e117+ (incident 1, max_utterance_s unset) and @ 8303864+ (incident 2, max_utterance_s=30.0 set, config edit predates session start); vad.threshold=0.65, min_speech_ms=300, min_silence_ms=500 (default); stt.temperature=0.0, device=cpu, compute_type=int8
evidence:
  - convobox-tui.log 2026-08-05 17:12:01-17:15:23 (incident 1: permanent lockup, max_utterance_s unset, ended only by Quit)
  - convobox-tui.log 2026-08-05 17:37:07-17:45:24 (incident 2: 8m16s silent stretch, max_utterance_s=30.0 active, self-recovered)
  - src/convobox/vad/segmenter.py (UtteranceSegmenter: _triggered state machine, _finish_run's emit gate, discarded_forced_runs counter — fix applied)
  - src/convobox/config.py:41-49 (VADConfig.max_utterance_s)
  - scripts/run_convobox.py:2173-2233 (_mic_chunks: canceller.process()/AEC dump capture counter runs inside the same generator the segmenter consumes)
  - scripts/run_convobox.py:1091-1156, 2318-2324 (_working_watchdog: polls discarded_forced_runs, logs on increase — fix applied)
  - tests/test_vad_segmenter.py (4 new tests for discarded_forced_runs)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; hit both incidents live, spoke into it for 3+ and 8+ minutes respectively with zero response)
    - Claude Code (Anthropic claude-sonnet-5) — live log investigation, code trace, writing, fix
  org: https://legionforge.org
  created: 2026-08-05T17:22:49-05:00
  revised: 2026-08-05T17:54:27-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The VAD segmenter can go silent for extended stretches even with max_utterance_s set

**Context for outsiders.** ConvoBox's voice pipeline is mic → AEC →
VAD segmenter (turns a raw audio stream into discrete "utterances") → STT
→ orchestrator → backend → TTS. The segmenter (Silero VAD, streamed
512-sample windows) is a small state machine: not-triggered → triggered
(speech in progress) → back to not-triggered once trailing silence
persists long enough, or a `max_utterance_s` cap forces it. This note
covers two live incidents the same day: first a truly permanent lockup
with the cap unset, then — after adding the cap as a same-session
mitigation — a second, shorter but still real silent stretch that
revealed the cap only partly closes the gap. A code fix (a visibility
counter + heartbeat log line) was built and tested the same session.

## Problem

**Incident 1** (`max_utterance_s` unset): after a long stretch of live
testing (pause/resume/hard-stop cycles, resume-word attempts, hotwords
testing), STT stopped producing anything at all — not mis-transcriptions,
not "no input" drops, total silence. The operator confirmed they were
actively speaking and watching the TUI when this was reported live; they
quit via the web UI's Quit button after ~3 minutes of getting nothing
back.

**Incident 2** (`max_utterance_s: 30.0` set, confirmed active — config
edited 17:24:42, this session started 17:25:57, after the edit): the same
symptom recurred, this time for 8 minutes 16 seconds
(17:37:07.703 → 17:45:24.194), then self-resolved — the very next spoken
"Athena" matched immediately. The operator noticed this directly: "Athena
just worked a few seconds ago... like something is listening now."

## Evidence

**Incident 1.** Last mic-side log line before the silence:

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

**Incident 2**, same day, after `vad.max_utterance_s: 30.0` was added:

```
17:37:07,703 INFO dropped (paused, not the resume word): 'hit his neck'
17:45:24,194 INFO Processing audio with duration 00:01.120        <- 8m16s later
17:45:24,911 INFO resumed listening (resume word matched): 'Athena'
```

Confirmed the config really was active for this run, not a stale-process
false alarm: `convobox.yaml` was edited 17:24:42 (adding the cap), this
session's own startup banner logged at 17:25:57 — after the edit. No
convenient mid-gap AEC-stats line exists this time (those only log after
a response plays, and nothing played during the silence), so the
capture-frame arithmetic from incident 1 could not be independently
repeated here — but the eventual self-recovery, combined with the cap
being provably active, points at a different, more specific mechanism
than a true permanent lockup (below).

## Mechanism

From `src/convobox/vad/segmenter.py`: once `_triggered` becomes `True`
(`_process_window`, line 187-203), there are exactly two paths back to
`False`:

1. **Natural end**: `_trailing_silence_windows >= min_silence_ms` worth
   of windows (line 219-220).
2. **Forced cap**: `len(self._speech) >= max_utterance_s`-derived window
   count (line 226-227) — `None` (unlimited) by default.

**Incident 1** had path 2 disabled entirely (`max_utterance_s` unset). If
trailing silence never cleanly accumulates `min_silence_ms` worth of
confidently-silent windows in a row, path 1 never fires either, and with
no other exit `_process_window` keeps appending every incoming window to
`self._speech` (an unbounded, ever-growing buffer) and returns `None`
forever. Nothing else in the app resets it — pause, hard-stop, and
safeword all operate downstream of the segmenter and never touch
`UtteranceSegmenter` state.

**Incident 2, with the cap enabled, exposed a second gap inside
`_finish_run` itself:**

```python
def _finish_run(self, forced: bool) -> np.ndarray | None:
    emit = self._speech_windows >= self._min_speech_windows
    utterance = np.concatenate(self._speech) if emit else None
    ...
    return utterance
```

The cap firing (`forced=True`) resets `_triggered` back to `False` — so
it is not permanently stuck this time — but `emit` only checks
`_speech_windows` (windows classified confidently `is_speech`), not the
total buffered window count that actually hit the cap. If the 30-second
run spent most of its time with the probability sitting in the exit-
hysteresis band (0.50–0.65 here — "neither confidently speech nor
confidently silence," `segmenter.py:211-217`) rather than confidently
above `threshold`, `_speech_windows` can stay below `min_speech_ms`
worth of windows even after a full 30s buffer. `emit` is then `False`:
the entire 30 seconds is **silently discarded** — no utterance, no log
line — and a fresh 30-second cycle starts immediately. If the same
ambient conditions persist, this repeats: ~16 silent 30-second cycles
account for the observed 8m16s gap. Self-recovery happens the moment one
cycle finally accumulates enough confident-speech windows to emit —
matching the operator's own observation that "Athena" worked immediately
once something finally came through.

So `max_utterance_s` converts "stuck forever" into "stuck in silent,
periodically-resetting cycles until conditions change" — a real
improvement (bounded instead of unbounded), but not a full fix, and with
zero visibility into either state: a caller watching only for exceptions
or `ERROR`-level logs would never notice, and even `INFO`-level logs show
nothing during either failure mode.

Ruled out: STT/model-level hang (no `Processing audio` line ever appeared
during either incident, so the transcriber was never even invoked — this
is upstream of STT entirely) and process crash/freeze (`/health` kept
responding both times, CPU showed the event loop still ticking).

## Fix applied

Added a visibility counter and a heartbeat log line — deliberately not a
VAD-tuning change, since the actual trigger (hysteresis-band stall vs.
genuinely-busy audio) is still unconfirmed and a threshold/hysteresis
change would be guessing at a mechanism, not fixing an observability gap:

- `UtteranceSegmenter.discarded_forced_runs` (`src/convobox/vad/segmenter.py`):
  a monotonically-increasing counter, incremented in `_finish_run` exactly
  when `forced=True and not emit` — the silent-discard case above. Same
  "pure counter, no side effects, caller polls and diffs" pattern already
  used by `was_forced` and `RecognitionErrorLadder`.
- `_working_watchdog` (`scripts/run_convobox.py:1091-1156, 2318-2324`):
  the existing 1s heartbeat poll now also diffs `discarded_forced_runs`
  each tick and logs a `WARNING` on increase, naming the field note for
  context. Same poll-and-diff pattern the heartbeat already uses for the
  silently-busy-backend and continue-prompt jobs sharing this tick.
- 4 new tests in `tests/test_vad_segmenter.py`: counter starts at zero,
  increments only for the forced+not-emitted case, stays zero when a
  capped run DOES emit normally, and stays zero for a natural (non-forced)
  short-run discard (a different, pre-existing code path). Full suite:
  1244 passed (was 1240), ruff/mypy clean on all touched files.

Not yet done: live re-test with the fix deployed (need to reproduce the
silent stretch again and confirm the new `WARNING` line actually appears
instead of silence), and the still-open question of which trigger
(hysteresis-band stall vs. busy audio) actually caused either incident.

## What transfers

- **An unbounded VAD buffer with no external reset path is a silent,
  total, zero-log failure mode** — worse than a crash, because nothing
  signals it happened. (validated-live, incident 1)
- **A bounded cap alone does not guarantee visibility.** `max_utterance_s`
  correctly prevents a permanent lockup, but a forced-and-discarded run
  is exactly as silent as an unbounded one was — the failure just repeats
  in shorter cycles instead of running forever. A mitigation that changes
  *how long* a silent failure lasts is not the same as fixing its
  *silence*. (validated-live, incident 2)
- **The exact trigger for either incident is still unconfirmed**
  (hysteresis-band stall vs. genuinely-busy audio) — logging
  `last_probability` periodically while stuck would settle it next time;
  not built, deliberately deferred rather than guessed at. (hypothesis)
- **Fixed**: the visibility gap itself, via `discarded_forced_runs` +
  the heartbeat warning (see Fix applied). Live re-verification against
  a real recurrence is the natural next step, not yet done.
