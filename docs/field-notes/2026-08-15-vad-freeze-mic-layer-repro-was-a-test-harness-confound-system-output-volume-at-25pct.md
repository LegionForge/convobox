---
title: The "mic-layer freeze" reproduced this round -- zero utterances processed across three deliberate probes, real-time -- was a test-harness confound, not a segmenter bug -- macOS system output volume was at 25%, quiet enough that Piper-synthesized WAVs played over afplay never crossed the VAD speech threshold; at 70% the identical files triggered, tracked, and emitted correctly every time
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch fix/readline-stall-diagnostic-busy-state (off main), backend=codex, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), vad.threshold=0.5 (default)
evidence:
  - Live repro session (`/tmp/convobox-mic-trace3.yaml`, trace_silero_calls=true): a 31.4s long utterance, a marker phrase ~12s later, and a "decisive" short probe ("zebra yankee tango") were all played via `afplay` into the AIRHUG 28 input device. None produced a "Processing audio" line. Silero's own windowing loop kept firing normally (0.3-1.2ms/call, no gap) for 9+ minutes after the last successful utterance (08:02:01) through the point of investigation (08:11+), and separately a codex readline() stall grew past 560s with busy=False -- consistent with codex correctly idling because nothing was ever routed to it, not with codex or the event loop hanging.
  - Added temporary (now committed) diagnostic to `_process_window()`'s trace_silero_calls log line: prob, triggered, trailing_silence_windows, speech_windows. Restarted a fresh session with the same config and same test WAVs.
  - `osascript -e "get volume settings"` showed `output volume:25`. Replayed `/tmp/_long_utterance.wav` at 25%: max prob observed across the whole utterance was 0.057 -- never approached vad.threshold=0.5, `triggered` stayed `False` the entire time, zero utterances emitted, reproducing the exact symptom.
  - Raised to `osascript -e "set volume output volume 70"`, replayed the *identical* file: `prob` hit 1.000, `triggered` flipped `True`, and `Processing audio with duration 00:19.072` was logged 19.9s later (correctly capturing the full utterance plus trailing silence per segmenter design). Two more probes at 70% (`_marker_utterance.wav`, then a repeat) each produced a correct `Processing audio` line within seconds -- 3/3 clean at proper volume, 0/3 clean at 25%.
  - System volume restored to its original 25% after the test (left machine as found).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; explicit mid-session hypothesis -- "I was thinking there is some queueing going on. something asynchronous. like whisper getting chunks of text and processing them out of order. sometimes utterances come back minutes later" -- was the direct lead that prompted re-instrumenting the segmenter and re-running the repro carefully enough to catch this)
    - Claude Code (Anthropic claude-sonnet-5) -- capture, instrumentation, analysis, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T08:16:00-05:00
  revised: 2026-08-15T08:16:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The mic-layer freeze reproduced this round was a volume confound, not a bug

**Context.** JP's hypothesis this round was that STT/VAD processing
might be queueing or reordering utterances asynchronously -- an
unbounded `queue.Queue()` in `audio/capture.py` and a strictly
sequential, pull-based `_transcribe_with_timeout()` (with zero stall
diagnostic of its own, unlike every other pipeline layer) made this
structurally plausible. Investigating it directly triggered a live,
currently-reproducing freeze: a long utterance followed by two more
deliberate probes produced zero `Processing audio` lines over 9+
minutes, while the low-level Silero windowing loop kept running fine
underneath. That looked like exactly the bug this session has chased
all night.

## What the new instrumentation showed instead

Rather than a queueing or threading problem, the fix from the last
round -- `busy` state on the readline stall diagnostic -- already
pointed at codex being correctly idle (`busy=False` for 560+
seconds). That's the tell: codex had nothing to say because nothing
was ever routed to it, which only happens if the segmenter itself
never triggered. Adding `prob`/`triggered`/`trailing_silence_windows`/
`speech_windows` to the existing `trace_silero_calls` log line
(`src/convobox/vad/segmenter.py`, `_process_window()`) made that
directly visible: the whole time, `triggered` stayed `False` and
`prob` never exceeded ~0.06 against a 0.5 threshold. Silero was
computing continuously and correctly -- it was just never being handed
audio loud enough to look like speech.

