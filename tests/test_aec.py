from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip(
    "aec_audio_processing",
    reason="AEC extra not installed (Windows-only wheels; install with .[aec])",
)

from convobox.audio.aec import EchoCanceller, _resample  # noqa: E402

_SR = 16000


def _farend(seconds: float) -> np.ndarray:
    t = np.arange(int(_SR * seconds)) / _SR
    return (0.5 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 933 * t)).astype(
        np.float32
    )


def test_synthetic_echo_is_suppressed_by_at_least_20db() -> None:
    # The empirical experiment that justified adopting the library,
    # pinned as a regression test: a far-end signal echoed into the mic
    # at 50ms delay and -4dB must come out heavily attenuated once the
    # adaptive filter converges. Measured ~43dB on adoption day; the
    # 20dB floor here leaves margin for library-version variance while
    # still proving real cancellation (20dB = 10x amplitude reduction).
    rng = np.random.default_rng(7)
    canceller = EchoCanceller(delay_ms=50)
    farend = _farend(4.0)
    delay = int(0.05 * _SR)
    mic = np.zeros_like(farend)
    mic[delay:] = 0.6 * farend[:-delay]
    mic += 0.002 * rng.standard_normal(len(mic)).astype(np.float32)

    chunk = 512  # deliberately NOT a multiple of the 160-sample APM frame
    in_rms: list[float] = []
    out_rms: list[float] = []
    for start in range(0, len(farend) - chunk, chunk):
        canceller.feed_reverse(farend[start : start + chunk], _SR)
        out = canceller.process(mic[start : start + chunk])
        in_rms.append(float(np.sqrt(np.mean(mic[start : start + chunk] ** 2))))
        out_rms.append(float(np.sqrt(np.mean(out**2))))

    last_second = _SR // chunk
    suppression_db = 20 * math.log10(
        (sum(in_rms[-last_second:]) / last_second)
        / max(sum(out_rms[-last_second:]) / last_second, 1e-9)
    )
    assert suppression_db >= 20.0, f"only {suppression_db:.1f} dB of echo suppression"


def test_near_end_speech_survives_when_no_echo_present() -> None:
    # Double-talk sanity: with silence on the far end, the near-end
    # signal must pass through essentially intact (no self-destruction).
    canceller = EchoCanceller(delay_ms=50)
    speech = _farend(2.0) * 0.4  # stands in for the user's voice
    silence = np.zeros(160 * 4, dtype=np.float32)

    out_rms = 0.0
    in_rms = 0.0
    chunk = 640
    for start in range(0, len(speech) - chunk, chunk):
        canceller.feed_reverse(silence, _SR)
        out = canceller.process(speech[start : start + chunk])
        if start > len(speech) // 2:  # after warm-up
            in_rms += float(np.sqrt(np.mean(speech[start : start + chunk] ** 2)))
            out_rms += float(np.sqrt(np.mean(out**2)))

    assert out_rms >= 0.5 * in_rms, "near-end speech was destroyed without any echo present"


def test_process_preserves_chunk_length_for_arbitrary_sizes() -> None:
    # The VAD depends on chunk timing; output length must equal input
    # length even for sizes that don't divide into 10ms frames.
    canceller = EchoCanceller(delay_ms=50)
    for size in (512, 160, 100, 1, 333, 4096):
        chunk = np.zeros(size, dtype=np.float32)
        assert len(canceller.process(chunk)) == size


def test_feed_reverse_accepts_tts_sample_rate() -> None:
    # The reference arrives at Piper's 22.05kHz; must not raise and must
    # buffer correctly across odd block sizes.
    canceller = EchoCanceller(delay_ms=50)
    block = np.zeros(1024, dtype=np.float32)
    for _ in range(10):
        canceller.feed_reverse(block, 22050)


def test_resample_preserves_duration() -> None:
    audio = np.zeros(22050, dtype=np.float32)  # 1s at 22.05k
    out = _resample(audio, 22050, 16000)
    assert len(out) == 16000


def test_enabling_without_package_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if name == "aec_audio_processing":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match=r"\[aec\]"):
        EchoCanceller()
