from __future__ import annotations

import gc
import importlib.util
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from faster_whisper import WhisperModel
from huggingface_hub.errors import LocalEntryNotFoundError

from convobox.config import STTConfig
from convobox.stt.base import SAMPLE_RATE, STTEngine, TranscriptResult

# Re-exported for backward compatibility: TranscriptResult and SAMPLE_RATE
# now live in convobox.stt.base (shared with the STTEngine interface), but
# code importing them from here keeps working.
__all__ = ["SAMPLE_RATE", "LocalTranscriber", "TranscriptResult"]

logger = logging.getLogger(__name__)


def _register_cuda_dll_directories() -> None:
    """Point Windows' DLL loader at the pip-installed CUDA runtime
    libraries (the `cuda` extra) so ctranslate2 can actually find cuBLAS.

    ctranslate2's Windows wheel bundles cuDNN directly (a .dll sitting
    right next to ctranslate2.dll) but not cuBLAS -- pip-installed
    nvidia-*-cu12 packages don't add themselves to Windows' DLL search
    path the way a system-wide CUDA Toolkit install does, so without
    this, WhisperModel(device="cuda"/"auto") CONSTRUCTS fine but the
    first real .transcribe() call fails with "Library cublas64_12.dll is
    not found or cannot be loaded" (live-confirmed, 2026-07-20 -- silently
    broke an entire UAT session because an unrelated except RuntimeError
    block absorbed it as the known transient MKL allocator quirk). cuBLAS
    is resolved lazily (delay-loaded) by ctranslate2 -- confirmed by the
    real failure surfacing from inside .transcribe(), not at
    WhisperModel construction -- so calling this at import time (before
    any construction) is early enough regardless of import order.

    **`os.add_dll_directory` alone does NOT fix this (live-confirmed,
    2026-07-22): the failure reproduces byte-for-byte even with the
    correct `bin/` directories registered via `AddDllDirectory`.**
    Prepending the same directories to the `PATH` environment variable
    instead does fix it (also live-confirmed: real `.transcribe()` call
    succeeds on an NVIDIA 4060 with this and only this change). Root
    cause, best understanding: `AddDllDirectory`-registered paths are
    only consulted by loader calls made in "safe search mode"
    (`LOAD_LIBRARY_SEARCH_*` flags); ctranslate2's own delay-load
    resolution of its cuBLAS dependency apparently does not opt into
    that mode, so it falls through to the classic search order, which
    does include `PATH`. Kept `add_dll_directory` alongside `PATH` (does
    no harm, and covers whichever other internal loads DO use safe
    search mode) rather than removing it -- this is a "both, since only
    one is confirmed necessary" call, not a "one is proven redundant"
    one.

    No-op on non-Windows or when the `cuda` extra isn't installed --
    CPU-only machines and CI's Linux runners never exercise this path.
    """
    if sys.platform != "win32":
        return
    for package in ("nvidia.cublas", "nvidia.cuda_nvrtc", "nvidia.cuda_runtime"):
        try:
            spec = importlib.util.find_spec(package)
        except ImportError:
            continue
        if spec is None or not spec.submodule_search_locations:
            continue
        for location in spec.submodule_search_locations:
            bin_dir = Path(location) / "bin"
            if not bin_dir.is_dir():
                continue
            os.add_dll_directory(str(bin_dir))
            bin_dir_str = str(bin_dir)
            path_entries = os.environ.get("PATH", "").split(os.pathsep)
            if bin_dir_str not in path_entries:
                os.environ["PATH"] = bin_dir_str + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dll_directories()


class _WhisperLikeModel(Protocol):
    """Structural type for the WhisperModel.transcribe() shape -- lets
    `model_factory` (below) accept a real WhisperModel or a test fake
    without either depending on the other.
    """

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> tuple[Any, Any]: ...


# Substrings from real CUDA/cuBLAS/cuDNN library-loading failures --
# confirmed live, 2026-07-20: a GPU that ctranslate2 can *detect*
# (get_cuda_device_count() > 0, since that just queries the NVIDIA
# driver) is not necessarily one it can actually RUN on -- the real CUDA
# runtime libraries (cuBLAS specifically) can be missing from the
# environment, and the resulting RuntimeError only surfaces on the
# FIRST real inference call (WhisperModel construction itself succeeds),
# not at construction time. This is a PERMANENT failure, unlike the
# transient MKL/CPU allocator leak below -- retrying the same device
# forever just repeats it on every utterance with no user-facing signal
# ("no responses detected"), so it needs a different response: fall back
# to cpu, not reload-and-retry-same-device.
_GPU_UNAVAILABLE_MARKERS = (
    "is not found or cannot be loaded",  # e.g. "Library cublas64_12.dll is not found..."
    "cublas",
    "cudnn",
    "cuda driver",
    "cuda_runtime",
)


