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

**Status:** still open, **escalated 2026-08-12 -- likely two distinct
bugs, not one.** PR #269 (2026-08-12) targeted this bug's then-leading
hypothesis (thread-pool contention) and did not fix it -- live re-tested
the same day, three clean reproductions, #269's own new stall diagnostic
never fired once. A same-day follow-up session then caught **two real
short capture stalls (1-4s, confirmed zero queue backlog -- not the
"backlog piling up" hypothesis, a genuine brief capture-callback hiccup,
now directly observable for the first time)**, and separately, **a 12+
minute freeze that resisted every recovery path tried** (web resume, the
hard-stop API, even killing a hung backend subprocess that was itself
stuck at the time) -- only a full process kill ended it. CPU forensics
during that long freeze (target process pinned at a literal, sustained
0% CPU) point at a genuine blocking wait with no timeout, most likely in
backend-subprocess I/O, not the VAD/capture layer at all. **Treat this
as safety-relevant and unresolved -- not a release candidate until at
least the long-freeze variant is understood.** Full evidence in both
2026-08-12 field notes linked below.

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

**Follow-up (2026-08-07, same day, later): recurred live a second time,
with real-time confirmation it blocks BOTH safety-relevant control
paths at once, and that it self-recovers.** JP hit this directly while
paused, following a runaway-repetition hard-stop (see the field note's
newest addendum for the full transcript): the "stop"/"eject" safeword
phrases had no effect, the web Stop button had no effect, and the web
Resume Listening button also had no effect -- reported live, in that
order, while it was happening. `convobox-tui.log` confirms genuine,
total silence for exactly 2m9.4s (18:41:50.939 -> 18:44:00.358), then a
`resumed listening (web UI)` line with no process restart in between.
**Two new findings this recurrence adds:**
1. Voice safeword, the web `/api/stop` handler, and the web
   `/api/listening` resume handler are THREE genuinely different code
   paths (a mic-loop hook and two separate HTTP routes) -- all three
   going unresponsive together is itself strong corroborating evidence
   for the shared-event-loop-blocked hypothesis above, gathered in real
   time while it was actively happening, not reconstructed from logs
   after the fact. Raises this from "diagnosed by reading the code" to
   "diagnosed by reading the code, with live behavioral confirmation
   matching the prediction."
2. **This instance was not permanent -- it self-recovered after
   2m9.4s with no kill/restart.** Operational guidance until this is
   actually fixed: waiting it out for a couple of minutes is a real,
   confirmed-working option, not just "kill the process" (killing is
   still reasonable if immediate control matters more than waiting on
   an unconfirmed recovery -- this is one data point, not a guarantee
   every recurrence resolves this fast).

**Priority raised** given both control paths that exist specifically
for safety (the safeword AND the web Stop button) failed simultaneously
in a real session -- worth prioritizing the `feed()`-granularity
offload fix proposed above over other STT/VAD polish work.

**Fix implemented 2026-08-07 (schema/unit-level; not yet live-validated
against a real recurrence).** New `UtteranceSegmenter.feed_async()`
(`src/convobox/vad/segmenter.py`) wraps the existing synchronous
`feed()` in `asyncio.to_thread()`, at exactly the `feed()`-granularity
proposed above. `segment()` (the mic loop's only real-time streaming
consumer) now awaits `feed_async()` instead of calling `feed()`
directly; `feed()` itself is unchanged and still synchronous, so every
existing caller (tests, any offline/non-realtime processing) keeps
identical behavior.

Deliberately **not** a timeout/abandon/invalidate mechanism like PR
#217's analogous STT fix: `transcribe()` is stateless per call, but
Silero's model carries sequential recurrent state across windows via
`reset_states()`, and abandoning an in-flight window while its
background thread still runs risks that thread's eventual completion
racing a fresh call against the same (not documented as thread-safe)
model object. Plain thread offload alone already addresses the
documented symptom -- other event-loop tasks (the web server's HTTP
routes, the watchdog, TUI redraw) stay responsive while a slow/stuck
window call runs in its own thread -- without introducing that new
race.

New test `test_feed_async_does_not_block_other_concurrent_work`
(`tests/test_vad_segmenter.py`) proves the mechanism the same way PR
#217's `test_timeout_does_not_block_other_concurrent_work` did: a
model call blocked via `time.sleep()` inside a worker thread does not
prevent concurrently-scheduled `asyncio.sleep()` ticks from firing on
the event loop. Full suite green (1273 passed), `ruff`/`mypy` clean on
the touched files.

