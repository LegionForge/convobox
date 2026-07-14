from __future__ import annotations

from pathlib import Path

from convobox.config import AppConfig, load_config

_EXAMPLE = Path(__file__).resolve().parent.parent / "convobox.example.yaml"


def test_example_config_exists() -> None:
    assert _EXAMPLE.exists(), "convobox.example.yaml must ship at the repo root"


def test_example_config_parses_into_appconfig() -> None:
    # The shipped example must always be valid against the current schema
    # -- this test fails the moment a field is renamed/removed without the
    # example being updated, so onboarding docs can't silently rot.
    config = load_config(_EXAMPLE)
    assert isinstance(config, AppConfig)


def test_example_config_reflects_documented_defaults() -> None:
    config = load_config(_EXAMPLE)
    # Spot-check the values the comments promise, across every section.
    assert config.backend.name == "opencode"
    assert config.tts.engine == "piper"
    assert config.tts.voice == "en_US-lessac-medium"
    assert config.stt.model == "base"
    assert config.audio.echo_cancellation is False
    assert config.audio.sample_rate == 16000
    assert config.interaction.interrupt_preset == "do-not-disturb"
    assert "stop stop stop" in config.safeword.hard_stop_phrases


def test_example_config_only_uses_known_fields() -> None:
    # pydantic ignores unknown keys by default, so a typo'd field would
    # parse silently. Round-trip the example's dict through AppConfig and
    # confirm nothing was dropped: every top-level key must be a real
    # AppConfig field.
    import yaml

    raw = yaml.safe_load(_EXAMPLE.read_text(encoding="utf-8")) or {}
    known = set(AppConfig.model_fields)
    unknown = set(raw) - known
    assert not unknown, f"convobox.example.yaml has unknown top-level keys: {unknown}"
