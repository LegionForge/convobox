from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from huggingface_hub.errors import LocalEntryNotFoundError

from convobox.config import STTConfig
from convobox.stt.transcriber import (
    LocalTranscriber,
    _memory_diagnostic,
    _build_whisper_model,
    _register_cuda_dll_directories,
)


@dataclass
class _FakeSegment:
    text: str
    avg_logprob: float


@dataclass
class _FakeInfo:
    language: str
    language_probability: float


class _FakeModel:
    """Stands in for faster_whisper.WhisperModel.

    `fail_times` lets a test make the first N calls raise RuntimeError
    (mirroring the real ctranslate2 native-allocator failure, which
    surfaces directly from the .transcribe() call itself -- confirmed
    from a real crash traceback, 2026-07-14 -- not from iterating the
    lazy segment generator) before succeeding.
    """

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def transcribe(self, audio: np.ndarray, language: str | None = None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("could not create a memory object")
        segments = [_FakeSegment(text="hello world", avg_logprob=-0.2)]
        info = _FakeInfo(language="en", language_probability=0.95)
        return segments, info


def _config() -> STTConfig:
    return STTConfig(model="tiny.en", device="cpu", compute_type="int8")


def test_normal_transcription_returns_expected_result() -> None:
    model = _FakeModel()
    transcriber = LocalTranscriber(_config(), model_factory=lambda: model)
    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == "hello world"
    assert result.language == "en"
    assert result.language_probability == 0.95
    assert result.avg_logprob == pytest.approx(-0.2)


def test_model_factory_called_once_at_construction() -> None:
    calls = []

    def factory():
        calls.append(1)
        return _FakeModel()

    LocalTranscriber(_config(), model_factory=factory)
    assert len(calls) == 1


def test_native_allocator_failure_is_recovered_not_raised() -> None:
    # The real crash (SYSTRAN/faster-whisper#660/#390, live-confirmed
    # 2026-07-14): ctranslate2's native allocator fails after many
    # transcribe() calls in a long-lived process. Must not propagate and
    # kill the whole voice loop.
    failing_model = _FakeModel(fail_times=1)
    transcriber = LocalTranscriber(_config(), model_factory=lambda: failing_model)
    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == ""
    assert result.segments == []
    assert result.language_probability == 0.0
    assert result.avg_logprob == pytest.approx(-10.0)
    assert result.duration_s == pytest.approx(1.0)  # 16000 samples @ 16kHz


def test_native_allocator_failure_reloads_the_model() -> None:
    # The reload must actually replace self._model (calling the factory
    # again), not just catch-and-ignore -- otherwise every subsequent
    # transcription would keep hitting the same broken allocator state.
    # Factory fails only the first model it ever builds, so construction
    # succeeds and the first transcribe() call is what triggers recovery.
    models_built: list[_FakeModel] = []
    built = {"count": 0}

    def flaky_factory():
        built["count"] += 1
        fail = 1 if built["count"] == 1 else 0
        m = _FakeModel(fail_times=fail)
        models_built.append(m)
        return m

    transcriber = LocalTranscriber(_config(), model_factory=flaky_factory)
    assert len(models_built) == 1  # constructed once, up front

    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == ""  # first model's only call failed
    assert len(models_built) == 2  # recovery built a replacement

    # The replacement model (fail_times=0) must now be the one in use.
    result2 = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert result2.text == "hello world"
    assert len(models_built) == 2  # no further reload needed


def test_recovery_does_not_swallow_the_utterance_permanently() -> None:
    # One failed utterance must not corrupt the transcriber for future
    # calls -- exactly the "one lost utterance beats losing the whole
    # session" behavior this fix exists for.
    built = {"count": 0}

    def flaky_factory():
        built["count"] += 1
        fail = 1 if built["count"] == 1 else 0
        return _FakeModel(fail_times=fail)

    transcriber = LocalTranscriber(_config(), model_factory=flaky_factory)
    first = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    second = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert first.text == ""
    assert second.text == "hello world"


def test_reload_failure_does_not_crash_the_process() -> None:
    # Real gap found + fixed live, 2026-07-14: a real UAT session crashed
    # with an UNHANDLED RuntimeError raised from the reload attempt
    # itself (constructing the replacement WhisperModel also hit the
    # native allocator failure). The reload's own construction call --
    # not just the original transcribe() call -- must be caught too.
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        if calls["count"] == 1:
            # Construction succeeds; the model fails on its first
            # transcribe() call, triggering the reload path below.
            return _FakeModel(fail_times=1)
        # The reload's OWN construction fails -- this is what crashed
        # the real session (an unhandled RuntimeError, not caught by
        # the original transcribe()-only try/except).
        raise RuntimeError("mkl_malloc: failed to allocate memory")

    transcriber = LocalTranscriber(_config(), model_factory=factory)
    result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))  # must not raise

    assert result.text == ""
    assert calls["count"] == 2


