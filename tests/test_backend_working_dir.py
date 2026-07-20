from __future__ import annotations

from pathlib import Path

import pytest

from convobox.adapters import create_backend_adapter
from convobox.adapters.claude_code import ClaudeCodeAdapter
from convobox.adapters.codex import CodexAdapter
from convobox.config import BackendConfig
from scripts.run_convobox import _check_backend_working_dir


def test_backend_config_working_dir_defaults_none() -> None:
    assert BackendConfig().working_dir is None
    assert BackendConfig(working_dir="C:/tmp/work").working_dir == "C:/tmp/work"


def test_factory_threads_working_dir_to_subprocess_backends() -> None:
    codex = create_backend_adapter(BackendConfig(name="codex", working_dir="C:/ws"))
    assert isinstance(codex, CodexAdapter)
    assert codex._working_dir == "C:/ws"
    claude = create_backend_adapter(
        BackendConfig(name="claude-code", command=["claude"], working_dir="C:/ws")
    )
    assert isinstance(claude, ClaudeCodeAdapter)
    assert claude._working_dir == "C:/ws"


def test_check_raises_when_working_dir_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(SystemExit, match="not an existing directory"):
        _check_backend_working_dir(
            BackendConfig(name="codex", working_dir=str(missing))
        )


def test_check_passes_for_a_real_isolated_dir(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _check_backend_working_dir(BackendConfig(name="codex", working_dir=str(tmp_path)))
    # A real, separate directory produces no source-tree warning.
    assert "own source tree" not in caplog.text


def test_check_warns_when_working_dir_is_convobox_source(caplog: pytest.LogCaptureFixture) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _check_backend_working_dir(
        BackendConfig(name="codex", working_dir=str(repo_root))
    )
    assert "own source tree" in caplog.text


def test_check_warns_when_unset_for_subprocess_backend(caplog: pytest.LogCaptureFixture) -> None:
    _check_backend_working_dir(BackendConfig(name="codex", working_dir=None))
    assert "can modify its source" in caplog.text


def test_check_warns_when_set_for_opencode(caplog: pytest.LogCaptureFixture) -> None:
    _check_backend_working_dir(
        BackendConfig(name="opencode", working_dir="C:/ws")
    )
    assert "NO effect on the opencode backend" in caplog.text
