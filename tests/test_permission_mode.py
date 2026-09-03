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
from scripts.run_convobox import (
    _check_backend_permission_mode,
    _check_plan_mode_blocks_artifact_tools,
)

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


def test_startup_guard_exits_on_codex_approve_mode() -> None:
    # Same clean SystemExit layer as the claude-code approval-gap check
    # above -- fails here, not deep inside CodexAdapter.__init__, so this
    # reads as a deliberate startup guard rather than a raw traceback.
    backend = BackendConfig(name="codex", permission_mode="approve")
    interaction = InteractionConfig()
    with pytest.raises(SystemExit, match="not currently safe"):
        _check_backend_permission_mode(backend, interaction)


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


# 2026-09-02: "approve" has no working codex-cli mapping -- live-verified
# via a raw JSON-RPC probe (bypassing this adapter entirely, so this
# isn't a ConvoBox protocol-usage bug) that no approval_policy value both
# starts successfully AND actually escalates a write to approval on
# current codex-cli. `untrusted` (the old value here) is rejected
# outright at spawn; `on-request`/`on-failure` with `sandbox_mode=
# read-only` both silently let the write through with zero approval RPC.
# Only `never`/`read-only` (plan mode's own mapping) actually blocks a
# write -- there's no way to get "ask before writing" out of codex-cli
# right now, so this fails loudly instead of silently providing zero
# protection. See docs/KNOWN-ISSUES.md and codex.py's
# _CODEX_APPROVE_MODE_ERROR for the full writeup.
def test_codex_approve_fails_loudly_instead_of_silently_unsafe() -> None:
    with pytest.raises(RuntimeError, match="not currently safe"):
        _permission_config_args("approve")


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


# --- _check_plan_mode_blocks_artifact_tools: warns that plan mode
# reliably blocks the artifact-pane MCP tools headless (docs/ARTIFACT-
# PANE-SCOPE.md), only called from run() once the MCP server is actually
# being mounted -- see run_convobox.py's own call site.

def test_plan_mode_warns_for_claude_code(caplog: pytest.LogCaptureFixture) -> None:
    backend = BackendConfig(name="claude-code", permission_mode="plan")
    with caplog.at_level("WARNING"):
        _check_plan_mode_blocks_artifact_tools(backend)
    assert "show_document" in caplog.text
    assert "get_shown_artifact" in caplog.text


def test_no_warning_for_claude_code_permissive(caplog: pytest.LogCaptureFixture) -> None:
    backend = BackendConfig(name="claude-code", permission_mode="permissive")
    with caplog.at_level("WARNING"):
        _check_plan_mode_blocks_artifact_tools(backend)
    assert caplog.text == ""


def test_no_warning_for_non_claude_code_backend_in_plan_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # opencode/codex don't wire the artifact MCP tools through claude-code's
    # ExitPlanMode mechanism at all -- this warning is claude-code-specific.
    backend = BackendConfig(name="codex", permission_mode="plan")
    with caplog.at_level("WARNING"):
        _check_plan_mode_blocks_artifact_tools(backend)
    assert caplog.text == ""
