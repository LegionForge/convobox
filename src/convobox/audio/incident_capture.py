"""Opt-in, bounded audio capture for diagnosing interaction incidents.

Audio is retained only in memory until ``trigger`` is called.  A trigger
writes a short pre-roll plus a bounded post-roll to a timestamped incident
directory, alongside a small JSON manifest.  Normal runs never construct this
class, so they neither retain nor write diagnostic audio.
"""

from __future__ import annotations

import json
import threading
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_SAMPLE_RATE = 16000

# observe_reference() is only ever called per real block WRITTEN to the
# output device (AudioPlayer.on_block_played's contract, not queue time --
# see aec.py's module docstring for why). If synthesis stalls between
# blocks, nothing calls this method for that real duration -- the reference
# channel would otherwise become time-compressed relative to mic-raw's
# continuous wall-clock recording, which is exactly the blind spot
# docs/field-notes/2026-07-26-reference-capture-is-time-compressed-not-wall-clock.md
# found: a cross-correlation between a time-compressed reference and a
# wall-clock-continuous mic signal can miss real echo entirely. A small
# tolerance (normal scheduling jitter between back-to-back blocks) avoids
# padding for noise; only a genuine gap gets silence inserted.
_REFERENCE_GAP_TOLERANCE_S = 0.05


def _pcm(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class IncidentCapture:
    """Keep an in-memory pre-roll and persist it only for a real incident."""

    def __init__(self, root: Path, before_s: float = 5.0, after_s: float = 10.0) -> None:
        if before_s <= 0 or after_s <= 0:
            raise ValueError("incident capture durations must be positive")
        self.root, self.before_s, self.after_s = root, before_s, after_s
        self._limit = int(before_s * _SAMPLE_RATE)
        self._history: dict[str, deque[np.ndarray]] = {
            "mic-raw": deque(), "mic-processed": deque(), "reference": deque(),
        }
        self._history_samples = {name: 0 for name in self._history}
        self._writers: dict[str, wave.Wave_write] | None = None
        self._directory: Path | None = None
        self._events: list[dict[str, Any]] = []
        self._ends_at = 0.0
        self._lock = threading.RLock()
        # Wall-clock time (time.monotonic() scale) the NEXT observe_reference
        # call is expected at if playback continues gap-free -- None before
        # the first call. See _REFERENCE_GAP_TOLERANCE_S above.
        self._reference_next_expected_at: float | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._writers is not None

    def observe_mic(self, raw: np.ndarray, processed: np.ndarray) -> None:
        self._observe("mic-raw", raw)
        self._observe("mic-processed", processed)

    def observe_reference(self, audio: np.ndarray, sample_rate: int) -> None:
        if sample_rate != _SAMPLE_RATE:
            positions = np.linspace(0, len(audio), round(len(audio) * _SAMPLE_RATE / sample_rate), endpoint=False)
            audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
        now = time.monotonic()
        if self._reference_next_expected_at is not None:
            gap_s = now - self._reference_next_expected_at
            if gap_s > _REFERENCE_GAP_TOLERANCE_S:
                silence = np.zeros(int(round(gap_s * _SAMPLE_RATE)), dtype=np.float32)
                self._observe("reference", silence)
        self._observe("reference", audio)
        self._reference_next_expected_at = now + len(audio) / _SAMPLE_RATE

    def trigger(self, event: str, metadata: dict[str, Any] | None = None) -> Path | None:
        """Persist an incident; related events share the same bounded capture."""
        with self._lock:
            if self._writers is not None:
                self._events.append({"event": event, "metadata": metadata or {}})
                self._write_manifest()
                return None
            now = time.monotonic()
            self._directory = self.root / time.strftime("%Y%m%d-%H%M%S")
            self._directory.mkdir(parents=True, exist_ok=False)
            self._writers = {name: self._open(name) for name in self._history}
            for name, chunks in self._history.items():
                for chunk in chunks:
                    self._writers[name].writeframes(_pcm(chunk))
            self._events = [{"event": event, "metadata": metadata or {}}]
            self._write_manifest()
            self._ends_at = now + self.after_s
            return self._directory

    def close(self) -> None:
        with self._lock:
            if self._writers is None:
                return
            for writer in self._writers.values():
                writer.close()
            self._writers = None

    def _observe(self, name: str, audio: np.ndarray) -> None:
        samples = np.asarray(audio, dtype=np.float32).copy()
        with self._lock:
            if self._writers is not None:
                self._writers[name].writeframes(_pcm(samples))
                if time.monotonic() >= self._ends_at:
                    self.close()
                return
            history = self._history[name]
            history.append(samples)
            self._history_samples[name] += len(samples)
            while self._history_samples[name] > self._limit:
                excess = self._history_samples[name] - self._limit
                oldest = history[0]
                if len(oldest) <= excess:
                    self._history_samples[name] -= len(history.popleft())
                else:
                    history[0] = oldest[excess:]
                    self._history_samples[name] -= excess

    def _open(self, name: str) -> wave.Wave_write:
        assert self._directory is not None  # nosec B101 -- set by trigger() before _open() is ever called
        writer = wave.open(str(self._directory / f"{name}.wav"), "wb")  # noqa: SIM115
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_SAMPLE_RATE)
        return writer

    def _write_manifest(self) -> None:
        assert self._directory is not None  # nosec B101 -- set by trigger() before _write_manifest() is ever called
        manifest = {
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "pre_roll_s": self.before_s,
            "post_roll_s": self.after_s,
            "events": self._events,
        }
        (self._directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
