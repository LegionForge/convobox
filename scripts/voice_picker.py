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
import difflib
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


def delete_voice(key: str, voices_dir: Path) -> list[Path]:
    """Remove a downloaded voice's files; returns what was deleted.

    A voice is exactly two files (<key>.onnx + <key>.onnx.json); a
    missing .json is tolerated (a half-finished download should still be
    deletable). Never touches anything else in the directory -- notably
    not voices.json, the catalog cache.
    """
    removed = []
    for candidate in (voices_dir / f"{key}.onnx", voices_dir / f"{key}.onnx.json"):
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate)
    return removed


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


_COMMANDS = [
    "search", "list", "play", "get", "delete", "use", "text", "rate", "volume", "help", "quit",
]

_HELP = """\
How this works: search the catalog, listen to candidates, then pick one.
Search results are NUMBERED -- every command that takes a voice accepts
either that number or the full voice key.

  search TERM     find voices by language or name.
                    search french        search spanish mexico
                    search en_GB         search irish
  list            show the voices already downloaded (numbered, so you
                  can 'play 1' them too)
  play N|KEY      hear a voice speak the sample text through your
                  speakers (offers to download it first if needed).
                    play 3               play en_GB-alba-medium
  get N|KEY       download a voice without playing it
  delete N|KEY    remove a downloaded voice's files from disk
                  (asks first; 'list' to see what's installed)
  use N|KEY       choose the voice -- prints the convobox.yaml snippet
                  when you quit
  text SENTENCE   change the sample text used by 'play'.
                    text How is the weather today?
                  ('text' alone resets to the default sentence)
  rate NUMBER     speech speed (1.0 normal, 1.2 faster, 0.8 slower)
  volume NUMBER   loudness (1.0 normal, 0.5 half)
  help            show this again
  quit            leave (prints your chosen voice's config snippet)

A typical session:
  > search english          (see what's available, numbered)
  > play 5                  (listen to #5 from those results)
  > text Testing, one two.  (try your own sentence)
  > play 5                  (hear it again with the new text)
  > use 5                   (that's the one)
  > quit                    (get the convobox.yaml snippet)
"""


def resolve_key(arg: str, numbered: list[str]) -> tuple[str | None, str | None]:
    """Turn a command argument into a voice key.

    Accepts either a number referring to the most recently displayed
    list (search results or 'list' output) or a literal voice key.
    Returns (key, error_message) -- exactly one is set.
    """
    if arg.isdigit():
        index = int(arg)
        if not numbered:
            return None, "no list to pick from yet -- run 'search' or 'list' first"
        if not 1 <= index <= len(numbered):
            return None, f"pick a number between 1 and {len(numbered)} (from the last list shown)"
        return numbered[index - 1], None
    return arg, None


def suggest_command(cmd: str) -> str | None:
    matches = difflib.get_close_matches(cmd, _COMMANDS, n=1, cutoff=0.5)
    return matches[0] if matches else None


def _print_numbered(catalog: Catalog, keys: list[str], installed: list[str]) -> None:
    for index, key in enumerate(keys, start=1):
        marker = "*" if key in installed else " "
        print(f" {index:>3}. {marker} {describe(catalog, key)}")


