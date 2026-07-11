"""Shared helper for scripts that print or read non-Latin script."""

from __future__ import annotations

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
