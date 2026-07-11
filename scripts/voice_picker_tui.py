"""Full-screen voice picker: browse, audition, and manage Piper voices.

The screen-oriented sibling of scripts/voice_picker.py (which keeps its
REPL and flag modes for scripting). Arrow keys move, highlighted keys
act, the layout reads the terminal size every frame and scales to it.

Rendering is deliberately ASCII-only (+ - | > * and letters) with
ANSI reverse-video for the selection bar -- no box-drawing glyphs, no
special fonts, so it displays identically on any terminal that speaks
ANSI (Windows Terminal, conhost with VT enabled, every POSIX terminal).

Keys:
  Up/Down, PgUp/PgDn, Home/End   move through the (filtered) voice list
  /       filter the list (type to narrow; Enter keeps it, Esc clears)
  P       play the highlighted voice (downloads it first if needed)
  S       stop playback
  D       download the highlighted voice
  X       delete the highlighted voice's files (asks first)
  Enter   choose the highlighted voice (snippet printed on quit)
  T       edit the sample text spoken by P
  + / -   speech rate up / down       < / >   volume down / up
  Q       quit (offers to save the chosen voice to convobox.yaml)

No new dependencies: hand-rolled ANSI + msvcrt (Windows) / termios
(POSIX) keyboard input, reusing scripts/voice_picker.py's catalog,
download, delete, and synthesis plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/voice_picker_tui.py`) and imported as
# scripts.voice_picker_tui (e.g. from a pytest test).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _console import use_utf8_console
from voice_picker import (
    DEFAULT_SAMPLE_TEXT,
    Catalog,
    delete_voice,
    download,
    installed_voices,
    load_catalog,
    offer_config_write,
    search_catalog,
)

from convobox.audio.playback import AudioPlayer
from convobox.config import TTSConfig
from convobox.tts import create_tts_engine
from convobox.tts.factory import DEFAULT_VOICES_DIR

_REVERSE = "\x1b[7m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
_MIN_WIDTH = 40
_MIN_HEIGHT = 12


# --- pure layout helpers (unit-tested) ---------------------------------


def fit(text: str, width: int) -> str:
    """Truncate (with ...) or pad text to exactly width columns."""
    if width <= 0:
        return ""
    if len(text) > width:
        return text[: width - 3] + "..." if width > 3 else text[:width]
    return text.ljust(width)


def viewport_start(selected: int, total: int, height: int, current_start: int) -> int:
    """Scroll the window just enough to keep the selection visible."""
    if total <= height:
        return 0
    start = current_start
    if selected < start:
        start = selected
    elif selected >= start + height:
        start = selected - height + 1
    return max(0, min(start, total - height))


def voice_row(catalog: Catalog, key: str, installed: bool, width: int) -> str:
    info = catalog.get(key, {})
    lang = info.get("language", {})
    language = f"{lang.get('name_english', '?')} ({lang.get('country_english', '?')})"
    quality = str(info.get("quality", "?"))
    speakers = info.get("num_speakers", 1)
    spk = f"{speakers}spk" if isinstance(speakers, int) and speakers > 1 else ""
    marker = "*" if installed else " "
    # Key column sized for the longest real catalog keys (~34 chars);
    # short fixed-width columns (quality, speakers) before the language so
    # narrow terminals truncate the language tail, not the useful bits.
    left = f" {marker} {key:<36.36} {quality:<8.8} {spk:<5.5} "
    return fit(left + language, width)


@dataclass
class TuiState:
    catalog: Catalog
    voices_dir: Path
    all_keys: list[str] = field(default_factory=list)
    filtered: list[str] = field(default_factory=list)
    installed: set[str] = field(default_factory=set)
    filter_text: str = ""
    selected: int = 0
    view_start: int = 0
    sample_text: str = DEFAULT_SAMPLE_TEXT
    rate: float = 1.0
    volume: float = 1.0
    chosen: str | None = None
    status: str = "Up/Down to browse, / to filter, P to play, Q to quit"
    mode: str = "browse"  # browse | filter | text | confirm-delete
    edit_buffer: str = ""

    def __post_init__(self) -> None:
        self.all_keys = sorted(self.catalog)
        self.refresh_installed()
        self.apply_filter()

    def refresh_installed(self) -> None:
        self.installed = set(installed_voices(self.voices_dir))

    def apply_filter(self) -> None:
        self.filtered = (
            search_catalog(self.catalog, self.filter_text) if self.filter_text else self.all_keys
        )
        self.selected = min(self.selected, max(0, len(self.filtered) - 1))

    def current_key(self) -> str | None:
        if not self.filtered:
            return None
        return self.filtered[self.selected]

    def move(self, delta: int) -> None:
        if self.filtered:
            self.selected = max(0, min(self.selected + delta, len(self.filtered) - 1))


def render(state: TuiState, width: int, height: int) -> list[str]:
    """Produce exactly `height` lines of exactly `width` visible columns.

    Pure: no I/O, no terminal calls -- what makes the layout testable.
    Only ANSI SGR codes (reverse/bold/reset) beyond printable ASCII.
    """
    width = max(width, _MIN_WIDTH)
    height = max(height, _MIN_HEIGHT)
    sep = "+" + "-" * (width - 2) + "+"
    lines: list[str] = []

    title = " ConvoBox Voice Picker"
    counts = f"{len(state.filtered)}/{len(state.catalog)} voices | {len(state.installed)} installed "
    lines.append(fit(title + " " * max(1, width - len(title) - len(counts)) + counts, width))

    if state.mode == "filter":
        filter_line = f" Filter: {state.edit_buffer}_   (Enter keep, Esc clear)"
    elif state.filter_text:
        filter_line = f" Filter: {state.filter_text}   (/ to change)"
    else:
        filter_line = " Filter: (none -- press / to filter, e.g. 'french')"
    lines.append(fit(filter_line, width))
    lines.append(sep)

    footer = [
        sep,
        fit(
            f" Sample: {state.sample_text[:max(0, width - 40)]!r}  rate {state.rate:g}  vol {state.volume:g}",
            width,
        ),
        fit(f" Chosen: {state.chosen or '(none yet -- Enter chooses the highlighted voice)'}", width),
        fit(" " + state.status, width),
        fit(
            " [Up/Dn] move  [/] filter  [P]lay  [S]top  [D]ownload  [X] delete",
            width,
        ),
        fit(" [Enter] choose  [T] text  [+/-] rate  [</>] volume  [Q]uit", width),
    ]

    list_height = height - len(lines) - len(footer)
    state.view_start = viewport_start(state.selected, len(state.filtered), list_height, state.view_start)
    visible = state.filtered[state.view_start : state.view_start + list_height]
    for row_index, key in enumerate(visible):
        absolute = state.view_start + row_index
        row = voice_row(state.catalog, key, key in state.installed, width - 2)
        pointer = ">" if absolute == state.selected else " "
        line = pointer + row + " "
        if absolute == state.selected:
            line = _REVERSE + line + _RESET
        lines.append(line)
    for _ in range(list_height - len(visible)):
        lines.append(fit("", width))

    lines.extend(footer)
    return lines


# --- terminal plumbing --------------------------------------------------


def _enable_ansi() -> None:
    if os.name == "nt":
        # SECURITY EXCEPTION: B605/B607 (os.system) -- the empty string is
        # the long-standing conhost trick to enable VT escape processing;
        # no user input is involved, nothing is actually executed. Same
        # pattern, same rationale as scripts/voice_tui.py.
        os.system("")  # nosec B605 B607


def read_key() -> str:
    """Block for one keypress; arrows/specials come back as names."""
    # sys.platform (not os.name): mypy narrows on it, so the POSIX branch
    # below type-checks as unreachable on Windows instead of erroring on
    # termios/tty being empty stubs there.
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "H": "UP", "P": "DOWN", "I": "PGUP", "Q": "PGDN", "G": "HOME", "O": "END",
            }.get(code, "")
        if ch == "\r":
            return "ENTER"
        if ch == "\x08":
            return "BACKSPACE"
        if ch == "\x1b":
            return "ESC"
        return ch

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            if ch in ("\r", "\n"):
                return "ENTER"
            if ch == "\x7f":
                return "BACKSPACE"
            return ch
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return "ESC"
        seq = sys.stdin.read(1)
        if seq != "[":
            return "ESC"
        code = sys.stdin.read(1)
        if code == "5" and sys.stdin.read(1) == "~":
            return "PGUP"
        if code == "6" and sys.stdin.read(1) == "~":
            return "PGDN"
        return {"A": "UP", "B": "DOWN", "H": "HOME", "F": "END"}.get(code, "")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def draw(state: TuiState) -> None:
    size = os.get_terminal_size()
    frame = render(state, size.columns, size.lines)
    # Home the cursor and overwrite in place (no clear-screen per frame:
    # avoids flicker); every line is padded to full width so leftovers
    # from the previous frame can't bleed through.
    sys.stdout.write("\x1b[H" + "\n".join(frame))
    sys.stdout.flush()


# --- actions ------------------------------------------------------------


def _play(state: TuiState, player: AudioPlayer) -> None:
    key = state.current_key()
    if key is None:
        state.status = "nothing to play (empty list)"
        return
    if key not in state.installed:
        state.status = f"downloading {key} ..."
        draw(state)
        try:
            download(key, state.voices_dir)
        except Exception as exc:
            state.status = f"download failed: {exc}"
            return
        state.refresh_installed()
    state.status = f"synthesizing with {key} ..."
    draw(state)
    try:
        tts = create_tts_engine(
            TTSConfig(voice=key, rate=state.rate, volume=state.volume), state.voices_dir
        )
        t0 = time.perf_counter()
        audio = asyncio.run(tts.synthesize(state.sample_text))
        synth_ms = (time.perf_counter() - t0) * 1000
        player.play(audio, tts.sample_rate)
        duration = len(audio) / tts.sample_rate
        state.status = f"playing {key} ({duration:.1f}s, synthesized in {synth_ms:.0f}ms) -- S stops"
    except Exception as exc:
        state.status = f"audition failed: {exc}"


def _download(state: TuiState) -> None:
    key = state.current_key()
    if key is None:
        return
    if key in state.installed:
        state.status = f"{key} is already downloaded"
        return
    state.status = f"downloading {key} ..."
    draw(state)
    try:
        download(key, state.voices_dir)
        state.refresh_installed()
        state.status = f"downloaded {key} -- P plays it"
    except Exception as exc:
        state.status = f"download failed: {exc}"


def _handle_browse(state: TuiState, key: str, player: AudioPlayer) -> bool:
    """Returns False when it's time to quit."""
    lowered = key.lower() if len(key) == 1 else key
    if lowered in ("q", "ESC"):
        return False
    if key == "UP":
        state.move(-1)
    elif key == "DOWN":
        state.move(1)
    elif key == "PGUP":
        state.move(-20)
    elif key == "PGDN":
        state.move(20)
    elif key == "HOME":
        state.move(-len(state.filtered))
    elif key == "END":
        state.move(len(state.filtered))
    elif lowered == "/":
        state.mode = "filter"
        state.edit_buffer = state.filter_text
    elif lowered == "p":
        _play(state, player)
    elif lowered == "s":
        player.stop()
        state.status = "playback stopped"
    elif lowered == "d":
        _download(state)
    elif lowered == "x":
        if state.current_key() is None:
            pass
        elif state.current_key() not in state.installed:
            state.status = f"{state.current_key()} is not downloaded (nothing to delete)"
        else:
            state.mode = "confirm-delete"
            # SECURITY EXCEPTION: B608 (hardcoded SQL expressions) -- this is
            # a UI status message that happens to contain the words "delete
            # ... from"; there is no SQL, no database, and no query engine
            # anywhere in this program. Mitigation: none needed, string is
            # only ever printed to the terminal.
            state.status = f"delete {state.current_key()} from disk? [y/N]"  # nosec B608
    elif key == "ENTER":
        chosen = state.current_key()
        if chosen is not None:
            state.chosen = chosen
            state.status = f"chosen: {chosen} -- Q quits and offers to save it to convobox.yaml"
    elif lowered == "t":
        state.mode = "text"
        state.edit_buffer = state.sample_text
        state.status = "type the new sample text (Enter done, Esc cancel)"
    elif lowered == "+":
        state.rate = round(state.rate + 0.1, 2)
    elif lowered == "-":
        state.rate = max(0.1, round(state.rate - 0.1, 2))
    elif lowered == "<":
        state.volume = max(0.0, round(state.volume - 0.1, 2))
    elif lowered == ">":
        state.volume = round(state.volume + 0.1, 2)
    return True