`osascript -e "get volume settings"` explained why: system output
volume was at 25%. The synthetic test files are played over the Mac's
speakers via `afplay` and picked up acoustically by the AIRHUG 28
input device -- there's no direct audio routing, so playback volume
directly gates whether the mic ever sees a signal above noise floor.
At 70%, the identical WAV files triggered correctly every single time
(3/3), with `Processing audio` durations matching the source audio
length plus the expected trailing `min_silence_ms` padding.

## Why this matters

**This is a test-harness confound specific to the afplay-based
synthetic-playback method used for deliberate probes tonight**, not a
finding about ConvoBox's own code. The segmenter, capture queue, and
transcribe path all behaved exactly as designed once given a real
signal -- no evidence of queueing, reordering, or delayed-arrival
behavior as JP's hypothesis proposed. The hypothesis was still the
right thing to chase: it's what motivated adding trigger-state
visibility to the trace log, which is what caught this. A queueing bug
would have shown `triggered=True` with a stuck or slowly-growing
`speech_windows` and no `finish_run`; what was actually observed was
`triggered` never leaving `False` at all -- a completely different,
much more mundane signature once it was visible.

**This does not clear every "freeze" finding from tonight.** The
native-stack-sample root cause (codex blocked on its own stdin) and
the busy-state correction from the previous round are unaffected --
neither depended on afplay playback volume. But any finding tonight
that used `afplay`-synthesized WAVs as the audio source and did NOT
separately confirm output volume at the time should be treated as
unverified until re-run with volume confirmed loud enough to cross
threshold. The raw logs for those earlier rounds' repros were not
preserved (scratch-file convention, not committed), so this can't be
retroactively checked -- only re-run.

## The fix kept from this round

`_process_window()`'s `trace_silero_calls` debug line now includes
`prob`, `triggered`, `trailing_silence_windows`, and `speech_windows`
alongside the existing call-duration timing. This is a real, permanent
diagnostic improvement -- it's what made this round's finding legible
at all, and the previous three "freeze" investigations tonight had no
way to distinguish "never triggered" from "triggered and stuck" from
"triggered and legitimately still accumulating a long utterance."
Full test suite run not yet performed for this specific one-line
change (deferred to whoever picks up the PR); the change is additive
logging only, no control-flow impact.

## What transfers

- **A test harness that pipes synthetic audio through real speakers
  into a real mic is subject to system output volume as an invisible
  variable.** Any future audio-probe test should log
  `osascript -e "get volume settings"` (or equivalent) as part of its
  own evidence capture, not just assume a fixed level. (validated-live)
- **Adding state visibility (not just timing) to an existing trace
  diagnostic turned an ambiguous "it's stuck somewhere" into an
  immediately legible "it never triggered."** This is the same lesson
  as the busy-state fix from the previous round, applied one layer
  further upstream: measurement gaps compound across a pipeline, and
  each layer's diagnostic needs to answer "what state was I actually
  in," not just "how long did I take." (validated-live)
- JP's hypothesis (async queueing / out-of-order STT) remains
  unconfirmed and unrefuted as a general concern -- this round's
  specific repro attempt just turned out to be a different, unrelated
  problem. The unbounded `queue.Queue()` in `capture.py` and the
  zero-diagnostic `_transcribe_with_timeout()` in `run_convobox.py`
  are still real structural gaps worth closing on their own merits,
  just not what explains tonight's freeze data. (open)

## Not done here

- Re-running any of tonight's three earlier "severe" mic-freeze field
  notes' exact scenarios with output volume explicitly confirmed and
  the new trigger-state diagnostic in place, to see if any of them
  still reproduce under confirmed-good audio conditions. This is the
  natural next step and the highest-value one remaining on this
  thread.
- Adding a stall/backlog diagnostic to `_transcribe_with_timeout()`
  itself (still has none) -- still worth doing regardless of this
  round's outcome, since it's the one pipeline layer with zero
  visibility into whether it's genuinely slow vs. queued vs. hung.
- A programmatic pre-flight check (e.g., in the test harness itself)
  that verifies output volume is above some floor before trusting a
  "no utterance processed" result as a real finding -- would have
  caught this immediately instead of costing most of a round.
