from __future__ import annotations

from pathlib import Path

import pytest

from convobox.config import (
    AudioConfig,
    InteractionConfig,
    aec_estimate_path,
    read_aec_estimate,
    resolve_config_path,
    write_aec_estimate,
)


# --- resolve_config_path: the explicit-path / CONVOBOX_CONFIG / default
# fallback order load_config() and settings_tui.py's default_config_path()
# both delegate to, extracted so this order lives in exactly one place. ---


def test_resolve_config_path_uses_the_explicit_path_when_given() -> None:
    assert resolve_config_path("custom.yaml") == Path("custom.yaml")


def test_resolve_config_path_uses_the_env_var_when_no_explicit_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONVOBOX_CONFIG", "from-env.yaml")
    assert resolve_config_path() == Path("from-env.yaml")


def test_resolve_config_path_defaults_to_convobox_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONVOBOX_CONFIG", raising=False)
    assert resolve_config_path() == Path("convobox.yaml")


def test_resolve_config_path_explicit_path_wins_over_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONVOBOX_CONFIG", "from-env.yaml")
    assert resolve_config_path("explicit.yaml") == Path("explicit.yaml")


# --- AEC delay auto-tune sentinel ---


def test_aec_delay_ms_defaults_to_none() -> None:
    # None = auto-tune (the recommended default). A real int explicitly
    # overrides auto-tuning -- see AudioConfig's own field comment for
    # the 2026-07-15 incident this sentinel exists to prevent: a plain
    # model_dump() used to always write a literal 100 into convobox.yaml
    # on every Settings TUI save, permanently disabling auto-tuning
    # whether the user meant to touch that field or not.
    assert AudioConfig().aec_delay_ms is None


def test_approval_phrase_is_opt_in_and_validated() -> None:
    assert InteractionConfig().approval_phrase is None
    assert InteractionConfig(approval_phrase="cobalt night and gale").approval_phrase == "cobalt night and gale"
    with pytest.raises(ValueError, match="common affirmations"):
        InteractionConfig(approval_phrase="yes")


# --- AEC estimate sidecar: run_convobox.py's diagnostic write, the
# Settings TUI's read -- deliberately NOT part of convobox.yaml itself
# (see write_aec_estimate's docstring for why). ---


def test_aec_estimate_path_is_a_sidecar_next_to_the_config(tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    assert aec_estimate_path(config_path) == tmp_path / "convobox.yaml.aec-estimate.json"


def test_write_then_read_aec_estimate_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    write_aec_estimate(config_path, 222, 180.3, 32.1)

    result = read_aec_estimate(config_path)

    assert result is not None
    assert result["delay_ms"] == 222
    assert result["output_latency_ms"] == pytest.approx(180.3)
    assert result["input_latency_ms"] == pytest.approx(32.1)
    assert "measured_at" in result


def test_read_aec_estimate_returns_none_when_never_written(tmp_path: Path) -> None:
    assert read_aec_estimate(tmp_path / "convobox.yaml") is None


def test_read_aec_estimate_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    aec_estimate_path(config_path).write_text("not valid json {{{")

    assert read_aec_estimate(config_path) is None


def test_write_aec_estimate_never_raises_when_the_directory_does_not_exist(
    tmp_path: Path,
) -> None:
    # A diagnostic write must never crash the voice loop -- best-effort
    # only, same discipline as _memory_diagnostic() in the STT module.
    config_path = tmp_path / "nonexistent-dir" / "convobox.yaml"
    write_aec_estimate(config_path, 222, 180.0, 32.0)  # must not raise
    assert read_aec_estimate(config_path) is None


# --- InteractionConfig.approval_phrase: voice-gated tool approval (Phase 3)
# is OFF by default, and reuses ConfirmwordDetector's own construction-time
# safety guard -- no dedicated logic duplicated here. ---


def test_approval_phrase_defaults_to_none() -> None:
    assert InteractionConfig().approval_phrase is None


def test_approval_phrase_accepts_a_distinctive_phrase() -> None:
    assert InteractionConfig(approval_phrase="alpha bravo delta").approval_phrase == (
        "alpha bravo delta"
    )


def test_approval_phrase_rejects_a_common_affirmation_only_phrase() -> None:
    with pytest.raises(ValueError, match="common affirmations"):
        InteractionConfig(approval_phrase="yes")


def test_approval_timeout_s_has_a_sane_default() -> None:
    assert InteractionConfig().approval_timeout_s == 30.0
