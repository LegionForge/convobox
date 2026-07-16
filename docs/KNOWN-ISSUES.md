# Known issues

Diagnosed problems we've chosen to defer, with enough detail to pick up
without re-investigating. Fixed issues move out of here into the changelog /
PR history.

---

## faster-whisper's native allocator can fail during a long session

**Status:** mitigated (2026-07-14), root cause is upstream and unfixed.
`LocalTranscriber` (`src/convobox/stt/transcriber.py`) now catches this and
recovers automatically -- see below. This entry documents the underlying
cause for anyone debugging a recurrence or deciding whether to chase a real
upstream fix later.

**Symptom.** Live-confirmed 2026-07-14: a real ~13-minute UAT session
(claude-code backend, ~20 transcriptions in) crashed the whole
`run_convobox.py` process with an unhandled `RuntimeError: could not create
a memory object`, raised from inside `WhisperModel.transcribe()` ->
`detect_language()` -> `self.model.encode()`. Independently reproduced the
same failure class this same session while live-verifying a detector's
default vocabulary via a throwaway TTS->STT round-trip script: repeated
`transcribe()` calls in one long-lived process eventually failed with
`mkl_malloc: failed to allocate memory` (a different message, same
underlying allocator exhaustion), reproducible even in a fresh process
with system RAM never actually low (26GB free throughout, confirmed via
`Get-CimInstance Win32_OperatingSystem`) -- ruling out simple system-wide
memory pressure as the cause.

**Root cause: known, unresolved upstream issue, not a ConvoBox bug.**
ctranslate2's native (MKL on Windows) allocator leaks memory across
repeated inference calls in a long-lived process -- documented in
SYSTRAN/faster-whisper#660 ("Faster whisper holding memory not releasing
it, killing the flask server") and #390 ("Memory Leak investigation"),
both open/unresolved as of this writing. Not something Python-level
`gc.collect()` can fix, since the leaked memory is native (C++) heap, not
Python-managed objects.

**Mitigation shipped.** `LocalTranscriber.transcribe()` catches
`RuntimeError` around the model call, logs a warning with the real
exception and traceback (nothing silently swallowed), reloads the
`WhisperModel` (resets its allocator state -- the practical workaround for
this whole class of native-library leak), and returns an empty
`TranscriptResult` so the failed utterance is treated as unheard/dropped
by the normal low-confidence-transcript gate rather than crashing the
process. One lost utterance instead of a dead session. `model_factory` is
injectable for tests (`tests/test_transcriber.py`), so the recovery path
is unit-tested without needing to actually reproduce the native failure.

**Why not "actually fix" it.** The leak is inside ctranslate2's C++
runtime, several layers below anything ConvoBox's Python code controls --
not fixable here. Worth revisiting if a future ctranslate2/faster-whisper
release resolves the upstream issue, or if the reload mitigation itself
turns out to be insufficient (e.g. recurring often enough within a single
session to be disruptive) during a longer live-mic UAT pass than this
session's own testing has covered.

**Follow-up (2026-07-14): the reload used to make things worse under
load, now fixed.** Found live while investigating an unrelated UAT log
that surfaced an unexpected `huggingface.co` call: `WhisperModel(...)`
construction makes a real network request by default (a model-revision
freshness check) *even when the model is already fully cached* -- and
since every allocator-failure recovery above calls the exact same
construction path, a session recurring the native-allocator bug several
times would ALSO re-attempt that network call on every single recovery,
right when things are already degraded, with no guaranteed timeout.
`_build_whisper_model()` now tries `local_files_only=True` first,
falling back to the network only if nothing is cached yet (first-time
setup) -- every recovery after the first successful load is now fully
offline. See the commit message on the fix for verification details.

