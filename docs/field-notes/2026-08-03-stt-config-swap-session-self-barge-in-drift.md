---
title: An evening of STT model/device/compute_type swaps was too confounded to isolate a cause for self-barge-in rate -- backend, model size, and elapsed session time all moved together at every transition; only the VRAM ceiling and the AEC-delay-stability negative result are solid
status: hypothesis
date: 2026-08-03
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #204 branch (feat/stt-hotwords-bias); faster-whisper (base, large-v3); ctranslate2 compute_type default/float16/float32; WebRTC AEC3 via aec_audio_processing; aec_delay_ms auto-tune (measured 222ms, all sessions); backends claude-code and codex (both permissive mode)
evidence:
  - convobox-UAT/convobox-tui.log lines 24097-24610+ (2026-08-03 20:41-22:06, five restarts)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live voice UAT, config swaps, session narration; correctly rejected this note's initial backend-correlation framing as overstated)
    - Claude Code (Anthropic claude-sonnet-5) -- live log analysis during the session, rate/verdict tabulation, initial (overstated) correlation claim, revision after operator pushback, writing
  org: https://legionforge.org
  created: 2026-08-03T22:12:00-05:00
  revised: 2026-08-03T22:25:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# An STT config-swap evening: too confounded to name a cause, but a real VRAM ceiling and a clean AEC-delay negative result survive

**Revision note (2026-08-03T22:25):** this note originally led with "backend
choice (claude-code vs. codex) correlates with self-barge-in rate" as a
diagnosed finding. The operator correctly rejected that conclusion live,
prompting a re-check: Window A (claude-code) held a flat ~1 barge-in/min
for its entire 11.5-minute run with no ramp, while Window E (also
claude-code, run last) went 0->2->1->1->4->4->4->~7/min within its own
14 minutes -- same backend, opposite shapes. That contrast is
incompatible with backend alone driving the rate. Downgraded to
`hypothesis` status; see Mechanism for the full confound accounting.

