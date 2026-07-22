from __future__ import annotations

import pytest

from convobox.adapters.claude_code import _resolve_flags
from convobox.adapters.codex import _permission_config_args
from convobox.config import BackendConfig, detect_permission_conflict


# --- config field + validator ---

def test_permission_mode_defaults_to_plan() -> None:
    assert BackendConfig().permission_mode == "plan"


def test_permission_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="permission_mode"):
        BackendConfig(permission_mode="yolo")


@pytest.mark.parametrize("mode", ["plan", "approve", "permissive"])
def test_permission_mode_accepts_valid_values(mode: str) -> None:
    assert BackendConfig(permission_mode=mode).permission_mode == mode


# --- conflict detection (permission_mode is the single source of truth) ---

def test_conflict_flags_codex_sandbox_in_command() -> None:
    cfg = BackendConfig(name="codex", command=["codex", "--sandbox", "danger-full-access"])
    assert detect_permission_conflict(cfg) is not None


def test_conflict_flags_codex_config_override() -> None:
    cfg = BackendConfig(name="codex", command=["codex", "-c", "approval_policy=never"])
    assert detect_permission_conflict(cfg) is not None


def test_conflict_flags_claude_dangerous_skip() -> None:
    cfg = BackendConfig(name="claude-code", command=["claude", "--dangerously-skip-permissions"])
    assert detect_permission_conflict(cfg) is not None


def test_no_conflict_for_orthogonal_tool_scoping() -> None:
    # --disallowedTools scopes tools, not the write/execute posture -- must compose.
    cfg = BackendConfig(name="claude-code", command=["claude", "--disallowedTools", "Bash"])
    assert detect_permission_conflict(cfg) is None


def test_no_conflict_for_plain_command() -> None:
    assert detect_permission_conflict(BackendConfig(name="codex", command=["codex"])) is None


# --- codex translation -> -c overrides ---

def test_codex_plan_is_read_only_no_prompts() -> None:
    args = _permission_config_args("plan")
    assert "sandbox_mode=read-only" in args
    assert "approval_policy=never" in args


def test_codex_approve_escalates_writes() -> None:
    args = _permission_config_args("approve")
    assert "approval_policy=untrusted" in args
    assert "sandbox_mode=workspace-write" in args


def test_codex_permissive_writes_without_asking() -> None:
    args = _permission_config_args("permissive")
    assert "approval_policy=never" in args
    assert "sandbox_mode=workspace-write" in args


# --- claude-code translation -> --permission-mode ---

def test_claude_plan_and_permissive_translate() -> None:
    assert "plan" in _resolve_flags(["claude"], "plan")
    assert "acceptEdits" in _resolve_flags(["claude"], "permissive")


def test_claude_approve_now_has_a_real_per_call_channel() -> None:
    # Superseded 2026-07-2x: headless mode has no NATIVE per-call approval
    # channel, but ClaudeCodeAdapter now builds one (a PreToolUse hook --
    # see its module docstring), so "approve" no longer degrades to "plan"
    # -- it resolves to the same CLI flag as "permissive" (acceptEdits,
    # so Claude actually attempts tool calls), and the hook is what
    # differs between the two (wired only for "approve"; see
    # ClaudeCodeAdapter.__init__'s interactive_approval derivation).
    assert "acceptEdits" in _resolve_flags(["claude"], "approve")
    assert "plan" not in _resolve_flags(["claude"], "approve")


def test_claude_user_permission_flag_wins_over_translation() -> None:
    flags = _resolve_flags(["claude", "--permission-mode", "acceptEdits"], "plan")
    # We do not inject our own when the user already set one.
    assert flags.count("--permission-mode") == 0
