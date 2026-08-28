from __future__ import annotations

import pytest

from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.approval import ApprovalDetector
from convobox.tui import ConversationTuiState
from scripts.run_convobox import (
    ApprovalPromptGate,
    LastSpokenResponse,
    WorkingIndicator,
    _deny_pending_approval_before_text_exit,
    _on_backend_event,
    _render_approval_explanation,
)


def _gate(timeout_s: float = 2.5) -> ApprovalPromptGate:
    return ApprovalPromptGate(ApprovalDetector(approval_phrase="nightingale"), timeout_s)


def test_not_waiting_by_default() -> None:
    gate = _gate()
    assert gate.is_waiting is False


def test_start_waiting_sets_is_waiting() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.is_waiting is True


def test_observe_transcript_approve_ends_the_wait_and_returns_approve() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("nightingale", now=1.0) == "approve"
    assert gate.is_waiting is False


def test_observe_transcript_deny_ends_the_wait_and_returns_deny() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("no", now=1.0) == "deny"
    assert gate.is_waiting is False


def test_observe_transcript_discuss_does_not_end_the_wait() -> None:
    # The whole point, unlike ContinuePromptGate's "pass": an approval
    # prompt must stay open and answerable across a clarifying exchange.
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.observe_transcript("what does that command do?", now=1.0) == "discuss"
    assert gate.is_waiting is True


def test_cancel_wait_ends_the_wait_without_a_transcript() -> None:
    # WebApprovalBridge's path: a decision arrives from the web UI, not a
    # transcript -- the gate still needs to stop waiting so the mic loop's
    # own observe_timeout() doesn't later fire "deny" against an
    # already-resolved (or since-superseded) request.
    gate = _gate()
    gate.start_waiting(now=0.0)
    gate.cancel_wait()
    assert gate.is_waiting is False


def test_cancel_wait_when_not_waiting_is_a_harmless_noop() -> None:
    gate = _gate()
    gate.cancel_wait()  # must not raise
    assert gate.is_waiting is False


def test_observe_transcript_discuss_resets_the_waiting_clock() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_transcript("tell me more first", now=12.0) == "discuss"
    # 2.4s since the discuss reply (now=12.0) -- would have expired if
    # measured from the original start_waiting(now=10.0) instead.
    assert gate.observe_timeout(now=14.4) is None
    assert gate.observe_timeout(now=14.5) == "deny"


def test_observe_transcript_explain_does_not_end_the_wait() -> None:
    # Like "discuss", but the caller gets an explicit outcome to act on
    # (speak pending_explanation) instead of a silent no-op.
    gate = _gate()
    gate.start_waiting(now=0.0, explanation="a command execution request")
    assert gate.observe_transcript("explain", now=1.0) == "explain"
    assert gate.is_waiting is True


def test_observe_transcript_explain_resets_the_waiting_clock() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_transcript("can you clarify?", now=12.0) == "explain"
    assert gate.observe_timeout(now=14.4) is None
    assert gate.observe_timeout(now=14.5) == "deny"


def test_pending_explanation_defaults_to_none() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    assert gate.pending_explanation is None


def test_pending_explanation_returns_what_start_waiting_was_given() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0, explanation="a file change to config.yaml")
    assert gate.pending_explanation == "a file change to config.yaml"


def test_observe_transcript_unclear_speech_does_not_change_state() -> None:
    # Normalizes to nothing -- no signal at all, not even "discuss".
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_transcript("...!!!", now=11.0) is None
    assert gate.is_waiting is True
    # Clock was NOT reset by the no-signal utterance.
    assert gate.observe_timeout(now=12.5) == "deny"


def test_observe_timeout_none_while_not_waiting() -> None:
    gate = _gate(timeout_s=2.5)
    assert gate.observe_timeout(now=100.0) is None


def test_observe_timeout_none_before_the_window_elapses() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_timeout(now=11.0) is None
    assert gate.is_waiting is True


def test_observe_timeout_deny_exactly_once_when_the_window_elapses() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    assert gate.observe_timeout(now=12.5) == "deny"
    assert gate.is_waiting is False
    # Already expired -- a second poll tick must not fire again.
    assert gate.observe_timeout(now=13.0) is None


def test_an_approve_reply_before_the_timeout_prevents_the_timeout_from_firing() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    gate.observe_transcript("nightingale", now=10.5)
    assert gate.observe_timeout(now=12.5) is None


def test_start_waiting_again_resets_the_window() -> None:
    gate = _gate(timeout_s=2.5)
    gate.start_waiting(now=10.0)
    gate.start_waiting(now=11.0)
    assert gate.observe_timeout(now=12.5) is None  # only 1.5s since the SECOND start
    assert gate.observe_timeout(now=13.5) == "deny"


