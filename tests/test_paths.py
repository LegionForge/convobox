"""convobox.paths (2026-09-04): where ConvoBox's own state lives on disk.

JP asked directly: uninstall/upgrade was a mess because every default
(config, voices, history, backups, the --tui log) resolved relative to
whatever directory `convobox` happened to be run from. These tests cover
the new OS-idiomatic user-data-directory defaults and the one-time
legacy-layout migration, both isolated from this machine's REAL user-data
directory (monkeypatching convobox.paths.user_data_dir directly, not
platformdirs itself -- the point under test is "do these functions agree
on one root and migrate correctly," not platformdirs' own OS-detection
logic, which is a well-tested third-party library, not this project's
code).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convobox import paths


@pytest.fixture
def fake_user_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "user-data"
    monkeypatch.setattr(paths, "user_data_dir", lambda: root)
    return root


# --- default_* all resolve under one root ---


def test_default_config_path_is_under_user_data_dir(fake_user_data_dir: Path) -> None:
    assert paths.default_config_path() == fake_user_data_dir / "convobox.yaml"


def test_default_piper_and_kokoro_dirs_share_one_models_parent(
    fake_user_data_dir: Path,
) -> None:
    assert paths.default_piper_voices_dir() == fake_user_data_dir / "models" / "piper"
    assert paths.default_kokoro_dir() == fake_user_data_dir / "models" / "kokoro"


def test_default_history_dir_is_under_user_data_dir(fake_user_data_dir: Path) -> None:
    assert paths.default_history_dir() == fake_user_data_dir / "history"


def test_default_backups_dir_matches_settings_tuis_own_dirname(
    fake_user_data_dir: Path,
) -> None:
    # settings_tui.py's _backup_dir() always resolves relative to the
    # ACTUAL config file's parent -- this constant only agrees with that
    # dirname (".convobox-backups") for the common case where the config
    # lives at default_config_path() itself, which is also what
    # migrate_legacy_layout() below relies on.
    assert paths.default_backups_dir() == fake_user_data_dir / ".convobox-backups"


def test_default_log_path_is_under_user_data_dir(fake_user_data_dir: Path) -> None:
    assert paths.default_log_path() == fake_user_data_dir / "convobox-tui.log"


def test_none_of_the_default_functions_create_anything(fake_user_data_dir: Path) -> None:
    # Every default_*() function just returns a path -- callers create
    # the directory themselves at the point of use (module docstring's
    # own stated contract).
    paths.default_config_path()
    paths.default_piper_voices_dir()
    paths.default_kokoro_dir()
    paths.default_history_dir()
    paths.default_backups_dir()
    paths.default_log_path()
    assert not fake_user_data_dir.exists()


# --- migrate_legacy_layout() ---


def test_migrate_moves_a_legacy_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_user_data_dir: Path
) -> None:
    cwd = tmp_path / "legacy-cwd"
    cwd.mkdir()
    (cwd / "convobox.yaml").write_text("backend: {name: opencode}\n")
    monkeypatch.chdir(cwd)

    moved = paths.migrate_legacy_layout()

    assert len(moved) == 1
    assert not (cwd / "convobox.yaml").exists()
    assert paths.default_config_path().read_text() == "backend: {name: opencode}\n"


def test_migrate_moves_every_legacy_item_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_user_data_dir: Path
) -> None:
    cwd = tmp_path / "legacy-cwd"
    cwd.mkdir()
    (cwd / "convobox.yaml").write_text("x: 1\n")
    (cwd / ".models" / "piper").mkdir(parents=True)
    (cwd / ".models" / "piper" / "voice.onnx").write_text("fake")
    (cwd / ".models" / "kokoro").mkdir(parents=True)
    (cwd / ".models" / "kokoro" / "kokoro-v1.0.onnx").write_text("fake")
    (cwd / ".convobox-history").mkdir()
    (cwd / ".convobox-history" / "events.db").write_text("fake")
    (cwd / ".convobox-backups").mkdir()
    (cwd / ".convobox-backups" / "convobox.yaml.backup-x").write_text("fake")
    (cwd / "convobox-tui.log").write_text("fake log\n")
    monkeypatch.chdir(cwd)

    moved = paths.migrate_legacy_layout()

    assert len(moved) == 6
    assert paths.default_config_path().exists()
    assert (paths.default_piper_voices_dir() / "voice.onnx").exists()
    assert (paths.default_kokoro_dir() / "kokoro-v1.0.onnx").exists()
    assert (paths.default_history_dir() / "events.db").exists()
    assert (paths.default_backups_dir() / "convobox.yaml.backup-x").exists()
    assert paths.default_log_path().read_text() == "fake log\n"
    # Nothing left behind at the old locations.
    assert list(cwd.iterdir()) == []


def test_migrate_is_a_noop_with_nothing_legacy_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_user_data_dir: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert paths.migrate_legacy_layout() == []
    assert not fake_user_data_dir.exists()


def test_migrate_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_user_data_dir: Path
) -> None:
    cwd = tmp_path / "legacy-cwd"
    cwd.mkdir()
    (cwd / "convobox.yaml").write_text("x: 1\n")
    monkeypatch.chdir(cwd)

    first = paths.migrate_legacy_layout()
    second = paths.migrate_legacy_layout()

    assert len(first) == 1
    assert second == []


def test_migrate_never_overwrites_an_existing_new_location_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_user_data_dir: Path
) -> None:
    # A user who already has a real config at the new location (e.g. ran
    # a fresh install, THEN happened to cd into an old directory with a
    # stale legacy convobox.yaml from testing) must never have their
    # current config silently clobbered by an unrelated legacy file.
    fake_user_data_dir.mkdir(parents=True)
    (fake_user_data_dir / "convobox.yaml").write_text("real: config\n")

    cwd = tmp_path / "legacy-cwd"
    cwd.mkdir()
    (cwd / "convobox.yaml").write_text("stale: legacy\n")
    monkeypatch.chdir(cwd)

    moved = paths.migrate_legacy_layout()

    assert moved == []
    assert paths.default_config_path().read_text() == "real: config\n"
    assert (cwd / "convobox.yaml").read_text() == "stale: legacy\n"


def test_migrate_handles_a_partial_legacy_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_user_data_dir: Path
) -> None:
    # Only some legacy items present (e.g. history tracking was never
    # enabled, so no .convobox-history/ ever existed) -- must migrate
    # what's there and skip what isn't, not require all-or-nothing.
    cwd = tmp_path / "legacy-cwd"
    cwd.mkdir()
    (cwd / "convobox.yaml").write_text("x: 1\n")
    monkeypatch.chdir(cwd)

    moved = paths.migrate_legacy_layout()

    assert len(moved) == 1
    assert not paths.default_history_dir().exists()
    assert not paths.default_piper_voices_dir().exists()