def _interactive(voices_dir: Path, refresh: bool) -> None:
    player = AudioPlayer()
    catalog = load_catalog(voices_dir, refresh=refresh)
    rate = 1.0
    volume = 1.0
    text = DEFAULT_SAMPLE_TEXT
    chosen: str | None = None
    numbered: list[str] = []  # what on-screen numbers currently refer to

    print(f"ConvoBox voice picker -- {len(catalog)} voices in the Piper catalog")
    print("(* marks voices already downloaded)")
    print()
    print(_HELP)

    while True:
        installed = installed_voices(voices_dir)
        print()
        status = f"[sample: {text!r} | rate {rate} | volume {volume}"
        status += f" | chosen: {chosen}]" if chosen else " | no voice chosen yet]"
        print(status)
        try:
            raw = input("voice-picker> ").strip()
        except EOFError:
            break
        if not raw:
            continue
        cmd, _, arg = raw.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd in ("help", "?"):
            print(_HELP)
        elif cmd == "search":
            if not arg:
                print("what should I search for? e.g.:  search german   search en_US")
                continue
            matches = search_catalog(catalog, arg)
            if not matches:
                print(f"nothing matching {arg!r} -- try a language name (e.g. 'search dutch')")
                continue
            numbered = matches[:_SEARCH_DISPLAY_CAP]
            _print_numbered(catalog, numbered, installed)
            if len(matches) > _SEARCH_DISPLAY_CAP:
                print(f" ... and {len(matches) - _SEARCH_DISPLAY_CAP} more (narrow your search)")
            print("next: 'play NUMBER' to hear one, 'use NUMBER' to choose it")
        elif cmd == "list":
            if not installed:
                print("no voices downloaded yet -- 'search' the catalog, then 'get' or 'play' one")
                continue
            numbered = list(installed)
            _print_numbered(catalog, numbered, installed)
        elif cmd == "text":
            text = arg or DEFAULT_SAMPLE_TEXT
            print(f"sample text is now: {text!r}")
        elif cmd in ("rate", "volume"):
            try:
                value = float(arg)
            except ValueError:
                example = "rate 1.2" if cmd == "rate" else "volume 0.8"
                print(f"{cmd} needs a number, e.g.:  {example}")
                continue
            if cmd == "rate":
                rate = value
            else:
                volume = value
            print(f"{cmd} is now {value} (re-'play' a voice to hear the difference)")
        elif cmd in ("get", "play", "use", "delete"):
            if not arg:
                print(f"usage: {cmd} NUMBER (from the last list) or {cmd} VOICE-KEY")
                continue
            key, error = resolve_key(arg, numbered)
            if key is None:
                print(error)
                continue
            if cmd == "delete":
                if key not in installed:
                    print(f"{key} is not downloaded ('list' shows what is)")
                    continue
                reply = input(f"delete {key} from disk? [y/N] ").strip().lower()
                if reply != "y":
                    continue
                for removed in delete_voice(key, voices_dir):
                    print(f"deleted {removed}")
                if chosen == key:
                    chosen = None
                    print("(that was the chosen voice -- choice cleared)")
            elif cmd == "get":
                try:
                    download(key, voices_dir)
                    print(f"downloaded -- 'play {arg}' to hear it")
                except Exception as exc:  # CLI: report and keep looping, not fatal
                    print(f"download failed: {exc}")
            elif cmd == "play":
                if key not in installed:
                    reply = input(
                        f"{key} is not downloaded -- download it now? [y/N] "
                    ).strip().lower()
                    if reply != "y":
                        continue
                    try:
                        download(key, voices_dir)
                    except Exception as exc:  # CLI: report and keep looping, not fatal
                        print(f"download failed: {exc}")
                        continue
                try:
                    audition(key, voices_dir, text, rate, volume, player)
                    print(f"that was {describe(catalog, key)} -- 'use {arg}' to choose it")
                except Exception as exc:  # CLI: report and keep looping, not fatal
                    print(f"audition failed: {exc}")
            else:  # use
                chosen = key
                print(f"chosen: {chosen} -- 'quit' to get the convobox.yaml snippet")
        else:
            hint = suggest_command(cmd)
            suffix = f" -- did you mean {hint!r}?" if hint else ""
            print(f"unknown command: {cmd!r}{suffix}  ('help' lists all commands)")

    if chosen:
        print_config_snippet(chosen, rate, volume)
    else:
        print("no voice selected (use 'use NUMBER-or-KEY' before quitting to get a config snippet)")


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voices-dir", default=str(DEFAULT_VOICES_DIR))
    parser.add_argument("--refresh-catalog", action="store_true", help="re-fetch the cached voice catalog")
    parser.add_argument("--list-installed", action="store_true")
    parser.add_argument("--search", metavar="TERM", help="search the catalog by name/language/code")
    parser.add_argument("--download", metavar="KEY")
    parser.add_argument("--delete", metavar="KEY", help="remove a downloaded voice's files")
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

    if args.delete:
        ran_something = True
        if args.delete not in installed_voices(voices_dir):
            print(f"{args.delete} is not downloaded in {voices_dir}")
        else:
            for removed in delete_voice(args.delete, voices_dir):
                print(f"deleted {removed}")

    if args.audition:
        ran_something = True
        player = AudioPlayer()
        audition(args.audition, voices_dir, args.text, args.rate, args.volume, player)
        print_config_snippet(args.audition, args.rate, args.volume)

    if not ran_something:
        _interactive(voices_dir, refresh=args.refresh_catalog)


if __name__ == "__main__":
    main()
