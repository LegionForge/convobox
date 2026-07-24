from __future__ import annotations

import logging
from pathlib import Path

from convobox.config import TTSConfig
from convobox.tts.base import TTSEngine
from convobox.tts.piper import PiperTTSEngine

DEFAULT_VOICES_DIR = Path(".models/piper")

logger = logging.getLogger(__name__)


def resolve_voice_paths(
    voice: str, voices_dir: Path = DEFAULT_VOICES_DIR
) -> tuple[Path, Path]:
    """Map a Piper voice key (e.g. "en_US-lessac-medium") to its local files,
    downloading it first if it isn't cached yet.

    ConvoBox never bundles a voice you didn't ask for, but per the
    install-at-setup philosophy (docs/ROADMAP.md's "Pluggable STT/TTS
    engines"), a voice named in config that isn't present yet is fetched
    automatically here rather than making the operator run
    scripts/voice_picker.py by hand first -- mirrors
    convobox.stt.transcriber's existing one-time-download-then-offline
    pattern for the Whisper model. scripts/voice_picker.py itself remains
    the way to browse/audition/choose a voice before picking one; this is
    just the "it's configured, go get it" path.
    """
    model_path = voices_dir / f"{voice}.onnx"
    config_path = voices_dir / f"{voice}.onnx.json"
    if not model_path.exists() or not config_path.exists():
        logger.info(
            "TTS voice %r not downloaded yet -- fetching to %s (one-time; "
            "use scripts/voice_picker.py to browse/audition other voices)",
            voice, voices_dir,
        )
        from piper.download_voices import download_voice

        voices_dir.mkdir(parents=True, exist_ok=True)
        try:
            download_voice(voice, voices_dir)
        except Exception as exc:
            raise FileNotFoundError(
                f"voice {voice!r} could not be downloaded automatically ({exc}). "
                f"Browse available voices with: python scripts/voice_picker.py"
            ) from exc
    if not model_path.exists() or not config_path.exists():
        raise FileNotFoundError(
            f"voice {voice!r} download did not produce the expected files in "
            f"{voices_dir} (expected {model_path.name} + {config_path.name})"
        )
    return model_path, config_path


def create_tts_engine(
    config: TTSConfig, voices_dir: Path = DEFAULT_VOICES_DIR
) -> TTSEngine:
    """Build the TTSEngine described by config.tts -- the only place that reads it.

    TTSConfig.voice/rate/volume existed in config.py with nothing wired to
    them; every script constructed a PiperTTSEngine by hand with a
    hardcoded voice. This is that missing wiring.
    """
    if config.engine != "piper":
        raise NotImplementedError(
            f"tts.engine {config.engine!r} is not implemented; "
            f"only 'piper' is available"
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
        speaker=config.speaker,
    )
