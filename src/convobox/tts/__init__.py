from convobox.config import TTSConfig
from convobox.tts.base import MAX_TEXT_LENGTH, TTSEngine, sanitize_text
from convobox.tts.piper import PiperTTSEngine

__all__ = [
    "MAX_TEXT_LENGTH",
    "PiperTTSEngine",
    "TTSEngine",
    "create_tts_engine",
    "sanitize_text",
]


def create_tts_engine(config: TTSConfig) -> TTSEngine:
    if config.engine == "piper":
        return PiperTTSEngine(model_path=config.model_path, config_path=config.config_path)
    raise ValueError(f"unknown tts.engine {config.engine!r} (only 'piper' is implemented)")
