"""Tests for scripts/doctor.py's own orchestration logic.

Deliberately does NOT re-test detect_permission_conflict/detect_claude_code_
approval_gap/detect_working_dir_not_git or probe_audio/probe_stt/probe_tts/
probe_backend themselves -- each already has its own exhaustive test
coverage (test_config.py, test_permission_mode.py, test_settings_tui.py).
These tests only cover doctor.py's own wiring: does it call the right
function, wrap a non-None/raised result into the right Finding, and get
main()'s exit code right.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import doctor  # type: ignore[import-not-found]

from convobox.config import AppConfig, BackendConfig, InteractionConfig


def test_static_config_findings_reports_load_problems() -> None:
    findings = doctor.static_config_findings(AppConfig(), ["stt.compute_type: some validation message"])
    assert findings == [doctor.Finding("config", "fail", "stt.compute_type: some validation message")]


def test_static_config_findings_reports_permission_conflict() -> None:
    config = AppConfig(
        backend=BackendConfig(
            name="claude-code",
            permission_mode="plan",
            command=["claude", "--dangerously-skip-permissions"],
        )
    )
    findings = doctor.static_config_findings(config, [])
    assert len(findings) == 1
    assert findings[0].check == "backend.permission_mode"
    assert findings[0].level == "fail"
    assert "--dangerously-skip-permissions" in findings[0].message


def test_static_config_findings_reports_claude_code_approval_gap() -> None:
    config = AppConfig(
        backend=BackendConfig(name="claude-code", permission_mode="approve"),
        interaction=InteractionConfig(approval_phrase=None),
    )
    findings = doctor.static_config_findings(config, [])
    assert len(findings) == 1
    assert findings[0].check == "backend.permission_mode"
    assert findings[0].level == "fail"
    assert "approval_phrase is unset" in findings[0].message


def test_static_config_findings_reports_working_dir_not_git(tmp_path: Path) -> None:
    non_git_dir = tmp_path / "scratch"
    non_git_dir.mkdir()
    config = AppConfig(backend=BackendConfig(working_dir=str(non_git_dir)))
    findings = doctor.static_config_findings(config, [])
    assert len(findings) == 1
    assert findings[0].check == "backend.working_dir"
    assert findings[0].level == "warn"


def test_static_config_findings_clean_config_reports_nothing() -> None:
    assert doctor.static_config_findings(AppConfig(), []) == []


def test_extras_findings_reports_missing_aec_when_echo_cancellation_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "aec_audio_processing", None)
    config = AppConfig()
    config.audio.echo_cancellation = True
    findings = doctor.extras_findings(config)
    assert len(findings) == 1
    assert findings[0].check == "audio.echo_cancellation"
    assert findings[0].level == "fail"
    assert "uv pip install" in findings[0].message


def test_extras_findings_says_nothing_when_aec_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "aec_audio_processing", object())
    config = AppConfig()
    config.audio.echo_cancellation = True
    assert doctor.extras_findings(config) == []


def test_extras_findings_reports_missing_web_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastapi", None)
    config = AppConfig()
    config.web.enabled = True
    findings = doctor.extras_findings(config)
    assert len(findings) == 1
    assert findings[0].check == "web.enabled"
    assert "uv sync --extra web" in findings[0].message


def test_extras_findings_reports_missing_piper_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "piper", None)
    config = AppConfig()
    config.tts.engine = "piper"
    findings = doctor.extras_findings(config)
    assert len(findings) == 1
    assert findings[0].check == "tts.engine"
    assert "uv sync --extra piper" in findings[0].message


def test_extras_findings_skips_checks_for_features_not_enabled() -> None:
    # echo_cancellation off, web off, tts.engine default (kokoro) -- none
    # of the three extras checks are even attempted, regardless of what's
    # actually installed.
    assert doctor.extras_findings(AppConfig()) == []


@pytest.mark.asyncio
async def test_live_findings_reports_probe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_probe_audio(config: AppConfig) -> str:
        return "speaker OK"

    monkeypatch.setattr(doctor, "probe_audio", fake_probe_audio)
    findings = await doctor.live_findings(AppConfig(), {"audio"})
    assert findings == [doctor.Finding("audio", "info", "speaker OK")]


@pytest.mark.asyncio
async def test_live_findings_turns_a_raised_exception_into_a_fail_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe_stt(config: AppConfig) -> str:
        raise RuntimeError("no STT model available")

    monkeypatch.setattr(doctor, "probe_stt", fake_probe_stt)
    findings = await doctor.live_findings(AppConfig(), {"stt"})
    assert len(findings) == 1
    assert findings[0].check == "stt"
    assert findings[0].level == "fail"
    assert "no STT model available" in findings[0].message


@pytest.mark.asyncio
async def test_live_findings_only_runs_the_requested_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def make_fake(name: str):
        async def fake(config: AppConfig) -> str:
            calls.append(name)
            return f"{name} ok"

        return fake

    monkeypatch.setattr(doctor, "probe_audio", await make_fake("audio"))
    monkeypatch.setattr(doctor, "probe_stt", await make_fake("stt"))
    monkeypatch.setattr(doctor, "probe_tts", await make_fake("tts"))
    monkeypatch.setattr(doctor, "probe_backend", await make_fake("backend"))

    await doctor.live_findings(AppConfig(), {"tts"})

    assert calls == ["tts"]


def test_main_exits_zero_when_no_problems_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["convobox-doctor", "--config", str(tmp_path / "convobox.yaml")])
    exit_code = doctor.main()
    assert exit_code == 0
    assert "No blocking problems found." in capsys.readouterr().out


def test_main_exits_one_when_a_problem_is_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "convobox.yaml"
    config_path.write_text(
        "backend:\n  name: claude-code\n  permission_mode: approve\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["convobox-doctor", "--config", str(config_path)])
    exit_code = doctor.main()
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "problem(s) found" in output
    assert "approval_phrase is unset" in output
