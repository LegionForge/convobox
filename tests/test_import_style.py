"""AGENTS.md rule 15: a module is imported one way per file.

The code-quality bot flags a module imported with both ``import m`` and
``from m import x`` in the same file, and an unresolved bot thread blocks a
merge (``main`` requires conversation resolution). Ruff has no rule for this,
so it is enforced here, locally, before a push.

``_LEGACY`` lists files that predate the rule. It can only shrink: a file that
is cleaned up must be removed from it (the second test fails if it is not), and
a new offender fails the first test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("src", "scripts", "tests")
_SKIP_PARTS = frozenset({"build", "dist", "node_modules", "site-packages"})

_LEGACY = frozenset(
    {
        "tests/test_claude_code_adapter.py",
        "tests/test_claude_code_approval.py",
        "tests/test_incident_capture.py",
        "tests/test_orchestrator.py",
        "tests/test_stt_factory.py",
        "tests/test_tts.py",
        "tests/test_tts_kokoro.py",
    }
)


def mixed_import_modules(source: str | bytes, filename: str = "<source>") -> list[str]:
    """Modules imported both as ``import m`` and as ``from m import x``.

    Matches CodeQL's py/import-and-import-from by exact module name, so
    ``import a`` + ``from a.b import x`` is flagged by neither. Absolute imports
    only: a relative ``from . import x`` is skipped (the repo has none in src/;
    CodeQL would resolve it, so a future one is a known gap).
    Function-local imports count: that is where the pattern usually hides.
    """
    tree = ast.parse(source, filename=filename)
    plain = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    from_ = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and not n.level
    }
    return sorted(plain & from_)


def _offenders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for scan_dir in _SCAN_DIRS:
        for path in sorted((_ROOT / scan_dir).rglob("*.py")):
            rel = path.relative_to(_ROOT)
            if _SKIP_PARTS & set(rel.parts) or any(
                p.startswith(".") for p in rel.parts
            ):
                continue
            # bytes + filename: ast handles encoding cookies and BOMs, and a
            # syntax error names the file instead of "<unknown>".
            mixed = mixed_import_modules(path.read_bytes(), filename=str(rel))
            if mixed:
                found[path.relative_to(_ROOT).as_posix()] = mixed
    return found


# --- the detector itself ------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import os\nfrom os import path\n", ["os"]),
        ("from a.b import C\n\ndef f():\n    import a.b as m\n", ["a.b"]),
        ("import os\nimport sys\n", []),
        ("from os import path\nfrom os import sep\n", []),
        ("import a.b\nfrom a import b\n", []),  # different dotted names
        ("import x\nfrom . import x\n", []),  # relative imports are out of scope
        ("import a\nfrom a.b import x\n", []),  # exact module name, like CodeQL
        (
            "from a import X\nif TYPE_CHECKING:\n    import a as m\n",
            ["a"],
        ),  # a TYPE_CHECKING block still counts
    ],
)
def test_detector_finds_exactly_the_mixed_case(
    source: str, expected: list[str]
) -> None:
    assert mixed_import_modules(source) == expected


# --- the repo ---------------------------------------------------------------


def test_no_new_file_mixes_import_styles() -> None:
    new = {f: m for f, m in _offenders().items() if f not in _LEGACY}
    assert not new, (
        "AGENTS.md rule 15: import each module one way per file. "
        f"Offending files (module list): {new}. Use `from x import name`, or import "
        "the module once at the top and use `x_mod.Name`; see rule 15."
    )


def test_legacy_allowlist_only_shrinks() -> None:
    fixed = sorted(_LEGACY - set(_offenders()))
    assert not fixed, (
        f"These files no longer mix import styles; remove them from _LEGACY: {fixed}"
    )