def _looks_like_gpu_unavailable(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _GPU_UNAVAILABLE_MARKERS)


def _build_whisper_model(config: STTConfig, device_override: str | None = None) -> WhisperModel:
    """Construct the real WhisperModel, preferring the local cache.

    faster-whisper/huggingface_hub otherwise makes a real network call on
    EVERY construction (GET .../api/models/<repo>/revision/<rev>, a "did
    this change on the Hub" freshness check) even when the model is
    already fully cached -- pointless network dependency for a tool whose
    whole premise is local-first (README: "without leaving infrastructure
    you control"), and genuinely costly here specifically: the native-
    allocator recovery below reconstructs the model via this same
    function, so without this fix, every recovery during a degraded
    session would ALSO re-attempt that network call, with no guaranteed
    timeout, right when things are already going wrong. Found + explained
    to JP live, 2026-07-14, while investigating an unrelated UAT log that
    surfaced the call.

    local_files_only=True skips the network entirely and raises
    LocalEntryNotFoundError if nothing is cached yet -- caught here and
    retried with the normal (network-enabled) path, so first-time setup
    (no model downloaded yet) still works exactly as before. Every
    subsequent construction after that first download is fully offline.

    device_override, when set, wins over config.device -- used by
    LocalTranscriber's permanent cpu fallback once a GPU has been
    confirmed unusable this session (see _looks_like_gpu_unavailable).
    """
    device = device_override or config.device
    try:
        return WhisperModel(
            config.model, device=device, compute_type=config.compute_type,
            local_files_only=True,
        )
    except LocalEntryNotFoundError:
        logger.info(
            "STT model %r not cached locally yet -- downloading (one-time; "
            "every construction after this one will be offline)",
            config.model,
        )
        return WhisperModel(
            config.model, device=device, compute_type=config.compute_type,
        )


def _memory_diagnostic() -> str:
    """Best-effort one-line note on real available system RAM, for the
    native-allocator-failure log lines below.

    Exists specifically to answer the question a tester asks the moment
    they see "failed to allocate memory": is this genuinely out of RAM?
    Live-confirmed, 2026-07-14 (same session): this failure recurs with
    26-28GB free the whole time (`Get-CimInstance Win32_OperatingSystem`),
    i.e. it is the known, unresolved ctranslate2/MKL native-allocator bug
    (SYSTRAN/faster-whisper#660, #390), not real memory pressure -- but
    that was established by a manual out-of-band check, not anything the
    log itself said. Folding the same check into the log line means the
    answer is right there next time, no separate investigation needed.

    Windows-only (`ctypes` + `GlobalMemoryStatusEx`, no new dependency --
    this project has no psutil/cross-platform memory-info dependency
    today and adding one for a diagnostic-only log line isn't worth it).
    Degrades to a plain "unavailable" note elsewhere or on any failure --
    never allowed to raise, since this only ever runs inside an
    already-failing path and must not compound it.
    """
    if sys.platform != "win32":
        return "memory info unavailable (not Windows)"
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return "memory info unavailable (GlobalMemoryStatusEx failed)"
        available_mb = status.ullAvailPhys / (1024 * 1024)
        if available_mb >= 1024:
            # Plenty of real RAM free -- almost certainly the known
            # allocator quirk, not an actual shortage. Say so directly
            # rather than making the reader re-derive that conclusion.
            return (
                f"{available_mb:.0f}MB RAM available -- likely the known "
                f"ctranslate2/MKL allocator quirk, not a real memory shortage"
            )
        return f"{available_mb:.0f}MB RAM available -- genuinely low, worth checking other processes"
    except Exception:  # noqa: BLE001 -- diagnostic-only, must never raise
        return "memory info unavailable"


