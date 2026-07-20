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
  hangs until answered. This adapter auto-declines them all with a
  warning log: a voice loop has no approval UI yet, and silently
  auto-APPROVING shell commands from a voice-driven agent would be
  indefensible. Voice-driven approval is future work.

  **The deny payload is per-method, not one blanket `{"decision":
  "decline"}` (bug found + fixed 2026-07-14, live-verified for the
  reachable path).** Reading codex-cli 0.144.1's own published JSON
  schemas (`codex app-server generate-json-schema`) shows the response
  shape differs by method:
  `item/commandExecution/requestApproval`/`item/fileChange/requestApproval`
  (the current protocol's approval requests) take `{"decision":
  "decline"}`; `execCommandApproval`/`applyPatchApproval` (legacy names,
  still declared in the server's schema union) have NO `"decline"`
  value in their `ReviewDecision` enum at all -- the schema-correct deny
  is `{"decision": "denied"}`; `item/permissions/requestApproval` has an
  entirely different shape with no `"decision"` field -- a required
  `"permissions"` object naming what's granted, so `{"permissions": {}}`
  (grant nothing) is the deny-equivalent. See `_APPROVAL_DENY_PAYLOADS`.
  **Live-verified, both current-protocol methods**: spawned a real
  `codex app-server` (0.144.1) with `approvalPolicy: "untrusted"`. (1)
  Asked it to run a destructive-flavored command (`rm -f` on a
  nonexistent file, safe by construction) -- confirmed the server sends
  `item/commandExecution/requestApproval`, and this adapter's exact
  `{"decision": "decline"}` produced `"exec command rejected by user"`;
  the command never ran. (2) Asked it to WRITE a file via its editing
  tool (2026-07-14, second probe) -- confirmed the server sends
  `item/fileChange/requestApproval` (fired twice; codex retried once
  before giving up), and the same `{"decision": "decline"}` response
  worked both times: the model reported *"I couldn't complete the file
  creation because the file-editing tool request was rejected by the
  environment"*, and the target file was confirmed absent from disk
  afterward. Both of the current protocol's two approval methods are now
  live-confirmed, not just schema-read. The legacy-method
  (`execCommandApproval`/`applyPatchApproval`) and permissions-method
  payloads remain schema-verified but **not** observed live -- this
  server version sent only the two current-protocol methods across both
  probe sessions, suggesting (not yet proof) the legacy names may be
  unreachable dead code for this client/server combination.

- **A pending approval request survives a deliberate delay + unrelated
  traffic on the same connection (confirmed live, 2026-07-14).** Probed
  for the future "discuss" flow (`docs/DESIGN-0.3.0-interaction-and-safety.md`
  phase 2 -- the user asks a question about a pending approval instead of
  deciding immediately): captured a real pending
  `item/commandExecution/requestApproval` request, left it deliberately
  unanswered for 20s, sent a completely unrelated request on the *same*
  JSON-RPC connection in the meantime (a second, independent
  `thread/start` -- got a normal response, proving the pipe isn't
  serialized behind the pending approval), then answered the *original*
  request's id -- it resolved normally (`"exec command rejected by
  user"`, clean `turn/completed`). The server does not time out or
  invalidate a pending approval across an intervening exchange, at least
  at this scale (one 20s delay, one interleaved request) -- not proof of
  no-timeout at arbitrary scale, but enough to unblock building
  "discuss" without a request-preservation workaround.

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
import os
import shutil
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
# auto-declined (see module docstring), with a per-method deny payload --
# NOT one blanket {"decision": "decline"} -- because the response schema
# differs by method (confirmed against codex-cli 0.144.1's own published
# schemas, cross-checked live for the reachable one; see module docstring):
# - item/commandExecution/requestApproval, item/fileChange/requestApproval
#   (the current protocol's approval requests): {"decision": "decline"}.
# - execCommandApproval, applyPatchApproval (legacy protocol names, still
#   declared in the server's schema union but not observed live against
#   this version): {"decision": "decline"} is NOT a valid ReviewDecision
#   value for these -- the schema-correct deny is {"decision": "denied"}.
# - item/permissions/requestApproval: an entirely different response
#   shape (no "decision" field at all -- a required "permissions" object
#   naming what's granted). {"permissions": {}} grants nothing, the
#   schema-correct equivalent of declining.
_APPROVAL_DENY_PAYLOADS: dict[str, dict[str, Any]] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": "denied"},
    "applyPatchApproval": {"decision": "denied"},
    "item/permissions/requestApproval": {"permissions": {}},
}

# These are the two current app-server approval methods whose approve and
# decline response shapes have both been confirmed against Codex's schema.
# Older protocol names and permissions requests stay fail-closed: the former
# use a different review-decision vocabulary, and the latter require a
# structured permissions grant that ConvoBox cannot safely infer from speech.
_INTERACTIVE_APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)

_EOF = object()


def _resolve_command(command: Sequence[str] | None) -> list[str]:
    resolved = list(command) if command else ["codex"]
    if os.name != "nt" or not resolved:
        return resolved
    head = resolved[0]
    if head.lower() != "codex":
        return resolved
    for candidate in ("codex.cmd", "codex.exe", "codex"):
        path = shutil.which(candidate)
        if path:
            return [path, *resolved[1:]]
    return resolved


