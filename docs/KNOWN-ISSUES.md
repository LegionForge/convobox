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

**Follow-up (2026-07-22): the same leak also surfaces as a bare numpy
`MemoryError`, which the `except RuntimeError` above did NOT catch --
now fixed.** Live-confirmed mid-UAT, testing Codex `approve` mode right
after PR #133 merged the full `permission_mode` design: an unhandled
`numpy._core._exceptions._ArrayMemoryError: Unable to allocate 1.15 MiB
for an array with shape (1, 376, 400)`, raised from `np.fft.rfft` inside
faster-whisper's own feature extractor (computing the mel spectrogram,
*before* ctranslate2's encode step ever runs) -- crashed the whole
session exactly like the original 2026-07-14 incidents this mitigation
exists to prevent. Confirmed via `_ArrayMemoryError.__mro__` that this
is a `MemoryError` subclass, not a `RuntimeError` -- an entirely
different exception hierarchy than what ctranslate2 itself raises for
the same underlying native-allocator pressure, so the existing catch
never had a chance of covering it. Both `LocalTranscriber.transcribe()`
and `_reload_model()` now catch `(RuntimeError, MemoryError)` together.
See `docs/field-notes/2026-07-22-native-allocator-leak-also-surfaces-as-numpy-memoryerror.md`
for the full writeup; `tests/test_transcriber.py::test_numpy_array_memory_error_is_recovered_not_raised`
covers it the same way the RuntimeError case already was.

**Follow-up (2026-08-02): the "recurring often enough to be disruptive"
condition this entry already flagged as worth revisiting -- now actually
hit live, on `large-v3`.** A `convobox-UAT` session (CPU fallback after a
separate CUDA-extra-not-installed gap, unrelated) hit the allocator
failure after ~9 successful transcriptions (~15 minutes) -- faster than
the original 2026-07-14 baseline (~20 transcriptions/13 minutes),
plausibly because `large-v3`'s much larger per-call native memory
footprint exhausts the leaking arena sooner than the smaller model that
first surfaced this bug. The mitigation above worked exactly as designed
(no crash, no unhandled traceback) -- but the reload it triggers **never
recovered**: every retry over the following 4+ minutes hit the identical
`mkl_malloc` failure, `self._model` stayed `None` for the rest of the
session, and every subsequent utterance was silently treated as unheard.
The mitigation's job was "don't crash," not "always recover," and it did
exactly that -- but this is the first live confirmation that once the
native allocator gets into this state, it can stay broken for the rest
of a session rather than self-healing on a later retry, which is worth
knowing before assuming "no crash" means "still working."