**Still needed before this can be marked resolved**: live
re-verification against a real recurrence of the freeze (the same gap
PR #217's own field note flagged for its STT-side fix) -- this is
unit-proven-correct, not yet confirmed to actually prevent the next
live Stop/Resume-button lockup.

**Follow-up (2026-08-07, live UAT with this fix applied): the freeze
recurred, and the result is a genuine partial improvement, not a full
fix -- worth being precise about which part actually changed.** JP ran
a live voice UAT session on a branch combining this fix with PR #230's
STT changes, deliberately stress-testing pause/resume cycling. The
freeze recurred: real, active speech produced zero log activity
(`convobox-tui.log`, 20:57:40 -> 20:59:32, ~1m52s) -- confirmed live by
JP ("was hung for a few minutes... but had to manually resume
listening"; "during the gap, I was trying some utterances[,] but
stopped [trying] until a few minutes later"), i.e. this was not silence
being mistaken for a freeze, it was real speech the mic pipeline never
processed.

**What's different from the original incident, and why it matters:**
JP recovered by clicking the web UI's Resume Listening button, **and it
worked** -- in the original 2026-08-07 incident this follow-up's
sibling entry documents, all three recovery paths (voice safeword, web
Stop, web Resume) were simultaneously unresponsive for the same
2-minute-class duration. This time only the mic/voice path was stuck;
the web route stayed alive and functional. That is exactly what this
fix's own design claims -- offloading `feed()`'s Silero calls to a
worker thread keeps the *rest of the event loop* (HTTP routes, the
watchdog, TUI) responsive while a slow/stuck window call runs -- and
this live recurrence is the first real evidence that claim holds, not
just the unit test's proof of the mechanism in isolation.

**What the fix was never going to solve, and didn't:** `segment()`'s
own consumption of incoming mic chunks is still strictly sequential --
`await self.feed_async(chunk)` blocks that specific async generator
until the offloaded call returns, no matter which thread it runs in.
If one window's Silero call genuinely hangs, no *later* audio can be
processed until it returns, regardless of threading. This recurrence is
consistent with that being exactly what happened: the mic pipeline
itself stayed stuck for ~2 minutes while the rest of the app didn't.
**Net: this fix contains the blast radius (proven, live, this session)
but does not resolve the underlying hang (still reproduces, live, this
session) -- "partially validated," not "validated" or "insufficient."**

**Root cause of the underlying hang is still unconfirmed.** Not
determined this pass: whether it's genuinely Silero's own ONNX
inference stalling (OS scheduling, resource contention, a driver-level
stall), or something else entirely that this fix's instrumentation
can't currently distinguish from that. The immediate diagnostic gap:
there is no logging of when a `feed_async()` call starts, how long it
takes, or that it's still pending -- a future recurrence produces the
same "silence, then it's back" signature regardless of what's actually
stuck inside that await. Next concrete step, not done here: add
start/elapsed timing around the `asyncio.to_thread(self.feed, chunk)`
call in `feed_async()` (e.g. log a warning if a single call exceeds
some multiple of the ~1-3ms Silero normally takes), so the next
recurrence's own log distinguishes "one window call is still running,
N seconds in" from the current signature's total silence.

**JP's own real-time qualitative read, same session, worth recording
verbatim-close:** "stop and resume listening seem to be significantly
more reliable... assuming 1) I don't pound the paused client with lots
of hotwords, and 2) I don't spam the client while paused with lots of
conversation." Two things line up with this: the recurrence above
happened during a deliberate rapid-fire pause-overload stress test
(short repeated hotword-biased phrases, utterances arriving every few
seconds), and both this fix and PR #217's now both offload onto
`asyncio.to_thread()`'s shared default executor -- a real, untested
hypothesis for a follow-up session: sustained high-rate utterances
during a pause could be piling up VAD and/or STT thread submissions
faster than they drain, which would produce exactly this "fine under
normal use, hangs under rapid-fire stress" pattern without requiring
Silero itself to ever actually stall. Not confirmed -- a concrete lead
for whoever picks up the diagnostic-logging step above, not a
diagnosis.

**Follow-up (2026-08-12): PR #269 shipped for exactly this hypothesis,
same-day live re-test shows it did not fix the freeze, and its own new
diagnostic never fired.** PR #269 gave the thread-pool-contention theory
above a concrete mechanism (two indefinite blockers -- mic capture's
queue read and Piper's chunk pump -- competing with VAD/STT for the
shared default executor) and dedicated executors for both, plus a
queued-vs-running split added to `feed_async()`'s own stall warning
specifically so a recurrence would show which of the two was actually
happening.

Live re-tested the same day, immediately after merge: three clean
reproductions in ~15 minutes of the same rapid-fire-hotwords-while-paused
stress that produced the original incidents (durations 52.8s/72.7s/60.7s;
web UI Resume Listening recovered all three immediately, voice resume
did not work during any of them). `feed_async()`'s stall warning never
fired once. Since that warning is inside the exact code path #269's fix
targeted, its silence across three real occurrences is evidence the
stall isn't there -- not just an inconclusive result.

New leading candidate: `MicrophoneStream.stream()`'s own blocking
`queue.get()` (also given a dedicated executor by #269, but with no
equivalent stall diagnostic until this same pass). Under continuous
capture this call should return within about one blocksize (~32ms)
regardless of silence vs. speech, since the audio callback enqueues
chunks on a fixed hardware cadence -- so unlike a VAD/STT model call,
this one running long for real would be a genuinely abnormal, specific
signal (either real contention on its single-worker executor, or the
underlying sounddevice callback has stopped delivering chunks entirely).
Added the same queued-vs-running instrumentation here too, plus queue
backlog depth (to test JP's own live hypothesis that chunks might be
piling up behind a stalled consumer rather than capture itself
stopping) -- **not yet live-verified against a real recurrence.**

Full timing evidence, exact log excerpts, and reasoning:
`docs/field-notes/2026-08-12-vad-freeze-live-reproduced-three-times-pr269-did-not-fix-it.md`.

**Net: still an open, safety-relevant bug.** Not a release blocker in
the sense of a regression -- the web-side recovery path remains a real,
repeatable workaround -- but the underlying freeze itself is unresolved,
and the mechanism actually responsible is once again unconfirmed.

**Follow-up (2026-08-12, same day, later): a repeatable synthetic-speech
harness confirms the short stalls above are real (zero queue backlog
both times, ruling out the backlog-piling-up idea), then catches a
qualitatively worse, 12+ minute freeze that resisted every recovery path
tried -- web resume, the hard-stop API, and even killing a hung backend
subprocess that happened to be stuck at the same time. Only a full
process kill ended it.** The web UI's recovery path, 3-for-3 earlier the
same day, failed on this attempt -- don't treat it as a guaranteed
mitigation. CPU forensics (target process pinned at a literal, sustained
0% during the freeze) point toward a genuine blocking wait with no
timeout, likely in backend-subprocess I/O rather than the VAD/capture
layer -- a plausible, different mechanism from everything hypothesized
above, not yet confirmed. This may be two distinct bugs sharing a
symptom, not one. Full evidence:
`docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-and-a-12-minute-unrecoverable-one.md`.

**Correction + headline number, same evening, later still:** the session
above ran with Windows' own mic "Audio Enhancements" ON the whole time
(discovered live, disabled, fixed synthetic-audio pickup immediately) --
an unknown fraction of that session's "total silence" was this OS-level
setting suppressing the *test signal*, not ConvoBox's own pipeline
stuck. A second, unrelated bug in the test harness's own success
detection was also found and fixed (it was restarting visibly-healthy
sessions). With **both** confounds removed, a clean 10-cycle automated
batch still shows a real, frequent stall: **30% of cycles required a
full session restart** (near-total audio pickup silence), and clean
pause+resume success occurred in only 2/10 cycles. Resume-word matcher
logic itself was traced and confirmed correct (`resumeword/detector.py`,
`ListeningGate.observe()`) -- most "resume failed" readings are more
likely a downstream consequence of the pause phrase itself sometimes not
registering, not a matcher bug. **This 30% figure is the current
best-controlled estimate of how often this stress pattern produces a
real stall** -- treat it as the headline number for release discussions,
superseding the smaller/less-controlled samples above. Full evidence:
`docs/field-notes/2026-08-12-vad-freeze-exhaustive-batch-after-fixing-windows-enhancements-confound.md`.

