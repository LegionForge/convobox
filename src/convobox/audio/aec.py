"""Acoustic echo cancellation via WebRTC's audio processing module.

Wraps the `aec-audio-processing` package (WebRTC APM / AEC3 -- the same
canceller VoIP products use; BSD-3) behind ConvoBox's audio shapes:
float32 chunks of arbitrary length in, float32 out, with the 10ms
int16 framing APM demands handled internally.

Verified empirically before this module was written (the house rule):
a synthetic far-end signal echoed into the near-end at 50ms delay came
out attenuated by ~43dB after adaptation -- the echo lands at the noise
floor. See tests/test_aec.py, which pins that experiment as a test.

Two call sites, two threads, by design:

- feed_reverse(chunk, rate): the far-end reference -- called from the
  PLAYBACK thread with each block actually being written to the device
  (NOT at queue time: streamed synthesis runs faster than realtime, so
  queue-time feeding would race the reference several seconds ahead of
  the audio and blow APM's delay tolerance). Resampled to the
  canceller's rate internally.
- process(chunk): the near-end mic signal -- called from the capture
  path before VAD. Returns the echo-cancelled chunk.

Python's GIL serializes the underlying APM calls, so the two-thread use
is safe without extra locking.

The dependency is an optional extra (`pip install -e ".[aec]"`): its
wheels are Windows-only today, and CI's Linux runners must keep working
without it. Import failures surface at construction with instructions,
not at module import.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# A capture frame only counts toward the attenuation estimate if the
# far end was fed within this window -- attenuation of silence is
# meaningless and would corrupt the average.
_REVERSE_ACTIVE_WINDOW_S = 0.25

# APM's native format: 10ms frames. We run it at the pipeline's rate.
_AEC_RATE = 16000
_FRAME = 160  # 10ms at 16kHz


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear-interpolation resample -- reference-signal grade.

    The far-end reference doesn't need audiophile resampling; APM's
    adaptive filter absorbs small spectral error. Same approach as
    scripts/roundtrip_smoketest.py, for the same reason.
    """
    if source_rate == target_rate:
        return audio
    duration = len(audio) / source_rate
    target_len = int(duration * target_rate)
    source_x = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


class EchoCanceller:
    """Stateful AEC: feed what the speakers play, filter what the mic hears."""

    def __init__(self, delay_ms: int = 100) -> None:
        try:
            from aec_audio_processing import AudioProcessor
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise RuntimeError(
                "audio.echo_cancellation is enabled but the AEC package is "
                'not installed. Install it with: uv pip install -e ".[aec]" '
                "(Windows wheels; other platforms may need a source build)"
            ) from exc
        self._apm: Any = AudioProcessor(
            enable_aec=True, enable_ns=False, enable_agc=False, enable_vad=False
        )
        self._apm.set_stream_format(_AEC_RATE, 1)
        self._apm.set_reverse_stream_format(_AEC_RATE, 1)
        self._apm.set_stream_delay(delay_ms)
        self._delay_ms = delay_ms
        # Partial-frame carries: chunks arrive in arbitrary sizes; APM
        # only eats exact 10ms frames.
        self._reverse_carry = np.zeros(0, dtype=np.float32)
        self._capture_carry = np.zeros(0, dtype=np.float32)
        # Capture-side output must preserve chunk sizes 1:1 for the VAD,
        # so processed samples are pooled and re-cut to the input length.
        self._processed_pool = np.zeros(0, dtype=np.float32)
        # Telemetry (all cheap): lets UAT answer "is AEC actually doing
        # anything in THIS room?" with numbers instead of vibes.
        self.reverse_frames = 0
        self.capture_frames = 0
        self._reverse_last_fed = 0.0
        self._rms_in: deque[float] = deque(maxlen=400)  # ~4s of 10ms frames
        self._rms_out: deque[float] = deque(maxlen=400)

    def set_delay(self, delay_ms: int) -> None:
        """Update the render-to-capture delay hint (applied per frame)."""
        self._delay_ms = delay_ms

    @property
    def delay_ms(self) -> int:
        return self._delay_ms

    def attenuation_db(self) -> float | None:
        """Estimated echo attenuation over recent playback-active capture.

        Compares capture RMS in vs out for frames processed while the
        far end was recently fed. None until there's enough signal to
        say anything (short/quiet samples would just be noise). This is
        an ERLE-flavored estimate, not a lab measurement -- it includes
        the user's own speech when they double-talk -- but across a
        response's worth of frames it clearly separates "AEC inert"
        (~0dB) from "AEC converged" (15dB+).
        """
        if len(self._rms_in) < 20:
            return None
        rms_in = sum(self._rms_in) / len(self._rms_in)
        rms_out = sum(self._rms_out) / len(self._rms_out)
        if rms_in < 1e-4:
            return None  # effectively silence; ratio would be noise
        return 20 * math.log10(rms_in / max(rms_out, 1e-9))

    def reset_stats(self) -> None:
        self._rms_in.clear()
        self._rms_out.clear()

    def feed_reverse(self, chunk: np.ndarray, sample_rate: int) -> None:
        """Register far-end audio (what the speakers are playing right now)."""
        resampled = _resample(np.asarray(chunk, dtype=np.float32), sample_rate, _AEC_RATE)
        self._reverse_carry = np.concatenate([self._reverse_carry, resampled])
        while len(self._reverse_carry) >= _FRAME:
            frame, self._reverse_carry = (
                self._reverse_carry[:_FRAME],
                self._reverse_carry[_FRAME:],
            )
            self._apm.process_reverse_stream(_to_int16_bytes(frame))
            # Keep the delay hint fresh: APM consumes it per 10ms frame.
            self._apm.set_stream_delay(self._delay_ms)
            self.reverse_frames += 1
        self._reverse_last_fed = time.monotonic()

    def process(self, chunk: np.ndarray) -> np.ndarray:
        """Echo-cancel a near-end (mic) chunk; output length == input length.

        The first few chunks after startup are passed through with less
        than full cancellation while the pool fills and the filter
        adapts -- APM needs a few hundred ms of signal to converge, which
        is inherent to adaptive cancellation, not a defect here.
        """
        samples = np.asarray(chunk, dtype=np.float32)
        self._capture_carry = np.concatenate([self._capture_carry, samples])
        while len(self._capture_carry) >= _FRAME:
            frame, self._capture_carry = (
                self._capture_carry[:_FRAME],
                self._capture_carry[_FRAME:],
            )
            out_frame = _from_int16_bytes(self._apm.process_stream(_to_int16_bytes(frame)))
            self._processed_pool = np.concatenate([self._processed_pool, out_frame])
            self.capture_frames += 1
            if time.monotonic() - self._reverse_last_fed < _REVERSE_ACTIVE_WINDOW_S:
                self._rms_in.append(float(np.sqrt(np.mean(frame**2))))
                self._rms_out.append(float(np.sqrt(np.mean(out_frame**2))))
        if len(self._processed_pool) >= len(samples):
            result, self._processed_pool = (
                self._processed_pool[: len(samples)],
                self._processed_pool[len(samples) :],
            )
            return result
        # Not enough processed audio pooled yet (startup): pad with the
        # tail of the raw input so chunk timing never stalls the VAD.
        deficit = len(samples) - len(self._processed_pool)
        result = np.concatenate([self._processed_pool, samples[-deficit:]])
        self._processed_pool = np.zeros(0, dtype=np.float32)
        return result


def _to_int16_bytes(frame: np.ndarray) -> bytes:
    return (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _from_int16_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
