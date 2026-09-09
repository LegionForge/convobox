"""Adapter for ACP (Agent Control Protocol) over JSON-RPC-over-stdio.

Supports both OpenCode (`opencode acp`) and Kilo (`kilo acp --cwd ...`) as
the underlying backend -- Kilo is a hard fork of OpenCode's protocol
(docs/ROADMAP.md), and both were live-probed against this exact adapter
module (2026-09-05/06, this Mac, real spawned `opencode acp` process,
throwaway probe scripts, same convention as every other adapter's own
module docstring uses) -- the first commit of this file (PR #384) got
several of the wire-level details wrong from documentation/inference
alone; every method name and payload shape below is now real-process
confirmed, not just schema-read:

- **Handshake is a top-level `initialize`** (`{protocolVersion,
  clientCapabilities}`), NOT `session/initialize`. A session is then
  created with **`session/new`** (`{cwd, mcpServers}`), NOT
  `session/start` -- neither of the latter two exist; opencode 1.18.20
  returns `-32601 Method not found` for anything else.
- **`session/prompt` takes `{sessionId, prompt: [{type: "text", text}]}`**,
  a content-block array -- NOT `{sessionId, text}`.
- **Notifications arrive as a real JSON-RPC notification** (`"method":
  "session/update"`, no `"id"`), whose `params.update` object carries the
  actual event, discriminated by `update.sessionUpdate` (e.g.
  `agent_message_chunk`, `tool_call`, `tool_call_update`, `usage_update`,
  `available_commands_update`) -- NOT a bare top-level `"type"` field on
  the outer payload. The first commit's dispatcher treated every
  `"method"`-bearing payload as a *request* needing a reply, so real
  notifications (method + no id) fell through and were silently dropped
  -- the entire text/tool-call stream never reached `_process_notification`
  at all before this fix.
- **`session/cancel` must be sent as a notification (no `"id"`), not a
  request.** Sent as a request (this file's first commit awaited a
  response via `_request`), opencode returns `-32601 Method not found`
  for it. Sent as a notification, it genuinely aborts an in-flight tool
  call within the same event loop tick -- `session/prompt`'s own pending
  response then resolves with `stopReason: "cancelled"`. Same conclusion
  Kilo's earlier live pass reached (docs/ROADMAP.md, PR #377/#378) for
  its own `session/cancel`, now confirmed for OpenCode too and narrowed
  to the exact reason the first attempt failed: request vs. notification,
  not a missing method.
- **No steering primitive exists in ACP at all** (established
  2026-09-03, docs/field-notes/2026-09-03-stt-parakeet-prototype-and-acp-
  protocol-probes.md) -- the first commit's `send_interject` called a
  `session/steer` method that was never confirmed to exist and isn't
  part of the spec; removed in favor of the same degrade-to-a-fresh-
  message pattern claude_code.py's own send_interject already uses for
  a backend with no steer/queue distinction.
- Pre-setting the model via `session/set_config_option` (`{sessionId,
  configId: "model", value}`) before the first prompt is opt-in
  (`model` param) and applies to BOTH backends, not just Kilo as this
  docstring previously claimed -- live-verified 2026-09-08 that a fresh
  ACP session can default to an unauthenticated/unusable model for
  OpenCode just as easily as for Kilo (docs/ROADMAP.md). Leave unset to
  accept the session's own default.
- **Permission-request handling (`session/request_permission`) is now
  CONFIRMED, live-verified 2026-09-07** -- it does NOT fire by default
  (full-trust posture, matching every earlier pass), and ACP's own
  `session/set_mode` has no third "ask" option either (just `build`/
  `plan`, see the set_mode bullet below). It DOES fire when OpenCode's
  own PROJECT config (an `opencode.json` in the session's cwd with
  `{"permission": {"edit": "ask", "bash": "ask"}}`) requests it -- a
  posture set entirely on the agent's own side, invisible to and not
  controllable through anything in this adapter or `backend.
  permission_mode`. This file's existing auto-decline (`{"outcome":
  {"outcome": "cancelled"}}`) was accepted with no protocol error, and
  the tool call the request was for then correctly reported `status:
  "failed"` with a human-readable "The user rejected permission to use
  this specific tool call." message -- confirming both the response
  shape this file already sent AND the "failed" tool_call_update
  handling below, previously a best-effort guess, are correct. No
  live-answerable voice-gated channel is wired up for this on
  ConvoBox's own side -- auto-decline stays the deliberate, fail-closed
  default (an opencode.json requesting "ask" gets a silent, safe no,
  not a hang or a crash) rather than building one, since this posture
  isn't reachable from `backend.permission_mode` at all -- a user would
  have to opt into it from OUTSIDE ConvoBox's own config surface.
- **`permission_mode == "plan"` maps to `session/set_mode(sessionId,
  "plan")`** (`{sessionId, modeId}` -- note `modeId`, not `mode`; result
  is `{}` on success), called once per session right after `session/
  new`. Live-verified 2026-09-07: this genuinely blocks a real file-write
  prompt against OpenCode -- the agent explained it was in plan mode and
  no file was created, no `tool_call` update was even emitted (the model
  itself declines, matching ROADMAP's prior single-test finding, now
  re-confirmed). `"permissive"` needs no protocol call: full-trust is
  already the session's own default. `"approve"` has no ACP equivalent
  -- there is no live-answerable per-tool approval channel by default for
  either backend (see the bullet above), so `run_convobox.py`'s startup
  guard rejects `backend.name == "acp"` with `permission_mode ==
  "approve"` outright (same fail-closed stance as codex's own currently-
  broken `approve` mode) rather than silently downgrading to `plan` or
  silently upgrading to full trust -- either would violate what the user
  actually configured without telling them.

Transport: JSON-RPC multiplexes requests/responses and notifications on one
bidirectional pipe, similar to codex.py. A background reader task routes
responses to their awaiting futures and pushes notification-derived
BackendEvents onto a queue.

Architecture:
- `__init__` takes a CLI command (e.g., ["opencode", "acp"] or
  ["kilo", "acp", "--cwd", "/path"]) and optional permission_mode/working_dir.
- `_ensure_session()` spawns the process and performs the ACP handshake
  (`initialize`, then `session/new` if needed).
- `send_text()` -> `session/prompt` with streamed event consumption.
- `send_hard_stop()` -> `session/cancel` (as a notification) to interrupt
  an active prompt.
- `is_busy()` tracks whether a prompt is actively being processed.
- `events()` is an async generator of BackendEvents (text, tool calls, etc.).
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


class ACPBackendDied(RuntimeError):
    """Raised for every request still pending when the backend process
    exits (or its stdout pipe closes) before answering -- see
    _read_loop()'s own finally block. A distinct subclass of RuntimeError,
    not a bare one, so _ensure_session()'s own `except RuntimeError`
    blocks (around session/set_config_option / session/set_mode, which
    deliberately log-and-continue on an ordinary per-call failure) can
    tell "this one call failed" apart from "the whole backend is gone"
    and re-raise the latter instead of silently swallowing it and trying
    the next call against a process that no longer exists.
    """


class ACPAdapter(BackendAdapter):
    """Adapter for ACP (Agent Control Protocol) backends (OpenCode, Kilo)."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        backend: str = "opencode",
        permission_mode: str = "plan",
        working_dir: str | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize ACP adapter.

        Args:
            command: CLI command to spawn (e.g., ["opencode", "acp"]).
                     If None, defaults to [backend, "acp"].
            backend: "opencode" or "kilo" (used for command resolution).
            permission_mode: Permission level ("plan", "permissive", or "approve").
            working_dir: Working directory for the spawned process.
            model: Provider/model-id to pin via session/set_config_option,
                same config field and format OpenCodeAdapter already uses
                (backend.model). Live-verified 2026-09-08: a FRESH ACP
                session -- for OpenCode as well as Kilo, not just Kilo as
                previously believed -- can default to a model this account
                isn't actually configured to use (observed: OpenCode's own
                default session picked "openai/gpt-5.6-terra" while this
                machine's real working model is "inception/mercury-2");
                prompting against it doesn't error, it just never resolves
                until _RESPONSE_TIMEOUT_S elapses. Leave unset to accept
                the session's own default (today's original behavior,
                works if that default happens to be valid for your
                account).
        """
        self._backend = backend
        self._command = _resolve_command(command or [backend, "acp"], backend=backend)
        self._permission_mode = permission_mode
        self._working_dir = working_dir
        self._model = model

        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._session_id: str | None = None
        self._busy = False
        self._request_seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[BackendEvent | object] = asyncio.Queue()
        # Tracks the in-flight session/prompt round trip so its own
        # completion can clear is_busy()/emit DONE without blocking the
        # caller -- see send_text()'s own docstring for why.
        self._prompt_task: asyncio.Task[None] | None = None

    async def send_text(self, text: str) -> None:
        """Send a text prompt to the backend.

        Fired as a background task, NOT awaited inline -- live-verified
        2026-09-08: session/prompt's own JSON-RPC response only resolves
        once the WHOLE turn completes (it returns the final stopReason/
        usage, not just an ack the way codex.py's turn/start does).
        Awaiting it here would block the caller -- Orchestrator.
        handle_transcript(), called directly from ConvoBox's own mic
        loop for every utterance -- for the full turn duration, tool
        calls included: mic capture keeps running, but is_busy() would
        report False the ENTIRE time a response is actually being
        generated (busy was only ever set AFTER the blocking await
        returned, i.e. after the turn was already over), so a mid-turn
        utterance gets routed to a second concurrent send_text() instead
        of send_interject() at the Orchestrator's own is_busy() check.
        Fixed the same way codex.py's send_text is structured (see that
        adapter's own comment): busy is set before the request, and a
        background task -- not a notification handler, since ACP has no
        separate "turn completed" notification the way codex.py's
        turn/completed is -- clears it and emits DONE/ERROR once
        session/prompt actually resolves.
        """
        session_id = await self._ensure_session()
        self._busy = True
        self._prompt_task = asyncio.create_task(self._await_prompt(session_id, text))

    async def _await_prompt(self, session_id: str, text: str) -> None:
        """Background completion of one session/prompt round trip -- see
        send_text()'s own docstring for why this isn't awaited inline."""
        task = asyncio.current_task()
        try:
            # Live-verified 2026-09-06: session/prompt takes a content-block
            # array, not a bare "text" string -- see this module's own
            # docstring.
            await self._request(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            )
            event = BackendEvent(type=BackendEventType.DONE)
        except asyncio.CancelledError:
            # aclose() tearing this down -- nothing left to report to.
            return
        except Exception as exc:  # noqa: BLE001 -- a background task's own exception would otherwise vanish silently (never retrieved)
            event = BackendEvent(type=BackendEventType.ERROR, content=f"{type(exc).__name__}: {exc}")
        if self._prompt_task is not task:
            # Superseded by a newer send_text() (e.g. one issued right
            # after send_hard_stop(), which already cleared busy/reported
            # its own outcome) -- this completion belongs to an
            # already-abandoned turn; do not touch shared state for it.
            return
        self._busy = False
        await self._events.put(event)

    async def send_interject(self, text: str) -> None:
        """Send a soft interject (mid-turn steering) to the backend.

        ACP has no steering primitive at all (confirmed 2026-09-03, see
        this module's own docstring) -- unlike claude_code.py, there is no
        wire message to reach for here, not even a queue-behind-the-
        current-turn one. Always degrades to a fresh send_text(), the same
        choice claude_code.py's own send_interject makes for the same
        reason (no steer/queue distinction on this backend).
        """
        await self.send_text(text)

    async def send_hard_stop(self) -> None:
        """Send a hard stop (cancel) to interrupt the active prompt.

        Live-verified 2026-09-06: session/cancel must be sent as a
        notification (no "id") -- sent as a request awaiting a response
        (this file's first version), opencode returns -32601 Method not
        found. As a notification it genuinely aborts an in-flight tool
        call; session/prompt's own pending response then resolves with
        stopReason: "cancelled" rather than ever timing out.

        Guards on the process actually being alive, not just
        self._session_id/self._busy: a stray safeword arriving after
        force_kill() (which has no override here and delegates straight
        to aclose() -- self._proc is None afterward, but self._session_id
        is deliberately left set, see _ensure_session's own respawn
        check) used to reach `_notify` -> `_write`'s own `assert
        self._proc is not None`, raising AssertionError out of
        Orchestrator.hard_stop() and skipping stop_event_loop()/
        approval_gate.cancel_wait() entirely. Same proc-liveness guard
        codex.py's own send_hard_stop uses, for the same reason.
        """
        if (
            self._proc is None
            or self._proc.returncode is not None
            or self._session_id is None
            or not self._busy
        ):
            # Nothing in flight (or nothing left alive to interrupt) -- a
            # stray safeword must be a safe no-op, not spawn a process
            # just to stop it, and must still clear busy so a stale True
            # doesn't linger past whatever left it that way.
            self._busy = False
            return
        try:
            await self._notify(
                "session/cancel",
                {"sessionId": self._session_id},
            )
        except OSError:
            logger.warning("ACP session/cancel failed", exc_info=True)
        self._busy = False

    def is_busy(self) -> bool:
        """Return True if the adapter is currently processing a prompt."""
        return self._busy

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        """Async generator of BackendEvents from the backend."""
        try:
            while True:
                event = await self._events.get()
                if event is _EOF:
                    return
                if isinstance(event, BackendEvent):
                    yield event
        finally:
            # Last-resort safety net, same as codex.py's own events(): if
            # the consumer stops for any reason, nothing else clears busy.
            self._busy = False

    async def aclose(self) -> None:
        """Close the adapter and clean up resources."""
        if self._prompt_task is not None:
            self._prompt_task.cancel()
            # Awaited for synchronization only (Task[None] -- assigning its
            # result would trip mypy's func-returns-value check, so this
            # stays a bare statement rather than this file's usual `_ =
            # await expr` house style; matches codex.py's own identical
            # shape in _terminate_and_kill_process).
            with contextlib.suppress(asyncio.CancelledError):
                await self._prompt_task

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        # Cleared here (not left in self._proc) so a second aclose()/
        # force_kill() call -- force_kill() has no override of its own and
        # delegates straight here, see BackendAdapter.force_kill()'s
        # contract -- sees proc is None and returns immediately instead of
        # calling .terminate() on an already-dead process. Live-caught
        # 2026-09-09: on Windows, .terminate() on a transport whose real
        # OS process already exited raises ProcessLookupError from
        # asyncio's own _check_proc(), which the old unguarded `if
        # self._proc is not None: self._proc.terminate()` had no defense
        # against -- violating aclose()'s own documented "must be
        # idempotent and must not raise" contract. Mirrors codex.py's own
        # _terminate_and_kill_process() for the same reason.
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    async def _ensure_session(self) -> str:
        """Ensure the process is spawned and a session is initialized."""
        async with self._lock:
            if self._proc is None or self._proc.returncode is not None:
                if self._reader_task is not None:
                    # A stale reader from a previous, now-dead process --
                    # live-caught 2026-09-09: _read_loop() used to
                    # dereference self._proc fresh on every iteration
                    # instead of taking the process as a parameter, so an
                    # old reader could still be draining its last buffered
                    # lines when this respawn overwrote self._proc/
                    # self._reader_task, then loop back around and call
                    # readline() on the NEW process's stdout concurrently
                    # with the new reader -- "readline() called while
                    # another coroutine is already waiting for incoming
                    # data". _read_loop is now pinned to the process it
                    # was started for (see its own signature), so this
                    # cancel is just tidiness (no reference to the old
                    # task would otherwise remain), not load-bearing for
                    # correctness the way it would have been before.
                    self._reader_task.cancel()
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
                self._reader_task = asyncio.create_task(self._read_loop(self._proc))

                # ACP handshake: a top-level `initialize`, not scoped under
                # session/ -- live-verified 2026-09-06 against opencode
                # 1.18.20 (see this module's own docstring).
                await self._request(
                    "initialize",
                    {"protocolVersion": 1, "clientCapabilities": {}},
                )

            if self._session_id is None:
                # session/new creates a session; live-verified 2026-09-06
                # (the first version of this file used the nonexistent
                # session/start, see this module's own docstring). cwd is
                # required -- falls back to the current directory, matching
                # what the spawned process's own cwd already resolved to
                # when self._working_dir is None.
                result = await self._request(
                    "session/new",
                    {"cwd": self._working_dir or os.getcwd(), "mcpServers": []},
                )
                session_id = result.get("sessionId")
                if not isinstance(session_id, str):
                    raise RuntimeError(f"ACP session/new returned no sessionId: {result!r}")
                self._session_id = session_id

                # Pin the model if the caller configured one (backend.model,
                # same field/format OpenCodeAdapter already uses) -- applies
                # to BOTH opencode and kilo, not just kilo as an earlier
                # version of this code assumed. Live-verified 2026-09-08:
                # opencode's own fresh ACP session can default to an
                # unusable model just as easily as kilo's does (see this
                # module's own docstring and __init__'s `model` param) --
                # there is no known-safe value to guess if the caller
                # doesn't set one, so this stays opt-in rather than
                # hardcoding a specific model name that would only be
                # correct for one account's own provider configuration.
                if self._model is not None:
                    try:
                        await self._request(
                            "session/set_config_option",
                            {
                                "sessionId": session_id,
                                "configId": "model",
                                "value": self._model,
                            },
                        )
                    except ACPBackendDied:
                        raise
                    except RuntimeError as e:
                        logger.warning(f"Failed to set ACP model to {self._model!r}: {e}")

                # Map permission_mode onto ACP's own session/set_mode --
                # live-verified 2026-09-07 (see this module's own
                # docstring): modeId "plan" genuinely blocks a real
                # file-write prompt (the model declines, no tool_call
                # emitted, no file created). "permissive" needs no call
                # (full trust is the session's own default already).
                # "approve" has no ACP equivalent and is rejected earlier,
                # at run_convobox.py's own startup guard -- this should
                # never see permission_mode == "approve" in practice.
                if self._permission_mode == "plan":
                    try:
                        await self._request(
                            "session/set_mode",
                            {"sessionId": session_id, "modeId": "plan"},
                        )
                    except ACPBackendDied:
                        raise
                    except RuntimeError as e:
                        logger.warning(f"Failed to set ACP session mode to 'plan': {e}")

            return self._session_id

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        """Background task that reads responses and notifications from the
        backend.

        Takes `proc` as a parameter rather than reading self._proc fresh
        each iteration -- live-caught 2026-09-09: dereferencing self._proc
        let a stale reader (still draining its final buffered lines after
        its own process died) collide with a freshly spawned respawn's own
        reader on the SAME StreamReader object, "readline() called while
        another coroutine is already waiting for incoming data" -- pinning
        this task to the exact process it was started for (same choice
        codex.py's own _read_loop(proc) already makes) means an old reader
        only ever reads its own old proc's stdout, never a newer one's.
        """
        assert proc.stdout is not None  # nosec B101
        task = asyncio.current_task()

        try:
            while True:
                line = await readline_with_stall_diagnostic(
                    proc.stdout,
                    proc,
                    label="ACP",
                    busy=self.is_busy,
                )

                if not line:
                    logger.info("ACP backend closed stdout")
                    return

                try:
                    payload = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"Failed to decode ACP line: {e}")
                    continue

                # Route responses to pending requests. future.done() is
                # checked before touching it in both branches (live-caught
                # 2026-09-09): asyncio.wait_for's own timeout path cancels
                # the future first and only removes it from self._pending
                # afterward, on the timed-out coroutine's own turn -- a
                # response arriving in that same gap would otherwise hit
                # set_result/set_exception on an already-cancelled future
                # and raise InvalidStateError, killing this whole loop.
                if "id" in payload and "result" in payload:
                    request_id = payload["id"]
                    future = self._pending.get(request_id)
                    if future is not None and not future.done():
                        result = payload.get("result")
                        future.set_result(result if isinstance(result, dict) else {})
                    self._pending.pop(request_id, None)
                elif "id" in payload and "error" in payload:
                    request_id = payload["id"]
                    future = self._pending.get(request_id)
                    if future is not None and not future.done():
                        error = payload.get("error", {})
                        future.set_exception(RuntimeError(f"ACP error: {error}"))
                    self._pending.pop(request_id, None)
                elif "method" in payload and "id" in payload:
                    # Server REQUEST (e.g., session/request_permission) --
                    # has both method and id, unlike a notification, and
                    # needs a reply. Auto-decline: full-trust is this
                    # backend's own live-confirmed default (see this
                    # module's own docstring), so this path is a rarely-
                    # exercised safety net, not something a live process
                    # has ever actually sent a response shape for --
                    # this decline shape is a best-effort guess, not
                    # independently confirmed.
                    request_id = payload["id"]
                    await self._write({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"outcome": {"outcome": "cancelled"}},
                    })
                elif "method" in payload:
                    # Notification: has a method but no id (session/update
                    # is the one that matters -- text/tool-call streaming).
                    # Live-verified 2026-09-06: the real event lives at
                    # params.update, discriminated by update.sessionUpdate
                    # -- NOT a bare top-level "type" on the outer payload
                    # (see this module's own docstring for why the first
                    # version of this branch never reached here at all).
                    update = payload.get("params", {}).get("update")
                    if isinstance(update, dict):
                        await self._process_notification(update)

        except asyncio.CancelledError:
            # Read loop cancelled, clean shutdown expected -- aclose()
            # already tears down proc/pending on its own; nothing further
            # to reject here (see the finally block's own guard below).
            raise
        except (OSError, ValueError):
            # Live-caught 2026-09-09: this loop previously caught ONLY
            # CancelledError, so a readline()/drain() OSError (broken
            # pipe) or a ValueError (a line over _STREAM_LIMIT) escaped
            # as this TASK's own exception -- aclose()'s `await
            # self._reader_task` then re-raised it right back out of
            # aclose(), violating that method's own documented "must not
            # raise" contract (and, since force_kill() has no override
            # and delegates straight to aclose(), force_kill() too).
            # Mirrors codex.py's own _read_loop, which catches exactly
            # these two for the same reason.
            logger.warning("ACP read loop died", exc_info=True)
        except Exception:
            # True last resort: aclose()/force_kill() escalating a
            # kill_phrase/safeword eject must NEVER raise (base.py's own
            # contract), and an eject silently failing to fire is worse
            # than logging an unanticipated bug and tearing down cleanly
            # anyway.
            logger.exception("ACP read loop died from an unexpected exception")
        finally:
            # Live-caught 2026-09-09: this used to be `await
            # self._events.put(_EOF)` alone -- no self._busy = False, and
            # no rejection of whatever's still in self._pending. Losing
            # busy meant is_busy() stayed True forever after the backend
            # died, routing the next utterance to send_interject() instead
            # of send_text() while believing a dead turn was still live.
            # Losing the pending rejection meant a request awaiting
            # _RESPONSE_TIMEOUT_S (30s) had no way to learn the backend
            # was already gone -- it just sat there for the full 30s
            # before timing out on its own, and if the caller was
            # _await_prompt (session/prompt itself), the resulting ERROR
            # event usually arrived too late to reach anyone: events()
            # had already returned on the _EOF pushed here, and
            # Orchestrator's own consumer does not re-subscribe to a
            # cleanly-ended generator. put_nowait (not the awaited put()
            # this used to be) matches codex.py's own idiom -- safe today
            # regardless since self._events has no maxsize, but a future
            # bounded queue wouldn't silently reintroduce a cancellation
            # hazard here the way an awaited put() would.
            self._busy = False
            prompt_task = self._prompt_task
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        ACPBackendDied("ACP backend process exited before a response arrived")
                    )
            # Live-caught 2026-09-09, in the test written to pin the fix
            # above: future.set_exception() only SCHEDULES _await_prompt's
            # own reaction (it awaits this exact future via _request) --
            # it does not run it. Pushing _EOF immediately afterward, in
            # the same synchronous stretch, put it on the queue BEFORE
            # _await_prompt ever got a turn to push its own ERROR event
            # behind it, so events()'s consumer saw _EOF first and
            # returned without ever seeing the ERROR that followed it one
            # queue slot too late. aclose() already awaits _prompt_task
            # BEFORE cancelling _reader_task, so in the aclose()-triggered
            # shutdown path this is already done and the await below is a
            # no-op; it only actually waits in the "backend died on its
            # own" path finding #1 above is about. Bounded and swallowed
            # -- this is best-effort ordering, not something that should
            # ever block real teardown.
            if prompt_task is not None and not prompt_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(prompt_task), timeout=1.0)
                except (asyncio.CancelledError, TimeoutError):  # must not skip the _EOF push below no matter what happens in this await (nothing to log: both are expected outcomes here, not bugs). TimeoutError is wait_for's own bound firing; CancelledError is the one that actually matters -- live-caught 2026-09-09 by an independent verification review of this exact fix: contextlib.suppress(Exception), used in an earlier version of this line, does NOT catch CancelledError (a BaseException since 3.8) -- cancelling prompt_task elsewhere (aclose() cancels it directly) while this shielded await is in flight propagates ITS cancellation through the shield's own outer future regardless, which used to skip put_nowait(_EOF) entirely and permanently hang events() for the rest of the session (Orchestrator only re-creates its consumer task when it's .done(), not merely stuck). Deliberately NOT a bare `except BaseException`: _await_prompt (the only thing prompt_task can ever be) never lets anything else escape it -- it catches Exception broadly and CancelledError specifically, always completing normally or via one of these two -- so a narrower tuple here is both correct and avoids also swallowing KeyboardInterrupt/SystemExit/GeneratorExit the way a blind BaseException catch would.
                    pass
            # Live-caught 2026-09-09 by the same review: a STALE reader
            # (this one) can still be inside the shielded await above when
            # _ensure_session's respawn branch reassigns self._reader_task
            # to a fresh reader for a NEW process. Pushing _EOF after that
            # point would end the NEW generation's own events() consumer
            # with a leftover from a generation that's already gone --
            # only push it if nothing has superseded this exact task in
            # the meantime, same identity check _await_prompt already uses
            # for the same reason.
            if self._reader_task is task:
                self._events.put_nowait(_EOF)

    async def _process_notification(self, update: dict[str, Any]) -> None:
        """Convert one session/update's `update` object into a BackendEvent.

        `update` is already unwrapped (params.update, not the outer
        JSON-RPC envelope) -- see _read_loop. Shapes below are live-
        verified 2026-09-06 against a real `opencode acp` process
        (session/prompt -> agent_message_chunk/tool_call/tool_call_update);
        agent_thought_chunk, usage_update, and available_commands_update
        are real, observed sessionUpdate kinds this adapter has no use for
        and intentionally drops.
        """
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content")
            text = content.get("text", "") if isinstance(content, dict) else ""
            if text:
                await self._events.put(BackendEvent(type=BackendEventType.TEXT, content=text))
        elif kind == "tool_call":
            await self._events.put(
                BackendEvent(
                    type=BackendEventType.TOOL_CALL,
                    tool=update.get("title"),
                    tool_input=json.dumps(update.get("rawInput"))
                    if update.get("rawInput") is not None
                    else None,
                )
            )
        elif kind == "tool_call_update" and update.get("status") in ("completed", "failed"):
            # Live-verified 2026-09-07 (a real tool failure, not inferred:
            # opencode's own `read` tool against a nonexistent path) that a
            # "failed" update carries the SAME `content` text-block array a
            # "completed" one does -- e.g. content[0].content.text ==
            # "File not found: ..." -- in addition to a `rawOutput` field
            # (here `{"error": "File not found: ..."}`). Prefer the same
            # human-readable text extraction both statuses share rather
            # than falling back to raw JSON only for the failed case --
            # there is no reason a tool failure should read worse than its
            # own success path.
            content_blocks = update.get("content") or []
            texts = [
                block["content"]["text"]
                for block in content_blocks
                if isinstance(block, dict)
                and isinstance(block.get("content"), dict)
                and "text" in block["content"]
            ]
            text_or_raw = "\n".join(texts) if texts else json.dumps(update.get("rawOutput"))
            if update.get("status") == "completed":
                await self._events.put(
                    BackendEvent(type=BackendEventType.TOOL_RESULT, tool_output=text_or_raw)
                )
            else:
                await self._events.put(
                    BackendEvent(type=BackendEventType.ERROR, content=text_or_raw)
                )

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
            raise RuntimeError(f"ACP request {method} timed out after {_RESPONSE_TIMEOUT_S}s")
        finally:
            # Not just in the TimeoutError branch above -- live-caught
            # 2026-09-09: a write failure (_write raising, e.g. a broken
            # pipe) or this coroutine itself being cancelled (aclose()
            # cancelling _prompt_task mid session/prompt) both used to
            # leave this request's Future in self._pending forever, with
            # nothing left to ever pop it. pop(..., None) rather than del
            # -- _read_loop's own finally block may have already rejected
            # and removed every pending entry by the time this runs (the
            # backend died while this exact request was in flight).
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no "id", no response expected).

        Distinct from _request: some ACP methods (session/cancel,
        live-verified 2026-09-06 -- see this module's own docstring) are
        only accepted in notification form. Sending them via _request
        attaches an "id" the server never replies to in that shape,
        producing a -32601 Method not found instead of the intended
        effect.
        """
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        await self._write(payload)

    async def _write(self, payload: dict[str, Any]) -> None:
        """Write a JSON-RPC payload to the backend's stdin."""
        assert self._proc is not None and self._proc.stdin is not None  # nosec B101
        self._proc.stdin.write(json.dumps(payload).encode() + b"\n")
        await self._proc.stdin.drain()