def test_codex_approval_event_starts_gate_and_sets_tui_warning() -> None:
    gate = _gate()
    state = ConversationTuiState()
    _on_backend_event(
        state,
        LastSpokenResponse(),
        BackendEvent(
            BackendEventType.APPROVAL_REQUEST,
            content="APPROVAL REQUIRED — COMMAND EXECUTION\n\nRequested command:\necho harmless",
        ),
        "cobalt night and gale",
        gate,
    )
    assert gate.is_waiting is True
    assert state.warning is not None
    assert "echo harmless" in state.warning
    assert "cobalt night and gale" in state.warning


def test_error_event_adds_a_system_turn_to_the_tui_transcript() -> None:
    # docs/UAT-checklist.md [T6]: live-confirmed 2026-07-30 that ERROR
    # events (e.g. a TTS synthesis failure) reached the log and the web
    # UI but were silently dropped by this function in --tui mode, since
    # it only ever handled APPROVAL_REQUEST/TEXT. A "system" turn matches
    # [U10]'s existing convention for session-level events worth seeing
    # inline (paused/resumed, forced cutoff).
    state = ConversationTuiState()
    _on_backend_event(
        state,
        LastSpokenResponse(),
        BackendEvent(BackendEventType.ERROR, content="speech synthesis failed partway through"),
    )
    assert len(state.turns) == 1
    assert state.turns[0].speaker == "system"
    assert "speech synthesis failed partway through" in state.turns[0].text


def test_error_event_with_no_content_adds_no_turn() -> None:
    state = ConversationTuiState()
    _on_backend_event(
        state, LastSpokenResponse(), BackendEvent(BackendEventType.ERROR, content=None)
    )
    assert state.turns == []


def test_error_event_with_no_tui_state_does_not_raise() -> None:
    # Plain (non-`--tui`) mode passes tui_state=None -- must be a safe
    # no-op, same as every other branch in this function.
    _on_backend_event(
        None, LastSpokenResponse(), BackendEvent(BackendEventType.ERROR, content="boom")
    )


# --- indicator.current_activity wiring: KNOWN-ISSUES.md's 2026-07-31
# "Backend can go silently busy for minutes" entry -- the heartbeat needs
# to show what's running, not just how long. ---


def test_tool_call_event_sets_indicator_current_activity() -> None:
    indicator = WorkingIndicator()
    _on_backend_event(
        None, LastSpokenResponse(),
        BackendEvent(BackendEventType.TOOL_CALL, tool="bash", tool_input='{"command": "ls"}'),
        indicator=indicator,
    )
    assert indicator.current_activity == "bash"


def test_tool_result_event_clears_indicator_current_activity() -> None:
    # Tool finished -- back to "thinking" until the next TOOL_CALL/TEXT,
    # not stuck showing the just-finished tool's name.
    indicator = WorkingIndicator()
    indicator.current_activity = "bash"
    _on_backend_event(
        None, LastSpokenResponse(),
        BackendEvent(BackendEventType.TOOL_RESULT, tool="bash", tool_output="file1\nfile2"),
        indicator=indicator,
    )
    assert indicator.current_activity is None


def test_text_event_clears_indicator_current_activity() -> None:
    # A final response means the turn is done "working" -- don't let a
    # stale tool tag linger into the brief gap before playback resets it.
    indicator = WorkingIndicator()
    indicator.current_activity = "bash"
    _on_backend_event(
        None, LastSpokenResponse(),
        BackendEvent(BackendEventType.TEXT, content="done"),
        indicator=indicator,
    )
    assert indicator.current_activity is None


def test_tool_call_event_with_no_indicator_does_not_raise() -> None:
    # indicator is optional -- every pre-existing call site that doesn't
    # pass one (all the tests above it in this file) must stay valid.
    _on_backend_event(
        None, LastSpokenResponse(), BackendEvent(BackendEventType.TOOL_CALL, tool="bash")
    )


# --- pending_explanation wiring: JP, 2026-07-23 -- "explain" needs
# something concrete to speak back, cross-backend. ---


def test_codex_approval_event_populates_pending_explanation_from_content() -> None:
    gate = _gate()
    _on_backend_event(
        None,
        LastSpokenResponse(),
        BackendEvent(
            BackendEventType.APPROVAL_REQUEST,
            content="APPROVAL REQUIRED — COMMAND EXECUTION\n\nRequested command:\necho harmless",
        ),
        "cobalt night and gale",
        gate,
    )
    assert gate.pending_explanation == (
        "APPROVAL REQUIRED — COMMAND EXECUTION\n\nRequested command:\necho harmless"
    )