def test_model_stays_unavailable_after_a_failed_reload_and_retries_next_call() -> None:
    # After a failed reload, the transcriber has no usable model -- the
    # NEXT transcribe() call (not just a background timer) must retry
    # building one rather than staying permanently broken for the rest
    # of the session.
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeModel(fail_times=1)
        if calls["count"] == 2:
            raise RuntimeError("mkl_malloc: failed to allocate memory")
        return _FakeModel(fail_times=0)  # third build (retry) succeeds

    transcriber = LocalTranscriber(_config(), model_factory=factory)
    first = transcriber.transcribe(np.zeros(16000, dtype=np.float32))  # fails, reload also fails
    assert first.text == ""
    assert calls["count"] == 2

    second = transcriber.transcribe(np.zeros(16000, dtype=np.float32))  # retries the reload
    assert second.text == "hello world"
    assert calls["count"] == 3


def test_reload_drops_the_old_model_reference_before_rebuilding() -> None:
    # The old (broken) model must be released (self._model set to None)
    # BEFORE the factory is called again for the replacement -- reduces
    # peak native memory during the reload window itself, which matters
    # specifically because the allocator is already under pressure when
    # this path runs at all (asking it to hold both the old and new
    # model simultaneously is exactly the wrong move here).
    holder: dict[str, LocalTranscriber] = {}
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        if calls["count"] == 2:
            transcriber = holder["transcriber"]
            assert transcriber._model is None
        fail = 1 if calls["count"] == 1 else 0
        return _FakeModel(fail_times=fail)

    transcriber = LocalTranscriber(_config(), model_factory=factory)
    holder["transcriber"] = transcriber
    transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert calls["count"] == 2


def test_memory_diagnostic_never_raises_and_returns_a_string() -> None:
    # Diagnostic-only helper for the failure log lines -- must never
    # itself raise, since it only ever runs inside an already-failing
    # path and must not compound it.
    result = _memory_diagnostic()
    assert isinstance(result, str)
    assert result != ""


# --- _build_whisper_model: prefers the local cache, avoiding the
# per-construction network freshness check faster-whisper otherwise makes
# even when the model is already fully downloaded (found live, 2026-07-14,
# investigating an unexpected huggingface.co call in a UAT log -- costly
# specifically because the native-allocator recovery above reconstructs
# the model via this same function, so every recovery during a degraded
# session would otherwise ALSO re-attempt a network call). ---


def test_build_whisper_model_tries_local_files_only_first() -> None:
    with patch("convobox.stt.transcriber.WhisperModel") as mock_cls:
        mock_cls.return_value = MagicMock()
        _build_whisper_model(_config())

    mock_cls.assert_called_once_with(
        "tiny.en", device="cpu", compute_type="int8", local_files_only=True
    )


def test_build_whisper_model_falls_back_to_network_when_not_cached() -> None:
    # First call (local_files_only=True) raises "not cached yet"; the
    # function must retry WITHOUT local_files_only (network-enabled) --
    # first-time setup (no model downloaded yet) must keep working exactly
    # as before this change.
    online_model = MagicMock()

    def side_effect(*args, **kwargs):
        if kwargs.get("local_files_only"):
            raise LocalEntryNotFoundError("not cached")
        return online_model

    with patch("convobox.stt.transcriber.WhisperModel", side_effect=side_effect) as mock_cls:
        result = _build_whisper_model(_config())

    assert result is online_model
    assert mock_cls.call_count == 2
    first_call, second_call = mock_cls.call_args_list
    assert first_call.kwargs["local_files_only"] is True
    assert "local_files_only" not in second_call.kwargs


def test_build_whisper_model_used_as_the_default_factory() -> None:
    # LocalTranscriber's default model_factory (no injection) must route
    # through _build_whisper_model, not construct WhisperModel directly --
    # otherwise this whole fix would be dead code nothing actually calls.
    with patch("convobox.stt.transcriber.WhisperModel") as mock_cls:
        mock_cls.return_value = MagicMock()
        LocalTranscriber(_config())

    mock_cls.assert_called_once_with(
        "tiny.en", device="cpu", compute_type="int8", local_files_only=True
    )


# --- GPU-detected-but-not-usable fallback (found live, 2026-07-20): a real
# NVIDIA GPU can be DETECTED (ctranslate2 just queries the driver) without
# being USABLE (needs the real CUDA runtime, e.g. cuBLAS) -- confirmed live
# by "Library cublas64_12.dll is not found or cannot be loaded" crashing
# EVERY transcription in a session with device="auto"/"cuda". ---