**Follow-up (2026-07-14): the recovery ITSELF could crash the process --
now fixed.** JP hit this live, mid-UAT, and reported it directly ("malloc
error... I thought I had enough memory"): the reload path
(`self._model = self._model_factory()`) was not itself wrapped in a
try/except. When the reload's OWN `WhisperModel` construction hit the
same native-allocator failure -- not a hypothetical, this is exactly
what happened in JP's session, a second, unhandled
`RuntimeError: mkl_malloc: failed to allocate memory` raised from
`ctranslate2.models.Whisper.__init__` -- it propagated all the way up
through `asyncio.run(run(args))` uncaught and killed the whole voice
loop, exactly the crash this whole mitigation exists to prevent.

Two changes:
1. `LocalTranscriber._reload_model()` now wraps the factory call in its
   own try/except. On success, `self._model` holds the new model as
   before. On failure, `self._model` is set to `None` (not left pointing
   at the old, still-broken instance) and the transcriber stays in a
   degraded-but-alive state; the NEXT `transcribe()` call detects
   `self._model is None` and retries the reload automatically -- no
   background timer, no permanent breakage, bounded by real utterances
   rather than a busy-retry loop.
2. The old model reference is dropped and `gc.collect()` is called
   **before** rebuilding, not after (or never). While `self._model`
   still pointed at the broken instance during the old reload code,
   calling the factory again meant asking the allocator to hold both the
   old and new model's native memory simultaneously -- exactly the wrong
   move when the allocator is already under enough pressure to be
   failing. This doesn't touch the underlying LEAK (still native C++
   heap, still not something Python GC reaches, per the existing
   explanation above) but it does reduce peak usage during the reload
   window itself, which is a real, distinct lever.

**Also added: a memory diagnostic in the failure log lines**
(`_memory_diagnostic()`), directly answering the question a tester asks
the moment they see "failed to allocate memory" -- Windows-only
(`ctypes` + `GlobalMemoryStatusEx`, no new dependency), reports real
available RAM, and if it's comfortably high, says outright that this
looks like the known allocator quirk rather than a real shortage
(matching this issue's own already-confirmed 26-28GB-free pattern from
earlier in the same session) -- no separate out-of-band check needed the
next time this recurs.

---

## WASAPI output plays speech an octave too high ("static chipmunk")

**Status:** deferred (2026-07-12). Mitigation: use an **MME** output device.
WASAPI is documented as low-latency-but-finicky in
`scripts/audio_devices.py` and `docs/DESIGN-echo-and-barge-in.md`.

**Symptom.** With a WASAPI output device pinned (e.g.
`Headphones (Realtek(R) Audio), Windows WASAPI`), TTS playback is pitched up
about an octave with a static/gargle over it. The tester's exact
description across three UAT runs: *"the speech frequency is doubled but the
speech rate is right"* — i.e. **pitch up an octave, tempo correct.** MME and
DirectSound outputs on the same machine are clean.

**Two distinct causes — one fixed, one not.**

1. **Static at the seams — FIXED** (streaming resampler, this same work).
   Streaming playback resampled each TTS chunk in isolation, injecting a
   phase discontinuity at every chunk boundary. Inaudible at an integer
   device ratio (22050→44100, MME) but clicking at a non-integer ratio
   (22050→48000, any 48 kHz WASAPI device). Fixed by `_StreamResampler`
   (`src/convobox/audio/playback.py`): per-chunk RMS error vs a whole-buffer
   resample dropped from 0.024 to ~0 at 48000. This removed the *clicky*
   component but not the octave shift.

2. **Octave-up pitch — NOT FIXED.** Tempo-correct + pitch-doubled is the
   textbook signature of **mono audio mishandled on a stereo device** at the
   channel layer, inside PortAudio's WASAPI shared-mode conversion — below
   ConvoBox's Python. The player opens the stream `channels=1` and writes a
   mono buffer; the Realtek WASAPI endpoint's shared mix format is stereo
   48 kHz, and PortAudio's mono→stereo path appears to reinterpret rather
   than duplicate the samples on this driver.

**Evidence.**
- Offline frame-count tests show playback writes the *correct* number of
  frames at 48000 (implied duration == true duration), so it is **not** a
  sample-rate/resampling error — those change tempo, which is correct here.
- `AudioPlayer.play()` and `play_stream()` both produce correct-duration
  output numerically; the corruption is only audible from the physical DAC.