def _handle_edit(state: TuiState, key: str) -> None:
    """Shared editing for filter/text modes."""
    if key == "ENTER":
        if state.mode == "filter":
            state.filter_text = state.edit_buffer
            state.apply_filter()
            state.status = f"{len(state.filtered)} voice(s) match" if state.filter_text else "filter cleared"
        else:
            state.sample_text = state.edit_buffer or DEFAULT_SAMPLE_TEXT
            state.status = "sample text updated -- P plays it"
        state.mode = "browse"
    elif key == "ESC":
        if state.mode == "filter":
            state.filter_text = ""
            state.apply_filter()
        state.mode = "browse"
        state.status = "cancelled"
    elif key == "BACKSPACE":
        state.edit_buffer = state.edit_buffer[:-1]
        if state.mode == "filter":
            state.filter_text = state.edit_buffer
            state.apply_filter()
    elif len(key) == 1 and key.isprintable():
        state.edit_buffer += key
        if state.mode == "filter":
            state.filter_text = state.edit_buffer
            state.apply_filter()


def run_tui(voices_dir: Path, refresh: bool) -> None:
    catalog = load_catalog(voices_dir, refresh=refresh)
    state = TuiState(catalog=catalog, voices_dir=voices_dir)
    player = AudioPlayer()
    _enable_ansi()
    sys.stdout.write("\x1b[?25l\x1b[2J")  # hide cursor, clear once
    try:
        running = True
        while running:
            draw(state)
            key = read_key()
            if not key:
                continue
            if state.mode == "browse":
                running = _handle_browse(state, key, player)
            elif state.mode in ("filter", "text"):
                _handle_edit(state, key)
            elif state.mode == "confirm-delete":
                current = state.current_key()
                if key.lower() == "y" and current is not None:
                    delete_voice(current, state.voices_dir)
                    state.refresh_installed()
                    if state.chosen == current:
                        state.chosen = None
                    state.status = f"deleted {current}"
                else:
                    state.status = "delete cancelled"
                state.mode = "browse"
    finally:
        player.stop()
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")  # cursor back, clean exit
        sys.stdout.flush()
    if state.chosen:
        offer_config_write(state.chosen, state.rate, state.volume)
    else:
        print("no voice selected (highlight one and press Enter before quitting)")


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--voices-dir", default=str(DEFAULT_VOICES_DIR))
    parser.add_argument("--refresh-catalog", action="store_true")
    args = parser.parse_args()
    run_tui(Path(args.voices_dir), refresh=args.refresh_catalog)


if __name__ == "__main__":
    main()
