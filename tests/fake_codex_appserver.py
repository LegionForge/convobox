"""A fake `codex app-server` speaking real JSON-RPC-over-stdio, for tests.

Runs as an actual subprocess (spawned with sys.executable "<this file>
app-server"), so CodexAdapter is exercised over genuine pipe transport --
same discipline as fake_claude_cli.py and the real-socket OpenCodeServer.

Message shapes mirror the installed CLI's own schema bundle
(`codex app-server generate-json-schema`, codex-cli 0.144.1) and a live
probe -- see src/convobox/adapters/codex.py's module docstring.
Set FAKE_CODEX_NO_THREAD_ID=1 in the environment to make thread/start
respond with no thread id (an empty thread object) -- there's no turn
text to script this by, since it happens before any turn is ever sent.

Turn behavior is scripted by the prompt text:

  contains "use a tool" -> commandExecution item + agentMessage + completed
  contains "hang"       -> turn/started only; completes on turn/interrupt
  contains "hang and vanish on interrupt" -> like "hang", but exits without
                            responding to turn/interrupt (checked before the
                            plain "hang" match) -- simulates the app-server
                            dying while a request is genuinely in flight
  contains "emit garbage first" -> writes one malformed (non-JSON) stdout
                            line before the normal echo response
  contains "needs approval" -> server->client approval request first (current
                            protocol method); echoes the decision back
  contains "needs file edit approval" -> item/fileChange/requestApproval
                            (current protocol, live-confirmed 2026-07-14 --
                            see codex.py's module docstring)
  contains "needs legacy exec approval" -> same, but the legacy
                            execCommandApproval method name
  contains "needs legacy patch approval" -> same, but applyPatchApproval
  contains "needs permissions approval" -> item/permissions/requestApproval
                            (different response shape -- no "decision" key,
                            a "permissions" object instead); echoes that back
  contains "fail"       -> turn/completed with status "failed"
  contains "die"        -> exits mid-turn
  anything else         -> echoes the prompt as agentMessage + completed
"""

from __future__ import annotations

import json
import os
import sys

THREAD_ID = "thr_test"


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def respond(request_id: object, result: dict) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def respond_error(request_id: object, message: str) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -1, "message": message}})


def notify(method: str, params: dict) -> None:
    emit({"jsonrpc": "2.0", "method": method, "params": params})


def turn_completed(turn_id: str, status: str = "completed", error: object = None) -> None:
    notify(
        "turn/completed",
        {"threadId": THREAD_ID, "turn": {"id": turn_id, "status": status, "error": error}},
    )


def agent_message(text: str) -> None:
    notify(
        "item/completed",
        {"threadId": THREAD_ID, "item": {"type": "agentMessage", "id": "msg_1", "text": text}},
    )


