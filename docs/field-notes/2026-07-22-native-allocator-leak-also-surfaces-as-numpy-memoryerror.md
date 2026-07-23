---
title: ctranslate2's native-allocator leak also surfaces as a bare numpy MemoryError, not just RuntimeError — and that manifestation wasn't caught
status: validated-live
date: 2026-07-22
project: ConvoBox (github.com/LegionForge/convobox)
versions: faster-whisper (ctranslate2 backend), numpy (pocketfft rfft), Python 3.12 (Windows)
evidence:
  - Live crash, 2026-07-22, ~22:12 local, mid-UAT session testing Codex approval mode after PR #133's merge — full traceback ending in `numpy._core._exceptions._ArrayMemoryError: Unable to allocate 1.15 MiB for an array with shape (1, 376, 400) and data type float64`, unhandled, killed the entire run_convobox.py process
  - `python -c "import numpy._core._exceptions as e; print(e._ArrayMemoryError.__mro__)"` — confirms `(_ArrayMemoryError, MemoryError, Exception, BaseException, object)`, i.e. NOT a RuntimeError subclass
  - src/convobox/stt/transcriber.py's LocalTranscriber.transcribe() and _reload_model(), pre-fix: `except RuntimeError` only
  - Pre-existing, already-documented sibling incident (2026-07-14, same file's own comments): the identical underlying ctranslate2/MKL native-allocator leak (SYSTRAN/faster-whisper#660, #390) manifesting as `RuntimeError: mkl_malloc: failed to allocate memory` / `could not create a memory object` — already caught and recovered from before this fix
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT session, pasted the real traceback)
    - Claude Code (Anthropic claude-sonnet-5) — diagnosis, fix, test, writing
  org: https://legionforge.org
  created: 2026-07-22T22:30:00-05:00
  revised: 2026-07-22T22:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The native-allocator leak also surfaces as a bare numpy MemoryError

**Context for outsiders.** ConvoBox transcribes speech locally with
faster-whisper, whose backend (ctranslate2) has a known, unresolved
upstream bug: its native (MKL, on Windows) memory allocator leaks across
many `transcribe()` calls in a long-lived process, eventually failing —
not from real memory pressure, but from allocator fragmentation/pressure
that recycling the model object resets. ConvoBox already had a recovery
mechanism for this, built and verified live on 2026-07-14. This note is
about a second, different-looking manifestation of the *same* leak that
the existing mechanism didn't cover.

## Problem

A live UAT session (testing the Codex `approve` permission mode
immediately after PR #133 merged the full `permission_mode` design)
crashed hard with an unhandled traceback:

```
File ".../faster_whisper/feature_extractor.py", line 189, in stft
    output = np.fft.rfft(input_array, n=n_fft, axis=-1, norm=norm)
File ".../numpy/fft/_pocketfft.py", line 417, in rfft
    output = _raw_fft(a, n, axis, True, True, norm, out=out)
numpy._core._exceptions._ArrayMemoryError: Unable to allocate 1.15 MiB
for an array with shape (1, 376, 400) and data type float64
```

1.15 MiB is a trivial allocation — this is not a real out-of-memory
condition, same conclusion as the 2026-07-14 incident (confirmed there
via `_memory_diagnostic()`'s real available-RAM check). The existing
recovery code in `LocalTranscriber.transcribe()` already has a broad
`except RuntimeError` specifically for this class of failure, with
extensive comments explaining why it's deliberately broad. But
`numpy._core._exceptions._ArrayMemoryError` is a `MemoryError` subclass,
**not** a `RuntimeError` — confirmed directly:

```python
>>> import numpy._core._exceptions as e
>>> e._ArrayMemoryError.__mro__
(<class '...ArrayMemoryError'>, <class 'MemoryError'>, <class 'Exception'>, ...)
```

So this manifestation of the leak fell straight through the existing
`except RuntimeError` clause, propagated all the way up through the
async event loop, and crashed the entire live voice session — the exact
failure mode (unhandled traceback kills the whole process) the
2026-07-14 fix was written to prevent, just via a different exception
type than that fix anticipated.

## Evidence

Two call sites in `src/convobox/stt/transcriber.py` only caught
`RuntimeError`: `LocalTranscriber.transcribe()`'s per-utterance recovery,
and `_reload_model()`'s own "never raises" contract (rebuilding the
model can hit the same native pressure the first failure did). Both
widened to `except (RuntimeError, MemoryError)`. `_looks_like_gpu_unavailable()`
(called inside the first except block, to distinguish a permanently
unusable GPU from this transient leak) already typed its parameter as
`BaseException` and only does `str(exc).lower()` pattern matching, so it
needed no change to handle either exception type correctly.

Verified: `tests/test_transcriber.py::test_numpy_array_memory_error_is_recovered_not_raised`
— a fake model raising `MemoryError` (not `RuntimeError`) on its first
`transcribe()` call is now absorbed exactly like the existing
`RuntimeError` case (empty result, model reloaded, next utterance
succeeds normally), instead of propagating.

## Mechanism

Same root cause as 2026-07-14 (ctranslate2's native allocator under
pressure from a long-lived process), different code path surfacing it:
this time the allocation failure happened inside **numpy's** own
`rfft`/pocketfft call in faster-whisper's feature extractor (computing
the mel spectrogram before ctranslate2's own encode step ever runs),
rather than inside ctranslate2's native encode/decode internals. numpy
raises its own `MemoryError` subclass for allocation failures, entirely
independent of whatever exception type ctranslate2's own native code
happens to surface — so a fix scoped to "catch what ctranslate2 raises"
was always going to miss "what numpy raises for the same underlying
pressure," since they're unrelated exception hierarchies from the
caller's point of view.

## What transfers

- **A recovery mechanism built around one observed exception type/message
  is only as complete as the incidents that produced it.** The original
  `except RuntimeError` was correct for every incident known at the time
  it was written; a genuinely different manifestation of the *same*
  underlying condition, through a *different* library in the same call
  stack, used a different exception hierarchy entirely.
- **When wrapping a call into a native/compiled dependency (ctranslate2,
  numpy, or similar) specifically to catch "this native thing sometimes
  fails transiently," consider catching `(RuntimeError, MemoryError)`
  together** rather than just `RuntimeError` — both are plausible
  vocabulary for "a native allocation failed," and which one a given
  library picks isn't something the caller controls or should have to
  special-case per library.
- **"Never raises" as a documented function contract (see
  `_reload_model`'s own docstring) needs its except clause audited
  whenever a new failure mode is found elsewhere in the same call
  chain** — the contract doesn't enforce itself; a second call site
  wrapping the same underlying operation can independently have the same
  gap.