class LocalTranscriber(STTEngine):
    def __init__(
        self,
        config: STTConfig,
        model_factory: Callable[[], _WhisperLikeModel] | None = None,
    ) -> None:
        # `model_factory` is an injection point for tests (a fake model
        # with a `.transcribe()` method, no real Whisper weights needed) --
        # every real caller passes only `config` and gets the real
        # WhisperModel, unchanged from before this parameter existed.
        self._config = config
        self._custom_factory = model_factory
        # Set once a GPU has been confirmed unusable this session (see
        # _looks_like_gpu_unavailable) -- from then on every rebuild goes
        # to cpu, regardless of what config.device says. None of this
        # applies when a custom model_factory is injected (tests): a
        # fake model can't raise a real GPU-unavailable error, so the
        # override is simply never set for that path.
        self._device_override: str | None = None
        self._model: _WhisperLikeModel | None = self._build_model()
        self._warm_up()

    def _warm_up(self) -> None:
        """Absorb a GPU-unavailable failure here, not on the user's first
        real utterance.

        ctranslate2 delay-loads cuBLAS: WhisperModel construction succeeds
        even when the GPU is detected but unusable (e.g. a missing
        cublas64_12.dll) -- the failure only surfaces on the first real
        .transcribe() call (see _looks_like_gpu_unavailable's docstring).
        Without this, the user's own first utterance was always the one
        that triggered AND absorbed that failure, silently discarded as
        "unheard" by the existing recovery below -- live-confirmed
        2026-07-21 (a fresh session's first utterance was consistently
        empty-STT-result-discarded, traced to this exact mechanism).
        Transcribing a short burst of silence here instead means any such
        failure (and the resulting permanent cpu fallback) happens before
        the user's real speech ever reaches the pipeline. Also reduces
        first-utterance latency incidentally: the first inference on any
        accelerator is typically the slowest (kernel/algorithm selection),
        not specific to this bug.

        Skipped when a custom model_factory is injected (tests use a fake
        model with no real GPU path to warm up) or when config.device is
        explicitly "cpu" (nothing to absorb -- same guard condition as the
        real fallback check in transcribe()).
        """
        if self._custom_factory is not None or self._config.device == "cpu":
            return
        self.transcribe(np.zeros(int(0.5 * SAMPLE_RATE), dtype=np.float32))

    def _build_model(self) -> _WhisperLikeModel:
        if self._custom_factory is not None:
            return self._custom_factory()
        return _build_whisper_model(self._config, device_override=self._device_override)

    @property
    def resolved_device(self) -> str:
        """The device this session's model actually runs on.

        config.device can be "auto", which ctranslate2 (not this class)
        resolves internally -- reading the real WhisperModel's own
        `.model.device` (the underlying ctranslate2 Whisper instance) is
        the only way to know whether "auto" landed on cuda or cpu, short
        of duplicating ctranslate2's own detection logic here. Falls back
        to `_device_override` (set once a GPU has been confirmed unusable
        this session -- see _looks_like_gpu_unavailable) or the raw
        config value when no real model is available to introspect (a
        test's injected fake model, or mid-reload after a failure).
        """
        inner = getattr(self._model, "model", None)
        device = getattr(inner, "device", None)
        if isinstance(device, str):
            return device
        return self._device_override or self._config.device

    def _empty_result(self, audio: np.ndarray, start: float) -> TranscriptResult:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return TranscriptResult(
            text="",
            language="",
            language_probability=0.0,
            latency_ms=latency_ms,
            duration_s=len(audio) / SAMPLE_RATE,
            avg_logprob=-10.0,
            segments=[],
        )

    def _reload_model(self) -> bool:
        """Rebuild `self._model`, returning whether it now holds a usable
        model. Never raises -- a failed reload leaves `self._model` as
        `None` rather than propagating, so a second native-allocator
        failure during recovery degrades the same way the first one does
        (one more unheard utterance) instead of crashing the process.

        Real gap found + fixed live, 2026-07-14: JP's session crashed
        with an UNHANDLED `RuntimeError: mkl_malloc: failed to allocate
        memory` raised from the reload itself -- the original mitigation
        (below) assumed rebuilding a fresh `WhisperModel` always
        succeeds, which is false when the native allocator is under
        enough pressure that even a fresh construction fails. Dropping
        the old model reference and forcing a collection BEFORE
        rebuilding (rather than after, or not at all) also reduces peak
        native memory during the reload window itself -- while `self.
        _model` still pointed at the old (broken) instance, calling the
        factory again meant asking the allocator to hold both the old
        and the new model simultaneously, which is exactly the wrong
        move when the allocator is already the thing under pressure.
        """
        self._model = None
        gc.collect()
        try:
            self._model = self._build_model()
        except RuntimeError:
            logger.error(
                "STT model reload ALSO failed -- staying unavailable, will "
                "retry on the next utterance instead of crashing the "
                "session (%s)",
                _memory_diagnostic(),
                exc_info=True,
            )
            return False
        return True

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        # faster-whisper expects a contiguous float32 mono array at 16kHz.
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        start = time.perf_counter()

        if self._model is None:
            # A previous reload attempt failed and left no usable model
            # (see _reload_model's docstring) -- retry building one now,
            # since there's no model here whose .transcribe() call could
            # itself raise the RuntimeError the except block below exists
            # to catch.
            if not self._reload_model():
                return self._empty_result(audio, start)

        model = self._model
        if model is None:
            # Unreachable in practice -- _reload_model()'s contract
            # guarantees self._model is set whenever it returns True (the
            # only way past the block above without an early return). This
            # satisfies mypy's inability to narrow an instance attribute
            # across a method call, not a real runtime path.
            return self._empty_result(audio, start)

        try:
            segments, info = model.transcribe(
                audio,
                language=self._config.language,
            )
            # transcribe() returns a lazy generator; materializing it here
            # is what actually runs the decode, so it must stay inside the
            # try (ctranslate2's native encode() failure surfaces during
            # iteration, not the transcribe() call itself) and the timing.
            segment_list = list(segments)
        except RuntimeError as exc:
            if (
                self._custom_factory is None
                and self._device_override is None
                and self._config.device != "cpu"
                and _looks_like_gpu_unavailable(exc)
            ):
                # A GPU that's DETECTED (ctranslate2 queries the driver,
                # cheap) is not necessarily one that's USABLE (needs the
                # real CUDA runtime libraries, e.g. cuBLAS) -- confirmed
                # live, 2026-07-20: config.device="auto"/"cuda" resolved
                # to a real NVIDIA GPU, but "Library cublas64_12.dll is
                # not found or cannot be loaded" crashed EVERY single
                # transcription, silently, for the rest of the session
                # (this same broad except block reloaded the same broken
                # device every time -- correct behavior for the transient
                # MKL/CPU allocator leak below, but wrong here: this
                # failure is permanent, not transient, so retrying the
                # same device just repeats it forever with no user-facing
                # signal beyond "nothing seems to be heard"). Falling back
                # to cpu for the rest of the session is the fix -- loud,
                # once, not a silent per-utterance repeat of the same crash.
                self._device_override = "cpu"
                logger.warning(
                    "STT device %r failed to actually run (GPU detected but "
                    "not usable -- likely a missing CUDA runtime library, "
                    "e.g. cuBLAS): %s -- falling back to cpu for the rest of "
                    "this session. Fix the CUDA install or set stt.device: "
                    "cpu explicitly to silence this.",
                    self._config.device, exc,
                )
                self._reload_model()
                return self._empty_result(audio, start)
            # Known, unresolved upstream issue: ctranslate2's native
            # (MKL on Windows) allocator leaks memory across repeated
            # transcribe() calls in a long-lived process, eventually
            # failing with "mkl_malloc: failed to allocate memory" /
            # "could not create a memory object"
            # (SYSTRAN/faster-whisper#660, #390) -- confirmed live,
            # 2026-07-14, crashing a real ~13-minute UAT session (~20
            # transcriptions in) with an unhandled traceback that killed
            # the whole voice loop. Not a ConvoBox bug and not something
            # Python-level garbage collection can fix (it's native heap
            # LEAK across many calls -- see `_reload_model`'s docstring
            # for why a `gc.collect()` still helps here regardless, for a
            # different reason: reducing peak usage during the reload
            # itself, not reclaiming the underlying leak). The practical
            # mitigation is recycling the model object, which resets its
            # allocator state. Broad `except RuntimeError` is deliberate,
            # not lazy: reloading-and-treating-as-unheard is SAFE
            # regardless of the actual cause (it can only make an STT
            # hiccup non-fatal, never mask a silent wrong answer), and the
            # full exception is still logged at WARNING with a traceback --
            # nothing here is silently swallowed, it's converted from a
            # fatal crash into a loud, recoverable one. One lost utterance
            # is a far better failure mode than losing the entire session.
            logger.warning(
                "faster-whisper native transcribe() failure -- reloading the "
                "STT model and treating this utterance as unheard "
                "(see SYSTRAN/faster-whisper#660 if this recurs; %s)",
                _memory_diagnostic(),
                exc_info=True,
            )
            self._reload_model()
            return self._empty_result(audio, start)
        latency_ms = (time.perf_counter() - start) * 1000.0

        segment_texts = [segment.text.strip() for segment in segment_list]
        # -10.0 when nothing decoded: exp(-10) ~= 0, i.e. zero confidence,
        # without the -inf that would poison downstream arithmetic.
        avg_logprob = (
            sum(segment.avg_logprob for segment in segment_list) / len(segment_list)
            if segment_list
            else -10.0
        )

        return TranscriptResult(
            text=" ".join(segment_texts).strip(),
            language=info.language,
            language_probability=info.language_probability,
            latency_ms=latency_ms,
            duration_s=len(audio) / SAMPLE_RATE,
            avg_logprob=float(avg_logprob),
            segments=segment_texts,
        )