# backend.permission_mode -> codex config overrides, injected as `-c
# key=value` at spawn (which take precedence over ~/.codex/config.toml, so
# the posture is ConvoBox's decision, not the user's codex config).
# Verified live (2026-07-20): `codex -c approval_policy=X -c sandbox_mode=Y
# app-server` starts and honors the overrides. sandbox_mode enum:
# read-only | workspace-write | danger-full-access; approval_policy
# includes untrusted (escalate writes to approval) and never.
_PERMISSION_CODEX_OVERRIDES: dict[str, tuple[str, str]] = {
    # (approval_policy, sandbox_mode)
    "plan": ("never", "read-only"),          # investigate; cannot write; no prompts
    "approve": ("untrusted", "workspace-write"),  # writes escalate -> voice gate
    "permissive": ("never", "workspace-write"),   # writes freely, no prompts
}


def _permission_config_args(permission_mode: str) -> list[str]:
    override = _PERMISSION_CODEX_OVERRIDES.get(permission_mode)
    if override is None:
        return []
    approval_policy, sandbox_mode = override
    return [
        "-c", f"approval_policy={approval_policy}",
        "-c", f"sandbox_mode={sandbox_mode}",
    ]


class CodexAdapter(BackendAdapter):
    def __init__(
        self, command: Sequence[str] | None = None, permission_mode: str = "plan"
    ) -> None:
        self._command = _resolve_command(command)
        # Injected before the `app-server` subcommand at spawn -- see
        # _permission_config_args and _ensure_thread's create_subprocess_exec.
        self._permission_args = _permission_config_args(permission_mode)
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()  # guards spawn + handshake (see _ensure_thread)
        self._reader_task: asyncio.Task[None] | None = None
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._busy = False
        self._request_seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[BackendEvent | object] = asyncio.Queue()
        self._interactive_approvals = False
        self._pending_approval: tuple[int, str] | None = None

    def set_interactive_approvals(self, enabled: bool) -> None:
        self._interactive_approvals = enabled

    async def resolve_pending_approval(self, approved: bool) -> bool:
        """Answer one operator-held current-protocol approval request.

        The app-server leaves the originating turn blocked until this JSON-RPC
        response arrives.  There is deliberately no auto-approve fallback:
        an unexpected/missing request is reported as ``False`` to the caller.
        """
        pending = self._pending_approval
        if pending is None:
            return False
        request_id, method = pending
        deny_payload = _APPROVAL_DENY_PAYLOADS[method]
        payload = {"decision": "approve"} if approved else deny_payload
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": payload})
        self._pending_approval = None
        return True

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
                    # `-c` config overrides are global codex options and MUST
                    # come before the `app-server` subcommand.
                    *self._permission_args,
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
                self._pending_approval = None
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
        # Never leave an operator-held request dangling when the safeword
        # aborts the turn.  Declining first is both safer and gives the server
        # a well-formed answer before the interrupt lands.
        if self._pending_approval is not None:
            with contextlib.suppress(OSError, ConnectionError):
                await self.resolve_pending_approval(False)
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
        payload = _APPROVAL_DENY_PAYLOADS.get(method)
        if payload is not None:
            if method in _INTERACTIVE_APPROVAL_METHODS and self._interactive_approvals:
                request_id = msg.get("id")
                if not isinstance(request_id, int):
                    logger.warning("codex approval request had a non-integer id; declining")
                    await self._write({"jsonrpc": "2.0", "id": request_id, "result": payload})
                    return
                if self._pending_approval is not None:
                    # Codex normally blocks the turn on one approval.  If a
                    # future server version sends another before the first is
                    # answered, never replace the decision the user is seeing.
                    logger.warning("second codex approval arrived while one was pending; declining")
                    await self._write({"jsonrpc": "2.0", "id": request_id, "result": payload})
                    return
                self._pending_approval = (request_id, method)
                params = msg.get("params")
                self._events.put_nowait(
                    BackendEvent(
                        type=BackendEventType.APPROVAL_REQUEST,
                        content=_describe_approval_request(method, params),
                    )
                )
                logger.warning("codex approval request pending operator decision: %s", method)
                return
            # Deny-but-continue, never auto-approve -- see module docstring.
            logger.warning(
                "auto-declining codex approval request %s (no voice approval UI yet)",
                method,
            )
            await self._write({"jsonrpc": "2.0", "id": msg["id"], "result": payload})
            return
        # Unknown method outside the deny-payload map -- "decision": "decline"
        # is the best-effort fallback (matches the request/response shape
        # every KNOWN approval method except item/permissions/requestApproval
        # uses), not a verified-correct answer for whatever this is.
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


def _describe_approval_request(method: str, params: object) -> str:
    """Render the action Codex asked to perform for the local approval UI."""
    data = params if isinstance(params, dict) else {}
    label = (
        "COMMAND EXECUTION"
        if method == "item/commandExecution/requestApproval"
        else "FILE CHANGE"
    )
    lines = [f"APPROVAL REQUIRED — {label}"]
    command = data.get("command")
    changes = data.get("changes") or data.get("patch")
    if isinstance(command, str) and command.strip():
        lines.extend(("", "Requested command:", command))
    elif isinstance(changes, str) and changes.strip():
        lines.extend(("", "Requested change:", changes))
    else:
        # Keep an unfamiliar-but-current request inspectable instead of
        # presenting an empty warning.  The UI wraps it; the cap prevents a
        # pathological payload from monopolizing the terminal.
        lines.extend(("", "Request details:", json.dumps(data, indent=2)[:2000]))
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        lines.extend(("", f"Working directory: {cwd}"))
    reason = data.get("reason")
    if isinstance(reason, str) and reason.strip():
        lines.extend(("", f"Reason: {reason}"))
    return "\n".join(lines)


def _safe_json_loads(data: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
