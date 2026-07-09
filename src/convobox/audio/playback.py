from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd


class AudioPlayer:
    """Plays float32 audio through sounddevice with interruptible playback.

    stop() enables barge-in: a response can be cut off the moment the user
    starts speaking, rather than waiting for playback to finish.
    """

    def __init__(self, device: str | int | None = None) -> None:
        self.device = device
        self._stream: sd.OutputStream | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Start playing samples. Non-blocking; replaces any current playback."""
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(samples, sample_rate), daemon=True
        )
        self._thread.start()

    def _run(self, samples: np.ndarray, sample_rate: int) -> None:
        channels = 1 if samples.ndim == 1 else samples.shape[1]
        blocksize = 1024
        stream = sd.OutputStream(
            samplerate=sample_rate,
            device=self.device,
            channels=channels,
            dtype="float32",
        )
        self._stream = stream
        stream.start()
        try:
            for start in range(0, len(samples), blocksize):
                if self._stop.is_set():
                    break
                stream.write(samples[start : start + blocksize])
        finally:
            stream.stop()
            stream.close()
            self._stream = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()