**Instrumentation pass, 2026-08-14 (no fix yet -- diagnostic only).**
Implements the "not done this session" next step from the 12-minute-
freeze field note above: both backend adapters' `readline()` calls
(`codex.py`'s `_read_loop`, `claude_code.py`'s `_read_loop` and
`_drain_stderr`) had the exact shape the field note's leading candidate
pointed at -- an unbounded read with no timeout and no explicit check for
"the process died without the pipe EOF'ing" -- and neither had the
queued-vs-running stall diagnostic `capture.py`/`segmenter.py` already
have. Added a shared helper, `readline_with_stall_diagnostic()`
(`convobox/adapters/base.py`), same non-destructive `asyncio.wait()`
polling shape (never cancels the underlying read), used by all three
call sites; each stall warning now also logs `proc.returncode`. Also
added a DEBUG log line for `ListeningGate.observe()`'s `"pass"` outcome
(`run_convobox.py`, right after the `"pause"` branch) -- previously
silent, this is the exact ambiguity the exhaustive-batch note above had
to resolve by manual code-tracing (was a "resume" transcript ever
actually paused-state, or did the pause phrase never register in the
first place). Neither change fixes the freeze -- both exist purely so
the *next* recurrence produces real telemetry instead of the silence
every prior live repro has produced. All diagnosed on Windows; **not
yet reproduced or tested on macOS.** Next real step: reproduce with this
instrumentation live (Windows first, since that's where every prior
repro happened) and read what actually fired.

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

**Follow-up (2026-08-10): the live mic+speaker attenuation measurement
this entry asked for has now been run, on this machine's real hardware
(AIRHUG 28 mic, Mac mini Speakers) -- different finding than expected.**
Ran `scripts/acoustic_calibration.py` (the repo's own unattended
real-room AEC/VAD calibration tool, previously only exercised on
Windows) twice independently, in a dedicated `git worktree` at
`convobox-UAT` (kept separate from this dev tree; see AGENTS.md's
"claim scope before editing" precedent) with a real Piper voice
(`en_US-lessac-medium`) actually played through the speakers and
captured back through the mic:

- Trial 1: `attenuation=2.49dB, ceiling=1.92dB` (auto-estimated delay
  238ms). Trial 2 (independent run): `attenuation=5.08dB,
  ceiling=0.69dB`. **Both readings sit at or below the tool's own
  "measurable ceiling"** -- per `EchoCanceller.measurable_ceiling_db()`'s
  own docstring, that means speaker echo at this mic barely rises above
  room ambient noise in the first place, not that AEC is failing to
  cancel it. `raw_playback_rms` (0.0047-0.0049) vs `ambient_rms`
  (0.0037-0.0040) confirms it directly: the un-cancelled echo is only
  marginally louder than the room's own noise floor.
- **Zero false barge-ins in either trial, with AEC on OR off**
  (`false_barge_ins: 0` for both `raw_vad` and `processed_vad`, both
  runs) -- the actual safety-relevant signal (would self-echo trip a
  spurious interrupt) reads clean even in the AEC-off condition on this
  hardware. Raw VAD did register 1-2 short utterances from the
  un-cancelled echo (once even peaking at `peak_vad_probability=0.997`),
  but never sustained long enough to cross `BargeInMonitor`'s own
  threshold -- the same distinction this repo's `[G1]`/`[G2]` UAT
  entries already draw between "VAD notices something" and "a real
  barge-in fires."
- **Reads as a genuinely different acoustic situation than the Windows
  finding below (erratic 0.5-12dB, clearly-above-ambient echo)**, not a
  contradiction of it -- plausibly this mic (AIRHUG 28) and/or the Mac
  mini Speakers' real-world coupling in this room is simply quieter
  relative to ambient noise than JP's Windows setup was. Only 2 trials,
  one room, one hardware pair -- not enough to generalize to "macOS is
  fine," just enough to say this specific machine's speaker-echo problem
  (if this repo ever needs to chase one on it) looks small relative to
  room noise, not that AEC itself is unusually strong or weak here.
- Full JSON reports + raw/AEC-processed WAV evidence live under the UAT
  worktree's own `uat-acoustic-calibration/` (gitignored scratch,
  per this project's own convention -- not copied into this repo).

**Follow-up (2026-08-11): first real human-speech demo on macOS —
safeword and barge-in both confirmed live, plus a real self-triggered
barge-in loop found and diagnosed.** JP demoed ConvoBox live to his son
(real speech, not synthetic injection). The safeword fired correctly 3
times (`stop stop stop` x2, `abort abort abort` x1); barge-in
(`interaction.interrupt_preset: conversational`) fired correctly on
the first two deliberate interrupts, then entered a real, sustained
self-triggered loop (20 barge-ins in ~90s, several firing with no one
present). Diagnosed live: 18 of 19 barge-in events showed
`UNDER-CANCELLING`, with attenuation staying close to this session's
steady-state baseline (6.54dB vs. 6.75dB) while the measured
echo-to-ambient ceiling spiked (14.22dB vs. ~0.53dB baseline) — rapid
back-to-back short turns (each cut short by the previous false
trigger) measurably increases the echo reaching the mic relative to
ambient, leaving proportionally more residual for a fixed amount of
real cancellation. `do-not-disturb` mode (this config's original
default) is not subject to this risk, since ordinary speech can't
trigger anything during playback there. No fix built or proposed this
pass — live characterization only. Full writeup:
`docs/field-notes/2026-08-11-macos-live-human-demo-safeword-bargein-and-self-echo-loop.md`.

