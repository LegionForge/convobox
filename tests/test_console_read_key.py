"""Real pty-driven regression tests for _console.read_key() -- the shared
implementation settings_tui.py and voice_picker_tui.py both call.

A plain unit test that monkeypatches read_key() (as most TUI tests do)
can't exercise the bug this file guards against: it's specifically about
the RAW BYTE-TIMING of an escape sequence arriving across multiple
select() wakeups, which only a real pty reproduces. See
docs/field-notes -- this is the same class of bug already found and
fixed in settings_tui.py's own copy on 2026-08-30/31, then found AGAIN
in voice_picker_tui.py's un-merged duplicate on 2026-09-02 (JP, live on
macOS: an arrow-key press sometimes just quit the picker outright).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# pty/fcntl/termios are POSIX-only -- this file's whole premise (a real
# pty reproducing an escape sequence's raw byte-timing) has no Windows
# equivalent. Guarded at collection time (not per-test) because the
# unconditional stdlib imports below would otherwise raise
# ModuleNotFoundError and abort the ENTIRE test run's collection on
# Windows, not just skip this file -- caught live, 2026-09-03 (Helios).
if sys.platform == "win32":
    pytest.skip(
        "pty/fcntl/termios are POSIX-only -- see this module's own docstring",
        allow_module_level=True,
    )

import fcntl  # noqa: E402
import pty  # noqa: E402
import select  # noqa: E402
import struct  # noqa: E402
import termios  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

# The subprocess under test: reads N keys via _console.read_key() and
# prints one line per key -- a minimal harness, not the real TUI, so a
# failure here can only be read_key() itself, never surrounding app logic.
_HELPER_SOURCE = """
import sys
sys.path.insert(0, {scripts_dir!r})
from _console import read_key
for _ in range({count}):
    print(read_key(), flush=True)
"""


def _open_pty() -> tuple[int, int]:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    return master, slave


def _start_helper(master: int, slave: int, count: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HELPER_SOURCE.format(scripts_dir=str(_SCRIPTS_DIR), count=count)],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "TERM": "xterm-256color"},
    )
    os.close(slave)
    return proc


def _drain(master: int, timeout: float) -> bytes:
    out = b""
    while select.select([master], [], [], timeout)[0]:
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_read_key_recognizes_an_arrow_key_whose_bytes_arrive_slowly() -> None:
    # 200ms between each byte of the escape sequence -- comfortably past
    # the OLD 50ms select() timeout voice_picker_tui.py's own (now-removed)
    # duplicate used, comfortably under the shared implementation's fixed
    # 1.0s one. A correct read_key() call returns "UP" for this, not a
    # bare "ESC" (which is what the pre-fix code misread it as).
    master, slave = _open_pty()
    proc = _start_helper(master, slave, count=1)
    try:
        time.sleep(0.2)
        os.write(master, b"\x1b")
        time.sleep(0.2)
        os.write(master, b"[")
        time.sleep(0.2)
        os.write(master, b"A")
        out = _drain(master, timeout=2.0)
        assert out.strip() == b"UP"
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_read_key_recognizes_a_ss3_arrow_sequence() -> None:
    # SS3 form ("\x1bOA"), the other real-world encoding for arrow keys
    # depending on the terminal's DECCKM cursor-key mode -- found missing
    # in settings_tui.py's own history (2026-08-30); confirms the shared
    # implementation still covers it after the 2026-09-02 merge.
    master, slave = _open_pty()
    proc = _start_helper(master, slave, count=1)
    try:
        # Startup grace period: the pty starts in cooked/echo mode until
        # read_key()'s own tty.setraw() call takes effect -- writing before
        # that window closes gets the bytes echoed back literally instead
        # of consumed as raw input (caught live writing this test).
        time.sleep(0.3)
        os.write(master, b"\x1bOA")
        out = _drain(master, timeout=2.0)
        assert out.strip() == b"UP"
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_read_key_recognizes_page_up_and_page_down() -> None:
    # voice_picker_tui.py-specific need (PgUp/PgDn to jump 20 rows) --
    # settings_tui.py never emits these, but the shared function must
    # still support both callers' full vocabularies.
    master, slave = _open_pty()
    proc = _start_helper(master, slave, count=2)
    try:
        time.sleep(0.3)
        os.write(master, b"\x1b[5~")
        # Give the first read_key() call a chance to consume and return
        # before sending the second sequence -- avoids the second write
        # racing the brief window between the two read_key() calls where
        # the tty is momentarily back in its (cooked) resting state.
        time.sleep(0.3)
        os.write(master, b"\x1b[6~")
        out = _drain(master, timeout=2.0)
        assert out.splitlines() == [b"PGUP", b"PGDN"]
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_read_key_treats_a_real_standalone_escape_as_esc() -> None:
    # A genuine lone Escape press (nothing follows within the timeout)
    # must still come back as "ESC" -- the fix must not turn every
    # legitimate Escape into a hang or a misread.
    master, slave = _open_pty()
    proc = _start_helper(master, slave, count=1)
    try:
        time.sleep(0.3)
        os.write(master, b"\x1b")
        out = _drain(master, timeout=2.0)
        assert out.strip() == b"ESC"
    finally:
        proc.kill()
        proc.wait(timeout=5)
