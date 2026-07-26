---
title: A barge-in can fire against a response that never made a sound, because is_playing() means "thread started," not "audio is out"
status: validated-live
date: 2026-07-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ fdd1b76; src/convobox/audio/playback.py AudioPlayer
evidence:
  - convobox-UAT/convobox-tui.log, 2026-07-25 21:15-21:42 session (non-incident-capture)
  - convobox-UAT/.aec-dumps/20260725-211536/ (reference.wav, mic-raw.wav, mic-processed.wav)
  - src/convobox/audio/playback.py:177-263 (AudioPlayer.play_stream / is_playing)
  - scripts/run_convobox.py:292-339 (BargeInMonitor.observe)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live voice UAT, flagged the anomaly live mid-session)
    - Claude Code (Anthropic claude-sonnet-5) — log/code analysis, cross-correlation verification, writing
    - OpenAI Codex (OpenAI GPT-5) — unrelated same-night commit (fdd1b76) whose incident-capture
      feature supplied the bounded recordings this investigation used; not involved in this finding
  org: https://legionforge.org
  created: 2026-07-25T22:20:00-05:00
  revised: 2026-07-25T22:20:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A barge-in can fire against a response that never made a sound

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents: mic and speakers are open simultaneously, and a
"barge-in" lets the user interrupt the assistant's spoken response
mid-sentence. Whether a barge-in is even considered is gated on
`player.is_playing()` — "is a response currently being spoken."

## Problem

During a long live UAT session testing an unrelated VAD-threshold
change, two `"barge-in: sustained speech during playback -- stopping
audio"` events fired back-to-back with no plausible audible content to
interrupt. The operator, live, correctly flagged one of them ("You
detect the barge in there.") as suspicious before any log analysis had
happened.

## Evidence

```
21:31:02,634 INFO response: Exactly. Let's use normal back-and-forth speech...
21:31:05,858 INFO barge-in: sustained speech during playback -- stopping audio
21:31:06,088 INFO AEC stats for last response: attenuation=6.8dB of ~1.0dB measurable ... frames(reverse=22110, capture=92489)
```

and, seconds later:

```
21:31:19,662 INFO response: Yes -- that interruption was detected cleanly.
21:31:20,173 INFO barge-in: sustained speech during playback -- stopping audio
21:31:20,208 INFO AEC stats for last response: attenuation=n/a of ~? measurable ... frames(reverse=22110, capture=93920)
```

`reverse` (the AEC reference/output frame counter) is **identical**
across both `AEC stats` lines bracketing that second "interrupted"
response — `22110` both times. Zero TTS reference frames were ever fed
between the response's text becoming available (21:31:19.662) and the
barge-in firing 0.5s later (21:31:20.173). No audio reached the
speaker. There was nothing to interrupt, yet the log reads exactly like
a normal echo-triggered barge-in.

## Mechanism

```python
# src/convobox/audio/playback.py
async def play_stream(self, chunks, sample_rate) -> None:
    self.stop()
    self._stop.clear()
    feed: queue.Queue[...] = queue.Queue()
    self._thread = threading.Thread(target=self._run_stream, args=(feed, sample_rate), daemon=True)
    self._thread.start()          # <-- is_playing() is True from here
    ...

def is_playing(self) -> bool:
    return self._thread is not None and self._thread.is_alive()
```

`is_playing()` reports `True` the instant the playback thread object
exists and is alive -- not when the first real audio block reaches
`stream.write()`. The reference feed used for AEC and incident capture
(`on_block_played`, called at `playback.py:241`) only fires once a real
chunk has been resampled and written; it is a strictly later, gated
event. Between those two points there is a real window -- TTS
synthesis latency, typically well under a second but nonzero -- where
`is_playing()` is `True` and the reference stream is completely silent.

`BargeInMonitor.observe()` (`scripts/run_convobox.py:321`) has no
visibility into any of this; it fires purely on `(segmenter.in_speech,
playing, chunk_ms)`. If the user keeps talking (entirely normal in
rapid back-and-forth conversation) during that silent pre-audio window,
the monitor correctly does its job by its own inputs and reports a
barge-in -- against a response that was never actually audible.

**Ruled out**: acoustic echo or VAD oversensitivity. The flat `reverse`
frame count rules out echo outright (there was no reference signal to
echo), and this is a different, code-level mechanism from `[E8]`'s
stale-delay-hint case or `[G3]`'s self-echo case.

**Functionally**, stopping the about-to-play response and accepting the
user's new words is the *correct* outcome under
`interrupt_preset: conversational` (`on_new_words: now`). The defect is
purely diagnostic/UX: the log line and the operator's real-time
perception both read "the assistant interrupted itself," when nothing
was ever interrupted.

## What transfers

- **A "thread started" boolean is not the same fact as "output is
  flowing," and any barge-in/interruption gate built on the former will
  misfire during synthesis latency.** This generalizes past ConvoBox:
  any voice pipeline that gates "am I currently speaking" on a
  playback-thread liveness flag rather than a first-real-frame marker
  has this exact race available to it. (validated-live)
- **When a "self barge-in" report doesn't correlate with any measurable
  reference audio (flat/absent reverse-frame count), stop looking at
  AEC or VAD sensitivity — check the state machine's readiness signal
  instead.** This is a different diagnostic branch point from the
  existing `[E8]`/`[G3]` echo-specific checks in
  `docs/UAT-checklist.md`, worth checking first since it's a much
  cheaper thing to rule in or out (one log line) than a cross-correlation.
  (validated-live)
- **Fix direction (not yet implemented):** distinguish "armed, no audio
  yet" from "audibly playing" in `AudioPlayer`, e.g. a separate
  `has_audible_output` flag flipped at the same point `on_block_played`
  first fires, and gate `BargeInMonitor` (or at minimum the log
  message) on that instead of raw thread liveness. (hypothesis)