@pytest.mark.parametrize(
    "message",
    [
        "Library cublas64_12.dll is not found or cannot be loaded",
        "Could not load library cudnn_ops64_9.dll",
        "CUDA driver version is insufficient for CUDA runtime version",
    ],
)
def test_looks_like_gpu_unavailable_matches_real_error_messages(message: str) -> None:
    from convobox.stt.transcriber import _looks_like_gpu_unavailable

    assert _looks_like_gpu_unavailable(RuntimeError(message))


def test_looks_like_gpu_unavailable_does_not_match_the_cpu_allocator_quirk() -> None:
    # The pre-existing, unrelated MKL/CPU allocator leak (SYSTRAN/faster-
    # whisper#660) must keep using the same-device reload path below, not
    # this one -- these two failure classes need different recoveries.
    from convobox.stt.transcriber import _looks_like_gpu_unavailable

    assert not _looks_like_gpu_unavailable(RuntimeError("mkl_malloc: failed to allocate memory"))
    assert not _looks_like_gpu_unavailable(RuntimeError("could not create a memory object"))


def test_gpu_unavailable_error_falls_back_to_cpu_permanently() -> None:
    # The construction-time warm-up (below) is what actually encounters
    # this failure now, not the first explicit transcribe() call -- see
    # test_gpu_unavailable_error_is_absorbed_during_construction_warmup
    # for that. This test now starts from a transcriber already fixed up
    # by the warm-up, and confirms cpu stays sticky across further calls.
    gpu_model = MagicMock()
    gpu_model.transcribe.side_effect = RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"
    )
    cpu_model = MagicMock()
    cpu_model.transcribe.return_value = (
        [_FakeSegment(text="hello world", avg_logprob=-0.2)],
        _FakeInfo(language="en", language_probability=0.95),
    )

    def side_effect(*args, **kwargs):
        return cpu_model if kwargs.get("device") == "cpu" else gpu_model

    config = STTConfig(model="tiny.en", device="cuda", compute_type="float16")
    with patch("convobox.stt.transcriber.WhisperModel", side_effect=side_effect):
        transcriber = LocalTranscriber(config)
        assert transcriber._device_override == "cpu"  # already fixed up by warm-up

        # The user's own utterances never see the broken device at all.
        result = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
        assert result.text == "hello world"
        result2 = transcriber.transcribe(np.zeros(16000, dtype=np.float32))
        assert result2.text == "hello world"
        assert transcriber._device_override == "cpu"


def test_gpu_unavailable_error_is_absorbed_during_construction_warmup() -> None:
    # Live-confirmed 2026-07-21: without the warm-up, the user's OWN
    # first real utterance was the one that triggered and absorbed this
    # fallback, silently discarded as "unheard" every fresh session.
    gpu_model = MagicMock()
    gpu_model.transcribe.side_effect = RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"
    )
    cpu_model = MagicMock()

    def side_effect(*args, **kwargs):
        return cpu_model if kwargs.get("device") == "cpu" else gpu_model

    config = STTConfig(model="tiny.en", device="cuda", compute_type="float16")
    with patch("convobox.stt.transcriber.WhisperModel", side_effect=side_effect) as mock_cls:
        transcriber = LocalTranscriber(config)

    # The warm-up called .transcribe() on the broken gpu_model before
    # __init__ ever returned, and the resulting reload already asked for
    # cpu explicitly -- not config.device.
    gpu_model.transcribe.assert_called_once()
    assert transcriber._device_override == "cpu"
    reload_call = mock_cls.call_args_list[-1]
    assert reload_call.kwargs["device"] == "cpu"


def test_warmup_is_skipped_for_explicit_cpu_device() -> None:
    # No GPU path exists to warm up -- must not add startup latency or an
    # extra transcribe() call for CPU-only configs.
    with patch("convobox.stt.transcriber.WhisperModel") as mock_cls:
        mock_cls.return_value = MagicMock()
        LocalTranscriber(_config())  # _config() uses device="cpu"
    mock_cls.return_value.transcribe.assert_not_called()


def test_warmup_is_skipped_for_injected_test_models() -> None:
    # A custom model_factory (test injection) bypasses the real GPU path
    # entirely -- the warm-up must not call it either.
    model = _FakeModel(fail_times=0)
    LocalTranscriber(
        STTConfig(model="tiny.en", device="cuda", compute_type="float16"),
        model_factory=lambda: model,
    )
    assert model.calls == 0


