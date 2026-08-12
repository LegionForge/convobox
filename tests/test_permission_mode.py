from __future__ import annotations

import pytest

from convobox.adapters.claude_code import _resolve_flags
from convobox.adapters.codex import _permission_config_args
from convobox.config import (
    BackendConfig,
    InteractionConfig,
    detect_claude_code_approval_gap,
    detect_permission_conflict,
)
from scripts.run_convobox import _check_backend_permission_mode

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


# --- claude-code approval-gap detection (GitHub issue #235, finding A1):
# permission_mode="approve" wires ClaudeCodeAdapter's hook purely from the
# mode itself, independent of whether anything can ever answer it --
# set_interactive_approvals() is a documented no-op for claude-code, so
# interaction.approval_phrase (which gates whether approval_gate exists at
# all) is the only thing that determines whether a pending request is ever
# resolved.

def test_approval_gap_flagged_for_claude_code_approve_without_phrase() -> None:
    backend = BackendConfig(name="claude-code", permission_mode="approve")
    interaction = InteractionConfig(approval_phrase=None)
    assert detect_claude_code_approval_gap(backend, interaction) is not None


def test_approval_gap_clear_for_claude_code_approve_with_phrase() -> None:
    backend = BackendConfig(name="claude-code", permission_mode="approve")
    interaction = InteractionConfig(approval_phrase="alpha bravo charlie")
    assert detect_claude_code_approval_gap(backend, interaction) is None


def test_approval_gap_clear_for_claude_code_plan_mode() -> None:
    # permission_mode=plan never wires the hook at all -- no phrase needed.
    backend = BackendConfig(name="claude-code", permission_mode="plan")
    interaction = InteractionConfig(approval_phrase=None)
    assert detect_claude_code_approval_gap(backend, interaction) is None


def test_approval_gap_clear_for_claude_code_permissive_mode() -> None:
    backend = BackendConfig(name="claude-code", permission_mode="permissive")
    interaction = InteractionConfig(approval_phrase=None)
    assert detect_claude_code_approval_gap(backend, interaction) is None


def test_approval_gap_not_flagged_for_codex() -> None:
    # Codex's set_interactive_approvals() genuinely toggles at runtime
    # (codex.py's own _interactive_approvals check) -- it fails safe by
    # cleanly denying with no pending state, unlike claude-code. This
    # check is claude-code-specific, not a general "approve needs a
    # phrase" rule.
    backend = BackendConfig(name="codex", permission_mode="approve")
    interaction = InteractionConfig(approval_phrase=None)
    assert detect_claude_code_approval_gap(backend, interaction) is None


# --- _check_backend_permission_mode: the real CLI startup guard, same
# fail-closed SystemExit treatment as _check_backend_working_dir's own
# tests (tests/test_backend_working_dir.py).

def test_startup_guard_exits_on_claude_code_approval_gap() -> None:
    backend = BackendConfig(name="claude-code", permission_mode="approve")
    interaction = InteractionConfig(approval_phrase=None)
    with pytest.raises(SystemExit, match="nothing able to ever answer it"):
        _check_backend_permission_mode(backend, interaction)


def test_startup_guard_passes_with_approval_phrase_set() -> None:
    backend = BackendConfig(name="claude-code", permission_mode="approve")
    interaction = InteractionConfig(approval_phrase="alpha bravo charlie")
    _check_backend_permission_mode(backend, interaction)  # must not raise


def test_startup_guard_still_catches_command_flag_conflicts() -> None:
    # Existing detect_permission_conflict() behavior must be unaffected by
    # the new check being added alongside it.
    backend = BackendConfig(
        name="claude-code",
        command=["claude", "--dangerously-skip-permissions"],
    )
    interaction = InteractionConfig()
    with pytest.raises(SystemExit):
        _check_backend_permission_mode(backend, interaction)


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
    # Fixed 2026-07-30: "permissive" used to map to the same acceptEdits
    # flag as "approve", which only auto-approves file-edit tools --
    # WebFetch/WebSearch/Bash/Read still generated a real approval request
    # headless mode has no channel to answer. bypassPermissions actually
    # matches "permissive"'s documented contract ("acts without asking").
    # See _PERMISSION_CLAUDE_MODE's own comment for the full writeup.
    assert "bypassPermissions" in _resolve_flags(["claude"], "permissive")


def test_claude_approve_now_has_a_real_per_call_channel() -> None:
    # Superseded 2026-07-2x: headless mode has no NATIVE per-call approval
    # channel, but ClaudeCodeAdapter now builds one (a PreToolUse hook --
    # see its module docstring), so "approve" no longer degrades to "plan"
    # -- it resolves to acceptEdits (so Claude actually attempts tool
    # calls) with the hook as the real gate on top (wired only for
    # "approve"; see ClaudeCodeAdapter.__init__'s interactive_approval
    # derivation). "permissive" no longer shares this flag -- it maps to
    # bypassPermissions instead (see test_claude_plan_and_permissive_translate).
    assert "acceptEdits" in _resolve_flags(["claude"], "approve")
    assert "plan" not in _resolve_flags(["claude"], "approve")


def test_claude_user_permission_flag_wins_over_translation() -> None:
    flags = _resolve_flags(["claude", "--permission-mode", "acceptEdits"], "plan")
    # We do not inject our own when the user already set one.
    assert flags.count("--permission-mode") == 0
