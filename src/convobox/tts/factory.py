from __future__ import annotations

import logging
from pathlib import Path

import httpx
import numpy as np

from convobox.config import TTSConfig
from convobox.tts.base import TTSEngine
from convobox.tts.kokoro import KokoroTTSEngine
from convobox.tts.piper import PiperTTSEngine

DEFAULT_VOICES_DIR = Path(".models/piper")

# Same release tag hosts both files (confirmed against the kokoro-onnx
# package's own README instructions, 2026-07-24) -- unlike Piper's
# per-voice HuggingFace catalog, Kokoro ships exactly one fixed
# model + voice-bundle pair, so there's no name to look up, just these
# two fixed filenames.
_KOKORO_RELEASE_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
_KOKORO_MODEL_FILENAME = "kokoro-v1.0.onnx"
_KOKORO_VOICES_FILENAME = "voices-v1.0.bin"

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


def _download_kokoro_asset(filename: str, dest: Path) -> None:
    logger.info(
        "Kokoro asset %r not downloaded yet -- fetching to %s (one-time, ~326MB "
        "for the model file)",
        filename, dest,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_KOKORO_RELEASE_BASE}/{filename}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        # read=None: these are large one-time downloads (the model file is
        # ~326MB) -- httpx's default 5s read timeout would abort mid-stream
        # on a normal connection. Same pattern as opencode.py's own
        # long-poll timeouts (short connect, unbounded read).
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=httpx.Timeout(10.0, read=None)
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
        tmp.replace(dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise FileNotFoundError(
            f"Kokoro asset {filename!r} could not be downloaded automatically "
            f"({exc}). Download it manually from {url} and place it at {dest}"
        ) from exc


def resolve_kokoro_model_paths(model_path: str, voices_path: str) -> tuple[Path, Path]:
    """Ensure the shared Kokoro model + voices files exist locally, downloading
    them from the upstream kokoro-onnx release first if either is missing.

    Mirrors resolve_voice_paths' auto-download-on-first-use convention
    (docs/ROADMAP.md's "carry the same convention to Kokoro's engine factory
    once it lands") -- unlike Piper's per-voice catalog, there's no voice
    name to look up here, just these two fixed files at whatever local path
    config.tts.model_path/voices_path name.
    """
    model = Path(model_path)
    voices = Path(voices_path)
    if not model.exists():
        _download_kokoro_asset(_KOKORO_MODEL_FILENAME, model)
    if not voices.exists():
        _download_kokoro_asset(_KOKORO_VOICES_FILENAME, voices)
    if not model.exists() or not voices.exists():
        raise FileNotFoundError(
            f"Kokoro model files did not download successfully to {model} / {voices}"
        )
    return model, voices


def list_kokoro_voices(voices_path: str) -> list[str]:
    """List every voice name bundled in a downloaded Kokoro voices file.

    Deliberately does NOT construct a KokoroTTSEngine to do this --
    Kokoro.get_voices() needs a full instance, which means loading the
    entire ~326MB ONNX model into an onnxruntime.InferenceSession just to
    read a name list. This reads the voices archive directly instead, the
    same bare `np.load(voices_path)` call kokoro_onnx's own __init__ uses
    internally (confirmed via inspect.getsource, not guessed) -- cheap
    (no model, no inference session) and gives the identical name list
    (verified live against a real downloaded voices-v1.0.bin: 54 names,
    e.g. "af_sarah", "bm_george", "jf_alpha" -- kokoro-onnx's own
    <lang><gender>_<name> convention).

    Returns [] if the file doesn't exist yet or isn't a readable archive
    (e.g. a partial download) -- callers should treat that as "not
    browsable yet, download it first," not a hard error.
    """
    path = Path(voices_path)
    if not path.exists():
        return []
    try:
        with np.load(path) as voices:
            return sorted(voices.files)
    except Exception:  # noqa: BLE001 -- best-effort discovery, never raise into a picker UI
        return []


def create_tts_engine(
    config: TTSConfig, voices_dir: Path = DEFAULT_VOICES_DIR
) -> TTSEngine:
    """Build the TTSEngine described by config.tts -- the only place that reads it."""
    if config.engine == "kokoro":
        if config.voice is None:
            raise ValueError("tts.voice is not set; Kokoro needs a voice name")
        model_path, voices_path = resolve_kokoro_model_paths(
            config.model_path, config.voices_path
        )
        return KokoroTTSEngine(
            model_path=str(model_path),
            voices_path=str(voices_path),
            voice=config.voice,
            speed=config.rate,
            lang=config.language,
        )

    if config.engine != "piper":
        raise NotImplementedError(
            f"tts.engine {config.engine!r} is not implemented; "
            "available engines are 'kokoro' and 'piper'"
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
