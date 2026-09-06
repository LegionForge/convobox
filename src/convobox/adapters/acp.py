"""Adapter for ACP (Agent Control Protocol) over JSON-RPC-over-stdio.

Supports both OpenCode (`opencode acp`) and Kilo (`kilo acp --cwd ...`) as
the underlying backend. Both use the same ACP wire format (newline-delimited
JSON-RPC) with minor differences in request/response/notification names and
session configuration.

Key differences:
- OpenCode: well-documented schema, tested extensively via codex.py adapter.
- Kilo: hard fork of OpenCode with a first-party `kilo acp` server. Permission
  model matches (full-trust default). Known gotcha: Kilo requires pre-setting
  the model via `session/set_config_option` before any `session/prompt` calls.
  Found and live-verified 2026-09-03/04 (PR #377).

Transport: JSON-RPC multiplexes requests/responses and notifications on one
bidirectional pipe, similar to codex.py. A background reader task routes
responses to their awaiting futures and pushes notification-derived
BackendEvents onto a queue.

Architecture:
- `__init__` takes a CLI command (e.g., ["opencode", "acp"] or
  ["kilo", "acp", "--cwd", "/path"]) and optional permission_mode/working_dir.
- `_ensure_session()` spawns the process and performs the ACP handshake
  (session/initialize, then session/start if needed).
- `send_text()` -> `session/prompt` with streamed event consumption.
- `send_hard_stop()` -> `session/cancel` to interrupt an active prompt.
- `is_busy()` tracks whether a prompt is actively being processed.
- `events()` is an async generator of BackendEvents (text, tool calls, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from convobox.adapters.base import (
    BackendAdapter,
    BackendEvent,
    BackendEventType,
    readline_with_stall_diagnostic,
)

logger = logging.getLogger(__name__)

_STREAM_LIMIT = 10 * 1024 * 1024
_RESPONSE_TIMEOUT_S = 30.0


def _resolve_command(command: Sequence[str] | None, backend: str = "opencode") -> list[str]:
    """Resolve the ACP backend command to an absolute path if needed.

    For opencode/kilo: prefer the resolved executable over a bare name,
    matching the pattern from codex.py's _resolve_command.
    """
    resolved = list(command) if command else [backend]
    if not resolved or os.name != "nt":
        return resolved

    head = resolved[0].lower()
    if head not in ("opencode", "kilo"):
        return resolved

    for candidate in (f"{head}.cmd", f"{head}.exe", head):
        path = shutil.which(candidate)
        if path:
            return [path, *resolved[1:]]

    return resolved


_EOF = object()


class ACPAdapter(BackendAdapter):
    """Adapter for ACP (Agent Control Protocol) backends (OpenCode, Kilo)."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        backend: str = "opencode",
        permission_mode: str = "plan",
        working_dir: str | None = None,
    ) -> None:
        """Initialize ACP adapter.

        Args:
            command: CLI command to spawn (e.g., ["opencode", "acp"]).
                     If None, defaults to [backend, "acp"].
            backend: "opencode" or "kilo" (used for command resolution).
            permission_mode: Permission level ("plan", "permissive", or "approve").
            working_dir: Working directory for the spawned process.
        """
        self._backend = backend
        self._command = _resolve_command(command or [backend, "acp"], backend=backend)
        self._permission_mode = permission_mode
        self._working_dir = working_dir

        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._session_id: str | None = None
        self._busy = False
        self._request_seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[BackendEvent | object] = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        """Send a text prompt to the backend."""
        session_id = await self._ensure_session()

        # Send session/prompt and consume the streamed response
        await self._request(
            "session/prompt",
            {"sessionId": session_id, "text": text},
        )
        self._busy = True
        # Events are pushed onto self._events by _read_loop as they arrive

    async def send_interject(self, text: str) -> None:
        """Send a soft interject (mid-turn steering) to the backend.

        ACP doesn't distinguish between "steer" (like Codex) and "queue"
        (like Claude Code) in the protocol, so this degrades to send_text
        if nothing is currently busy, or queues a continuation otherwise.
        """
        if self._busy and self._session_id is not None:
            # Attempt to steer the active session (backend-dependent behavior)
            await self._request(
                "session/steer",
                {"sessionId": self._session_id, "text": text},
            )
        else:
            # Nothing in flight, treat it as a fresh send_text
            await self.send_text(text)

    async def send_hard_stop(self) -> None:
        """Send a hard stop (cancel) to interrupt the active prompt."""
        if self._session_id is not None and self._busy:
            await self._request(
                "session/cancel",
                {"sessionId": self._session_id},
            )
            self._busy = False

    def is_busy(self) -> bool:
        """Return True if the adapter is currently processing a prompt."""
        return self._busy

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        """Async generator of BackendEvents from the backend."""
        while True:
            event = await self._events.get()
            if event is _EOF:
                break
            if isinstance(event, BackendEvent):
                yield event

    async def aclose(self) -> None:
        """Close the adapter and clean up resources."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._proc is not None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()

    async def _ensure_session(self) -> str:
        """Ensure the process is spawned and a session is initialized."""
        async with self._lock:
            if self._proc is None or self._proc.returncode is not None:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=_STREAM_LIMIT,
                    cwd=self._working_dir,
                )
                self._session_id = None
                self._busy = False
                self._pending = {}
                self._reader_task = asyncio.create_task(self._read_loop())

                # ACP handshake: session/initialize
                await self._request(
                    "session/initialize",
                    {"clientInfo": {"name": "convobox", "version": "0.4.0"}},
                )

            if self._session_id is None:
                # session/start to create a new session
                result = await self._request("session/start", {})
                session_id = result.get("sessionId")
                if not isinstance(session_id, str):
                    raise RuntimeError(f"ACP session/start returned no sessionId: {result!r}")
                self._session_id = session_id

                # Kilo-specific: pre-set the model via session/set_config_option
                # if backend is kilo (optional; OpenCode ignores it).
                if self._backend == "kilo":
                    # Try to set a default model. Kilo's default may differ from
                    # `kilo run`'s own default, so we set it explicitly.
                    # This is optional and won't fail if the model doesn't exist.
                    try:
                        await self._request(
                            "session/set_config_option",
                            {
                                "sessionId": session_id,
                                "configId": "model",
                                "value": "claude-3.5-sonnet",  # default; can be overridden by caller
                            },
                        )
                    except RuntimeError as e:
                        logger.warning(f"Failed to set Kilo model: {e}")

            return self._session_id

    async def _read_loop(self) -> None:
        """Background task that reads responses and notifications from the backend."""
        assert self._proc is not None and self._proc.stdout is not None  # nosec B101

        try:
            while True:
                line = await readline_with_stall_diagnostic(
                    self._proc.stdout,
                    self._proc,
                    label="ACP",
                    busy=self.is_busy,
                )

                if not line:
                    logger.info("ACP backend closed stdout")
                    break

                try:
                    payload = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"Failed to decode ACP line: {e}")
                    continue

                # Route responses to pending requests
                if "id" in payload and "result" in payload:
                    request_id = payload["id"]
                    if request_id in self._pending:
                        self._pending[request_id].set_result(payload.get("result", {}))
                        del self._pending[request_id]
                elif "id" in payload and "error" in payload:
                    request_id = payload["id"]
                    if request_id in self._pending:
                        error = payload.get("error", {})
                        self._pending[request_id].set_exception(
                            RuntimeError(f"ACP error: {error}")
                        )
                        del self._pending[request_id]
                elif "method" in payload:
                    # Server request (e.g., session/request_permission)
                    # Auto-decline all permissions for now
                    request_id = payload.get("id")
                    if request_id is not None:
                        deny = {"permissions": {}} if "permission" in payload.get("method", "") else {}
                        await self._write({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": deny,
                        })
                else:
                    # Notification (no id, no method=request)
                    await self._process_notification(payload)

        except asyncio.CancelledError:
            pass
        finally:
            await self._events.put(_EOF)

    async def _process_notification(self, payload: dict[str, Any]) -> None:
        """Process a notification from the backend."""
        # Parse common ACP events and convert to BackendEvent
        # This is a skeleton; real implementation maps ACP notification types
        # to BackendEventType (TEXT, TOOL_CALL, TOOL_RESULT, etc.)

        # Example: agent_message_chunk / agent_message_done -> TEXT
        if payload.get("type") == "agent_message_chunk" or payload.get("type") == "agent_message_done":
            text = payload.get("text", "")
            if text:
                event = BackendEvent(
                    type=BackendEventType.TEXT,
                    content=text,
                )
                await self._events.put(event)
        elif payload.get("type") == "tool_call":
            # tool_call -> TOOL_CALL event
            # Depends on ACP schema; placeholder
            pass

    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the response."""
        request_id = self._request_seq
        self._request_seq += 1

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            payload["params"] = params

        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._pending[request_id] = future

        try:
            await self._write(payload)
            return await asyncio.wait_for(future, timeout=_RESPONSE_TIMEOUT_S)
        except TimeoutError:
            del self._pending[request_id]
            raise RuntimeError(f"ACP request {method} timed out after {_RESPONSE_TIMEOUT_S}s")

    async def _write(self, payload: dict[str, Any]) -> None:
        """Write a JSON-RPC payload to the backend's stdin."""
        assert self._proc is not None and self._proc.stdin is not None  # nosec B101
        self._proc.stdin.write(json.dumps(payload).encode() + b"\n")
        await self._proc.stdin.drain()
