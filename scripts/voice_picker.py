"""Browse, download, and audition Piper voices; pick one for convobox.yaml.

Every TTS smoke test before this hardcoded one voice
(en_US-lessac-medium). Piper's catalog has 163 voices across 44
languages (https://huggingface.co/rhasspy/piper-voices) -- this is the
tool for trying others without hand-editing scripts.

Interactive mode (default, no flags): search the catalog, download and
audition voices through real speakers, adjust rate/volume, then print
the convobox.yaml snippet for whichever one you land on.

Flag mode, for scripting:
    python scripts/voice_picker.py --list-installed
    python scripts/voice_picker.py --search french
    python scripts/voice_picker.py --download fr_FR-siwis-medium
    python scripts/voice_picker.py --audition fr_FR-siwis-medium --text "Bonjour."

Reuses convobox.tts.create_tts_engine for actual synthesis (the same
code path a real ConvoBox session uses, not a hand-rolled construction)
and piper.download_voices.download_voice (already vetted, already used
by scripts/bootstrap_windows.ps1) for fetching voice files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/voice_picker.py`, where it's __main__ and
# Python auto-adds its own directory) and imported as scripts.voice_picker
# (e.g. from a pytest test), where nothing does that automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console

from convobox.audio.playback import AudioPlayer
from convobox.config import TTSConfig
from convobox.tts import create_tts_engine
from convobox.tts.factory import DEFAULT_VOICES_DIR

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json?download=true"
DEFAULT_SAMPLE_TEXT = "The quick brown fox jumps over the lazy dog."
_SEARCH_DISPLAY_CAP = 30

Catalog = dict[str, dict[str, Any]]


def load_catalog(voices_dir: Path, refresh: bool = False) -> Catalog:
    """Fetch Piper's voice catalog, cached locally after the first fetch."""
    cache_path = voices_dir / "voices.json"
    if not refresh and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    print(f"fetching voice catalog from {CATALOG_URL} ...", file=sys.stderr)
    # SECURITY EXCEPTION: B310 (urlopen: audit permitted schemes) --
    # CATALOG_URL is a hardcoded https:// module constant, not user input or
    # a variable built from one; same pattern piper.download_voices's own
    # list_voices()/download_voice() use internally for this exact file.
    # Mitigation: no scheme/host ever reaches this from a CLI arg or config.
    with urlopen(CATALOG_URL) as response:  # nosec B310
        data: Catalog = json.load(response)
    voices_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def installed_voices(voices_dir: Path) -> list[str]:
    if not voices_dir.exists():
        return []
    return sorted(p.stem for p in voices_dir.glob("*.onnx"))


def search_catalog(catalog: Catalog, query: str) -> list[str]:
    query = query.lower()
    return sorted(
        key
        for key, info in catalog.items()
        if query in key.lower()
        or query in info["language"]["name_english"].lower()
        or query in info["language"]["code"].lower()
    )


def describe(catalog: Catalog, key: str) -> str:
    info = catalog.get(key)
    if info is None:
        return key
    lang = info["language"]["name_english"]
    country = info["language"]["country_english"]
    quality = info["quality"]
    speakers = info["num_speakers"]
    speaker_note = f", {speakers} speakers" if speakers > 1 else ""
    return f"{key}  ({lang}, {country} - {quality}{speaker_note})"


def download(key: str, voices_dir: Path) -> None:
    from piper.download_voices import download_voice

    voices_dir.mkdir(parents=True, exist_ok=True)
    print(f"downloading {key} ...")
    download_voice(key, voices_dir)
    print(f"done: {voices_dir / (key + '.onnx')}")


def audition(
    key: str, voices_dir: Path, text: str, rate: float, volume: float, player: AudioPlayer
) -> None:
    print(f"loading {key} ...")
    tts = create_tts_engine(TTSConfig(voice=key, rate=rate, volume=volume), voices_dir)
    print(f"synthesizing: {text!r}")
    t0 = time.perf_counter()
    audio = asyncio.run(tts.synthesize(text))
    synth_ms = (time.perf_counter() - t0) * 1000
    duration_s = len(audio) / tts.sample_rate
    print(f"synthesized {duration_s:.2f}s of audio in {synth_ms:.0f}ms, playing...")
    player.play(audio, tts.sample_rate)
    player.wait()


def print_config_snippet(key: str, rate: float, volume: float) -> None:
    print()
    print("Add to convobox.yaml:")
    print("tts:")
    print(f'  voice: "{key}"')
    if rate != 1.0:
        print(f"  rate: {rate}")
    if volume != 1.0:
        print(f"  volume: {volume}")


