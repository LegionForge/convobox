from __future__ import annotations

import asyncio
import json

import pytest

from convobox.approval import hook_script

_ENV = {
    "CONVOBOX_APPROVAL_HOST": "127.0.0.1",
    "CONVOBOX_APPROVAL_PORT": "9999",
    "CONVOBOX_APPROVAL_TOKEN": "secret-token",
}


# --- decide(): fail-closed paths, no network involved ---


def test_decide_denies_when_env_vars_are_missing() -> None:
    output = hook_script.decide('{"tool_name": "Bash", "tool_input": {}}', env={})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_decide_denies_on_malformed_stdin() -> None:
    output = hook_script.decide("not json at all {", env=_ENV)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_decide_denies_on_non_dict_stdin() -> None:
    output = hook_script.decide("[1, 2, 3]", env=_ENV)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_decide_denies_on_a_non_numeric_port() -> None:
    env = {**_ENV, "CONVOBOX_APPROVAL_PORT": "not-a-port"}
    output = hook_script.decide('{"tool_name": "Bash"}', env=env)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_decide_delegates_to_request_decision_with_the_parsed_request() -> None:
    captured: dict[str, object] = {}

    def fake_request_decision(
        host: str, port: int, request: dict[str, object], timeout_s: float
    ) -> dict[str, object]:
        captured.update(host=host, port=port, request=request, timeout_s=timeout_s)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "ok",
            }
        }

    env = {**_ENV, "CONVOBOX_APPROVAL_TIMEOUT_S": "42"}
    output = hook_script.decide(
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
        env=env,
        request_decision=fake_request_decision,
    )
    assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert captured == {
        "host": "127.0.0.1",
        "port": 9999,
        "timeout_s": 42.0,
        "request": {
            "token": "secret-token",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        },
    }


def test_decide_falls_back_to_the_default_timeout_on_a_malformed_override() -> None:
    captured: dict[str, object] = {}

    def fake_request_decision(
        host: str, port: int, request: dict[str, object], timeout_s: float
    ) -> dict[str, object]:
        captured["timeout_s"] = timeout_s
        return {"hookSpecificOutput": {"permissionDecision": "deny"}}

    env = {**_ENV, "CONVOBOX_APPROVAL_TIMEOUT_S": "not-a-number"}
    hook_script.decide(
        '{"tool_name": "Bash"}', env=env, request_decision=fake_request_decision
    )
    assert captured["timeout_s"] == hook_script._DEFAULT_DECISION_TIMEOUT_S


# --- _request_decision: a real socket, same discipline as
# fake_claude_cli.py's real-subprocess/real-pipe testing ---


async def _serve_once(response_line: bytes) -> tuple[asyncio.AbstractServer, int]:
    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readline()
        writer.write(response_line)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_request_decision_allow_over_a_real_socket() -> None:
    server, port = await _serve_once(json.dumps({"decision": "allow"}).encode() + b"\n")
    try:
        result = await asyncio.to_thread(
            hook_script._request_decision,
            "127.0.0.1",
            port,
            {"token": "t", "tool_name": "Bash", "tool_input": {}},
            5.0,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_request_decision_deny_with_reason_over_a_real_socket() -> None:
    server, port = await _serve_once(
        json.dumps({"decision": "deny", "reason": "not now"}).encode() + b"\n"
    )
    try:
        result = await asyncio.to_thread(
            hook_script._request_decision,
            "127.0.0.1",
            port,
            {"token": "t", "tool_name": "Bash", "tool_input": {}},
            5.0,
        )
        output = result["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "not now" in output["permissionDecisionReason"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_request_decision_denies_on_a_malformed_reply() -> None:
    server, port = await _serve_once(b"not json at all\n")
    try:
        result = await asyncio.to_thread(
            hook_script._request_decision, "127.0.0.1", port, {"token": "t"}, 5.0
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        server.close()
        await server.wait_closed()


def test_request_decision_denies_on_connection_refused() -> None:
    # Nothing listening on this port -- the ConvoBox process isn't alive,
    # or its approval server already shut down. Must fail closed, not
    # raise.
    result = hook_script._request_decision("127.0.0.1", 1, {"token": "t"}, 1.0)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