**Second revision note (same session, operator's second pushback):** the
per-minute rate table below is itself a weaker metric than it looks, for
two reasons neither caught before publishing: (1) it counts every
`barge-in: sustained speech` log line as one undifferentiated event,
with no attempt to separate genuine barge-ins (the operator deliberately
talking over playback to test the feature -- a real fraction of tonight's
events, especially during the word-list rounds) from actual self-echo
false triggers -- the AEC verdict tag doesn't cleanly do this either
(`UNDER-CANCELLING` doesn't prove causation for that specific trigger;
`NO ECHO DETECTED` doesn't prove the trigger was genuine speech rather
than a quiet/misconfigured output at that moment), and the log's own
`dropped (spoken-echo filter, barge-in was our own echo)` lines only
catch echo good enough to text-match, which `[G9]` already documented as
an incomplete catch (leaked echo has transcribed as unrelated real
words before); (2) it divides by wall-clock session minutes, which
includes idle/listening/thinking time when barge-in is structurally
impossible (`player.audible` must be true) -- the real denominator would
be minutes of actual playback, not session duration, and that was never
computed. **The table below should be read as a raw incident log, not a
controlled comparative measurement.**

**Context for outsiders.** ConvoBox is a local voice frontend for CLI coding
agents (claude-code, codex, opencode): mic and speakers run simultaneously,
WebRTC AEC3 cancels the assistant's own TTS output from the mic signal, and
a `BargeInMonitor` stops playback when it detects sustained real speech
during a response. "Self-barge-in" is the failure mode where the system's
own under-cancelled echo is mistaken for a live interruption. This session
was operator-driven exploratory testing (STT `model`/`device`/`compute_type`
swaps to compare against an ongoing self-barge-in investigation), not a
scripted UAT pass -- the findings below are what a live log read surfaced
along the way, not a planned experiment.

## Problem

Across one evening (2026-08-03, 20:41-22:06), the operator swapped STT
`model`/`compute_type` five times, restarting `run_convobox.py --web` each
time (STT config requires a fresh process; there is no hot-reload path).
Self-barge-in rate varied enormously across these restarts, and the
operator's initial hypothesis (STT precision/model size drives the rate)
turned out to be entangled with an unplanned variable: the LLM backend
changed mid-evening too.

## Evidence

| Window | PID | Backend | STT config | Duration | Barge-ins | Rate/min | FLOOR-LIMITED | UNDER-CANCELLING | NO ECHO DETECTED |
|---|---|---|---|---|---|---|---|---|---|
| A | 56392 | **claude-code** | base/cuda/default (=float16 on cuda) | 11.5 min (20:41:59-20:53:31) | 12 | 1.04 | 4/17 (24%) | 9/17 (53%) | 4/17 (24%) |
| B | 33768 | **codex** | base/cuda/float16 | 1.8 min (21:23:43-21:25:33) | 8 | 4.37 | 1/7 (14%) | 3/7 (43%) | 3/7 (43%) |
| C | 60700 | **codex** | base/cuda/float16 (2nd run) | 3.8 min (21:28:57-21:32:43) | 10 | 2.65 | 0/9 (0%) | 6/9 (67%) | 3/9 (33%) |
| D | 37964 | codex | large-v3/cuda/**float32** | ~2.5 min (21:35:58-21:38:12) | n/a -- hung | n/a | -- | -- | -- |
| E | 63936 | **claude-code** | large-v3/cuda/float16 | 14.4 min (21:52:10-22:06:39) | 24+ (partial sample) | ramped 0->7/min | see Mechanism | see Mechanism | see Mechanism (late-session majority) |

`delay=222ms` appears identically on every single AEC-stats line across
all five restarts, from each session's very first response onward --
this is a fresh WebRTC AEC3 instance auto-estimating the delay on every
process restart (`aec_delay_ms` unset/auto in `convobox.yaml`), and it
converges to the same value instantly every time rather than drifting.

**Window D (float32 hang), representative lines:**
```
2026-08-03 21:36:05,476 INFO Processing audio with duration 00:00.500
2026-08-03 21:36:31,638 INFO Detected language 'en' with probability 0.26
```
26 seconds to language-detect a 0.5s clip. Later in the same session:
```
2026-08-03 21:37:11,487 INFO Processing audio with duration 00:03.424
2026-08-03 21:37:40,219 INFO Detected language 'en' with probability 0.98
2026-08-03 21:38:12,505 INFO transcript='Can you generate 20 random words for me, please?' ...
```
~29s then ~32s per step for a 3.4s clip. No traceback, no CUDA/cuBLAS
error, no exception anywhere in the log -- the operator manually exited
after observing the app was unresponsive ("GPU memory maxed").

**Window E, early segment** (21:52-21:56, mixed verdicts, ~1/min):
```
2026-08-03 21:54:13,287 INFO barge-in: sustained speech during playback -- stopping audio
2026-08-03 21:54:13,515 INFO AEC stats for last response: attenuation=9.0dB of ~15.3dB measurable  delay=222ms  [UNDER-CANCELLING: ~6.3dB of echo headroom remains]
```

**Window E, mid segment** (21:57-22:00, accelerated to ~4/min):
```
2026-08-03 21:58:15,306 INFO barge-in: sustained speech during playback -- stopping audio
2026-08-03 21:58:18,816 INFO barge-in: sustained speech during playback -- stopping audio
2026-08-03 21:58:22,257 INFO barge-in: sustained speech during playback -- stopping audio
2026-08-03 21:58:27,178 INFO barge-in: sustained speech during playback -- stopping audio
```
Four barge-ins inside a single 12-second window.

**Window E, late segment** (22:05:14-22:05:55, after a quiet 22:00:17-22:05:14
gap with zero barge-ins): 6 barge-ins in 41 seconds, 5 of 6 tagged
`NO ECHO DETECTED`:
```
2026-08-03 22:05:25,822 INFO AEC stats for last response: attenuation=5.6dB of ~-11.0dB measurable  delay=222ms  [NO ECHO DETECTED: barely any speaker sound is reaching the mic -- check the output device is audible; this is NOT a cancellation result]
2026-08-03 22:05:29,546 INFO AEC stats for last response: attenuation=5.9dB of ~-6.3dB measurable  delay=222ms  [NO ECHO DETECTED: ...]
2026-08-03 22:05:32,871 INFO AEC stats for last response: attenuation=7.2dB of ~1.1dB measurable  delay=222ms  [NO ECHO DETECTED: ...]
2026-08-03 22:05:43,544 INFO AEC stats for last response: attenuation=0.4dB of ~1.8dB measurable  delay=222ms  [NO ECHO DETECTED: ...]
2026-08-03 22:05:55,004 INFO AEC stats for last response: attenuation=4.1dB of ~2.8dB measurable  delay=222ms  [NO ECHO DETECTED: ...]
```
Contrast with the early/mid segments, which were dominated by
`UNDER-CANCELLING`/`FLOOR-LIMITED` (real echo present, sometimes
cancelled, sometimes not). By the late segment the AEC reference signal
itself is barely present (`~-11.0dB`, `~-6.3dB`, `~1.1dB measurable` --
several read as negative/near-zero, meaning the mic captured almost no
speaker output at all for AEC to even measure).

## Mechanism

**Backend correlation (diagnosed, not yet isolated).** The operator's own
framing going in was "backend isn't a contributing factor" -- the log
does not support ruling it out. Window A (claude-code) had the best
numbers of the night; every subsequent codex window (B, C, D) was worse
on every axis sampled; Window E (claude-code again, run last, after the
operator deliberately reverted the backend to isolate it) started as
well as Window A but degraded badly over its own 14 minutes (see below).
**Confound**: the backend switch (A -> B) happened at the same moment as
an STT `compute_type` swap (`default` -> `float16`) -- default already
resolves to float16 on cuda (`config.py`'s own compute_type help text),
so this was not actually a second STT variable, but the coincidence of
timing means this evening's data cannot cleanly separate "backend" from
"first codex session was also the first genuinely short session" as
explanations for B's elevated rate. This project's own UAT checklist
already documents backend-specific playback/interject-timing differences
(`[E7]`: multi-segment TEXT events from tool-calling backends previously
broke overlap-gate state; `[B1]`: codex interjects via `turn/steer`,
claude-code via a queued message with no true steering) -- a plausible
mechanism exists, it just hasn't been isolated with a clean single-variable
A/B yet.

**large-v3/float32 VRAM ceiling (the slowdown is validated-live; VRAM
exhaustion as the specific cause is a hypothesis).** No exception was
ever raised -- this project has a separate, well-documented cuBLAS-missing
failure mode from 2026-07-20 that fails fast and loud with a clear
RuntimeError, and this was not that. Instead, per-step latency ballooned
roughly 10-60x above every other session's cadence with zero error
signal. large-v3 is Whisper's largest model (1550M params); float32 uses
roughly double the VRAM of float16 for the same model. The absence of an
explicit CUDA out-of-memory exception is consistent with driver-level
memory-pressure throttling/paging rather than a clean allocation failure,
but this was not confirmed against `nvidia-smi` or any other independent
memory measurement during the incident -- the causal chain from "VRAM
pressure" to "30x per-step slowdown with no exception" is inferred, not
measured.

**The late-Window-E shift to `NO ECHO DETECTED`-dominant barge-ins is
unresolved.** `NO ECHO DETECTED` means the AEC reference signal is
barely present in the mic capture -- by definition, a barge-in under
this verdict cannot be a self-echo false-trigger (there is no echo for
AEC to have mistaken for speech). Two explanations were proposed live
and neither was confirmed before the operator stopped the session:
(1) these are genuine barge-ins (the operator talking over playback,
correctly detected as real speech, not a bug), or (2) the mic stopped
picking up speaker output partway through the session (volume, output
device, or physical positioning drift) -- which would be a real setup
problem unrelated to any of the evening's software variables. The
`delay=222ms` constancy through this segment argues against an AEC
config/estimator issue specifically; it does not distinguish between the
two explanations above.

**Ruled out:** AEC delay-hint drift on process restart (`[E8]`/`[E9]`'s
documented failure mode) -- delay converged to the same value instantly
on every one of the five restarts tonight, never drifted.

## What transfers

- **`stt.compute_type: default` resolves to `float16` on CUDA** (already
  documented in `scripts/settings_tui.py`'s help text) -- testing
  `default` and then explicit `float16` back to back on the same device
  is not two data points, it's one. (validated-live, confirms existing
  documented behavior)
- **large-v3 + float32 is not viable on this hardware (RTX 4060) --
  it silently degrades to ~30s/step rather than erroring, with no crash
  and no log signal beyond the response-time gap itself.** Worth an
  explicit warning or VRAM check before allowing this combination, since
  the failure is currently invisible except as "the app seems hung."
  (validated-live on the symptom; VRAM-exhaustion-as-cause is hypothesis)
- **No single variable (backend, STT model size, compute_type, or elapsed
  evening time) can be isolated as the cause of tonight's rate swings --
  every transition changed at least two of them at once, and the two
  same-backend windows (A and E) produced opposite within-session shapes
  (flat ~1/min vs. a 0->7/min ramp), which rules out backend alone as a
  sufficient explanation even though it looked correlated in the raw
  aggregate numbers.** The project's own prior findings (`[E7]`, `[B1]`)
  still make a backend-timing mechanism plausible in principle, and it
  remains worth a real controlled test -- but tonight's data does not
  support it as a standalone conclusion. (hypothesis, explicitly not
  diagnosed -- this was the operator's live correction to this note's
  first draft)
- **The barge-in counts and per-minute rates in this note conflate
  genuine barge-ins (operator deliberately talking over playback) with
  actual self-echo false triggers, and divide by wall-clock session time
  rather than actual-playback time** -- no attempt was made live to tag
  which events were which, and the log's available proxies (AEC verdict
  tag, spoken-echo-filter drop lines) don't cleanly separate them either
  (per `[G9]`, leaked echo has transcribed as real-sounding words before,
  so "not caught by the echo filter" isn't proof a trigger was genuine).
  **Treat every rate number in this note as a raw incident count, not a
  controlled measurement** -- a real comparative test would need to tag
  intentional-interrupt vs. passive-silence live, and normalize by
  playback-seconds, not session-minutes. (methodological gap, applies to
  this whole note)
- **A session's self-barge-in rate is not necessarily stable over its own
  lifetime, and the failure signature can change mid-session** -- Window
  E went from ~1/min (mixed UNDER-CANCELLING/FLOOR-LIMITED) to ~4/min to
  a late run dominated by NO-ECHO-DETECTED barge-ins, within one
  continuous 14-minute process with no config changes, while Window A
  (same backend, different STT model/time-of-evening) stayed flat for its
  full 11.5 minutes. Any future A/B that judges a config from only its
  first few minutes risks missing this kind of within-session drift --
  but note this observation is itself subject to the same event-tagging
  caveat immediately above. (validated-live that the *shape* differed
  between A and E; the drift's cause and whether it reflects a real
  reliability change rather than a change in operator behavior are both
  unconfirmed)