def test_claude_code_approval_event_populates_pending_explanation_from_tool_input() -> None:
    # Claude Code's hook-based APPROVAL_REQUEST carries tool/tool_input,
    # not content (see _on_backend_event's own comment) -- pending_explanation
    # must still have something sayable. Tests plain mode (the default).
    gate = _gate()
    _on_backend_event(
        None,
        LastSpokenResponse(),
        BackendEvent(
            BackendEventType.APPROVAL_REQUEST,
            tool="Write",
            tool_input='{"file_path": "config.yaml"}',
        ),
        "cobalt night and gale",
        gate,
        approval_explanation_mode="plain",
    )
    # Plain mode extracts the file_path and makes it human-friendly
    assert "config.yaml" in gate.pending_explanation and "Create or edit" in gate.pending_explanation


def test_render_approval_explanation_prefers_content_over_tool_input() -> None:
    assert _render_approval_explanation("full description", "Bash", '{"cmd": "ls"}') == (
        "full description"
    )


def test_render_approval_explanation_falls_back_to_tool_input() -> None:
    # Verbose mode shows raw JSON
    assert _render_approval_explanation(None, "Bash", '{"cmd": "ls"}', explanation_mode="verbose") == (
        'Bash with input: {"cmd": "ls"}'
    )


def test_render_approval_explanation_plain_mode_extracts_intent() -> None:
    # Plain mode extracts human-friendly details, not raw tool name
    result = _render_approval_explanation(None, "Write", '{"file_path": "test.py"}', explanation_mode="plain")
    assert "test.py" in result and "Create or edit" in result


def test_render_approval_explanation_never_empty_with_no_detail_at_all() -> None:
    assert _render_approval_explanation(None, None, None) == (
        "No further detail is available for this request."
    )
    assert _render_approval_explanation(None, "Bash", None) == (
        "No further detail is available for Bash."
    )


# --- _deny_pending_approval_before_text_exit: --text mode's exit-path fix
# for docs/KNOWN-ISSUES.md's "--text mode + permission_mode: approve
# abandons a pending approval instead of denying it" -- --text mode never
# runs _working_watchdog, so approval_gate.observe_timeout() is never
# ticked; without this, a pending approval sat open until an unrelated
# generic busy-timeout gave up and disconnected the backend without ever
# sending an explicit decline. ---


class _FakeOrchestrator:
    def __init__(self, has_pending: bool) -> None:
        self._has_pending = has_pending
        self.resolve_calls: list[bool] = []

    async def resolve_pending_approval(self, approved: bool) -> bool:
        self.resolve_calls.append(approved)
        return self._has_pending


class _FakeWebForwarder:
    def __init__(self) -> None:
        self.resolved_calls: list[bool] = []

    def forward_approval_resolved(self, approved: bool) -> None:
        self.resolved_calls.append(approved)


@pytest.mark.asyncio
async def test_deny_pending_approval_is_a_no_op_when_gate_is_none() -> None:
    orchestrator = _FakeOrchestrator(has_pending=True)
    await _deny_pending_approval_before_text_exit(orchestrator, None, None)
    assert orchestrator.resolve_calls == []


@pytest.mark.asyncio
async def test_deny_pending_approval_is_a_no_op_when_gate_is_not_waiting() -> None:
    gate = _gate()
    orchestrator = _FakeOrchestrator(has_pending=True)
    await _deny_pending_approval_before_text_exit(orchestrator, gate, None)
    assert orchestrator.resolve_calls == []


@pytest.mark.asyncio
async def test_deny_pending_approval_denies_and_clears_the_wait_when_waiting() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    orchestrator = _FakeOrchestrator(has_pending=True)
    forwarder = _FakeWebForwarder()

    await _deny_pending_approval_before_text_exit(orchestrator, gate, forwarder)

    # The explicit decline this whole fix exists to guarantee.
    assert orchestrator.resolve_calls == [False]
    assert forwarder.resolved_calls == [False]
    assert gate.is_waiting is False


@pytest.mark.asyncio
async def test_deny_pending_approval_clears_the_wait_even_if_adapter_had_nothing_pending() -> None:
    # A stale gate (race between the gate and the adapter's own state) must
    # still leave is_waiting False afterward -- same fail-closed handling
    # the mic loop's observe_timeout() path already relies on.
    gate = _gate()
    gate.start_waiting(now=0.0)
    orchestrator = _FakeOrchestrator(has_pending=False)

    await _deny_pending_approval_before_text_exit(orchestrator, gate, None)

    assert orchestrator.resolve_calls == [False]
    assert gate.is_waiting is False


@pytest.mark.asyncio
async def test_deny_pending_approval_tolerates_no_web_forwarder() -> None:
    gate = _gate()
    gate.start_waiting(now=0.0)
    orchestrator = _FakeOrchestrator(has_pending=True)
    await _deny_pending_approval_before_text_exit(orchestrator, gate, None)
    assert gate.is_waiting is False