def test_gpu_unavailable_fallback_does_not_apply_to_injected_test_models() -> None:
    # A custom model_factory (test injection) bypasses _build_whisper_model
    # entirely, so it can never receive a device override -- the fallback
    # logic must not even attempt to set one for this path.
    failing_model = _FakeModel(fail_times=0)
    failing_model.transcribe = MagicMock(
        side_effect=RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    )
    transcriber = LocalTranscriber(_config(), model_factory=lambda: failing_model)
    transcriber.transcribe(np.zeros(16000, dtype=np.float32))
    assert transcriber._device_override is None


# --- resolved_device: the TUI's GPU/CPU indicator (docs/UAT-checklist.md,
# live UAT feedback 2026-07-22) reads this, not config.stt.device, because
# "auto" doesn't say which device it actually resolved to. ---


class _FakeModelWithDevice(_FakeModel):
    """Like _FakeModel, but also exposes `.model.device` -- the real
    faster_whisper.WhisperModel's own attribute (its `.model` is the
    underlying ctranslate2 Whisper instance), which is what
    resolved_device introspects to learn what "auto" actually resolved
    to."""

    def __init__(self, device: str) -> None:
        super().__init__()
        self.model = SimpleNamespace(device=device)


def test_resolved_device_reads_the_real_models_own_device() -> None:
    model = _FakeModelWithDevice("cuda")
    transcriber = LocalTranscriber(
        STTConfig(model="tiny.en", device="auto", compute_type="int8"),
        model_factory=lambda: model,
    )
    assert transcriber.resolved_device == "cuda"


def test_resolved_device_falls_back_to_config_device_without_a_real_model() -> None:
    # _FakeModel (used throughout this file) has no `.model` attribute,
    # same shape as any test's injected fake -- resolved_device must
    # degrade to the configured value rather than raising.
    model = _FakeModel()
    transcriber = LocalTranscriber(_config(), model_factory=lambda: model)
    assert transcriber.resolved_device == "cpu"  # _config()'s device


def test_resolved_device_reflects_a_permanent_cpu_fallback() -> None:
    gpu_model = MagicMock()
    gpu_model.transcribe.side_effect = RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"
    )
    cpu_model = MagicMock()
    cpu_model.transcribe.return_value = (
        [_FakeSegment(text="hello world", avg_logprob=-0.2)],
        _FakeInfo(language="en", language_probability=0.95),
    )

    def side_effect(*args, **kwargs):
        return cpu_model if kwargs.get("device") == "cpu" else gpu_model

    config = STTConfig(model="tiny.en", device="cuda", compute_type="float16")
    with patch("convobox.stt.transcriber.WhisperModel", side_effect=side_effect):
        transcriber = LocalTranscriber(config)
        # MagicMock's auto-generated .model.device is a Mock, not a str,
        # so resolved_device falls back to _device_override here -- still
        # correctly reports "cpu", just via the fallback path rather than
        # introspecting a real ctranslate2 model.
        assert transcriber.resolved_device == "cpu"


# --- _register_cuda_dll_directories: fixes the real "Library cublas64_12.dll
# is not found or cannot be loaded" failure (live-confirmed on an NVIDIA
# 4060, 2026-07-22). os.add_dll_directory ALONE does not fix this (also
# live-confirmed) -- prepending the same directories to PATH does; see the
# function's own docstring for the root-cause reasoning. ---


def test_register_cuda_dll_directories_prepends_bin_dirs_to_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    fake_locations = {}
    for name in ("cublas", "cuda_nvrtc", "cuda_runtime"):
        pkg_dir = tmp_path / name
        bin_dir = pkg_dir / "bin"
        bin_dir.mkdir(parents=True)
        fake_locations[f"nvidia.{name}"] = pkg_dir

    def fake_find_spec(package: str):
        location = fake_locations.get(package)
        if location is None:
            return None
        spec = MagicMock()
        spec.submodule_search_locations = [str(location)]
        return spec

    monkeypatch.setattr("importlib.util.find_spec", fake_find_spec)
    monkeypatch.setattr("os.add_dll_directory", MagicMock(), raising=False)
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")

    _register_cuda_dll_directories()

    import os

    path_entries = os.environ["PATH"].split(os.pathsep)
    for name in ("cublas", "cuda_nvrtc", "cuda_runtime"):
        assert str(tmp_path / name / "bin") in path_entries


def test_register_cuda_dll_directories_is_a_noop_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", "/usr/bin")
    find_spec = MagicMock()
    monkeypatch.setattr("importlib.util.find_spec", find_spec)

    _register_cuda_dll_directories()

    find_spec.assert_not_called()


def test_register_cuda_dll_directories_is_a_noop_when_the_cuda_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")
    monkeypatch.setattr("importlib.util.find_spec", lambda package: None)
    monkeypatch.setattr("os.add_dll_directory", MagicMock(), raising=False)

    _register_cuda_dll_directories()  # must not raise

    import os

    assert os.environ["PATH"] == "C:\\Windows\\System32"
