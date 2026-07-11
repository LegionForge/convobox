"""Deferred sounddevice import.

sounddevice loads the native PortAudio library the moment it is imported
and raises OSError when that library is absent (Linux wheels don't bundle
PortAudio the way Windows/macOS wheels do — first seen as CI pytest
collection dying with "OSError: PortAudio library not found"). Importing
convobox.audio must not require working audio libraries; only actually
opening a stream may. Callers fetch the module through this function at
use time instead of at import time, which also gives tests a seam to
substitute fake stream classes without needing PortAudio present.
"""

from __future__ import annotations

from typing import Any


def import_sounddevice() -> Any:
    import sounddevice

    return sounddevice
