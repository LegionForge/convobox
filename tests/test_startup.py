from __future__ import annotations

import importlib.metadata

from convobox.startup import _resolve_convobox_version, startup_announcement


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


def test_resolve_convobox_version_matches_the_real_installed_distribution() -> None:
    # A bare "non-empty string" assertion (the test above) is satisfied by
    # the "dev" fallback too -- it never caught a real regression where
    # _resolve_convobox_version() looked up the wrong distribution name
    # ("convobox" instead of the actual "legionforge-convobox" from
    # pyproject.toml) and silently fell back to "dev" on every install,
    # not just a fresh checkout. Assert against the real distribution
    # metadata directly so a reintroduced name mismatch fails loudly here
    # instead of only being visible as a wrong startup banner.
    assert _resolve_convobox_version() == importlib.metadata.version("legionforge-convobox")
