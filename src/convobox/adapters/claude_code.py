"""Adapter for Claude Code's bidirectional stream-json CLI interface.

Everything here is grounded in live probes of a real installed CLI
(claude 2.1.207 on Windows, 2026-07-11) driven over
``--input-format stream-json --output-format stream-json`` -- the same
"verify against the real thing before writing the adapter" discipline
OPENCODE_API_NOTES.md documents, applied from the start this time
instead of after shipping a wrong adapter. Key empirical findings:

- The transport is a long-lived subprocess speaking NDJSON on
  stdin/stdout (this is the same interface the official Agent SDK wraps;
  spoken directly here to keep ConvoBox dependency-light).
- Multi-turn on one process works: send a user message, read events
  until a ``result`` message, send the next.
- A user message written mid-run is QUEUED as its own next turn, not
  steered into the current one (confirmed live: a "skip the rest"
  interjection sent mid-response did not shorten the running turn; it
  was answered as a separate turn afterwards). send_interject therefore
  has honest queue semantics on this backend, unlike OpenCode's true
  ``delivery="steer"``.
- ``{"type": "control_request", "request": {"subtype": "interrupt"}}``
  cancels the in-flight turn for real: the CLI answers with a
  ``control_response`` (success) and the interrupted turn still emits a
  terminal ``result`` message (subtype ``error_during_execution``), and
  the process remains usable for further turns afterwards (confirmed
  live). Its response's ``still_queued: []`` showed queued-but-unstarted
  messages do not survive the interrupt.
- Output NDJSON lines can exceed asyncio's 64KB default stream limit
  (the ``system/init`` line listing every tool/MCP server did, on the
  first probe) -- hence _STREAM_LIMIT.
- **Permission gate (confirmed live, 2026-07-13): headless ``--print``
  mode has NO runtime-answerable permission channel.** When Claude
  requests a tool that needs approval (e.g. Bash), the process emits the
  ``assistant`` tool_use block and then goes **completely silent** --
  no ``control_request``, no ``result``, nothing -- for as long as the
  process lives (confirmed: 25s+ of dead air on a real installed CLI
  before the probe was killed). This is not a ConvoBox bug to catch and
  answer; Anthropic's own docs confirm headless mode has no interactive
  prompt to answer and expects the permission decision to be made via a
  startup flag (``--permission-mode`` / ``--allowedTools``), not at
  runtime -- unlike Codex's app-server, which sends a real per-call
  JSON-RPC approval request this adapter COULD answer (see codex.py).
  Without a startup flag, every gated tool call hangs the voice session
  forever with zero signal -- exactly the ~50-90s silent stalls seen in
  live UAT before this was diagnosed.

  Fix: default to ``--permission-mode plan`` unless the caller's own
  ``command`` already sets ``--permission-mode`` (a user override always
  wins). Confirmed live: plan mode never hangs -- every turn ends with a
  real, speakable ``result`` -- and it never executes a write/exec
  action; a gated request comes back as explanatory text instead ("I
  can't run that without approval..."). This is the same safety stance
  Codex's adapter already takes (decline the destructive step, but let
  the turn finish) -- see codex.py's _APPROVAL_METHODS -- just expressed
  as a session-level flag because headless mode has no per-call channel
  to express it any other way. Voice-driven approval (wiring
  ConfirmwordDetector into an actual accept/decline flow) is future
  work, same as codex.py's TODO.

- **`--disallowedTools` (confirmed live, 2026-07-14): removes tools
  entirely, doesn't reproduce the hang above.** A real spawned
  ``claude --print ... --disallowedTools Bash Write Edit`` asked to run
  a shell command never emitted a ``Bash`` tool_use at all -- it
  searched for an available shell tool, found none, and reported back
  in plain text that it couldn't do that. Terminal ``result``:
  ``is_error=False, subtype=success`` -- a normal, clean turn end, not
  a stall. Read-only tools (Read/Glob) kept working normally under the
  same flags. This is a more granular sibling to the
  ``--permission-mode plan`` fix above (name specific tools/patterns to
  remove vs. plan mode's blanket no-writes stance).

  **No dedicated config field for this (resolved, 2026-07-14) -- use
  ``command:`` directly, it already supports it with zero new code.**
  ``_resolve_flags()`` only appends its own required protocol flags
  after whatever the caller's ``command`` already contains; it doesn't
  inspect or reject anything else. So::

      backend:
        name: claude-code
        command: ["claude", "--disallowedTools", "Bash", "Write", "Edit"]

  already works today. A dedicated config field or a hardcoded default
  deny-list were both considered and rejected: ``--permission-mode
  plan`` is already the universally-safe zero-config default (nothing
  executes, full stop), so there's no safe default deny-list to pick
  for the more-permissive case a user opts into by overriding
  ``--permission-mode`` themselves -- the right tool list is inherently
  workflow-specific, which is exactly what ``command:`` is already for.
  See ``docs/DESIGN-0.3.0-interaction-and-safety.md``'s phase 3 for the
  full reasoning.
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

# system/init alone blew past asyncio's 64KB default readline limit on a
# real CLI (it inventories every tool and MCP server); tool results can
# be bigger still.
_STREAM_LIMIT = 10 * 1024 * 1024

# Flags this adapter's protocol handling depends on; appended to whatever
# base command the config supplies, so a user command like
# ["claude", "--model", "..."] composes cleanly with them.
# --verbose is required for stream-json output under --print;
# --no-session-persistence keeps voice turns from piling up in the
# user's `claude --resume` picker.
_REQUIRED_FLAGS = [
    "--print",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--no-session-persistence",
]

# The safe default permission mode: never hangs (every turn ends with a
# speakable result) and never executes a write/exec action without the
# user opting into something more permissive themselves. See module
# docstring "Permission gate" for why this exists and what it replaces
# (there is no per-call channel to answer in headless mode, unlike
# codex.py's runtime decline).
_DEFAULT_PERMISSION_MODE = "plan"

# backend.permission_mode -> Claude Code's --permission-mode. NOTE the
# missing "approve": Claude Code's headless mode has NO per-call approval
# channel (see the module docstring's "Permission gate" -- a gated call
# just hangs the session), so voice-approval is impossible here. "approve"
# therefore degrades to "plan" with a warning (safe: nothing executes).
_PERMISSION_CLAUDE_MODE: dict[str, str] = {
    "plan": "plan",
    "permissive": "acceptEdits",
}


def _resolve_flags(command: Sequence[str], permission_mode: str = "plan") -> list[str]:
    """The flags to append after the caller's own command (pure, tested).

    Skips injecting --permission-mode if the caller's own command already
    sets one (an explicit user choice wins). Otherwise translates
    permission_mode; "approve" is not expressible headless (no per-call
    channel) and degrades to plan -- the caller logs the warning.
    """
    if "--permission-mode" in command:
        return list(_REQUIRED_FLAGS)
    mode = _PERMISSION_CLAUDE_MODE.get(permission_mode, _DEFAULT_PERMISSION_MODE)
    return [*_REQUIRED_FLAGS, "--permission-mode", mode]


class ClaudeCodeAdapter(BackendAdapter):
    def __init__(
        self, command: Sequence[str] | None = None, permission_mode: str = "plan"
    ) -> None:
        self._command = list(command) if command else ["claude"]
        if permission_mode == "approve":
            logger.warning(
                "backend.permission_mode='approve' is not supported by Claude "
                "Code (headless mode has no per-call approval channel) -- "
                "falling back to 'plan' (read-only). Use codex for voice-gated "
                "approvals, or 'permissive' to allow edits without a gate."
            )
        self._permission_mode = permission_mode
        self._proc: asyncio.subprocess.Process | None = None
        self._proc_lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None
        # A counter, not a bool: user messages queue (see module
        # docstring), so N sends produce N result messages and busy must
        # hold until the last one lands.
        self._pending = 0
        self._request_seq = 0

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        # Locked for the same reason OpenCodeAdapter._ensure_session is:
        # Orchestrator starts the event-consumer task and the first send
        # concurrently, and without the lock both saw _proc None and each
        # spawned its own claude process -- events attached to one, the
        # prompt went to the other, and the loser's _pending reset made
        # is_busy() lie. Found live (again): the mock-CLI tests drive send
        # and consume sequentially, so only a real Orchestrator run
        # interleaves them.
        async with self._proc_lock:
            if self._proc is None or self._proc.returncode is not None:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command,
                    *_resolve_flags(self._command, self._permission_mode),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=_STREAM_LIMIT,
                )
                # stderr must be drained somewhere or a chatty CLI can fill
                # the pipe and deadlock; drained to debug logs rather than
                # DEVNULL so real failures stay diagnosable.
                self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))
                self._pending = 0
            return self._proc

    # SECURITY EXCEPTION: B101 (assert stripped under python -O), all four
    # asserts below -- type-narrowing assertions on pipes, not security
    # boundaries. Every process this class touches is spawned by
    # _ensure_proc with stdin/stdout/stderr=PIPE, so the streams are
    # non-None by construction; under -O the very next line would raise
    # AttributeError on None instead -- same failure, not a behavior
    # change. Mitigation: single spawn site, private methods only.

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None  # nosec B101 -- spawned with stderr=PIPE
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            logger.debug("claude stderr: %s", line.decode(errors="replace").rstrip())

    async def _write_line(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode() + b"\n"
        proc = await self._ensure_proc()
        assert proc.stdin is not None  # nosec B101 -- spawned with stdin=PIPE
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError):
            # The process died but hasn't been reaped, so returncode was
            # still None and _ensure_proc trusted it. Reap it for real,
            # respawn once, and retry -- a second failure is a genuine
            # error that should surface.
            await proc.wait()
            proc = await self._ensure_proc()
            assert proc.stdin is not None  # nosec B101 -- spawned with stdin=PIPE
            proc.stdin.write(data)
            await proc.stdin.drain()

    async def _send_user_message(self, text: str) -> None:
        await self._write_line(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        )
        self._pending += 1

    async def send_text(self, text: str) -> None:
        await self._send_user_message(text)

    async def send_interject(self, text: str) -> None:
        # Same wire message as send_text: Claude Code's stream-json input
        # has no steer/queue distinction -- a mid-run user message queues
        # as the next turn (confirmed live, see module docstring). Kept as
        # a separate method so the BackendAdapter contract stays uniform
        # and backends with real steering (OpenCode) can honor it.
        await self._send_user_message(text)

    async def send_hard_stop(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            # Nothing running; a stray safeword before any send must be a
            # safe no-op (and must not spawn a process just to stop it).
            self._pending = 0
            return
        self._request_seq += 1
        try:
            await self._write_line(
                {
                    "type": "control_request",
                    "request_id": f"convobox-interrupt-{self._request_seq}",
                    "request": {"subtype": "interrupt"},
                }
            )
        except (OSError, ConnectionError):
            logger.warning("claude interrupt write failed", exc_info=True)
        # Queued-but-unstarted messages don't survive an interrupt
        # (still_queued was empty in the live probe), so their result
        # messages will never arrive -- zeroing here instead of waiting
        # for N results that aren't coming. The in-flight turn's own
        # terminal result DOES still arrive; the max(0, ...) guard in
        # events() absorbs that extra decrement.
        self._pending = 0

    def is_busy(self) -> bool:
        return self._pending > 0

    async def aclose(self) -> None:
        # Terminate the claude subprocess and await it here, while the loop
        # is alive, so its stdin/stdout/stderr pipe transports are closed
        # cleanly. Without this the transports are GC'd after the loop
        # closes and Windows asyncio prints "Event loop is closed" /
        # "unclosed transport" tracebacks on every exit. Idempotent.
        proc, self._proc = self._proc, None
        task, self._stderr_task = self._stderr_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            if proc.stdin is not None:
                proc.stdin.close()  # EOF lets claude exit gracefully
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        proc = await self._ensure_proc()
        assert proc.stdout is not None  # nosec B101 -- spawned with stdout=PIPE
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    # Process exited (or closed stdout): terminal for this
                    # generator. _ensure_proc will respawn on next send.
                    return
                outer = _safe_json_loads(line.decode(errors="replace"))
                if outer is None:
                    continue
                for event in self._to_backend_events(outer):
                    yield event
        finally:
            # Same last-resort safety net as OpenCodeAdapter: if this
            # generator ends for any reason (process died, consumer
            # cancelled), nothing else would ever clear busy.
            self._pending = 0

    def _to_backend_events(self, outer: dict[str, Any]) -> list[BackendEvent]:
        msg_type = outer.get("type")

        if msg_type == "assistant":
            events: list[BackendEvent] = []
            for block in _content_blocks(outer):
                block_type = block.get("type")
                if block_type == "text" and block.get("text"):
                    events.append(
                        BackendEvent(type=BackendEventType.TEXT, content=block["text"])
                    )
                elif block_type == "tool_use":
                    tool_input = block.get("input")
                    events.append(
                        BackendEvent(
                            type=BackendEventType.TOOL_CALL,
                            tool=block.get("name"),
                            tool_input=json.dumps(tool_input) if tool_input is not None else None,
                        )
                    )
                # thinking blocks are deliberately not surfaced: internal
                # deliberation is not something a voice UI should speak.
            return events

        if msg_type == "user":
            # The CLI echoes tool results back as user messages.
            return [
                BackendEvent(
                    type=BackendEventType.TOOL_RESULT,
                    tool_output=json.dumps(block.get("content")),
                )
                for block in _content_blocks(outer)
                if block.get("type") == "tool_result"
            ]

        if msg_type == "result":
            # One result per turn -- including the interrupted turn's
            # error_during_execution result after a hard stop, which
            # send_hard_stop already accounted for by zeroing _pending
            # (hence the max()).
            self._pending = max(0, self._pending - 1)
            if outer.get("is_error"):
                return [
                    BackendEvent(
                        type=BackendEventType.ERROR,
                        content=str(outer.get("result") or outer.get("subtype") or ""),
                    )
                ]
            return [BackendEvent(type=BackendEventType.DONE)]

        # system/*, control_response, rate_limit_event, stream_event
        # (partial chunks), ...: protocol plumbing with no slot in our
        # 5-value model. Narrow on purpose, like OpenCode's mapping.
        return []


def _content_blocks(outer: dict[str, Any]) -> list[dict[str, Any]]:
    content = (outer.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _safe_json_loads(data: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
