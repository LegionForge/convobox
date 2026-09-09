"""A fake ACP (Agent Client Protocol) server speaking real JSON-RPC-over-
stdio, newline-delimited, for tests.

Runs as an actual subprocess (spawned with sys.executable "<this file>"),
so ACPAdapter is exercised over genuine pipe transport -- same discipline
as fake_codex_appserver.py and fake_claude_cli.py.

Message shapes mirror src/convobox/adapters/acp.py's own module docstring
(live-verified against real spawned opencode/kilo acp processes,
2026-09-05 through 2026-09-08).

Turn behavior is scripted by the prompt text:

  contains "use a tool"     -> tool_call + tool_call_update(completed) +
                                agent_message_chunk, session/prompt then
                                resolves with stopReason "end_turn"
  contains "tool fails"     -> tool_call + tool_call_update(failed, with
                                BOTH a content text-block array and a
                                rawOutput field -- the real shape found
                                2026-09-07 probing a genuine tool
                                failure), session/prompt still resolves
                                normally (ACP has no separate "turn
                                failed" notification the way codex has
                                turn/completed(status="failed") -- a
                                failed TOOL doesn't mean a failed TURN)
  contains "needs approval" -> a server->client session/request_permission
                                REQUEST mid-turn (method+id); the reply IS
                                read back and its outcome echoed into the
                                rejected tool call's own text (proves the
                                real adapter's auto-decline actually
                                reaches this fake over the real pipe, not
                                just that _read_loop's dispatch logic
                                routes it correctly in isolation)
  contains "fail"           -> session/prompt's own JSON-RPC response is
                                an ERROR (checked after "tool fails" --
                                see below -- there is no ACP equivalent
                                of codex's turn/completed(status=
                                "failed"); a whole-turn failure is a
                                request-level error instead)
  contains "hang"           -> session/prompt never resolves on its own;
                                only resolves (stopReason "cancelled")
                                once a session/cancel NOTIFICATION
                                arrives for it -- live-verified shape,
                                see acp.py's own send_hard_stop() comment
  contains "die"            -> exits the process mid-turn, no response
                                ever sent
  contains "emit garbage first" -> one malformed (non-JSON) line before
                                the normal notification/response
  anything else             -> agent_message_chunk echoing the text,
                                session/prompt resolves with stopReason
                                "end_turn"
"""

from __future__ import annotations

import json
import sys

SESSION_ID = "sess_test"


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def respond(request_id: object, result: dict) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def respond_error(request_id: object, message: str) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message}})


def notify(method: str, params: dict) -> None:
    emit({"jsonrpc": "2.0", "method": method, "params": params})


def session_update(update: dict) -> None:
    notify("session/update", {"sessionId": SESSION_ID, "update": update})


def agent_text(text: str) -> None:
    session_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def tool_call(tool_id: str, title: str, kind: str) -> None:
    session_update({
        "sessionUpdate": "tool_call",
        "toolCallId": tool_id, "title": title, "kind": kind,
        "status": "pending", "rawInput": {},
    })


def tool_call_failed(tool_id: str, text: str) -> None:
    session_update({
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool_id, "status": "failed",
        "content": [{"type": "content", "content": {"type": "text", "text": text}}],
        "rawOutput": {"error": text},
    })


def tool_call_completed(tool_id: str, text: str) -> None:
    session_update({
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool_id, "status": "completed",
        "content": [{"type": "content", "content": {"type": "text", "text": text}}],
    })


_PERMISSION_REQUEST_ID = 9001


def main() -> None:
    # id of a currently-pending session/prompt request that hasn't
    # resolved yet (the "hang" scenario) -- session/cancel (a notification,
    # no id of its own) resolves THIS request when it arrives, same as a
    # real opencode/kilo acp process does.
    hung_request_id: object | None = None
    # id of a session/prompt request waiting on the CLIENT's answer to our
    # own server->client session/request_permission (the "needs approval"
    # scenario) -- resolved once that reply arrives, not immediately.
    pending_approval_prompt_id: object | None = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")

        if method == "initialize":
            respond(req_id, {"protocolVersion": 1, "agentCapabilities": {}})
        elif method == "session/new":
            respond(req_id, {"sessionId": SESSION_ID})
        elif method in ("session/set_config_option", "session/set_mode"):
            respond(req_id, {})
        elif method == "session/cancel":
            # Notification -- no id, no reply expected for THIS message.
            # Resolves whatever session/prompt request is still pending.
            if hung_request_id is not None:
                respond(hung_request_id, {"stopReason": "cancelled"})
                hung_request_id = None
        elif method is None and req_id == _PERMISSION_REQUEST_ID:
            # The CLIENT's reply to OUR OWN server->client
            # session/request_permission -- has no "method" (a plain
            # JSON-RPC response), unlike every other branch here. Echo
            # what it answered into the rejected tool call's own text so
            # a test can assert the real adapter's auto-decline shape
            # ({"outcome": {"outcome": "cancelled"}}) actually reached
            # this fake over the real pipe, not just that _read_loop's
            # dispatch logic routes a server-request correctly in
            # isolation.
            outcome = ((msg.get("result") or {}).get("outcome") or {}).get("outcome", "<missing>")
            tool_call("call_1", "write", "edit")
            tool_call_failed("call_1", f"permission outcome was: {outcome}")
            if pending_approval_prompt_id is not None:
                respond(pending_approval_prompt_id, {"stopReason": "end_turn"})
                pending_approval_prompt_id = None
        elif method == "session/prompt":
            text = " ".join(
                block.get("text", "")
                for block in msg["params"]["prompt"]
                if block.get("type") == "text"
            )
            if "die" in text:
                sys.exit(0)
            if "emit garbage first" in text:
                sys.stdout.write("not valid json at all {{{\n")
                sys.stdout.flush()
            if "hang" in text:
                hung_request_id = req_id
                continue
            if "tool fails" in text:
                tool_call("call_1", "read", "read")
                tool_call_failed("call_1", "File not found: nope.txt")
                respond(req_id, {"stopReason": "end_turn"})
                continue
            if "needs approval" in text:
                # Server->client REQUEST mid-turn -- session/prompt itself
                # does NOT resolve until the reply arrives (see the
                # `method is None and req_id == _PERMISSION_REQUEST_ID`
                # branch above), matching a real turn actually waiting on
                # the outcome before deciding how to proceed.
                pending_approval_prompt_id = req_id
                emit({
                    "jsonrpc": "2.0", "id": _PERMISSION_REQUEST_ID,
                    "method": "session/request_permission",
                    "params": {"sessionId": SESSION_ID},
                })
                continue
            if "fail" in text:
                respond_error(req_id, "model exploded")
                continue
            if "use a tool" in text:
                tool_call("call_1", "bash", "execute")
                tool_call_completed("call_1", "hi\n")
                agent_text("the tool ran")
                respond(req_id, {"stopReason": "end_turn"})
                continue
            agent_text(f"echo: {text}")
            respond(req_id, {"stopReason": "end_turn"})


if __name__ == "__main__":
    main()
