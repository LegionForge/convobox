"""Shared helpers for this repo's terminal scripts: UTF-8 I/O and raw-mode
single-keypress reading (settings_tui.py, voice_picker_tui.py).
"""

from __future__ import annotations

import os
import sys


def use_utf8_console() -> None:
    """Make stdin/stdout/stderr handle non-Latin script, everywhere.

    Windows' legacy console codepage (cp1252 etc.) can neither print nor
    read most of what a multilingual voice/STT script needs. Caught live
    (scripts/voice_picker.py, 2026-07-10): --text with Cyrillic crashed
    printing it with UnicodeEncodeError, and typed Japanese into an
    interactive prompt came back mojibake'd through stdin. reconfigure is
    a no-op on platforms already using a UTF-8-capable stream (most
    Linux/macOS terminals, and Windows Terminal in its default UTF-8
    codepage), and errors="replace" (output only; stdin keeps strict
    decoding so a genuinely undecodable byte surfaces as an error instead
    of silently corrupting a phrase about to be synthesized) keeps a more
    exotic stream from crashing the whole run over one print statement.
    """
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "replace"), (sys.stderr, "replace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors=errors)


# --- read_key() -----------------------------------------------------------
#
# 2026-09-02: settings_tui.py and voice_picker_tui.py each had their OWN
# copy of this. voice_picker_tui.py's copy never received the three fixes
# below (all found live, in settings_tui.py, on 2026-08-30/31) -- it still
# used tty.setraw()'s default TCSAFLUSH, sys.stdin.read(1), and a 50ms
# escape-continuation timeout. The 50ms timeout combined with ESC being
# bound directly to an UNCONFIRMED quit in voice_picker_tui.py (unlike
# settings_tui.py, which confirms first) made this concretely visible as
# "pressing an arrow key sometimes just quits the picker" (JP, reported
# live on macOS, 2026-09-02): a real arrow-key press whose bytes arrived
# more than 50ms apart got its ESC read alone, misread as a bare Escape,
# quitting instantly -- with the leftover "["/direction-letter bytes then
# surfacing as literal keystrokes on the NEXT read_key() call. One shared,
# already-hardened implementation instead of two copies that can drift
# apart again the same way.
_WIN_SCAN_TO_KEY = {
    "H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
    "G": "HOME", "O": "END", "I": "PGUP", "Q": "PGDN",
}
_CSI_LETTER_TO_KEY = {"A": "UP", "B": "DOWN", "D": "LEFT", "C": "RIGHT", "H": "HOME", "F": "END"}
# Numeric CSI sequences ("\x1b[5~", "\x1b[6~"), terminated by '~' -- a
# different shape than the single-letter ones above, so read separately.
_CSI_NUMBER_TO_KEY = {"5": "PGUP", "6": "PGDN"}


def _read_utf8_char(fd: int) -> str:
    """Read exactly one UTF-8 character from a raw fd via os.read() only.

    Used instead of sys.stdin.read(1) -- see read_key()'s own comment for
    why mixing that buffered read with a raw-fd select() call caused a
    live, reproduced bug. A leading byte's high bits say how many
    continuation bytes (0-3) a multi-byte character needs; ASCII
    (single-byte) input, the overwhelming majority of keystrokes here,
    takes the fast path with no extra reads.
    """
    first = os.read(fd, 1)
    if not first:
        return ""
    lead = first[0]
    if lead < 0x80:
        extra = 0
    elif lead >> 5 == 0b110:
        extra = 1
    elif lead >> 4 == 0b1110:
        extra = 2
    elif lead >> 3 == 0b11110:
        extra = 3
    else:
        extra = 0
    raw = first
    for _ in range(extra):
        raw += os.read(fd, 1)
    return raw.decode("utf-8", errors="replace")


def read_key() -> str:
    """Block for one keypress; arrows/Home/End/PgUp/PgDn come back as
    names, everything else as the literal character (or "ENTER"/
    "BACKSPACE"/"ESC")."""
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return _WIN_SCAN_TO_KEY.get(code, "")
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
        # TCSANOW, not tty.setraw()'s own default (TCSAFLUSH): this runs at
        # the top of EVERY read_key() call, not just once at startup, and
        # TCSAFLUSH discards any bytes already sitting in the kernel's
        # input queue at the exact moment it's applied. Live-reported
        # 2026-08-31: pressing two different arrow keys shortly after each
        # other (e.g. Right then Left) had the second one silently vanish
        # -- the TUI stayed on Right's result as if Left had never been
        # pressed. Reproduced directly with a pty harness (not inferred):
        # feeding Right immediately followed by Left showed the Left bytes
        # discarded right here, and the next read_key() call then hung
        # waiting on genuinely new input that was never coming. TCSANOW
        # switches into raw mode without touching whatever's already
        # queued.
        tty.setraw(fd, termios.TCSANOW)
        # Read via the raw fd (os.read), never sys.stdin.read(): the same
        # 2026-08-31 pty repro also caught a second, independent bug in an
        # earlier version -- Python's TextIOWrapper does its own userspace
        # buffering, so a single sys.stdin.read(1) call can silently pull
        # several already-arrived bytes into that buffer in one syscall.
        # The select() below only sees the raw kernel fd, which by then
        # has nothing left pending, so it times out on data that was
        # sitting right there the whole time, just invisible to it.
        # Reading everything through os.read() keeps what's been consumed
        # exactly in sync with what select() can see. Text fields can
        # contain non-ASCII input, so the leading byte is decoded as a
        # full UTF-8 character (1-4 bytes), not assumed to be a single
        # byte; the CSI/SS3 sequence bytes that follow an ESC are always
        # plain ASCII by protocol.
        ch = _read_utf8_char(fd)
        if ch != "\x1b":
            if ch in ("\r", "\n"):
                return "ENTER"
            if ch == "\x7f":
                return "BACKSPACE"
            return ch
        # Live-reported 2026-08-30 on a slower (4th-gen i7) Linux laptop:
        # arrow keys needed multiple presses before anything moved.
        # CONVOBOX_TUI_DEBUG_KEYS instrumentation caught it directly, not
        # by inference: a single Right-arrow press was consistently split
        # into three separate read_key() calls -- 'ESC' (this select()
        # timing out), then '[' and 'C' each read instantly on the NEXT
        # two calls (already sitting in the kernel buffer by then, just
        # arriving a beat after this timeout fired) -- each landing as an
        # independent no-op instead of one "RIGHT". An earlier attempt
        # widened this from 50ms to 300ms on the same theory; live logs
        # from that attempt showed 300ms still wasn't consistently enough
        # (repeated ESC/[/letter splits recorded with ~400-500ms between
        # them). Widened further to 1.0s: still a non-issue for the one
        # real standalone-ESC case (cancelling a modal) this timeout
        # exists to keep responsive -- a human cannot perceive a <1s delay
        # there as broken -- while giving real multi-byte sequences a much
        # larger margin on this hardware's apparent inter-byte latency.
        # voice_picker_tui.py's own copy of this, before it was merged
        # into this shared function, still used the old 50ms value -- see
        # this function's own module-level comment for what that caused.
        if not select.select([fd], [], [], 1.0)[0]:
            return "ESC"
        seq = os.read(fd, 1).decode("ascii", errors="replace")
        # Arrow/Home/End keys arrive as either CSI ("\x1b[A") or SS3
        # ("\x1bOA") sequences depending on the terminal's cursor-key mode
        # (DECCKM) -- a real, live-reported gap, 2026-08-30: this only ever
        # recognized CSI, so a terminal/multiplexer sending SS3 (common
        # depending on TERM/DECCKM state) had its second byte ("O")
        # swallowed here as a bare ESC, and the still-unread direction
        # letter (A/B/C/D) then surfaced as a literal keystroke on the
        # NEXT read_key() call instead of navigating. Both forms use the
        # same direction-letter code, so one lookup table covers both.
        if seq not in ("[", "O"):
            return "ESC"
        code = os.read(fd, 1).decode("ascii", errors="replace")
        if code in _CSI_NUMBER_TO_KEY:
            # PgUp/PgDn ("\x1b[5~"/"\x1b[6~") are terminated by a trailing
            # '~', a different shape than the single-letter sequences --
            # only recognize them if that terminator is actually there.
            terminator = os.read(fd, 1).decode("ascii", errors="replace")
            return _CSI_NUMBER_TO_KEY[code] if terminator == "~" else ""
        return _CSI_LETTER_TO_KEY.get(code, "")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
