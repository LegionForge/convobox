from __future__ import annotations

from pathlib import Path

from convobox.config import TTSConfig
from convobox.tts.base import TTSEngine
from convobox.tts.piper import PiperTTSEngine

DEFAULT_VOICES_DIR = Path(".models/piper")


def resolve_voice_paths(voice: str, voices_dir: Path = DEFAULT_VOICES_DIR) -> tuple[Path, Path]:
    """Map a Piper voice key (e.g. "en_US-lessac-medium") to its local files.

    Raises FileNotFoundError with a scripts/voice_picker.py hint rather than
    letting PiperVoice.load fail deeper in the stack with a less actionable
    "file not found" -- this is the first thing a misconfigured tts.voice
    hits, so the error needs to say what to run, not just what's missing.
    """
    model_path = voices_dir / f"{voice}.onnx"
    config_path = voices_dir / f"{voice}.onnx.json"
    if not model_path.exists() or not config_path.exists():
        raise FileNotFoundError(
            f"voice {voice!r} not found in {voices_dir} "
            f"(expected {model_path.name} + {config_path.name}). "
            f"Run: python scripts/voice_picker.py --download {voice}"
        )
    return model_path, config_path


def create_tts_engine(config: TTSConfig, voices_dir: Path = DEFAULT_VOICES_DIR) -> TTSEngine:
    """Build the TTSEngine described by config.tts -- the only place that reads it.

    TTSConfig.voice/rate/volume existed in config.py with nothing wired to
    them; every script constructed a PiperTTSEngine by hand with a
    hardcoded voice. This is that missing wiring.
    """
    if config.engine != "piper":
        raise NotImplementedError(
            f"tts.engine {config.engine!r} is not implemented; only 'piper' is available"
        )
    if config.voice is None:
        raise ValueError(
            "tts.voice is not set. Pick one with: python scripts/voice_picker.py"
        )
    model_path, config_path = resolve_voice_paths(config.voice, voices_dir)
    return PiperTTSEngine(
        model_path=str(model_path),
        config_path=str(config_path),
        rate=config.rate,
        volume=config.volume,
    )
