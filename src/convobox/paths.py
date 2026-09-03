"""Where ConvoBox's own state lives on disk: config, downloaded voice/
model files, conversation history, settings backups, the --tui log file.

2026-09-04, JP asked directly: uninstall/upgrade was messy because every
one of these defaulted to a path RELATIVE TO WHATEVER DIRECTORY THE USER
HAPPENED TO RUN `convobox` FROM (`convobox.yaml`, `.models/`,
`.convobox-history/`, `.convobox-backups/`, `convobox-tui.log`, all bare
relative paths, confirmed by grep -- resolve_config_path()'s own default
was literally `Path("convobox.yaml")`). Running from two different
directories silently created two independent installs; there was no
single place to point an uninstaller or a "just move this to back up/
restore" instruction at.

Consolidated into one OS-idiomatic user-data directory via `platformdirs`
(macOS: ~/Library/Application Support/ConvoBox; Linux: XDG_DATA_HOME,
usually ~/.local/share/convobox; Windows: %LOCALAPPDATA%\\ConvoBox) --
the same convention most modern CLI tools use, rather than a hand-rolled
~/.convobox/ that isn't idiomatic on macOS/Windows.

Every function here returns a path; NONE of them create the directory
-- callers that are about to write into one call .mkdir(parents=True,
exist_ok=True) themselves at the point of use, same as this codebase's
existing convention (e.g. artifacts.py's own working_dir handling).
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

import platformdirs

_APP_NAME = "ConvoBox"


def user_data_dir() -> Path:
    """The one root everything else here lives under. `appauthor=False`:
    platformdirs' Windows behavior otherwise nests an extra
    publisher-name directory level (LegionForge\\ConvoBox) that has no
    equivalent on macOS/Linux and isn't needed for a single-app tool
    like this one.
    """
    return Path(platformdirs.user_data_dir(_APP_NAME, appauthor=False))


def default_config_path() -> Path:
    return user_data_dir() / "convobox.yaml"


def default_models_dir() -> Path:
    """Parent of both engines' own subdirectories (models/piper,
    models/kokoro) -- one place, not two independent ones, since both
    are "downloaded model/voice files" in the same sense.
    """
    return user_data_dir() / "models"


def default_piper_voices_dir() -> Path:
    return default_models_dir() / "piper"


def default_kokoro_dir() -> Path:
    return default_models_dir() / "kokoro"


def default_history_dir() -> Path:
    return user_data_dir() / "history"


def default_backups_dir() -> Path:
    # ".convobox-backups", not "backups": settings_tui.py's own
    # _backup_dir() always resolves relative to the ACTUAL config file's
    # parent directory (so a custom --config path still gets its
    # backups colocated correctly) -- this constant only needs to agree
    # with that dirname for the case a config lives at
    # default_config_path() itself (the common case, and the migration
    # target below).
    return user_data_dir() / ".convobox-backups"


def default_log_path() -> Path:
    return user_data_dir() / "convobox-tui.log"


# --- legacy (pre-2026-09-04) layout, all relative to whatever directory
# `convobox` happened to be run from -- see migrate_legacy_layout() below.
_LEGACY_CONFIG = Path("convobox.yaml")
_LEGACY_PIPER_VOICES = Path(".models/piper")
_LEGACY_KOKORO = Path(".models/kokoro")
_LEGACY_HISTORY = Path(".convobox-history")
_LEGACY_BACKUPS = Path(".convobox-backups")
_LEGACY_LOG = Path("convobox-tui.log")


def migrate_legacy_layout() -> list[str]:
    """One-time, best-effort move of anything found at the OLD
    CWD-relative locations into the new user-data directory, called once
    at startup (run_convobox.py and settings_tui.py both).

    Self-limiting by construction, not a flag: only ever does anything
    the FIRST time (once default_config_path() exists, this function's
    own legacy-file checks below all still run, but shutil.move onto an
    existing destination would raise -- guarded per-item, not with one
    blanket "already migrated" marker file, so a partial legacy set
    migrates cleanly even if some items were already moved by hand).
    Non-destructive: MOVES (not copies + delete), and only when the new
    location doesn't already have that item -- never overwrites
    something the user has already started using at the new location.
    Returns what actually got migrated (source -> dest strings), for the
    caller to log; empty list means nothing legacy was found (the
    common case for a fresh install, and for every run after the first).
    """
    moved: list[str] = []
    pairs = (
        (_LEGACY_CONFIG, default_config_path()),
        (_LEGACY_PIPER_VOICES, default_piper_voices_dir()),
        (_LEGACY_KOKORO, default_kokoro_dir()),
        (_LEGACY_HISTORY, default_history_dir()),
        (_LEGACY_BACKUPS, default_backups_dir()),
        (_LEGACY_LOG, default_log_path()),
    )
    for src, dest in pairs:
        if not src.exists() or dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            moved.append(f"{src} -> {dest}")
        except OSError:
            # Best-effort: a permissions issue or a concurrent second
            # `convobox` process racing the same migration must not
            # crash startup over relocating a convenience default --
            # the legacy path is still readable in place if this fails,
            # nothing is lost.
            continue
    # Both piper and kokoro moved out of legacy .models/ leaves an empty
    # .models/ directory behind -- shutil.move only removes the item
    # itself, not a now-empty parent. Tidy it up too: the whole point of
    # this migration is a clean CWD afterward, not a near-clean one.
    legacy_models_dir = Path(".models")
    if legacy_models_dir.is_dir() and not any(legacy_models_dir.iterdir()):
        with contextlib.suppress(OSError):
            legacy_models_dir.rmdir()
    return moved
