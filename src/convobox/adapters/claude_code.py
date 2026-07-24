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

  Fix: default to ``--permission-mode plan`` (backend.permission_mode's
  own default) unless the caller's own ``command`` already sets
  ``--permission-mode`` (a user override always wins). Confirmed live:
  plan mode never hangs -- every turn ends with a real, speakable
  ``result`` -- and it never executes a write/exec action; a gated
  request comes back as explanatory text instead ("I can't run that
  without approval..."). This is the safe universal default;
  ``backend.permission_mode="approve"`` now has a REAL per-call channel
  of its own -- see "Voice-gated tool approval" below, which replaces
  what used to be a documented gap here.

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

- **Voice-gated tool approval (``permission_mode="approve"``), live-built
  and live-verified 2026-07-2x.** ``--permission-mode plan`` above solves
  the hang, but at the cost of never executing anything -- there was no
  real per-call channel to answer, the way codex.py's app-server has.
  This adapter now builds one, using Claude Code's PreToolUse hook
  mechanism, confirmed live over several probes:

  1. **A PreToolUse hook genuinely blocks the tool call on the hook
     subprocess's own wall-clock duration** (confirmed at 3s and 8s
     delays, exact timestamps matched). ``deny`` cleanly ends the turn
     with an explanatory assistant message and ``stop_reason: end_turn``
     (no hang, no crash); ``allow`` lets the tool actually execute.
  2. **``--permission-mode plan`` suppresses tool ATTEMPTS entirely**
     (confirmed live: under ``plan``, Claude drafted a plan and tried
     ``ExitPlanMode`` -- disabled headless -- and never attempted the
     real tool call, so the hook never fired at all). This rules out
     "keep plan as a safety net, hook gates on top of it" -- the hook can
     only gate a call Claude actually attempts, so interactive approval
     pairs with ``acceptEdits`` instead (the hook itself becomes the sole
     gate, not a second layer on plan mode).
  3. **``--settings <path>`` accepts an arbitrary external JSON file**,
     not just a project's own ``.claude/settings.json`` (confirmed live)
     -- so this adapter's hook config never touches the user's own
     working directory or its real settings file.
  4. **Omitting "matcher" in the hook config gates ALL tools**, not a
     named subset (confirmed live) -- the deliberate MVP scope per
     product decision: block/approve every tool call, selective
     per-tool-type gating is future work.

  Mechanism: before spawning, this adapter starts a loopback-only
  (127.0.0.1) asyncio TCP server and writes a temp ``--settings`` file
  wiring Claude Code's PreToolUse hook to
  ``convobox.approval.hook_script`` (stdlib-only, spawned via
  ``-m`` so it works regardless of install layout -- see that module's
  own docstring). The spawned ``claude`` process gets the server's
  host/port and a random per-session auth token via environment
  variables (inherited by the hook script, since Claude Code spawns it as
  a child) -- the token stops another local process from spoofing an
  approval decision over the same loopback port. When the hook connects,
  this adapter emits ``BackendEventType.APPROVAL_REQUEST`` (the same
  event queue TEXT/TOOL_CALL/DONE already flow through -- see the queue
  refactor below) and holds the connection open until
  ``resolve_pending_approval()`` answers it. Only one approval is ever
  pending at a time by construction (this adapter's one-subprocess-at-a-
  time turn model), unlike codex.py's per-request-id JSON-RPC answering --
  a second concurrent hook connection while one is pending is refused
  with an immediate deny rather than queued.

  Refactored ``events()`` from "read stdout directly" to "drain an
  internal queue fed by a background reader task" (matching codex.py's
  existing shape) -- required because there are now TWO independent
  async event sources (the subprocess's stdout AND the approval TCP
  server), and a single ``await proc.stdout.readline()`` can't also learn
  about a hook connection arriving while the subprocess is silently
  blocked inside that very hook (which is exactly what a gated tool call
  looks like: stdout goes quiet, that's the point).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
import tempfile
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType

logger = logging.getLogger(__name__)

# system/init alone blew past asyncio's 64KB default readline limit on a
# real CLI (it inventories every tool and MCP server); tool results can
# be bigger still.
_STREAM_LIMIT = 10 * 1024 * 1024

_EOF = object()

# 127.0.0.1 only -- this channel answers "should a tool call run", so it
# must never be reachable from anything but this adapter's own machine.
_APPROVAL_HOST = "127.0.0.1"
# A gated tool call is a real decision, not a quick reflex reply -- long
# enough that a genuinely slow voice decision doesn't trip it, short
# enough that a ConvoBox crash mid-approval doesn't wedge Claude Code
# forever. ApprovalPromptGate's own (shorter, config-driven) timeout is
# what normally resolves first; this is the hook script's own last-resort
# fail-closed backstop if ConvoBox itself goes silent.
_APPROVAL_DECISION_TIMEOUT_S = 120.0
# The permission mode interactive approval pairs with -- NOT plan (see
# module docstring, finding 2: plan suppresses tool attempts, so the hook
# never fires). The hook itself is the real gate here, not this flag.
_INTERACTIVE_APPROVAL_PERMISSION_MODE = "acceptEdits"

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

# backend.permission_mode -> Claude Code's --permission-mode. "approve"
# and "permissive" both resolve to the SAME underlying CLI flag
# (acceptEdits -- the only thing that flag controls is whether Claude
# ATTEMPTS tool calls at all; "plan" suppresses attempts entirely, see
# finding 2 below). What actually differs between "approve" and
# "permissive" is whether the PreToolUse hook gets wired up alongside it
# (see ClaudeCodeAdapter.__init__'s interactive_approval derivation) --
# "approve" gates every attempted call on a voice decision, "permissive"
# lets them all through ungated. Not independently live-verified that
# "permissive" (unwired, no hook) behaves identically to "approve" with
# every request answered "allow" -- both SHOULD, since the hook is the
# only thing that differs, but only "approve" has been driven through a
# real spawned process end-to-end tonight (see the mechanism section).
_PERMISSION_CLAUDE_MODE: dict[str, str] = {
    "plan": "plan",
    "approve": "acceptEdits",
    "permissive": "acceptEdits",
}


def _resolve_flags(command: Sequence[str], permission_mode: str = "plan") -> list[str]:
    """The flags to append after the caller's own command (pure, tested).

    Skips injecting --permission-mode if the caller's own command already
    sets one (an explicit user choice always wins over this adapter's
    default, the same "respect an explicit override" principle used for
    AEC delay and audio device resolution elsewhere). Otherwise translates
    permission_mode via _PERMISSION_CLAUDE_MODE; an unrecognized value
    falls back to the safe "plan" default rather than raising --
    BackendConfig's own field validator is the actual gate against typos,
    this is just defense in depth.
    """
    if "--permission-mode" in command:
        return list(_REQUIRED_FLAGS)
    mode = _PERMISSION_CLAUDE_MODE.get(permission_mode, _DEFAULT_PERMISSION_MODE)
    return [*_REQUIRED_FLAGS, "--permission-mode", mode]


def _parse_mcp_list_output(text: str) -> list[str]:
    """Extract MCP server names from `claude mcp list`'s human-readable
    output (pure, tested -- no documented --json/structured mode exists,
    confirmed live 2026-07-22: `claude mcp list --json` errors as an
    unknown option).

    Each real server line is ``<name>: <url-or-detail> ...`` -- splitting
    on the FIRST ": " correctly isolates the name even when the name
    itself contains periods/hyphens/spaces (confirmed against a real
    multi-server account whose connector names include exactly that kind
    of punctuation, e.g. "claude.ai Some Connector - Demo - 2026.01.07").
    Lines with no ": " (the "Checking MCP server health..." header, blank
    lines) are skipped rather than erroring -- this must degrade
    gracefully on any future CLI output-format change, not raise.
    """
    names: list[str] = []
    for line in text.splitlines():
        if ": " not in line:
            continue
        name = line.split(": ", 1)[0].strip()
        if name:
            names.append(name)
    return names


class ClaudeCodeAdapter(BackendAdapter):
    def __init__(
        self,
        command: Sequence[str] | None = None,
        permission_mode: str = "plan",
        working_dir: str | None = None,
    ) -> None:
        self._command = list(command) if command else ["claude"]
        self._permission_mode = permission_mode
        # Where the spawned agent reads/writes files. None -> inherit
        # ConvoBox's cwd; an explicit path isolates edits. See BackendConfig.
        self._working_dir = working_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._proc_lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        # A counter, not a bool: user messages queue (see module
        # docstring), so N sends produce N result messages and busy must
        # hold until the last one lands.
        self._pending = 0
        self._request_seq = 0
        # BackendEvent queue fed by _read_loop (stdout) AND
        # _handle_approval_connection (the hook TCP server) -- see module
        # docstring's "Refactored events()" paragraph for why one source
        # (stdout) can't cover both.
        self._events: asyncio.Queue[BackendEvent | object] = asyncio.Queue()

        # Voice-gated tool approval (see module docstring). Derived from
        # permission_mode, not a separate constructor argument -- "approve"
        # is the only mode that wires the hook/TCP server; "plan" and
        # "permissive" both leave this off (see _PERMISSION_CLAUDE_MODE's
        # own comment for why "permissive" doesn't need it: nothing gates
        # those attempts either way, so there's no approval to hold).
        self._interactive_approval = permission_mode == "approve"
        self._approval_token = secrets.token_hex(16)
        self._approval_server: asyncio.Server | None = None
        self._approval_port: int | None = None
        self._settings_path: Path | None = None
        # The hook connection currently awaiting a decision -- at most one
        # at a time (see module docstring, point 4 of the mechanism
        # paragraph).
        self._pending_approval_writer: asyncio.StreamWriter | None = None

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
                env = None
                extra_flags: list[str] = []
                if self._interactive_approval:
                    port = await self._ensure_approval_server()
                    settings_path = self._ensure_settings_file()
                    env = dict(os.environ)
                    env["CONVOBOX_APPROVAL_HOST"] = _APPROVAL_HOST
                    env["CONVOBOX_APPROVAL_PORT"] = str(port)
                    env["CONVOBOX_APPROVAL_TOKEN"] = self._approval_token
                    env["CONVOBOX_APPROVAL_TIMEOUT_S"] = str(_APPROVAL_DECISION_TIMEOUT_S)
                    extra_flags = ["--settings", str(settings_path)]
                elif self._permission_mode == "permissive":
                    # MCP tool calls hit a SEPARATE permission gate that
                    # --permission-mode doesn't cover at all (see
                    # _enumerate_mcp_server_names's docstring) -- without
                    # this, "permissive" (acts without asking) silently
                    # doesn't hold for any MCP server the user has
                    # configured, live-confirmed 2026-07-22.
                    mcp_settings_path = await self._ensure_mcp_permissions_settings_file()
                    extra_flags = ["--settings", str(mcp_settings_path)]
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command,
                    *_resolve_flags(self._command, self._permission_mode),
                    *extra_flags,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=_STREAM_LIMIT,
                    cwd=self._working_dir,
                    env=env,
                )
                # stderr must be drained somewhere or a chatty CLI can fill
                # the pipe and deadlock; drained to debug logs rather than
                # DEVNULL so real failures stay diagnosable.
                self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))
                self._reader_task = asyncio.create_task(self._read_loop(self._proc))
                self._pending = 0
            return self._proc

    async def _ensure_approval_server(self) -> int:
        """Start the loopback TCP server the hook script connects to, once
        per adapter lifetime (a respawned subprocess reuses the same
        server/token -- only the claude process itself dies and restarts,
        not ConvoBox's own approval channel)."""
        if self._approval_server is None:
            self._approval_server = await asyncio.start_server(
                self._handle_approval_connection, host=_APPROVAL_HOST, port=0
            )
            self._approval_port = self._approval_server.sockets[0].getsockname()[1]
        assert self._approval_port is not None  # nosec B101 -- set immediately above
        return self._approval_port

    def _ensure_settings_file(self) -> Path:
        """Write (once) the --settings JSON wiring Claude Code's
        PreToolUse hook to convobox.approval.hook_script, gating every
        tool (no "matcher" -- confirmed live this gates ALL tools, the
        deliberate MVP scope). A temp file, not the working directory's
        own .claude/settings.json -- confirmed live that --settings
        accepts an arbitrary external path, so this never touches or
        risks clobbering the user's real project settings."""
        if self._settings_path is None:
            fd, path = tempfile.mkstemp(
                prefix="convobox-claude-settings-", suffix=".json"
            )
            settings = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'"{sys.executable}" -m convobox.approval.hook_script',
                                }
                            ]
                        }
                    ]
                }
            }
            with os.fdopen(fd, "w") as f:
                json.dump(settings, f)
            self._settings_path = Path(path)
        return self._settings_path

    async def _enumerate_mcp_server_names(self) -> list[str]:
        """Every MCP server this machine's Claude Code install knows
        about, by name -- live-confirmed source of a SEPARATE permission
        gate from --permission-mode.

        Live-confirmed, 2026-07-22: an MCP tool call fails with "Claude
        requested permissions to use mcp__<server>__<tool>, but you
        haven't granted it yet" even under --permission-mode acceptEdits
        -- NOT a hang (the turn ends cleanly, is_error=False, the model
        just reports it can't proceed), but permissive mode's whole point
        ("acts without asking") doesn't hold for MCP tools without an
        explicit grant. --allowedTools does NOT grant this (tried, still
        rejected); a bare "mcp__*" wildcard in --settings permissions.allow
        does NOT work either (tried, still rejected) -- only the exact
        "mcp__<server-name>" (no tool suffix -- confirmed this grants
        every tool on that server, not just one) works.

        `claude mcp list` is the only complete enumeration (confirmed live:
        it includes claude.ai-account-level connectors -- Gmail, Drive,
        Calendar, an ERP connector -- that live outside any local config
        file this adapter could read directly, alongside locally-configured
        servers like a personal Obsidian bridge). OAuth-gated connectors
        ("Needs authentication" in the listing) can't actually be unblocked
        by this grant regardless -- their real gate is a one-time OAuth
        flow this adapter cannot run headless -- but including their names
        anyway is harmless (a no-op grant for a server that's separately
        blocked), so every name found is included rather than trying to
        pre-filter by connection status.

        Best-effort: `claude mcp list` health-checks every server (~3s on
        a real 7-server account, confirmed live) and returns [] on any
        failure (missing CLI, timeout, unexpected output) rather than
        ever blocking startup or crashing over a diagnostic listing.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                self._command[0] if self._command else "claude",
                "mcp",
                "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except (OSError, TimeoutError):
            logger.warning("claude mcp list failed; permissive mode will not grant MCP tools", exc_info=True)
            return []
        return _parse_mcp_list_output(stdout.decode(errors="replace"))

    async def _ensure_mcp_permissions_settings_file(self) -> Path:
        """Write (once) the --settings JSON granting every configured MCP
        server -- the permissive-mode counterpart to
        _ensure_settings_file's hook wiring. See
        _enumerate_mcp_server_names's own docstring for why this exists
        and what it can't fix (OAuth-gated connectors)."""
        if self._settings_path is None:
            server_names = await self._enumerate_mcp_server_names()
            fd, path = tempfile.mkstemp(
                prefix="convobox-claude-settings-", suffix=".json"
            )
            settings = {
                "permissions": {"allow": [f"mcp__{name}" for name in server_names]}
            }
            with os.fdopen(fd, "w") as f:
                json.dump(settings, f)
            self._settings_path = Path(path)
        return self._settings_path

    async def _handle_approval_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except TimeoutError:
            with contextlib.suppress(OSError):
                writer.close()
            return
        request = _safe_json_loads(line.decode(errors="replace"))
        if request is None or request.get("token") != self._approval_token:
            # Wrong/missing token: not a legitimate hook connection (either
            # a bug or another local process probing the port). Deny and
            # drop rather than trust it.
            await self._reject_connection(writer, "unauthorized")
            return
        if self._pending_approval_writer is not None:
            # This adapter's turn model only ever has one tool call in
            # flight at a time, so this should not happen -- answered
            # defensively rather than silently dropped or queued.
            await self._reject_connection(writer, "another approval already pending")
            return
        self._pending_approval_writer = writer
        tool_name = request.get("tool_name")
        tool_input = request.get("tool_input")
        self._events.put_nowait(
            BackendEvent(
                type=BackendEventType.APPROVAL_REQUEST,
                tool=tool_name if isinstance(tool_name, str) else None,
                tool_input=json.dumps(tool_input) if tool_input is not None else None,
            )
        )

    async def _reject_connection(self, writer: asyncio.StreamWriter, reason: str) -> None:
        with contextlib.suppress(OSError):
            writer.write(json.dumps({"decision": "deny", "reason": reason}).encode() + b"\n")
            await writer.drain()
        with contextlib.suppress(OSError):
            writer.close()

    async def resolve_pending_approval(self, approved: bool) -> bool:
        writer = self._pending_approval_writer
        if writer is None:
            return False
        self._pending_approval_writer = None
        # No "reason" on deny: the hook script's own fallback ("denied by
        # voice") already covers it -- a reason here would just double up
        # as "denied by voice: denied by voice" (found live).
        payload = {"decision": "allow"} if approved else {"decision": "deny"}
        try:
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            logger.warning(
                "failed to deliver the voice approval decision to the pending hook",
                exc_info=True,
            )
        finally:
            with contextlib.suppress(OSError):
                writer.close()
        return True

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
        stderr_task, self._stderr_task = self._stderr_task, None
        reader_task, self._reader_task = self._reader_task, None
        for task in (stderr_task, reader_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        # A pending approval must never be left hanging on a torn-down
        # adapter -- the hook script would otherwise block until its own
        # fail-closed timeout. Deny it now; a real answer just isn't
        # coming.
        if self._pending_approval_writer is not None:
            await self.resolve_pending_approval(False)
        server, self._approval_server = self._approval_server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        settings_path, self._settings_path = self._settings_path, None
        if settings_path is not None:
            with contextlib.suppress(OSError):
                settings_path.unlink()
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            if proc.stdin is not None:
                proc.stdin.close()  # EOF lets claude exit gracefully
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        """Feeds self._events from the subprocess's stdout -- the other
        source is _handle_approval_connection (the hook TCP server). See
        module docstring's "Refactored events()" paragraph for why this
        can't just be events()'s own loop anymore."""
        assert proc.stdout is not None  # nosec B101 -- spawned with stdout=PIPE
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                outer = _safe_json_loads(line.decode(errors="replace"))
                if outer is None:
                    continue
                for event in self._to_backend_events(outer):
                    self._events.put_nowait(event)
        finally:
            self._events.put_nowait(_EOF)

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        await self._ensure_proc()
        try:
            while True:
                item = await self._events.get()
                if item is _EOF:
                    # Process exited (or closed stdout): terminal for this
                    # generator. _ensure_proc will respawn on next send.
                    return
                yield item  # type: ignore[misc]
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
