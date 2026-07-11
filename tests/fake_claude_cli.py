"""A fake `claude` CLI speaking the real stream-json protocol over real pipes.

Runs as an actual subprocess in tests (spawned with sys.executable), so
ClaudeCodeAdapter is exercised over genuine stdin/stdout transport --
same discipline as test_opencode_adapter.py's real-socket OpenCodeServer,
for the same reason: the bugs live in transport handling, not in parsing.

Message shapes mirror a live claude 2.1.207 probe (see
src/convobox/adapters/claude_code.py's module docstring). Behavior is
scripted by the incoming prompt text:

  contains "use a tool" -> tool_use + tool_result turn, then text + result
  contains "hang"       -> emits one text, then no result until interrupted
  contains "fail"       -> result with is_error=true
  contains "die"        -> exits mid-turn without a result
  contains "bigline"    -> emits a >64KB system line first (stream-limit case)
  anything else         -> echoes the prompt as assistant text + result
"""

from __future__ import annotations

import json
import sys


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def emit_result(is_error: bool = False, subtype: str = "success", result: str | None = "ok") -> None:
    emit({"type": "result", "subtype": subtype, "is_error": is_error, "result": result})


def assistant(blocks: list[dict]) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def handle_prompt(text: str) -> None:
    if "bigline" in text:
        emit({"type": "system", "subtype": "init", "padding": "x" * 100_000})
    if "die" in text:
        sys.exit(0)
    if "hang" in text:
        emit(assistant([{"type": "text", "text": "starting the long task"}]))
        return  # no result -- the turn stays in flight until interrupt
    if "fail" in text:
        emit_result(is_error=True, subtype="error_during_execution", result="boom")
        return
    if "use a tool" in text:
        emit(assistant([{"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "ls"}}]))
        emit({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "file1\nfile2"}],
            },
        })
        emit(assistant([{"type": "text", "text": "the tool ran"}]))
        emit_result()
        return
    emit(assistant([{"type": "text", "text": f"echo: {text}"}]))
    emit_result()


def main() -> None:
    hanging = False
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "user":
            blocks = msg.get("message", {}).get("content", [])
            text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            if hanging:
                continue  # queued messages vanish on interrupt, like the real CLI
            if "hang" in text:
                hanging = True
            handle_prompt(text)
        elif msg.get("type") == "control_request":
            if msg.get("request", {}).get("subtype") == "interrupt":
                emit({
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": msg.get("request_id"),
                        "response": {"still_queued": []},
                    },
                })
                if hanging:
                    # The interrupted turn still emits its terminal result,
                    # exactly like the real CLI (observed live).
                    emit_result(is_error=True, subtype="error_during_execution", result=None)
                    hanging = False


if __name__ == "__main__":
    main()