**Follow-up (2026-08-11, same day): automated mitigation testing at the
exact demo volume (`tts.volume=4.0`, macOS system output 75%) —
a real, counterintuitive finding.** A 7-point AEC delay sweep found
**AEC-processed audio produced MORE false barge-ins than AEC-off, at
every single delay tested** (8-13 vs. 1) — the opposite of AEC's
intended effect, likely because residual-suppressor artifacts at this
volume are themselves speech-shaped enough to trip VAD more often than
the raw uncancelled echo. 400ms was the least-bad delay tested (8 vs.
10 for auto-238ms) but still far worse than AEC-off. A separate
4-point `barge_in_min_speech_ms` sweep (250/500/800/1200ms, N=1 each —
directional, not statistically robust) showed a real trend toward
1200ms converging to the AEC-off baseline (1 false trigger). Ranked
recommendation: lower the volume (biggest lever, matches this
session's whole volume-escalation arc), raise
`barge_in_min_speech_ms` if `conversational` mode must stay on at high
volume, set `aec_delay_ms: 400` explicitly as a smaller assist, or
fall back to `do-not-disturb`/headphones to sidestep the problem
entirely. Full writeup:
`docs/field-notes/2026-08-11-self-barge-in-mitigation-at-demo-volume.md`.

**Follow-up (2026-08-11, same day): combining both mitigations nearly
solves it, and a likely root cause was identified.** `aec_delay_ms=400`
+ `barge_in_min_speech_ms=1200` together, 4 real trials at the same
demo volume: mean 1.25 false barge-ins (2 of 4 trials hit zero), down
from 8-13 with no mitigation or either lever alone. **Likely root
cause, corroborated but not directly confirmed**: the Mac mini M4's
single built-in speaker (Apple's own spec lists it singular;
independent reviews describe it as prone to distortion at volume) may
be genuinely distorting acoustically at `tts.volume=4.0` + macOS
system volume 75% -- a linear AEC (WebRTC AEC3) structurally cannot
fully cancel a nonlinear/distorted acoustic path, which would explain
why AEC-processed audio measured worse than AEC-off at every delay
tested. No digital clipping found in the raw mic captures (peak
0.63-0.68/1.0), but that doesn't rule out acoustic distortion at the
speaker itself, a different phenomenon. **Also confirmed (JP directly
observed the LED)**: the AIRHUG 28 mic's own onboard "AI Noise
Reduction" DSP mode was OFF (green LED) throughout all testing this
session -- ruled out as a confound, not just assumed. Full writeup,
hardware specs, and sources:
`docs/field-notes/2026-08-11-self-barge-in-combined-mitigation-and-hardware-notes.md`.

**Follow-up (2026-08-11, same day): a full 119-trial volume sweep
(100%-20% system volume in 5% steps, N=7 per level, initial sweep +
3 corroborating up/down cycles) pins the transition zone precisely at
30-40% system output volume.** Above it, AEC consistently makes false
barge-ins worse than AEC-off (means of 8-13 vs. steady ~1); at and
below it, AEC flips back to normal (reducing false triggers below the
raw baseline). Also added a room RT60 measurement (exponential sine
sweep / Farina method): ~0.2s (T20) to ~0.4s (T30) in this session's
400 sq ft, hard-floored, open-plan test room -- shorter than the
room's "wet" subjective impression might suggest, plausibly because
being open on 3 sides lets reflected energy propagate away rather than
building up. Full raw data (every one of the 119 volume-sweep trials,
plus complete hardware/room specs) published for reuse:
`docs/field-notes/2026-08-11-full-volume-sweep-raw-data-and-room-rt60.md`.

**Follow-up (2026-08-11, same day): RT60 extended to N=50 repeat
measurements with ambient-noise logging.** T20 stayed tight and
reproducible (mean 0.2133s, sd 0.0138s, CV ~6.5%); T30 was noisier
(mean 0.4589s, sd 0.0573s, CV ~12.5%) -- confirms T20 is the more
trustworthy estimator here. Confirmed environmental state for the
whole batch: whole-house central AC running plus a box fan (Corsi-
Rosenthal configuration) on low, both throughout -- a real household
background-noise condition, not a silent-room ideal. A suggestive
N=10 pattern (lower ambient noise correlating with longer measured
RT60) held its direction at N=50 but was much weaker than it first
appeared (Pearson r=-0.243 for T20, r=-0.155 for T30) -- a real
example of a small sample overselling an effect size. Full 50-trial
raw data appended to the same field note:
`docs/field-notes/2026-08-11-full-volume-sweep-raw-data-and-room-rt60.md`.

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

**Follow-up (2026-08-07): a real upstream fix for the root cause
described above appears to exist, but this is diagnosed from opencode's
own changelog, NOT live-reverified against ConvoBox -- do not treat as
resolved without re-running the curl matrix above first.** opencode has
shipped 12 releases since 1.18.3 (up to v1.18.15 as of this check,
`gh release list -R sst/opencode`). Two changes in that range look like
they fix exactly this mechanism ("the server's API path never loads the
OAuth-credentialed provider"): **`fix(app): refresh V1 providers after
auth` (sst/opencode#38786, merged 2026-07-25)** -- its own root-cause
description: "V1 provider state is instance-cached... the refetch kept
returning the pre-auth connected-provider list," i.e. a newly
OAuth-authenticated provider's catalog never got rebuilt, which is the
same symptom as `GET /api/model` never listing `openai` here -- and
**`fix(app): refresh global provider state` (sst/opencode#39220, merged
2026-07-28)**, a closely related follow-up. Both predate the locally
installed v1.18.13 by several releases. **Not done, deliberately:** no
live opencode session was run to confirm `GET /api/model` now lists an
OAuth-authenticated provider, or that a `backend.model` pin against it
actually generates -- that would need a real API round-trip against a
live-authenticated provider, out of scope for an unattended research
pass. **Next step, concrete:** re-run the exact curl matrix this entry
already documents (pin `openai/*`, check `GET /api/model`, watch for a
generated reply) against the currently-installed opencode version before
re-enabling `backend.model` in any config -- if it passes, this whole
entry can move to a changelog/fixed note instead of KNOWN-ISSUES.md.

**Follow-up (2026-08-11): the curl matrix above was finally re-run live
(v1.18.15, macOS, real ChatGPT Plus/Pro OAuth credentials configured via
`opencode auth login`) -- STILL BROKEN, same symptom, now with the exact
mechanism identified, plus a more general bug found underneath it.**

`GET /api/model` / `GET /api/provider` on a fresh `opencode serve`
instance still list only the `opencode` (Zen) provider -- the
OAuth-authenticated `openai` provider never appears, exactly as
2026-07-18 found. `opencode run -m openai/gpt-5.4-mini "..."` (and
`gpt-5.4`, and `gpt-5.6-terra`) all answer correctly via the interactive
CLI in the same shell, same credentials -- confirming the split is still
serve-vs-CLI, not credential validity. So the two candidate upstream
fixes (`#38786`, `#39220`) either didn't land in 1.18.15 or don't
actually fix this specific symptom.

**The real mechanism, isolated with `--print-logs --log-level DEBUG`:**
pinning a session to `openai/gpt-5.4-mini` (or `gpt-5.4`, or
`gpt-5.6-terra` -- tried all three, identical) throws server-side:

```
ERROR message="Failed to drain Session" cause="SessionRunnerModel.ModelUnavailableError: Model unavailable: openai/gpt-5.4-mini ..."
```

**This error is logged and then silently discarded -- it never reaches
the API client in any form** (no SSE event, no session-state change, no
HTTP error). The client (ConvoBox, or a bare `curl` against the SSE
event stream, tested both ways) just waits forever with the prompt
sitting in `admitted`/`prompted` state.

**This turned out to be a more general opencode bug than "OAuth
provider not loaded," confirmed by triggering the identical hang three
different ways:**

1. **OAuth-credentialed model** (`openai/gpt-5.4-mini` et al, via `opencode
   auth login`): `SessionRunnerModel.ModelUnavailableError` (above).
2. **`opencode serve --pure`** (external plugins disabled, which is how
   the ChatGPT/Codex OAuth login is implemented): the *same* request
   instead fails with a clean `HTTP 401: Missing bearer or basic
   authentication in header` -- proving the plugin normally attaches
   the OAuth credential to outbound requests for the interactive CLI,
   but that attachment never happens for a `serve`-driven session.
3. **`opencode/hy3-free`** (opencode's own free Zen catalog, previously
   the verified-working model for ConvoBox per this entry's own
   `[L2]`/6d629be history): fails with `HTTP 402: "The account
   associated with the API Key is in arrears... top up the account"` --
   a billing suspension on **opencode's own infrastructure**, nothing
   to do with any credential configured on this machine, discovered
   only because the "known-good" free model was tried as a control and
   turned out not to be free/available right now either.

All three are different root causes at the provider layer -- but all
three produce the **exact same client-visible symptom**: `"Failed to
drain Session"` logged once, then permanent silence. **The actual bug
worth reporting upstream is this general one**: `opencode serve` never
propagates a provider/request failure back to the API caller, regardless
of *why* the provider call failed. `--text`-mode ConvoBox sessions
eventually give up via their own unrelated generic 120s "backend still
busy" bail-out (see the `--text`/`approve`-mode entry elsewhere in this
file for the identical shape of that same class of gap on the ConvoBox
side) -- but nothing ever tells the user *why* nothing happened.

**Not filed upstream yet.** Repro is clean and reproducible (3
independent trigger causes, identical symptom, `--log-level DEBUG`
output in hand) -- a good candidate for a real issue report on
`anomalyco/opencode` if this keeps mattering.

**Workaround found, real and confirmed end-to-end through ConvoBox
(2026-08-11, same session): a manually-declared custom provider in
`opencode.jsonc` sidesteps the whole bug.** All three failures above
share one thing in common -- every model tried came from opencode's
*built-in* provider-catalog/auth machinery (`opencode auth login` OAuth,
an `opencode auth login`-registered API key, or the built-in Zen
catalog). A provider declared directly in config, the same shape as
opencode's own `@ai-sdk/openai-compatible` custom-provider pattern
(seen independently in `anomalyco/opencode#12065`'s working example),
is a completely different code path and was NOT affected:

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "provider": {
    "ollama-local": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "qwen2.5-coder:7b": {} }
    }
  }
}
```

Pinning a session to `ollama-local/qwen2.5-coder:7b` (a local Ollama
instance, OpenAI-compatible endpoint) generated a real, complete
response through raw `curl` against `opencode serve` (`session.next.
text.ended` with actual text, clean `finish:"stop"`) -- and then through
**ConvoBox itself** (`--text` mode, real TTS spoken through the Mac mini
speakers). This is almost certainly the shape of what was running
successfully against opencode on Helios (Windows) earlier in this
project's history -- a manually-configured local/custom provider, not a
ChatGPT-Plus-OAuth or opencode-auth-registered API-key model.

**One separate, expected limitation surfaced by this same test, not a
bug -- verified, not just suspected:** `qwen2.5-coder:7b` returned the
requested tool call (`{"name": "write", "arguments": {...}}`) as plain
response TEXT instead of actually invoking it -- no file was created.
Confirmed this is a genuine model-capability gap, not an opencode/
ConvoBox wiring problem, by bypassing opencode's harness entirely:
called Ollama's own OpenAI-compatible `/v1/chat/completions` directly
with an explicit `tools` schema (the same shape opencode would send) --
identical result, `finish_reason: "stop"` with the call embedded as text
in `content`, no `tool_calls` array at all. This specific quantized
model just doesn't reliably emit native function-calling output despite
being handed a proper schema. A bigger/more agentic local model, or one
explicitly fine-tuned for tool use, would be the next thing to try if
local-model tool-calling through ConvoBox+opencode matters.

**A second, independent bug found while testing a real (Inception Labs)
API-key provider the same way: `{env:VAR}` substitution in
`opencode.jsonc` doesn't work, for ANY value, not just secrets.** Tried
`inception-direct` (`https://api.inceptionlabs.ai/v1`, an Inception Labs
API key) declared the same custom-provider way as the working Ollama
example above, with `"apiKey": "{env:INCEPTION_API_KEY}"` -- consistent
`HTTP 401: Incorrect API key provided`, even though (a) the key itself
was verified valid with a direct `curl` straight to Inception's API
(real `200`, real model list) and (b) the env var was confirmed present
in the `opencode serve` subprocess's actual environment via `ps eww`.
Read opencode's own substitution source
(`packages/opencode/src/config/variable.ts`'s `ConfigVariable.
substitute`, regex `/\{env:([^}]+)\}/g` against `process.env`) -- looks
correct and is applied to the whole raw config file text before JSON
parsing, so the mechanism should work in principle. **Isolated with a
clean control, no secret involved:** substituted `{env:OLLAMA_TEST_URL}`
(a harmless test value) into the *already-proven-working* Ollama
provider's `baseURL` field -- same `ModelUnavailableError` failure,
confirming this is general breakage of `{env:...}` for provider
`options` fields, not specific to API keys or to Inception. Hardcoding
the literal value directly in the file (both for the Inception key and
for the Ollama URL) works immediately every time. **Practical
consequence for anyone following the custom-provider workaround above:
`{env:VAR}` is not currently a safe way to keep a real API key out of
`opencode.jsonc` -- a working config today means the literal key sits
in that file in plaintext.** Not filed upstream yet; a good second
candidate alongside the `serve`-swallows-failures bug above.

**Follow-up, same session: Inception confirmed working end-to-end
through ConvoBox itself (not just raw curl), plus one more real bug --
a startup race, not a config problem.** With a fresh Inception key
hardcoded directly in `opencode.jsonc` (per the `{env:...}` bug above),
the *very first* request to a freshly-started `opencode serve` failed
with the same `ModelUnavailableError` seen throughout this
investigation -- but retrying the identical request against the
*same, now-warm* server succeeded immediately (`"banana"`, clean
`finish:"stop"`), and `scripts/run_convobox.py --text` against that
warm server produced a real spoken TTS response through the Mac mini
speakers. So there's a real startup race in `opencode serve`: a
provider/model can be correctly configured and still fail on the first
request after boot, before succeeding on every subsequent one. Anyone
hitting `ModelUnavailableError` on a custom provider should retry once
against an already-running server before concluding the config itself
is wrong.

**Closing finding: real tool-calling confirmed working end-to-end, not
just text generation.** Every earlier success this session (Ollama,
first Inception pass) only proved the model could generate text --
`qwen2.5-coder:7b` specifically could NOT invoke a real tool (see its
own entry above). Inception's `mercury-2` advertises
`"supported_features":["tools","json_mode","structured_outputs"]` in
its own `/v1/models` response, unlike the Ollama model tried -- worth
testing directly rather than assuming. Asked ConvoBox (`--text`,
`inception-direct/mercury-2`, warmed-up server) to create a file in the
sandbox: **the file was actually created, with the exact requested
content**, and ConvoBox spoke a real confirmation. First genuine
"the opencode agent actually did something" result in this entire
investigation, not just "opencode can talk."

**Practical state for ConvoBox today:** the opencode backend IS usable
via a manually-declared custom provider (confirmed working end-to-end
for actual text generation AND real tool-calling, both local/Ollama and
cloud/Inception, through ConvoBox itself); it remains unusable via
`opencode auth login` (OAuth or API-key) or the built-in Zen catalog,
for the reasons diagnosed above, and any custom-provider config that
needs a real credential currently has to hardcode it (the `{env:...}`
bug above) rather than reference an environment variable. A cold-start
retry may also be needed the first time a server starts. Full write-up:
`docs/field-notes/2026-08-11-permission-model-validation-claude-codex-opencode.md`.

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

**codex now has the same `ARTIFACT` wiring, schema-verified but NOT yet
live-verified end-to-end.** (2026-08-07, `feat/codex-artifact-pane-wiring`
branch, PR #219.) `CodexAdapter._resolve_artifact_writes`
(`src/convobox/adapters/codex.py`) reads a completed `fileChange` item's
`changes: [{path, kind, diff}]` array (confirmed via `codex app-server
generate-json-schema`, codex-cli 0.146.1) and emits an `ARTIFACT` event
per renderable, in-`working_dir` path -- same `ARTIFACT_MEDIA_TYPES`
allowlist and `working_dir` fencing as `ClaudeCodeAdapter`. Unit-tested
against a fake app-server, but **no live session has confirmed the real
`codex app-server` actually reports paths in this shape at runtime** (the
schema bundle and the module's other live probes were done in separate
sessions) -- the first live codex+artifact-pane UAT pass should treat
this as the thing to specifically confirm, not assume-working. See
`docs/field-notes/2026-08-07-codex-artifact-pane-wiring.md`.

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
(PR #219). The follow-up design call this entry originally asked for is
now written up: `docs/DESIGN-opencode-artifact-pane-wiring.md` (a second
concurrent `/api/event` subscription, `working_dir` fencing as the
correlation mechanism in place of a session ID the payload doesn't carry,
sliced into a log-only step before a real `ARTIFACT`-emitting one) -- not
implemented, still a design note, not a blind port of the codex pattern.

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

---

## "Open in editor" occasionally opens a different file than the one clicked -- fixed

**Status:** fixed, 2026-08-11 (PR #260) -- a stale-fetch race in
`renderArtifact()`'s editor-uri lookup, live-reproduced on the real running
app, then closed with a staleness guard. See below for the full trail:
one hypothesis ruled out (2026-08-09), the real mechanism structurally
identified but unconfirmed (2026-08-10, PR #249), then live-reproduced and
fixed (2026-08-11).

**Symptom, live-hit 2026-08-09** (real codex UAT session): clicking
"Open in editor" on an artifact once brought VS Code to the foreground
showing an unrelated file rather than the one just clicked.

**Ruled out:** a backslash-vs-forward-slash URI-formatting bug in
`get_artifact_editor_uri()` (`web/artifacts.py` built `vscode://file/`
URIs via `Path.__str__()`, which uses native Windows backslashes -- not
a valid URI path separator per RFC 3986). This was the original
diagnosis and PR #249 fixed it (`Path.as_posix()` instead). **Directly
disproven the same night**: JP tested the exact same, still-running,
*unpatched* server process (confirmed via process uptime, never
restarted since before the fix existed) by clicking the real button for
a real artifact (`TestObjects.java`) -- it opened the correct file
correctly, in a new VS Code window, despite the backslash URI. VS Code
on Windows tolerates the malformed-per-RFC URI fine in practice. The
`as_posix()` change is kept as a reasonable portability improvement
(still correct per spec, may matter on non-Windows setups), but it does
not explain the original symptom.

**Leading hypothesis, 2026-08-10 (PR #249):** a real sequencing gap in
`index.html`'s `renderArtifact()`. The "Open in editor" link's `href` is
set via a fire-and-forget `fetch(...editor-uri)` with no staleness
guard. **Correction to that same writeup**: it claimed the main content
render "already tracks `artifactLoadCounter` specifically to prevent a
stale response from clobbering a newer one" -- rechecking the code,
that's not accurate. `artifactLoadCounter` is incremented once per
render and used only as a cache-busting query param on the body-content
URL (`?t=${Date.now()}_${artifactLoadCounter}`); it was never actually
compared against anywhere, so no staleness check existed for *either*
the body content or the editor link. The body content happens to be
race-safe anyway, but for a different reason: each render creates fresh
DOM nodes (a new `<img>`/`<iframe>`, or `<pre>`/`<code>` appended after
`artifactBodyEl.innerHTML` was cleared), so a slow, stale response from
an old render either overwrites an element no longer in the DOM or gets
replaced outright. `artifactEditorLink`, by contrast, is a single
persistent element reused across every render -- there is nothing
structural protecting it, which is exactly why it was vulnerable and the
body content wasn't. Structurally confirmed by code reading; a same-night
attempted timed reproduction (artificial `setTimeout` delays) was
inconclusive, dominated by real Chrome tab-throttling (a requested
~50ms gap actually took ~800ms in practice).

**Live-reproduced, 2026-08-11:** confirmed on the real running app
(PR #249's branch, `--web`, real codex backend, working dir
`_artifact-test-scratch`) by monkey-patching `window.fetch` in the live
page to artificially delay the *first* of two real `editor-uri` calls,
then driving two back-to-back real file edits through the web UI's text
composer. Final observed state: artifact pane title/content = `test.js`
(correct, most recent edit), but `artifactEditorLink.href` =
`vscode://file/.../test.md` (wrong, an older edit) -- the exact symptom
from the original report, reproduced with real fetches against the real
`/api/artifacts/*/editor-uri` endpoint, not a mock. Notably, the run
that reproduced it did so via a *second real, undelayed* ARTIFACT event
for `test.md` that fired naturally after the `test.js` edits, not
primarily through the injected delay -- confirming the race is reachable
under real backend/tool-call timing, not just a contrived artificial
ordering.

**Fix:** added the staleness guard that was believed to already exist.
`renderArtifact()` now captures `artifactLoadCounter` into a local
`loadId` at call start; the editor-uri fetch's resolution callback
checks `loadId !== artifactLoadCounter` and discards the response if a
newer render has started since the fetch was issued -- same pattern the
2026-08-10 writeup described, actually wired in this time. Verified live
by re-running the same reproduction harness against the patched code:
the stale response is now discarded and the href stays correct.

---

## A hard-stop (safeword or pause phrase) does not guarantee an in-flight tool call actually stops

**Status:** validated-live, 2026-08-09, no fix built yet -- two follow-up
options identified (below), deliberately not implemented without
scoping the tradeoff first. Full evidence, exact timestamps, and the
mechanism writeup: `docs/field-notes/2026-08-09-hard-stop-does-not-
cancel-an-in-flight-tool-call.md`.

**Symptom.** During a real ~1h38m live voice UAT session (codex backend,
real headset), saying the pause phrase or a safeword while a
`commandExecution` tool call was in flight consistently produced this
sequence: the interrupt RPC (`turn/interrupt` for codex; the equivalent
`control_request interrupt` / `POST .../interrupt` for claude-code /
opencode) succeeds with no error, and ConvoBox's own state (pause/resume,
safeword-matched, "resumed listening") transitions cleanly and
immediately -- but the tool call's real `tool_result` doesn't arrive
until the underlying shell command finishes on its own schedule, **16 to
48+ seconds later**, across 5 separate incidents. Reproduces identically
whether triggered by voice or the web UI's Stop-listening button (rules
out an STT-timing explanation), and stacking multiple hard-stop signals
in a row during the same wait doesn't shorten it.

**Why:** all three backend adapters' `send_hard_stop()` only signal the
agent's own conversational/orchestration layer to stop -- none of the
three vendor APIs is documented to guarantee killing a shell subprocess
the agent already spawned for a tool call, and ConvoBox never has a
process handle on that subprocess (it only observes the eventual
`tool_result` the agent chooses to report). This directly relates to,
but is a different mechanism than, the entry above (a misheard safeword
landing on the pause phrase) -- that entry's "in-flight work is
cancelled either way" claim is about ConvoBox's OWN turn-level state,
which this finding doesn't contradict; the gap here is one level deeper,
at the tool call's own OS process.

**Unlike this repo's other "can't force-kill" findings (STT/AEC thread
offload), this one is solvable** -- an OS process (unlike an in-process
Python thread) can always be force-killed, and ConvoBox already holds a
real process handle on each backend's own CLI subprocess (used cleanly
in every adapter's `aclose()` on shutdown). The capability exists;
hard-stop just doesn't currently escalate to using it.

**Two follow-up options identified, neither built yet:**
1. **Honesty fix (small, low-risk):** don't let the UI say "resumed
   listening" as if everything stopped when a hard-stop was sent but no
   corresponding `tool_result`/turn-completion has arrived yet -- track
   and surface that pending-cleanup state truthfully instead of
   silently going quiet about it.
2. **Escalating force-kill (bigger, needs its own scoping/UAT pass):**
   if no completion arrives within a grace period after the polite
   interrupt, escalate to killing and respawning the backend process.
   Trades the whole session/thread's context for an actual guarantee --
   should be a deliberate, probably config-gated choice, not a silent
   default. Candidate follow-up test scenarios (from a same-session
   discussion with the codex backend itself, live-testing its own
   cancellation semantics): does the process actually die and stop
   performing side effects after an abort, or does aborting-then-
   restarting the same command produce duplicate/detached execution;
   how does a natural timeout compare to a manual abort; does a command
   with its own restart policy resist cancellation; what happens to
   output ordering (pre-delay vs. post-delay messages) when a delayed
   command is interrupted mid-stream.

---

## `--text` mode + `permission_mode: approve` abandons a pending approval instead of denying it

**Status:** diagnosed live 2026-08-11, macOS (Mac mini M4), both claude-code
and codex backends. Not fixed this pass -- fail-safe in practice (nothing
ever gets written without a real answer) but the mechanism is misleading
and worth a real fix.

**Symptom.** Ask either backend (in `--text` mode, `permission_mode: approve`)
to write a file: the approval prompt fires correctly
(`Approval needed to run Write. Say <phrase> to approve...` for claude-code;
`item/fileChange/requestApproval` for codex), then **exactly 120 seconds of
silence**, then `backend still busy after 120s; giving up the wait` and the
process exits. No file is ever created, on either backend, confirmed twice
for codex and once (to full resolution) for claude-code.

**Root cause.** `ApprovalPromptGate`'s own `approval_timeout_s` (default
30s), the thing that's supposed to auto-deny a silently-abandoned approval
prompt, is only ever ticked by `_working_watchdog` -- and
`scripts/run_convobox.py` only constructs `watchdog_task` in the mic-loop
setup path, well after `--text` mode's own early `return`. So in `--text`
mode, `approval_gate.observe_timeout()` is never called at all; the
approval just sits pending until an unrelated, generic 120s
"`backend still busy`" bail-out in `_drain_until_idle` gives up and the
script calls `adapter.aclose()`, disconnecting the backend without ever
sending an explicit decline.

**Why this matters even though nothing unsafe happens.** The net effect is
safe today (no destructive action executes without a real answer), but
what looks like "the system denied my request" is actually "the system
gave up waiting and disconnected" -- a real distinction if this approval
channel is ever built on further (e.g. surfaced to a caller who cares
*why* a request didn't go through, or a future mode where abandon and
deny should behave differently).

**Fix candidates, neither built yet:** either construct a lightweight
version of the watchdog (or just call `approval_gate.observe_timeout()`
on a bare timer) in `--text` mode too, or have `--text` mode's own exit
path call `resolve_pending_approval(False)` explicitly before
`adapter.aclose()`.

**Also attempted, inconclusive:** the live mic-loop voice-approval flow
itself (the thing `--text` mode structurally can't exercise) -- 4 live
synthetic-injection attempts, blocked by real, loud ambient background
noise in the test room that session (not a code issue). Full detail,
plus the clean `plan`/`permissive` mode confirmations on both backends
(N=2 each) and a re-confirmation that opencode remains untestable
(0 configured credentials): `docs/field-notes/2026-08-11-permission-model-validation-claude-codex-opencode.md`.

---

## STT error-ladder rejection gates on language probability, not decode confidence -- a low-confidence hallucination can slip through

**Status:** validated-live, 2026-08-12, single instance -- not yet
confirmed as a systematic gap across more samples.

**Symptom.** JP was speaking live, deliberately only saying variations
on "stop listening"/"resume listening". The pipeline transcribed
`'mayday listening resume alpha bravo'` (`lang=en (0.62) dec=0.31`) --
words he never said, not a garbled version of what he did say (confirmed
by direct comparison against his own real-time report). It was not
rejected; it went to the backend as ordinary conversation.

**Why it wasn't caught.** The error ladder's low-confidence rejection
(`stt.min_language_probability`, 0.4 in this config) checks the
**language-detection probability**, not the separately-logged **decode
confidence** (`dec=...`). This hallucination's language probability
(0.62) was comfortably above threshold even though its decode confidence
(0.31) was lower than two other transcripts the SAME session correctly
rejected minutes earlier (`'stop brake'` lang=0.40, `'stop please'`
lang=0.37). "Confident this is English" and "confident these are the
right words" are different signals; only the first currently gates
rejection.

**Why this matters beyond STT accuracy in general.** The hallucinated
content -- `"alpha bravo"` -- is two of the three words in this session's
real `approval_phrase` (`"alpha bravo delta"`). It fell one word short
and nothing unsafe happened, but it's a genuine near-miss on a
security-relevant phrase, produced by hallucination rather than real
speech, on a gate that measured the wrong confidence signal.

**Not yet done:** checking whether adding `dec` as a second gate
condition would catch cases like this without materially increasing
false rejections on good transcripts -- needs real distribution data
across both accepted and rejected transcripts, not just this one sample.
Full evidence: `docs/field-notes/2026-08-12-stt-hallucination-bypasses-the-language-probability-gate-near-miss-on-approval-phrase.md`.

---

## A safeword match in a transcript skips checking that same transcript for a pause phrase

**Status:** validated-live, 2026-08-12. Not a safety gap (the hard-stop
itself always fires correctly regardless) -- a real, code-confirmed
interaction gap between two independent control mechanisms, no fix
proposed yet.

**Symptom.** JP spoke a long, rapid-fire safeword sequence live; STT
transcribed it as one continuous 11.8s utterance containing multiple
safewords AND the pause phrase: `'break break break cancel cancel
cancel ... abort abort abort stop listening cancel cancel cancel ...'`.
The hard-stop fired correctly on `'break break break'` (first match).
`'stop listening'`, present verbatim later in the same transcript, was
never separately evaluated -- the session never entered the paused
state from this utterance.

**Mechanism, confirmed in code** (`scripts/run_convobox.py:2507-2547`):
the entire pause/resume check (`listening_gate.observe(text)`) lives
inside `if not is_hard_stop:`. When a safeword matches a transcript,
that whole block -- including the pause check -- is skipped entirely for
that transcript, not just reordered after the hard-stop.
`PauseListeningDetector` itself is unaffected and would have found the
phrase if asked; the gap is in the caller never asking.

**Why this is realistic, not contrived:** this project already has a
documented hallucination pattern (2026-08-06) where a single STT segment
can span many seconds of repeated/garbled phrases -- exactly the shape
that lets two different trigger phrases land in one utterance. This
session hit it live.

**Not fixed this session** -- worth a deliberate decision (run the pause
check unconditionally, independent of the hard-stop outcome, vs. keep
today's mutually-exclusive design) rather than a reflexive change. Full
evidence: `docs/field-notes/2026-08-12-safeword-and-pause-phrase-are-mutually-exclusive-within-one-utterance.md`.
