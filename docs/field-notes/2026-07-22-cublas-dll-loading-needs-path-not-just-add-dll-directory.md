---
title: os.add_dll_directory alone did not fix a pip-installed CUDA DLL that a C extension delay-loads on Windows — prepending PATH did
status: validated-live
date: 2026-07-22
project: ConvoBox (github.com/LegionForge/convobox)
versions: ctranslate2 (faster-whisper's backend, Windows wheel), nvidia-cublas-cu12 12.9.2.10, nvidia-cuda-nvrtc-cu12 12.9.86, nvidia-cuda-runtime-cu12 12.9.79, Python 3.12 (Windows), real NVIDIA RTX 4060
evidence:
  - Live crash, 2026-07-20 (predates this fix): "Library cublas64_12.dll is not found or cannot be loaded" from inside faster_whisper's .transcribe() call, on a real NVIDIA 4060
  - WIP stash from that session ("cuda extra + DLL dir registration attempt, cublas still not loading") using only os.add_dll_directory -- reproduced the identical failure live, 2026-07-22, even with the correct bin/ directory registered and the DLL confirmed present on disk at that exact path
  - Fix verified live, same session: prepending the same directories to os.environ["PATH"] (instead of/alongside os.add_dll_directory) let a real LocalTranscriber(device="cuda") construct AND transcribe real TTS-synthesized speech correctly, GPU confirmed in use
  - convobox src/convobox/stt/transcriber.py's _register_cuda_dll_directories()
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; flagged the unfinished item, real GPU hardware)
    - Claude Code (Anthropic claude-sonnet-5) — diagnosis, fix, live verification, writing
  org: https://legionforge.org
  created: 2026-07-22T10:30:00-05:00
  revised: 2026-07-22T10:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# os.add_dll_directory alone did not fix this — prepending PATH did

**Context for outsiders.** ConvoBox runs speech-to-text locally via
faster-whisper, whose backend (ctranslate2) can use an NVIDIA GPU for
much faster transcription. On Windows, the pip-installed CUDA libraries
(`nvidia-cublas-cu12` and friends) don't register themselves with
Windows' DLL search path the way a full CUDA Toolkit install does, so
without extra help the GPU path fails on the first real use, not at
setup. This note is about which fix for that actually worked, since a
plausible, commonly-recommended one didn't.

## Problem

`faster_whisper.WhisperModel(device="cuda")` constructs successfully —
device selection happens before any library is actually touched — but
the first real `.transcribe()` call raises `RuntimeError: Library
cublas64_12.dll is not found or cannot be loaded`. cuBLAS is
delay-loaded by ctranslate2, so the failure only surfaces from inside
the first real inference call, not construction — this had already
caused one real live-UAT incident (an unrelated `except RuntimeError`
handler absorbed it as a different, known, transient failure and
silently fell back to CPU for an entire session before anyone noticed).

## Evidence

The documented, standard fix for "a pip-installed native library isn't
on Windows' DLL search path" is `os.add_dll_directory()` (Python 3.8+),
called at import time with the package's own `bin/` directory. This was
tried first, from a real WIP stash:

```python
for package in ("nvidia.cublas", "nvidia.cuda_nvrtc", "nvidia.cuda_runtime"):
    spec = importlib.util.find_spec(package)
    for location in spec.submodule_search_locations:
        bin_dir = Path(location) / "bin"
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
```

Confirmed live, 2026-07-22: `cublas64_12.dll` genuinely exists at the
exact path this code registers (`.venv/Lib/site-packages/nvidia/cublas/bin/cublas64_12.dll`),
`importlib.util.find_spec` resolves the package correctly, and
`os.add_dll_directory` is called with the right path — and the
transcribe call still failed with the *identical* error message.

The fix: prepend the same directories to the `PATH` environment
variable instead (kept `os.add_dll_directory` alongside it rather than
removing it — only `PATH` is confirmed necessary, not that
`add_dll_directory` is useless):

```python
os.environ["PATH"] = bin_dir_str + os.pathsep + os.environ.get("PATH", "")
```

With this change and only this change, a real `LocalTranscriber(device="cuda")`
constructed and correctly transcribed real Piper-synthesized speech
("The quick brown fox jumps over the lazy dog.") on a real NVIDIA 4060,
confirmed running on the `cuda` device.

## Mechanism

Best understanding, not independently confirmed against ctranslate2's
own source: `AddDllDirectory`-registered paths (what `os.add_dll_directory`
calls under the hood) are only consulted by loader calls made in
Windows' "safe DLL search mode" — i.e. calls that pass
`LOAD_LIBRARY_SEARCH_DEFAULT_DIRS` (or a related `LOAD_LIBRARY_SEARCH_*`
flag) to `LoadLibraryEx`. Python's own interpreter opts itself into this
mode at startup, which is why `os.add_dll_directory` is the right advice
for **Python-level** `import`s of extension modules. But ctranslate2's
compiled binary resolves its own **runtime dependency** on cuBLAS
internally (a delay-loaded import, not a Python import) — and that
resolution apparently does not use the safe-search-mode flag, so it
falls back to the classic DLL search order, which does include the
directories on `PATH` but does NOT include directories added only via
`AddDllDirectory`. This would explain the exact symptom: a directory
that is unambiguously correct and registered is still invisible to the
one loader call that actually matters.

## What transfers

- **For any pip-installed native (non-Toolkit) CUDA library on Windows
  used from inside a compiled Python extension (not directly `import`ed
  by Python itself): try `PATH`, not just `os.add_dll_directory`,** if
  the extension's own dependency resolution is the thing failing rather
  than a plain Python import.
- **A "this is the documented fix" belief is still a hypothesis until
  tested against the real failure.** The stash this session inherited
  had already tried the standard-recommended fix and left a note that
  it didn't work — worth trusting that kind of prior negative result
  enough to test an alternative rather than re-deriving/re-trying the
  same fix from first principles.
- **Diagnostic that generalizes**: if a DLL is confirmed present at the
  exact path you're registering, and the loader still can't find it,
  suspect that you're registering the path via a mechanism the specific
  failing loader call doesn't consult — not that the path or file is
  wrong.
