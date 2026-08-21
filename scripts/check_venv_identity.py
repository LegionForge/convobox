"""Check whether the active venv's editable `convobox` install actually
resolves against THIS checkout, not a sibling one sharing the same venv.

Why this exists: docs/field-notes/2026-07-22-shared-venv-editable-install-
cross-contamination.md -- a shared venv (e.g. the UAT clone's `.venv`
directory-junctioned to dev's, to skip installing the whole audio/ML stack
twice) silently repoints `convobox`'s editable install to whichever
checkout last ran `uv sync`/`uv run`. The other checkout's own scripts keep
running, but every `import convobox...` inside them resolves against the
WRONG tree -- no error at import time, just a confusing, code-adjacent-
looking symptom later. Bit this project 3+ times in one evening (GitHub
issue #126, item 4) before being root-caused.

Usage: `python scripts/check_venv_identity.py` from either checkout, or
wire it into a sync script. Exits 0 if convobox resolves under this
checkout's own root, 1 (with the exact fix command) if not.
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_CHECKOUT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        import convobox
    except ImportError as exc:
        print(f"convobox is not importable in this environment: {exc}")
        return 1

    resolved_root = Path(convobox.__file__).resolve().parents[2]
    if resolved_root == _THIS_CHECKOUT:
        print(f"OK: convobox resolves to this checkout ({_THIS_CHECKOUT})")
        return 0

    print("MISMATCH: convobox is NOT resolving to this checkout.")
    print(f"  this checkout:    {_THIS_CHECKOUT}")
    print(f"  convobox resolves to: {resolved_root}")
    print(
        "\nThis venv is shared with another checkout, and the last "
        "`uv sync`/`uv run` there repointed the editable install away from "
        "this one -- every `import convobox` you run from here right now "
        "is silently testing THAT checkout's code, not this one's."
    )
    print(
        f"\nFix (run from {_THIS_CHECKOUT}):\n"
        "  uv cache clean convobox\n"
        "  uv sync --reinstall-package convobox --no-cache\n"
        "then re-run this script to confirm."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
