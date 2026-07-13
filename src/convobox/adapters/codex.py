"""Adapter for Codex CLI's app-server JSON-RPC-over-stdio interface.

Grounded the same way claude_code.py is: the protocol below was taken
from the installed CLI's own schema bundle (`codex app-server
generate-json-schema`, codex-cli 0.144.1) and then confirmed with live
probes against a real authenticated `codex app-server` before this
adapter was written. Key facts:

- `codex proto` no longer exists (forwards to the interactive CLI);
  `codex exec --json` is one-shot with no mid-run input or interrupt.
  app-server is the interface IDE integrations use and the only one
  with the busy/steer/interrupt semantics ConvoBox needs.
- Handshake: `initialize` request -> `initialized` notification ->
  `thread/start` -> per-utterance `turn/start`.
- `turn/steer` is REAL steering of the in-flight turn (unlike Claude
  Code's queue-only semantics); it requires `expectedTurnId` and fails
  if that turn is no longer active.
- `turn/interrupt {threadId, turnId}` cancels for real (confirmed
  live: interrupted turn emits `turn/completed`, and the same thread
  serves subsequent turns fine).
- Text arrives as `item/completed` notifications whose item has
  `type: "agentMessage"` and the full `text` (deltas exist too;
  ignored, same policy as OpenCode's text.ended-not-text.delta).
- The server can ask the CLIENT questions mid-turn (JSON-RPC server
  requests like `item/commandExecution/requestApproval`) and the turn
  hangs until answered. This adapter auto-declines them all
  (`decision: "decline"` = deny but let the turn continue) with a
  warning log: a voice loop has no approval UI yet, and silently
  auto-APPROVING shell commands from a voice-driven agent would be
  indefensible. Voice-driven approval is future work.

Transport architecture differs from claude_code.py deliberately:
JSON-RPC multiplexes request-responses and notifications on one pipe,
so a single background reader task routes responses to their awaiting
futures and pushes notification-derived BackendEvents onto a queue
that events() drains. (claude_code.py's stream has no responses to
route, so its events() can read the pipe directly.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType

logger = logging.getLogger(__name__)

_STREAM_LIMIT = 10 * 1024 * 1024
_RESPONSE_TIMEOUT_S = 30.0

# ThreadItem types that represent the agent doing something (vs. saying
# something); mapped to TOOL_CALL/TOOL_RESULT on item/started+completed.
_TOOL_ITEM_TYPES = frozenset({"commandExecution", "fileChange", "mcpToolCall", "webSearch"})

# JSON-RPC server->client requests that are approval prompts; all
# auto-declined (see module docstring).
_APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "execCommandApproval",
        "applyPatchApproval",
    }
)

_EOF = object()


class CodexAdapter(BackendAdapter):
    def __init__(self, command: Sequence[str] | None = None) -> None:
        self._command = list(command) if command else ["codex"]
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()  # guards spawn + handshake (see _ensure_thread)
        self._reader_task: asyncio.Task[None] | None = None
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._busy = False
        self._request_seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[BackendEvent | object] = asyncio.Queue()

    async def _ensure_thread(self) -> str:
        # Locked for the same live-proven reason as OpenCodeAdapter's
        # session lock and ClaudeCodeAdapter's process lock: Orchestrator
        # runs the event consumer and the first send concurrently, and an
        # unguarded "is it None yet?" check here would perform the whole
        # spawn+handshake twice.
        async with self._lock:
            if self._proc is None or self._proc.returncode is not None:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command,
                    "app-server",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=_STREAM_LIMIT,
                )
                self._thread_id = None
                self._active_turn_id = None
                self._busy = False
                self._pending = {}
                self._reader_task = asyncio.create_task(self._read_loop(self._proc))
                await self._request(
                    "initialize",
                    {"clientInfo": {"name": "convobox", "version": "0.1.0"}},
                )
                await self._notify("initialized")
            if self._thread_id is None:
                result = await self._request("thread/start", {})
                thread = result.get("thread") or {}
                thread_id = thread.get("id")
                if not isinstance(thread_id, str):
                    raise RuntimeError(f"codex thread/start returned no thread id: {result!r}")
                self._thread_id = thread_id
            return self._thread_id

    async def _write(self, payload: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None  # nosec B101 -- callers go through _ensure_thread, which spawns with stdin=PIPE
        self._proc.stdin.write(json.dumps(payload).encode() + b"\n")
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_seq += 1
        request_id = self._request_seq
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
            # Safe to await: the reader task (started before the first
            # request in _ensure_thread) resolves this, not events()'s
            # consumer -- so a bare send with no events() consumer can't
            # deadlock here.
            return await asyncio.wait_for(future, timeout=_RESPONSE_TIMEOUT_S)
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)

    async def send_text(self, text: str) -> None:
        thread_id = await self._ensure_thread()
        # Busy is set BEFORE the request and _active_turn_id is deliberately
        # NOT taken from the response: a fast turn can be fully processed by
        # the reader task (turn/started ... turn/completed, busy cleared)
        # before this coroutine resumes from the await, and assigning here
        # afterwards would re-latch busy/stale-turn state that the reader
        # already retired. The reader task is the single owner of
        # turn-lifecycle state; this method only flags intent (and unflags
        # it if the request itself fails).
        self._busy = True
        try:
            await self._request(
                "turn/start",
                {"threadId": thread_id, "input": [{"type": "text", "text": text}]},
            )
        except BaseException:
            self._busy = False
            raise

    async def send_interject(self, text: str) -> None:
        # Real steering, when there's a live turn to steer: turn/steer
        # injects into the in-flight turn (schema-required expectedTurnId
        # guards against steering a turn that just ended). With nothing in
        # flight there is nothing to steer, so it degrades to a fresh turn
        # rather than erroring -- the voice-UX-correct behavior when the
        # agent finished in the gap between is_busy() and the send.
        thread_id = await self._ensure_thread()
        turn_id = self._active_turn_id
        if turn_id is None or not self._busy:
            await self.send_text(text)
            return
        try:
            await self._request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": turn_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
        except _RpcError:
            # The turn ended (or was interrupted) between our check and
            # the steer landing; deliver the utterance as a new turn
            # instead of dropping what the user said.
            logger.info("turn/steer missed its turn; sending as a fresh turn instead")
            await self.send_text(text)

    async def send_hard_stop(self) -> None:
        if (
            self._proc is None
            or self._proc.returncode is not None
            or self._thread_id is None
            or self._active_turn_id is None
        ):
            # Nothing in flight; a stray safeword must be a safe no-op and
            # must not spawn a server just to stop it.
            self._busy = False
            return
        try:
            await self._request(
                "turn/interrupt",
                {"threadId": self._thread_id, "turnId": self._active_turn_id},
            )
        except (_RpcError, TimeoutError, OSError, ConnectionError):
            logger.warning("codex turn/interrupt failed", exc_info=True)
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    async def aclose(self) -> None:
        # Terminate the codex app-server subprocess and await it here, while
        # the loop is alive, so its pipe transports close cleanly instead of
        # being GC'd after the loop closes (which prints "Event loop is
        # closed" / "unclosed transport" tracebacks on Windows). Idempotent.
        proc, self._proc = self._proc, None
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            if proc.stdin is not None:
                proc.stdin.close()
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        await self._ensure_thread()
        try:
            while True:
                item = await self._events.get()
                if item is _EOF:
                    return
                yield item  # type: ignore[misc]
        finally:
            # Last-resort safety net, same as the other adapters: if the
            # consumer stops for any reason, nothing else clears busy.
            self._busy = False

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        """Single reader for the multiplexed pipe; see module docstring."""
        assert proc.stdout is not None  # nosec B101 -- spawned with stdout=PIPE
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                msg = _safe_json_loads(line.decode(errors="replace"))
                if msg is None:
                    continue
                if "method" not in msg and "id" in msg:
                    self._resolve_response(msg)
                elif "method" in msg and "id" in msg:
                    await self._answer_server_request(msg)
                else:
                    self._handle_notification(msg)
        except (OSError, ValueError):
            logger.warning("codex app-server read loop died", exc_info=True)
        finally:
            self._busy = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        ConnectionError("codex app-server exited")
                    )
            self._events.put_nowait(_EOF)

    def _resolve_response(self, msg: dict[str, Any]) -> None:
        future = self._pending.get(msg.get("id"))  # type: ignore[arg-type]
        if future is None or future.done():
            return
        if "error" in msg:
            future.set_exception(_RpcError(str(msg["error"])))
        else:
            result = msg.get("result")
            future.set_result(result if isinstance(result, dict) else {})

    async def _answer_server_request(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        if method in _APPROVAL_METHODS:
            # Deny-but-continue, never auto-approve -- see module docstring.
            logger.warning(
                "auto-declining codex approval request %s (no voice approval UI yet)",
                method,
            )
            await self._write(
                {"jsonrpc": "2.0", "id": msg["id"], "result": {"decision": "decline"}}
            )
            return
        logger.warning("unanswerable codex server request %s; declining generically", method)
        await self._write(
            {"jsonrpc": "2.0", "id": msg["id"], "result": {"decision": "decline"}}
        )

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "turn/started":
            turn = params.get("turn") or {}
            if isinstance(turn.get("id"), str):
                self._active_turn_id = turn["id"]
            self._busy = True
            return

        if method == "turn/completed":
            # Fires for every terminal TurnStatus (completed, interrupted,
            # failed) -- schema-confirmed enum. A failed turn surfaces as
            # ERROR; interrupted stays DONE (the user asked for that stop,
            # it isn't an error to report to them).
            self._busy = False
            self._active_turn_id = None
            turn = params.get("turn") or {}
            if turn.get("status") == "failed":
                self._events.put_nowait(
                    BackendEvent(
                        type=BackendEventType.ERROR,
                        content=json.dumps(turn.get("error") or "turn failed")[:500],
                    )
                )
            else:
                self._events.put_nowait(BackendEvent(type=BackendEventType.DONE))
            return

        if method == "error":
            self._events.put_nowait(
                BackendEvent(
                    type=BackendEventType.ERROR,
                    content=json.dumps(params.get("error", params))[:500],
                )
            )
            return

        if method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            item_type = item.get("type")
            if item_type == "agentMessage" and method == "item/completed":
                text = item.get("text")
                if text:
                    self._events.put_nowait(
                        BackendEvent(type=BackendEventType.TEXT, content=text)
                    )
            elif item_type in _TOOL_ITEM_TYPES:
                if method == "item/started":
                    self._events.put_nowait(
                        BackendEvent(
                            type=BackendEventType.TOOL_CALL,
                            tool=item_type,
                            tool_input=json.dumps(
                                item.get("command") or item.get("changes") or item
                            )[:500],
                        )
                    )
                else:
                    self._events.put_nowait(
                        BackendEvent(
                            type=BackendEventType.TOOL_RESULT,
                            tool_output=json.dumps(
                                item.get("aggregatedOutput") or item.get("status") or ""
                            )[:500],
                        )
                    )
        # Everything else (reasoning items, deltas, token usage, MCP
        # startup, rate limits, ...) is deliberately unmapped -- same
        # narrow-on-purpose policy as the other two adapters.


class _RpcError(RuntimeError):
    """A JSON-RPC error response from the app-server."""


def _safe_json_loads(data: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