- Could not auto-measure the emitted pitch: this sounddevice build's
  `sd.WasapiSettings` has no `loopback` kwarg, so WASAPI loopback capture
  (which would confirm 440 Hz → ~880 Hz) is unavailable here. Diagnosis
  rests on the tempo-correct-pitch-doubled acoustic signature.

**Candidate fix (untried).** Open the output stream at the device's **native
channel count** and upmix mono→N ourselves (duplicate the sample across
channels) instead of relying on PortAudio's WASAPI mono conversion. Care
required: the AEC far-end reference (`AudioPlayer.on_block_played`) must stay
**mono** at the device rate — feed the canceller the pre-upmix mono block,
not the interleaved stereo one. Verify with the tester's ear (or a working
loopback capture) before trusting it, since the last three WASAPI fixes each
looked right offline and still needed a live listen.

**Why deferred.** MME output works cleanly today and 183 ms of output
latency is fine for the prototype. WASAPI's ~22 ms is an optimization, not a
blocker, and the fix touches the playback core plus the AEC reference — worth
doing carefully, not rushing mid-UAT.

---

## WebRTC APM's noise suppression / auto gain control are unused (candidate, awaiting go-ahead)

**Status:** candidate, not built. Offered to JP directly (2026-07-15
evening, in response to his live report that mic+speaker AEC is still
leaking despite the delay-hint fix); awaiting his go-ahead before
touching this. The original "why not now" reasoning below is stale (it
predated the extensive AEC investigation this session has since done)
and is kept for history, not as the current blocker.

**What's there but unused, with more real detail than previously
recorded.** `EchoCanceller.__init__` (`src/convobox/audio/aec.py`)
constructs `AudioProcessor(enable_aec=True, enable_ns=False,
enable_agc=False, enable_vad=False)`. Re-inspected the installed
package's real constructor signature (2026-07-16, `inspect.signature`,
not assumed): `AudioProcessor.__init__(self, enable_aec=True,
enable_ns=True, ns_level=2, enable_agc=True, agc_mode=1,
enable_vad=True)` -- the binding's OWN defaults have NS and AGC ON,
with real tunable aggressiveness parameters (`ns_level`, `agc_mode`)
neither previously documented here nor exposed anywhere in ConvoBox.
`aec.py` deliberately overrides both to off. This means a future PR
needs to pick real values for `ns_level`/`agc_mode`, not just flip two
booleans -- worth live-testing a couple of settings rather than
guessing at the "right" level, same discipline as everything else this
session has verified against real hardware before committing to it.

**Why this might matter, concretely, not speculatively.** AGC directly
targets an already-documented, real finding: PR #74's live hardware
smoke test (`probe_audio()`, Settings TUI) reported `"mic: ... very
quiet -- raise the input gain or move closer"` against this machine's
actual default mic -- the exact condition AGC exists to correct. More
recently (2026-07-15 evening), JP's own live mic+speaker UAT log showed
persistently erratic, often poor echo attenuation (0.5-12dB, swinging
response to response) even with a correct AEC delay hint -- a genuinely
hard open-air acoustic coupling problem, not a leftover config bug (see
`docs/UAT-checklist.md` **[E8]**/**[E9]**). NS/AGC won't fix delay
estimation, but AGC in particular could reduce how hot the mic signal
runs from close speaker proximity, which plausibly makes AEC3's own
adaptive filter's job easier -- untested, not asserted as a fix.

**Original "why not now" reasoning (2026-07-14, superseded, kept for
history).** This touches the exact same `AudioProcessor` construction
JP was then actively mid-assessment on for a different reason (his own
PR #78 `[L3]` finding: AEC produces artifacts and drops real barge-in
with a headset, "recorded for assessment," not yet decided at the
time). That assessment has since resolved through extensive live UAT
(`[L4]`-`[L6]`, `[E8]`, `[E9]`) -- the attribution-ambiguity concern
that justified waiting no longer applies. The live JP go-ahead question
is the only remaining gate now.