def main() -> None:
    turn_seq = 0
    active_turn: str | None = None
    pending_approval_turn: str | None = None
    approval_req_id = 900
    die_on_interrupt = False

    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")

        if method == "initialize":
            respond(req_id, {"userAgent": "fake-codex/0.0.0"})
        elif method == "initialized":
            pass
        elif method == "thread/start":
            if os.environ.get("FAKE_CODEX_NO_THREAD_ID"):
                respond(req_id, {"thread": {}})
            else:
                respond(req_id, {"thread": {"id": THREAD_ID}})
        elif method == "turn/start":
            turn_seq += 1
            turn_id = f"turn_{turn_seq}"
            text = " ".join(
                i.get("text", "") for i in msg["params"]["input"] if i.get("type") == "text"
            )
            respond(req_id, {"turn": {"id": turn_id, "status": "inProgress"}})
            notify("turn/started", {"threadId": THREAD_ID, "turn": {"id": turn_id, "status": "inProgress"}})
            if "die" in text:
                sys.exit(0)
            if "hang and vanish on interrupt" in text:
                active_turn = turn_id
                die_on_interrupt = True
                continue
            if "hang" in text:
                active_turn = turn_id
                continue
            if "fail" in text:
                turn_completed(turn_id, status="failed", error={"message": "model exploded"})
                continue
            if "needs file edit approval" in text:
                pending_approval_turn = turn_id
                approval_req_id += 1
                emit({
                    "jsonrpc": "2.0",
                    "id": approval_req_id,
                    "method": "item/fileChange/requestApproval",
                    "params": {"threadId": THREAD_ID, "turnId": turn_id},
                })
                continue
            if "needs legacy exec approval" in text:
                pending_approval_turn = turn_id
                approval_req_id += 1
                emit({
                    "jsonrpc": "2.0",
                    "id": approval_req_id,
                    "method": "execCommandApproval",
                    "params": {"threadId": THREAD_ID, "turnId": turn_id, "command": "rm -rf /"},
                })
                continue
            if "needs legacy patch approval" in text:
                pending_approval_turn = turn_id
                approval_req_id += 1
                emit({
                    "jsonrpc": "2.0",
                    "id": approval_req_id,
                    "method": "applyPatchApproval",
                    "params": {"threadId": THREAD_ID, "turnId": turn_id},
                })
                continue
            if "needs permissions approval" in text:
                pending_approval_turn = turn_id
                approval_req_id += 1
                emit({
                    "jsonrpc": "2.0",
                    "id": approval_req_id,
                    "method": "item/permissions/requestApproval",
                    "params": {"threadId": THREAD_ID, "turnId": turn_id},
                })
                continue
            if "needs approval" in text:
                pending_approval_turn = turn_id
                approval_req_id += 1
                emit({
                    "jsonrpc": "2.0",
                    "id": approval_req_id,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"threadId": THREAD_ID, "turnId": turn_id, "command": "rm -rf /"},
                })
                continue
            if "emit garbage first" in text:
                sys.stdout.write("not valid json at all {{{\n")
                sys.stdout.flush()
                agent_message(f"echo: {text}")
                turn_completed(turn_id)
                continue
            if "use a tool" in text:
                notify("item/started", {
                    "threadId": THREAD_ID,
                    "item": {"type": "commandExecution", "id": "cmd_1", "command": "ls"},
                })
                notify("item/completed", {
                    "threadId": THREAD_ID,
                    "item": {"type": "commandExecution", "id": "cmd_1", "command": "ls",
                             "aggregatedOutput": "file1\nfile2", "status": "completed"},
                })
                agent_message("the tool ran")
                turn_completed(turn_id)
                continue
            agent_message(f"echo: {text}")
            turn_completed(turn_id)
        elif method == "turn/steer":
            expected = msg["params"].get("expectedTurnId")
            if active_turn is None or expected != active_turn:
                respond_error(req_id, f"no active turn matching {expected!r}")
                continue
            text = " ".join(
                i.get("text", "") for i in msg["params"]["input"] if i.get("type") == "text"
            )
            respond(req_id, {})
            agent_message(f"steered: {text}")
            turn_completed(active_turn)
            active_turn = None
        elif method == "turn/interrupt":
            turn_id = msg["params"].get("turnId")
            if die_on_interrupt and active_turn == turn_id:
                # Exit without responding -- the read loop's death must
                # fail this pending turn/interrupt request rather than
                # leave it (and the caller awaiting it) hanging forever.
                sys.exit(0)
            respond(req_id, {})
            if active_turn == turn_id:
                turn_completed(turn_id, status="interrupted")
                active_turn = None
        elif method is None and "id" in msg:
            # A response from the client to a server->client request
            # (the approval flow). Echo what it answered and finish the
            # turn. item/permissions/requestApproval's response has no
            # "decision" key at all (a "permissions" object instead) --
            # echo whichever key is actually present, don't assume.
            result = msg.get("result") or {}
            if "decision" in result:
                answer = result["decision"]
            else:
                answer = result.get("permissions")
            if pending_approval_turn is not None:
                agent_message(f"approval decision was: {answer}")
                turn_completed(pending_approval_turn)
                pending_approval_turn = None


if __name__ == "__main__":
    # argv[1] is "app-server" (the adapter appends it); accepted and ignored.
    main()
