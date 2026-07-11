# Barge-in / Interrupt Suggestions (ConvoBox UAT)

Notes from reading the audio + orchestration paths during UAT. Goal: make
"talk while the assistant is speaking" actually interrupt the assistant.

## Current behavior (as built)

Two distinct mechanisms exist:

1. **Hard stop (safeword)** — `orchestrator.py:50-57`
   `handle_transcript` calls `player.stop()` + `tts.stop()` immediately when a
   safeword matches. This cuts playback off the instant the phrase is spoken.
   ✅ Works today.

2. **Normal barge-in (just start talking)** — `scripts/run_convobox.py` main loop
   When an utterance overlaps with playback, the code does NOT stop the audio.
   It *drops the user's utterance* instead:
   ```python
   if player.is_playing() or utterance_overlapped_playback(...):
       log.info("dropped (overlapped response playback, no echo cancellation): %r", text)
       continue
   ```
   Comment explicitly says "(overlapped response playback, no echo cancellation)".
   So while the assistant talks, the user's speech is ignored, and the assistant
   keeps talking until its response is fully played. ❌ No true mid-speech cut.

3. **Echo filter** — `echo_filter.is_echo(text)`
   Text-based: drops input that matches ConvoBox's *own* recent synthesized
   speech. Not acoustic. If device-level AEC is absent, this is the only
   echo suppression in play.

4. **Mic capture constraints** — `src/convobox/audio/capture.py`
   `MicrophoneStream.start()` opens the `InputStream` with only
   `samplerate`, `blocksize`, `device`, `channels`, `dtype`. No
   `echoCancellation` / `noiseSuppression` / `autoGainControl` flags are set,
   so PortAudio/sounddevice defaults are used.

## Suggestion A — Real barge-in (stop playback on speech during playback)

Add a stop the moment VAD detects the start of an utterance while the assistant
is speaking. The cleanest hook is the main loop in `run_convobox.py`, before the
echo/quality gates:

```python
async for utterance in segmenter.segment(mic.stream()):
    if player.is_playing() or player.playback_ended_at is not None and not _settle:
        # True barge-in: cut the assistant off as soon as the user starts talking.
        player.stop()
        # (optionally also call await adapter.send_hard_stop() / send_interject
        #  if you want to abort the in-flight backend turn, not just the audio)
    result = transcriber.transcribe(utterance)
    ...
```

Considerations:
- Use `segmenter.in_speech` (already exposed in `vad/segmenter.py`) as an early
  trigger rather than waiting for a full utterance to be segmented. That shortens
  barge-in latency from "end of user's first sentence" to "first speech window".
- Decide the abort scope: stop audio only (assistant finishes thinking but is
  silent), or also `send_interject`/`send_hard_stop` to abort the backend turn
  (consistent with the safeword path). For a conversational barge-in, stopping
  audio + sending an interject is usually what users expect.
- Keep the `echo_filter` and `is_echo` checks so ConvoBox's own TTS bleeding
  into the mic doesn't instantly self-interrupt (a feedback loop).

## Suggestion B — Request acoustic echo cancellation at the device level

In `src/convobox/audio/capture.py`, set the AEC flags on the `InputStream`:

```python
self._stream = sd.InputStream(
    samplerate=self.sample_rate,
    blocksize=self.blocksize,
    device=self.device,
    channels=self.channels,
    dtype=_DTYPE,
    callback=self._callback,
    echoCancellation=True,
    noiseSuppression=True,
    autoGainControl=True,
)
```

Notes:
- These map to `PaStreamFlags`; support depends on the host API / device. On
  Windows WASAPI they are commonly honored. Log a warning if they are silently
  unsupported rather than assuming they worked.
- This complements (does not replace) the text `echo_filter`. Both layers are
  useful: AEC removes the acoustic echo; the text filter catches residual + the
  "my own words" case.

## Suggestion C — Expose barge-in behavior in config

Add a `barge_in: bool` (or `interrupt_mode: stop_audio | abort_turn | none`)
field to `config.py` so UAT can toggle between:
- `none` — current "drop overlapping input, keep talking" behavior,
- `stop_audio` — cut TTS only (Suggestion A, audio only),
- `abort_turn` — cut audio + abort the backend turn (safeword-like).

This makes the UAT matrix explicit and regression-testable.

## Open questions for UAT

- Do we want barge-in to abort the backend turn, or just silence audio?
- Should the safeword path and the normal barge-in path share the same abort
  logic (currently they don't — safeword aborts, normal input is dropped)?
- With no AEC at the device, is the text `echo_filter` good enough, or do we
  need acoustic AEC for reliable UAT sign-off?
- Does `utterance_overlapped_playback` (the trailing-silence window check) still
  make sense once barge-in stops audio immediately? It may now only matter for
  the brief overlap window before VAD fires.

---

## Review notes (Claude, 2026-07-11)

**Suggestion B is not implementable as written -- do not apply.**
`sounddevice.InputStream` has no `echoCancellation` / `noiseSuppression` /
`autoGainControl` parameters; those are WebRTC getUserMedia (browser)
constraint names, not PortAudio stream parameters. The proposed call
raises TypeError immediately. PortAudio exposes no AEC toggle on any
host API it supports.

**Suggestion A has a self-interruption hole as written.** Without
acoustic echo cancellation, "stop playback when VAD detects speech during
playback" cannot distinguish the user's voice from the assistant's own
voice arriving through the mic -- the TTS output itself trips VAD, and the
assistant would cut itself off moments into every response. The proposed
guard (the text-level echo filter) runs after transcription, seconds too
late to gate an immediate player.stop(). Conclusion: true barge-in is
blocked on real AEC, not on orchestration changes.

**Suggestion C is adopted as the target design** once AEC lands:
`interrupt_mode: none | stop_audio | abort_turn` in config, with
`stop_audio`/`abort_turn` only meaningful when echo cancellation is
active (or the user wears headphones -- worth a config note).

**The actual AEC path** (researched separately): the `aec-audio-processing`
PyPI package wraps WebRTC's audio processing module (AEC3 -- the same
canceller VoIP products use), BSD-3 licensed, ~1MB Windows wheels for
Python 3.11-3.13, with a far-end reverse-stream API that fits ConvoBox
perfectly since play_stream generates the reference signal sample-by-
sample. Integration sketch: tee playback chunks (resampled 22.05k->16k)
into APM's reverse stream; run mic chunks through process_stream before
VAD. Runtime cost: low single-digit %CPU, ~10ms added latency. The real
cost is room-specific tuning UAT.
