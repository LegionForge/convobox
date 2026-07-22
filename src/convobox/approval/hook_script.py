"""Standalone Claude Code PreToolUse hook for voice-gated tool approval
(docs/DESIGN-0.3.0-interaction-and-safety.md, Phase 3).

Claude Code spawns this as a subprocess for every gated tool call (wired
via the ``--settings`` file ClaudeCodeAdapter generates -- see its module
docstring) and BLOCKS the tool call on this process's stdout + exit code.
Live-confirmed, 2026-07-2x: a PreToolUse hook genuinely blocks Claude
Code's execution for the hook's own wall-clock duration; a "deny" decision
cleanly ends the turn with an explanatory assistant message (no hang, no
crash); "allow" lets the tool actually run. Also live-confirmed: omitting
"matcher" entirely in the hook config gates ALL tools, not just a named
subset -- the deliberate MVP scope (block/approve everything, not
selective by tool type).

Talks to ClaudeCodeAdapter's local approval TCP server -- 127.0.0.1 only,
loopback, never exposed to the network -- over a connection whose
address/port/auth token are passed as environment variables the adapter
sets on the spawned ``claude`` process (inherited here, since Claude Code
spawns this hook as a child of that process):

    CONVOBOX_APPROVAL_HOST, CONVOBOX_APPROVAL_PORT, CONVOBOX_APPROVAL_TOKEN

Deliberately dependency-light (stdlib only, no ``convobox`` import): this
runs once per gated tool call, so it must start fast, and it must keep
working even if something about the ``convobox`` package's own import
graph is broken -- the one process in this feature that absolutely must
not itself become a new failure mode.

FAILS CLOSED on every error path -- missing env vars (hook configured but
ConvoBox isn't the one that set it up), a refused/broken connection
(ConvoBox process not alive or already shut down), a malformed reply, or
a timeout waiting for the voice decision (silence must never be treated
as approval -- the same invariant ApprovalPromptGate's own timeout-denies
contract enforces on the other end of this channel). A stuck or silent
hook must never fail OPEN into "the tool call just runs anyway".
"""

from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any

_CONNECT_TIMEOUT_S = 5.0
_DEFAULT_DECISION_TIMEOUT_S = 120.0


def _deny_output(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow_output(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }


def decide(
    raw_stdin: str,
    env: dict[str, str],
    request_decision: Any = None,
) -> dict[str, Any]:
    """The full hook decision for one gated tool call.

    ``request_decision`` defaults to the real socket call (``_request_decision``);
    overridable so the env-var/malformed-input fail-closed paths are
    unit-testable without a real socket -- same "pure logic, I/O is a
    seam" convention as ConfirmwordDetector/ApprovalDetector.
    """
    if request_decision is None:
        request_decision = _request_decision

    try:
        payload = json.loads(raw_stdin)
    except json.JSONDecodeError:
        return _deny_output("ConvoBox hook: malformed hook input, denying")
    if not isinstance(payload, dict):
        return _deny_output("ConvoBox hook: malformed hook input, denying")

    host = env.get("CONVOBOX_APPROVAL_HOST")
    port_raw = env.get("CONVOBOX_APPROVAL_PORT")
    token = env.get("CONVOBOX_APPROVAL_TOKEN")
    if not host or not port_raw or not token:
        return _deny_output("ConvoBox voice-approval channel not configured; denying")
    try:
        port = int(port_raw)
    except ValueError:
        return _deny_output("ConvoBox voice-approval channel misconfigured; denying")

    timeout_s = _DEFAULT_DECISION_TIMEOUT_S
    timeout_raw = env.get("CONVOBOX_APPROVAL_TIMEOUT_S")
    if timeout_raw:
        try:
            timeout_s = float(timeout_raw)
        except ValueError:
            pass

    request = {
        "token": token,
        "tool_name": payload.get("tool_name"),
        "tool_input": payload.get("tool_input"),
    }
    return request_decision(host, port, request, timeout_s)  # type: ignore[no-any-return]


def _request_decision(
    host: str, port: int, request: dict[str, Any], timeout_s: float
) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_S) as sock:
            sock.sendall(json.dumps(request).encode() + b"\n")
            sock.settimeout(timeout_s)
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    return _deny_output(
                        "ConvoBox voice-approval connection closed before a decision; denying"
                    )
                buffer += chunk
    except OSError as exc:
        return _deny_output(f"ConvoBox voice-approval channel unreachable ({exc}); denying")

    line = buffer.split(b"\n", 1)[0]
    try:
        response = json.loads(line.decode(errors="replace"))
    except json.JSONDecodeError:
        return _deny_output("ConvoBox voice-approval channel sent a malformed reply; denying")
    if not isinstance(response, dict):
        return _deny_output("ConvoBox voice-approval channel sent a malformed reply; denying")

    if response.get("decision") == "allow":
        return _allow_output("approved by voice")
    reason = response.get("reason")
    return _deny_output(f"denied by voice: {reason}" if reason else "denied by voice")


def main() -> None:
    raw_stdin = sys.stdin.read()
    output = decide(raw_stdin, dict(os.environ))
    sys.stdout.write(json.dumps(output))
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
