from __future__ import annotations

from convobox.config import STTConfig
from convobox.stt.base import STTEngine

# Accepted aliases for the one implemented engine.
_FASTER_WHISPER = frozenset({"faster-whisper", "whisper", "faster_whisper"})


def create_stt_engine(config: STTConfig) -> STTEngine:
    """Build the STTEngine described by config.stt.

    The symmetric counterpart to create_tts_engine / create_backend_adapter:
    a single dispatch point so a new STT engine (or an install-at-setup
    plugin) slots in here without the runner knowing which engine it got.
    """
    if config.engine in _FASTER_WHISPER:
        # Imported lazily so selecting a different engine wouldn't pay
        # faster-whisper's import cost, and so this module stays importable
        # without the heavy dependency present.
        from convobox.stt.transcriber import LocalTranscriber

        return LocalTranscriber(config)
    raise ValueError(
        f"unknown stt.engine {config.engine!r} (implemented: 'faster-whisper')"
    )
