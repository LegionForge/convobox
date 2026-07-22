from __future__ import annotations

from scripts.run_convobox import _resolve_convobox_version, startup_announcement


def test_startup_announcement_includes_the_version() -> None:
    assert startup_announcement("0.2.0") == (
        "LegionForge ConvoBox, version 0.2.0, ready and standing by."
    )


def test_startup_announcement_with_a_dev_fallback_version() -> None:
    assert startup_announcement("dev") == (
        "LegionForge ConvoBox, version dev, ready and standing by."
    )


def test_resolve_convobox_version_returns_a_non_empty_string() -> None:
    # Installed (editable, in this dev checkout) -> the real pyproject.toml
    # version; never raises even if metadata were missing (see the
    # function's own docstring for the "dev" fallback).
    version = _resolve_convobox_version()
    assert isinstance(version, str) and version