**Follow-up (2026-08-03, SOTA STT research pass): no upstream fix
exists, and this specific leak looks abandoned even though ctranslate2
itself is not.** ctranslate2's most recent GitHub release is **v4.8.1,
dated 2026-07-03** -- about a month old as of this research, one of five
releases in the preceding six months (v4.7.0 2026-02-03, v4.7.1
2026-02-04, v4.7.2 2026-05-19, v4.8.0 2026-06-06, v4.8.1 2026-07-03 --
the latest adding Gemma4 12B dense model support). The project itself is
actively maintained; what's missing across all of those releases is any
evidence of a fix for *this* leak specifically. faster-whisper issue
#390 is closed via PR #448, but that fix
could not be confirmed to specifically cover the MKL allocator leak
(vs. a narrower SageMaker-specific OOM); #660 shows no confirmed
resolution; a related, still-open issue (**#992**, "Memory on GPU not
cleared after transcription") suggests this is an ongoing pattern in the
library, not a one-off bug that got fixed. Community-circulated
workarounds (pinning to older ctranslate2 versions, e.g. 3.24.0 for
CUDA11/cuDNN8) were reported in the context of a *different* GPU-
allocation bug, not confirmed for this specific leak -- not a verified
fix. Practical implication: keep treating this as permanently unfixed
upstream rather than "unfixed for now" -- the reload mitigation (and
accepting that it can leave STT dead for the rest of a session once
triggered, per the follow-up above) is likely the durable state of
things, not a stopgap. The clean long-term fix, if this becomes worth
real effort, is moving off ctranslate2 entirely (see ROADMAP.md's
"Alternative local STT engines" -- the NVIDIA Parakeet TDT / `onnx-asr`
candidate runs on ONNX Runtime instead, sidestepping this whole class of
bug rather than working around it).

---

## VAD segmenter's per-window model call is synchronous with no offload/timeout -- can plausibly freeze the whole app

**Status:** diagnosed (2026-08-07) by reading the code after a live
recurrence, **not fixed, not yet forensically confirmed the same way the
related transcribe()-freeze finding was** (see
docs/field-notes/2026-08-06-resume-word-hallucination-and-runaway-repetition.md's
addendum for the live incident this came from). We're still testing
this one -- recorded now for transparency and so it isn't lost, not
because a fix is ready.

**Symptom, live-hit 2026-08-06/07, `stt.device: cpu`** (after a related
fix, PR #217, was already merged into the checkout): the app went
completely unresponsive -- multiple confirmed voice attempts produced
not even a `dropped (...)` log line, and the web UI's Stop-listening
button (a completely separate code path, an HTTP handler via uvicorn,
not the mic loop) also produced no log line and no effect. Zero
`Processing audio` lines appeared for the entire stuck window, meaning
the freeze happened *before* any utterance was ever handed to
`transcriber.transcribe()`.

**Why this is a different bug than the transcribe() freeze PR #217
already fixed:** that fix offloads `transcriber.transcribe()` to a
thread with an optional timeout -- it only helps once an utterance has
already been segmented. This incident's total absence of `Processing
audio` lines means the STT call was never reached at all.

**Root-cause candidate.** `UtteranceSegmenter._process_window()`
(`src/convobox/vad/segmenter.py`) calls `self._model(torch.from_numpy
(window), _SAMPLE_RATE).item()` -- a synchronous Silero VAD (ONNX)
inference call, made once per 512-sample (32ms) window, directly inside
`async def segment()`'s consumption loop (via `feed()`), with no thread
offload and no timeout. Same architectural shape as the transcribe()
bug PR #217 fixed -- a synchronous ML inference call that can freeze the
whole single-threaded event loop if it ever hangs -- just upstream of
it and far more frequent (~31 calls/second of audio vs. once per
completed utterance). `MicrophoneStream`'s own `blocksize: int = 512`
(`src/convobox/audio/capture.py`) matches `_WINDOW_SAMPLES` exactly, so
every mic chunk feeds exactly one VAD window through this same
synchronous path -- there's no batching that would reduce call
frequency at the chunk-consumption layer.

**Why not fixed yet, and the design wrinkle that makes this harder than
PR #217:** `transcribe()` is called once per completed utterance;
offloading it to `asyncio.to_thread()` per call is cheap relative to
its own cost. This model call happens ~31x/second -- offloading every
individual window call the same way would add real per-call thread-pool
overhead at that frequency, potentially comparable in magnitude to
Silero's own (very fast) inference time. The likely right fix is
offloading at the `feed()` (per-chunk) granularity rather than per-
window (the two are ~1:1 today given the blocksize match, but `feed()`
is the natural async/sync boundary `segment()`'s generator already
awaits at, and doesn't require reaching inside `_process_window()`) --
proposed, not yet built or benchmarked for the added-overhead tradeoff.

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

## AEC builds from source on macOS — PyPI just doesn't ship a wheel for it

**Status:** verified 2026-07-16 on Apple Silicon (M4, macOS 26.5). Not a
bug — a gap in what was previously assumed. `aec.py`'s docstring and the
`aec` extra's comment in `pyproject.toml` both said the AEC package's
"wheels are Windows-only today," which reads like a platform limitation
of the underlying code. It isn't: `aec-audio-processing`'s sdist
(`setup.py`) already has full Darwin build support wired in — it builds
`webrtc-audio-processing` (the same WebRTC APM/AEC3 engine used on
Windows) via meson into a `.dylib`, with `-DWEBRTC_MAC` and ARM64 NEON
flags already set, correct macOS rpath handling for the built dylib, the
works. PyPI just only hosts prebuilt `win_amd64` wheels for it (1.0.0,
1.0.1); nobody has published a macOS wheel, so a plain `pip install`
silently falls back to failing rather than to a source build succeeding.

**Verified working, zero code changes to `EchoCanceller`.** Build
prerequisites (`meson`, `ninja`, `swig` — none installed by uv/pip)
installed via `brew install meson ninja swig`; Xcode CLT's `clang` was
already present. Then:

```
uv pip install --no-binary aec-audio-processing aec-audio-processing
```

builds cleanly in about 30s and produces a working extension —
`AudioProcessor(enable_aec=True, ...)` constructs, `process_reverse_stream`/
`process_stream` run, and all 13 existing `tests/test_aec.py` tests pass
against the real binding (previously these could only run on Windows).

**What this unblocks.** Signal-level AEC — and therefore live
mic+speaker attenuation UAT, analogous to JP's 2026-07-15 Windows run
(see the NS/AGC entry below and `docs/UAT-checklist.md` **[E8]**/**[E9]**)
— can now actually be exercised on macOS. Before this, macOS testing of
the barge-in/self-interruption problem was necessarily software-only
(overlap-gate, text-echo-filter), since `EchoCanceller.__init__` raised
immediately without the package installed.

**Not yet done.** A live mic+speaker attenuation measurement on macOS
hardware (the Mac equivalent of JP's Windows UAT run) hasn't been run —
this entry only confirms the canceller constructs and passes its unit
tests here, not that it converges well against this machine's actual
room/speaker/mic acoustics. `docs/KNOWN-ISSUES.md`'s existing note below
(erratic 0.5-12dB attenuation on Windows) may or may not reproduce
identically on macOS; that's a separate, still-open question.

**Not done as part of this pass, deliberately:** publishing a macOS wheel
upstream, or vendoring/prebuilding one for this repo's CI — out of scope
for a documentation-only note; would need its own decision about where
built artifacts live and how they're kept in sync with the pinned
`aec-audio-processing` version.

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

---

## opencode 1.18.3: session-level model pin silently never generates (upstream)

**Status:** diagnosed live 2026-07-18, upstream bug, no fix available
(1.18.3 is the latest release as of this entry). ConvoBox's
`backend.model` feature is effectively dead against this server version.

**Symptom.** With `backend.model` set (e.g. `openai/gpt-5.4-mini`), a
voice session creates its opencode session and POSTs the prompt (both
200 OK, prompt `admittedSeq` returned) but no assistant message is ever
created -- ConvoBox waits out its 120s busy window and gives up. No
error appears in the session's message list, the session object, or the
server's own console output; the session's `time.updated` never
advances past creation.

**Isolated with curl against a live 1.18.3 server** (ConvoBox not
involved), same prompt in all cases:

- unpinned session -> assistant reply in seconds (server default model)
- session pinned `{"providerID":"openai","id":"gpt-5.4-mini"}` -> never runs
- pinned to the Zen twin (`opencode/gpt-5.4-mini`) -> never runs
- pinned with explicit `"variant":"default"` and/or `"agent":"build"` -> never runs

So the pin MECHANISM is broken, not any one provider/credential. The
shape ConvoBox sends is still exactly what the server's own OpenAPI spec
(`GET /doc`) declares for `POST /api/session`.

**Also broken in 1.18.3:** the server ignores config-level default
models for API sessions. With `"model": "openai/gpt-5.4-mini"` (and even
`agent.build.model`) set in `~/.config/opencode/opencode.json`,
`opencode run` correctly uses gpt-5.4-mini, but API-created sessions
still answer with the built-in Zen default (`hy3-free`). CLI and server
resolve the default differently.

**The dedicated model-switch endpoint is broken the same way (found in
the same investigation).** The server exposes `POST
/api/session/{sessionID}/model` (body `{"model": ModelRef}` per its own
spec) -- the endpoint opencode's internal model chooser uses. It returns
204, the session object then genuinely shows the new model, a
`model-switched` marker lands in the message list -- and a subsequent
prompt still never generates. Worse: after prompting a switched session,
`GET .../message` for it stops responding entirely and the server needs
a restart (wedged twice, reproducibly). So all three routes to a
non-default model -- pin at creation, switch endpoint, config default --
are dead in 1.18.3's server, while `opencode run -m` works fine.

**Workaround for now:** leave `backend.model` unset (voice sessions run
on the server's own default) and treat model choice as pending an
upstream fix. Re-verify with the curl matrix above after any opencode
upgrade before re-adding a pin. A ConvoBox-side model chooser (Settings
TUI field fed from `GET /api/model`, the same source the internal
chooser reads) is the right shape once upstream generation works --
deliberately not built while every choosable value produces a dead
session.

**CORRECTION (2026-07-18 late, deeper investigation):** the pin mechanism
itself WORKS -- a session pinned to a model the server has actually
loaded (verified live: `opencode/grok-code`) generates normally. The real
bugs are narrower and nastier: (1) the server's API path never loads the
OAuth-credentialed `openai` provider -- `GET /api/model` lists only
api-key/config providers (Zen, inception, ollama-remote) even with
`"openai": {}` forced into config's provider block and a valid,
unexpired OAuth token; (2) pinning any model absent from that loaded
catalog (all `openai/*`, and Zen models the server build doesn't carry
like `opencode/gpt-5.4-mini`) hangs the session silently instead of
erroring -- that's what every earlier "pin is broken" observation
actually was; (3) the `opencode run`/TUI request path DOES load and use
the OAuth provider (verified: `opencode run -m openai/gpt-5.6-terra`
created its session on this same server and answered), but that lazy
load never becomes visible to API-created sessions -- retested
immediately after, still dead. Net: an API client (ConvoBox) cannot
reach ChatGPT-Plus-OAuth models in 1.18.3 at all; it CAN pin any model
in `GET /api/model` (the Zen catalog: grok-code, kimi-k2.5-free,
minimax-m3-free, qwen3.6-plus-free, ...). Config default `"model"` is
also ignored for API sessions (always Zen `hy3-free`).

---

## Web UI: artifact pane gaps (0.3.0)

**Status:** diagnosed/scoped, deferred. The web UI (docs/WEB-UI-USAGE.md)
is new in 0.3.0 -- these are known rough edges, not silently-missed bugs.

**PDF doesn't render inline in the artifact pane -- confirmed intentional
v1 design, not a bug (resolved as a non-issue, per the ConvoBox quickref's
PR #176 entry, 2026-07-29; this entry itself never got updated to say
so).** The original 2026-07-28 report observed a PDF opened via
`GET /api/artifacts/{path}` showing nothing inside the pane's frame.
Re-checked directly against current code: `src/convobox/adapters/base.py`'s
`ARTIFACT_MEDIA_TYPES` already maps `.pdf` -> `application/pdf`, so the
serving route (`src/convobox/web/artifacts.py`) sets the correct
`Content-Type` via `FileResponse` -- the backend was never the gap. The
frontend (`index.html`'s `renderArtifact()`) deliberately does NOT put
PDFs (or CSV/txt/md) in an `<iframe>`, by design: only
`_ARTIFACT_IMAGE_EXTENSIONS` get an `<img>` and `_ARTIFACT_HTML_EXTENSIONS`
get a sandboxed `<iframe>`; everything else in the allowlist renders a
plain "Download {filename}" link instead, exactly matching
`docs/ARTIFACT-PANE-SCOPE.md`'s own documented v1 rendering scope ("PDF/
CSV/plain text -> punt to a simple embed/pre fallback or a download link;
not worth [building rich viewers for] v1"). So today's real behavior is a
working download link, not a blank frame -- the "shows nothing" symptom
either predates this fallback-link code (same commit that shipped it,
`b40146e`, 2026-07-28) or was testing the raw API URL directly rather
than the real pane UI. No fix needed; a richer PDF/CSV viewer remains a
legitimate future v2 idea, not an open bug.

**opencode/codex backends don't trigger the artifact pane at all.** Only
the Claude Code adapter has the `Write`/`Edit` -> `ARTIFACT` event wiring
(`src/convobox/adapters/claude_code.py`). See `docs/ARTIFACT-PANE-SCOPE.md`.
(codex's half of this gap has a schema-verified fix in flight -- see PR #219.)

**opencode's `file.edited` event: the payload shape is now known, and it
turns out to be a bigger wiring job than "verify the format," not a
smaller one.** (2026-08-07, schema-checked against a real local
`opencode serve` instance, v1.18.13 -- `GET /doc`'s OpenAPI 3.1 spec
fetched live, no prompt sent, no LLM call made, zero cost.) Two real
findings:

1. **The payload is trivial**: `FileEdited`/`EventFileEdited`'s schema is
   just `{type: "file.edited", data: {file: <path string>}}` (or
   `properties: {file: ...}` on the older `/event` variant) -- no
   status/confirmation field at all, unlike codex's `fileChange` (which
   has `inProgress`/`completed`/`failed`/`declined`). If it arrived on
   the adapter's existing stream, wiring it would be close to trivial.
2. **It does NOT arrive on the adapter's existing stream, though --
   confirmed from the schema, not guessed.** `OpenCodeAdapter.events()`
   subscribes to `GET /api/session/{sessionID}/event`, whose SSE payload
   is typed `SessionDurableEvent` -- a 28-member union (`SessionNext*`
   only: prompted, step/tool/text/reasoning lifecycle, compaction,
   revert). `file.edited` is NOT one of those 28 members. It only
   appears in the broader `Event` (`/event`, 89 members) and `V2Event`
   (`/api/event`, 88 members) unions -- i.e. it's a **global,
   not session-scoped** event. Wiring it up means a SECOND, separate SSE
   subscription (`/api/event` most likely, matching the versioned `/api/`
   surface the rest of this adapter already uses) running alongside the
   existing session-scoped one, not just a new case in the current event
   parser. There's also a real correlation question the schema alone
   doesn't answer: `file.edited`'s `data` has no session ID, only a bare
   path, so multiple concurrent opencode sessions (if that's ever a real
   ConvoBox scenario) would be indistinguishable on this stream --
   `GlobalEvent`'s own envelope carries `directory`/`project`/`workspace`
   fields that might be enough to scope it to "this adapter's own
   server," but that's an architecture question, not confirmed here.

**Not done, deliberately:** no code was written for this. This is schema
evidence clarifying scope, the same discipline as the codex investigation
(PR #219) -- next step for whoever picks this up is a real design call on
the two-subscription approach (worth its own small design note before
implementation, given it's a bigger change than codex's single-stream fix
turned out to be), not a blind port of the codex pattern.

---

## Web UI: a short CancelledError traceback can appear on quit/Ctrl+C

**Status:** mostly mitigated (2026-07-29), one small residual known and
accepted. `EventBroadcaster.close_all()` (`src/convobox/web/stream.py`)
eliminated the larger, more common source of this -- an open browser
tab's live-events SSE connection being force-cancelled at shutdown --
live-verified: zero "Exception in ASGI application" lines with a real
open SSE connection, versus several before.

**What's still possible.** uvicorn's own internal lifespan-handling task
(`starlette/routing.py`'s `lifespan()` -> `uvicorn/lifespan/on.py`'s
`receive()`) can still log a short `asyncio.exceptions.CancelledError`
traceback when the web server is torn down via `should_exit=True`
(`_stop_web_server`) rather than uvicorn's own normal signal-triggered
shutdown sequence. `run_convobox.py` has to drive shutdown this way
because it owns SIGINT/SIGTERM/SIGBREAK itself
(`_install_web_sigint_override` -- see that function's docstring for
why: `uvicorn.Server.serve()` steals those signals from Python's
default handler for as long as it's running, so ConvoBox has to
register its own handler after uvicorn's to reliably quit at all).

**Why not chased further.** This appears to be an inherent
characteristic of driving uvicorn's shutdown from outside its own
signal-handling flow, not a ConvoBox bug with an obvious fix --
resolving it fully would mean real surgery on uvicorn's own internal
lifespan-protocol driver, disproportionate to a cosmetic log line. The
process genuinely exits cleanly either way (live-confirmed: no
orphaned processes after either symptom).

**Mitigation:** `run_convobox.py`'s `main()` prints a plain console
reassurance ("ConvoBox exited cleanly...") right after a clean
--web quit/Ctrl+C, printed directly (not via `log.info`, which --tui
redirects to a file -- exactly where this wouldn't help) so it's visible
in the same place the traceback, if any, appeared.

---

## Kokoro can't synthesize past ~510 phonemes -- hard model limit, not a config/mode issue

**Status:** diagnosed (root cause 2026-07-24; confirmed against upstream
docs 2026-07-30), unfixed. Workaround: use Piper for long responses.

**Symptom.** Live-confirmed 2026-07-30 (JP, manual A/B while testing
Piper): Piper reads long text fine; Kokoro reliably fails at around
~500 phonemes. This is the same mechanism already root-caused 2026-07-24
in `KokoroTTSEngine.synthesize_stream` (`src/convobox/tts/kokoro.py`):
kokoro-onnx's own `create_stream()` runs a detached background task with
no exception handling; text producing more than the model's phoneme
limit raises `IndexError` inside that task (`voice = voice[len(tokens)]`),
the task dies silently, and the consumer's `await queue.get()` hangs
forever at 0% CPU. ConvoBox bounds the hang with a 30s timeout
(`_CHUNK_TIMEOUT_S`) that turns it into a catchable `RuntimeError`
instead of an indefinite hang.

**Root cause: a confirmed hard architectural limit, not a runtime mode.**
Web-checked 2026-07-30 against Kokoro-82M's model card and the
kokoro-onnx source: the model's context length is 512 tokens, and with
mandatory pad tokens at the start and end, the effective max is **510
phoneme tokens per synthesis call** -- consistent with the ~500 JP
observed. This isn't a batching mode or config flag ConvoBox is missing;
projects that give Kokoro long-text support (e.g. Kokoro-FastAPI) do it
by pre-chunking text client-side into windows well under the limit (its
own defaults: ~175-250 target tokens, 450 absolute max) and stitching
the resulting audio, not by raising a limit on the model itself.

**Not yet built:** that pre-chunking layer. PR #175 (merged 2026-07-30)
makes the failure *visible* -- surfaces it as a logged error plus a
`BackendEvent(ERROR)` instead of a silent gap in the transcript -- but
its own scope note is explicit that it does not make Kokoro handle the
long text; a real fix means splitting text into safe-sized chunks
before each `synthesize_stream()` call, which needs a live mic session
to verify audio quality across chunk boundaries (naturalness/pacing at
the seam). Discussed and deliberately deferred (2026-07-30): a simpler,
lower-risk alternative if this becomes worth revisiting is auto-routing
by estimated phoneme/char count (Piper for long text, Kokoro for short)
rather than chunking Kokoro itself -- same benefit, none of the
audio-seam risk. Worth full chunking only if Piper's GPL-3.0 licensing
later becomes a reason to keep everything on the permissively-licensed
engine.

---

## Backend can go silently busy for minutes with zero output -- root cause unconfirmed

**Status:** diagnosed live 2026-07-31 (claude-code backend), root cause
**not** confirmed. Recorded now so it isn't lost, not because a fix is
ready -- the concrete next step is re-running with `--verbose` next time
this recurs (see below), not a code change.

**Symptom.** Live UAT session, `convobox-UAT` checkout @ `20181be`,
`backend.name: claude-code`, `--tui --aec-dump`, default (INFO) log
level. Three silent-busy stretches in one session, each ending in real
spoken output rather than a crash, error, or reconnect:
- 18:47:52 -> 19:01:29 (**822s / ~13.7 minutes**), resolved with audio at
  19:01:41.
- 19:02:12 -> 19:03:37 (90s), resolved with a fresh turn at 19:04:01.
- 19:04:01 -> 19:08:13 (270s), resolved with audio at 19:08:39.

All three immediately followed a plain-text (no-tool-call) response --
the live backend itself, mid-session, characterized its own stuck turn
as "both following a plain-text response with no tool call." No file in
the working tree changed timestamp during the worst stretch (checked via
`find . -newermt "2026-07-31 18:47:00" ! -newermt "2026-07-31 19:02:00"`,
zero matches outside the always-updating log/AEC-dump files) -- consistent
with either genuine extended "thinking" with no tool use, or a stuck
state producing nothing at all. Both are equally consistent with the
evidence gathered so far.

**Why root cause is unconfirmed.** `WorkingIndicator`
(`scripts/run_convobox.py`) only observes `adapter.is_busy()` and
`player.is_playing()` -- by design, it never times out or takes action
itself (the safeword is the intended abort path), so a long heartbeat is
not itself a bug, just a faithful report that `is_busy()` stayed `True`.
At the default INFO log level, individual backend stream events (tool
calls, thinking deltas) aren't logged, so a genuinely slow backend turn
and a ConvoBox-side state bug (`is_busy()` failing to clear after the
backend actually finished) look identical after the fact -- there's
currently no way to tell them apart from `convobox-tui.log` alone. No
native `claude` session transcript was found for this run either (the
project's own `~/.claude/projects/` entry for this working dir has no
`.jsonl` matching the session), so that avenue didn't help this time.

**Next step, not yet done:** re-run with `--verbose` (DEBUG logging)
next time a stall like this happens, so tool-call/thinking-level events
are actually captured during the stall. If a future occurrence shows
real backend events streaming the whole time, that confirms genuine
long-running backend work (not a ConvoBox bug, just a UX/observability
gap worth its own fix -- e.g. surfacing *what* the backend is doing, not
just how long). If a future occurrence shows zero backend-side events
for minutes at DEBUG level too, that would point at a real `is_busy()`
staleness bug and justify a code investigation this entry didn't have
enough evidence to start.

**Current guidance (JP, 2026-07-30):** Piper for long responses; Kokoro
is fine for short conversational replies where phoneme count won't
approach the limit. No code change proposed by this entry -- documenting
the finding so the ~500 number isn't re-diagnosed from scratch later.

**Sources:** Kokoro-82M-v1.0-ONNX context length / 510-phoneme limit --
[Hugging Face model card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX);
chunking workaround precedent --
[Kokoro-FastAPI README](https://github.com/remsky/Kokoro-FastAPI/blob/master/README.md).

---

## A hard-stopped in-flight turn can show as a generic "error_during_execution" turn -- cosmetic mislabel

**Status:** diagnosed (first noted 2026-08-01 during PR #191's live UAT),
unfixed. Cosmetic only -- never logged via this project's own logging,
never spoken, and doesn't affect the hard-stop itself, which works
correctly. Scoped fix identified, not built.

**Symptom.** Live-confirmed again 2026-08-01 (`convobox-UAT` @ `3d9d4b9`,
`backend.name: claude-code`, `--tui --web`): a `[TUI]` turn labeled
`error_during_execution` appears whenever a hard-stop (pause or safeword)
interrupts an in-flight `claude-code` CLI call. Concrete example from
this session's `convobox-tui.log`:
- `20:06:29,364 transcript='Stop listing.' ... busy=False` -- STT
  mis-transcribed "stop listening" as "Stop listing.", which matched
  neither the pause phrase nor the safeword, so it was sent to the
  backend as a real (nonsensical) query.
- `20:06:35 - 20:06:36` -- a second attempt correctly transcribed as
  `'Stop listening.'`, matched the pause phrase, and hard-stopped the
  still-busy "Stop listing." call via `send_hard_stop()`.
- The interrupted `claude-code` CLI process's own interrupt-confirmation
  text is what surfaces as the `error_during_execution` turn -- it's the
  CLI's own output, not a real ConvoBox error, and it's real behavior
  visible in the TUI turn history, not written to `convobox-tui.log` via
  this project's own `log.*()` calls at all (confirmed: the exact string
  `error_during_execution` does not appear anywhere in the text log for
  this session, only in the on-screen TUI transcript pane).

**Root cause.** `claude-code`'s headless-mode interrupt path (see
`src/convobox/adapters/claude_code.py`'s own module docstring on how this
adapter builds hard-stop since there's no native per-call channel) emits
its own confirmation output when a call is interrupted mid-execution.
`_on_backend_event` in `scripts/run_convobox.py` has no special case for
this and falls through to the generic ERROR system-turn tag ([U10]'s
convention for session-level events worth showing inline), the same
fallthrough noted for [T6]'s TTS-failure-in-`--tui` gap.

**Not yet decided:** whether to give this its own recognizable turn label
(distinguishing "backend confirms it was interrupted, as expected" from
"something actually errored") or leave it as-is since it's cosmetic and
never misleads about whether the hard-stop itself worked. Web UI behavior
not yet separately confirmed -- this session's evidence is TUI-only.

---

## A misheard safeword can land on the pause phrase instead of the safeword -- same hard-stop effect, different resulting state

**Status:** diagnosed live 2026-08-01, not a safety gap, no fix planned
(STT-accuracy category, same underlying risk already noted for
`resume_word`/`pause_listening_phrases` in `docs/UAT-checklist.md`'s [P7]
enhancement idea). Documented so the distinction between "safe" and
"expected state" isn't lost.

**Symptom.** Live UAT, `convobox-UAT` @ `3d9d4b9`, `20:07:08`: an
utterance intended (per JP's own live report) as the safeword ("stop stop
stop") was transcribed by STT as `'Stop listening.'` instead --
```
20:07:08,019 Detected language 'en' with probability 0.97
20:07:08,570 paused listening (matched 'Stop listening.') -- hard-stopped
in-flight work; say 'Athena' to resume
```
Both `SafewordDetector` and `PauseListeningDetector` check the same raw
STT transcript (`docs/DESIGN-barge-in.md`, "Pause/resume listening" --
safeword checked first, then the pause phrase); when STT mishears one
configured phrase as a different, *also*-configured phrase, whichever one
the transcript actually matches is the one that fires. There's no gap
where the utterance is silently swallowed -- it always resolves to
whatever ConvoBox actually heard.

**Why this is not a safety gap.** The pause path calls the exact same
`send_hard_stop()` the safeword path does (see `scripts/run_convobox.py`'s
pause branch), so in-flight work is cancelled either way -- confirmed in
this same session, where the mis-heard "stop listening" correctly
hard-stopped the bogus in-flight "Stop listing." call from the entry
above. The real, user-visible difference is state, not safety: the
safeword returns to normal listening immediately, while landing on the
pause phrase instead leaves the session paused, requiring the resume word
before it hears anything else again -- an extra step someone reaching for
the emergency-stop phrase likely didn't intend.

**No fix proposed.** This is the same STT-reliability category already
tracked for `resume_word` (docs/UAT-checklist.md's [P7] entry: "STT is
unreliable enough live that one exact phrase can be hard to hit
reliably"), not a new problem this feature introduced. Worth keeping in
mind if `pause_listening_phrases`/`hard_stop_phrases` are ever tuned
closer together in pronunciation.