def _interactive(voices_dir: Path, refresh: bool) -> None:
    player = AudioPlayer()
    catalog = load_catalog(voices_dir, refresh=refresh)
    rate = 1.0
    volume = 1.0
    text = DEFAULT_SAMPLE_TEXT
    chosen: str | None = None

    print(f"ConvoBox voice picker -- {len(catalog)} voices in the Piper catalog")
    print("commands: search TERM | play KEY | get KEY | text ... | rate F | volume F | use KEY | quit")

    while True:
        installed = installed_voices(voices_dir)
        print()
        print(f"installed ({len(installed)}): " + (", ".join(installed) if installed else "(none)"))
        print(f"sample text: {text!r}   rate={rate}  volume={volume}")
        try:
            raw = input("> ").strip()
        except EOFError:
            break
        if not raw:
            continue
        cmd, _, arg = raw.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "search":
            matches = search_catalog(catalog, arg)
            for match_key in matches[:_SEARCH_DISPLAY_CAP]:
                marker = "*" if match_key in installed else " "
                print(f" {marker} {describe(catalog, match_key)}")
            if not matches:
                print("no matches")
            elif len(matches) > _SEARCH_DISPLAY_CAP:
                print(f" ... and {len(matches) - _SEARCH_DISPLAY_CAP} more (narrow your search)")
        elif cmd == "text":
            text = arg or DEFAULT_SAMPLE_TEXT
        elif cmd in ("rate", "volume"):
            try:
                value = float(arg)
            except ValueError:
                print(f"{cmd} must be a number")
                continue
            if cmd == "rate":
                rate = value
            else:
                volume = value
        elif cmd == "get":
            if not arg:
                print("usage: get KEY")
                continue
            try:
                download(arg, voices_dir)
            except Exception as exc:  # CLI: report and keep looping, not fatal
                print(f"download failed: {exc}")
        elif cmd == "play":
            if not arg:
                print("usage: play KEY")
                continue
            if arg not in installed:
                reply = input(f"{arg} is not downloaded -- download it now? [y/N] ").strip().lower()
                if reply != "y":
                    continue
                try:
                    download(arg, voices_dir)
                except Exception as exc:  # CLI: report and keep looping, not fatal
                    print(f"download failed: {exc}")
                    continue
            try:
                audition(arg, voices_dir, text, rate, volume, player)
            except Exception as exc:  # CLI: report and keep looping, not fatal
                print(f"audition failed: {exc}")
        elif cmd == "use":
            chosen = arg or chosen
            if chosen:
                print(f"selected: {chosen}")
        else:
            print(f"unknown command: {cmd!r}")

    if chosen:
        print_config_snippet(chosen, rate, volume)
    else:
        print("no voice selected (use 'use KEY' before quitting to get a config snippet)")


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voices-dir", default=str(DEFAULT_VOICES_DIR))
    parser.add_argument("--refresh-catalog", action="store_true", help="re-fetch the cached voice catalog")
    parser.add_argument("--list-installed", action="store_true")
    parser.add_argument("--search", metavar="TERM", help="search the catalog by name/language/code")
    parser.add_argument("--download", metavar="KEY")
    parser.add_argument("--audition", metavar="KEY", help="requires the voice to already be downloaded")
    parser.add_argument("--text", default=DEFAULT_SAMPLE_TEXT)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=1.0)
    args = parser.parse_args()

    voices_dir = Path(args.voices_dir)
    ran_something = False

    if args.list_installed:
        ran_something = True
        installed = installed_voices(voices_dir)
        print(f"installed voices in {voices_dir} ({len(installed)}):")
        for key in installed:
            print(f"  {key}")

    if args.search is not None:
        ran_something = True
        catalog = load_catalog(voices_dir, refresh=args.refresh_catalog)
        installed_set = set(installed_voices(voices_dir))
        matches = search_catalog(catalog, args.search)
        print(f"{len(matches)} voice(s) matching {args.search!r}:")
        for key in matches:
            marker = "*" if key in installed_set else " "
            print(f" {marker} {describe(catalog, key)}")

    if args.download:
        ran_something = True
        download(args.download, voices_dir)

    if args.audition:
        ran_something = True
        player = AudioPlayer()
        audition(args.audition, voices_dir, args.text, args.rate, args.volume, player)
        print_config_snippet(args.audition, args.rate, args.volume)

    if not ran_something:
        _interactive(voices_dir, refresh=args.refresh_catalog)


if __name__ == "__main__":
    main()
