"""Adapter for Codex CLI's app-server JSON-RPC-over-stdio interface.

Grounded the same way claude_code.py is: the protocol below was taken
from the installed CLI's own schema bundle (`codex app-server
generate-json-schema`, codex-cli 0.144.1) and then confirmed with live
probes against a real authenticated `codex app-server` before this
adapter was written. NOTE (re-verified against codex-cli 0.148.0,
2026-08-22): `--out <DIR>` is now a required argument to
`generate-json-schema` -- the bare invocation recorded throughout this
file's history (as used when each version-tagged comment below was
written) now exits 2. Add `--out` if reproducing any of these checks
against current codex-cli. Key facts:

- `codex proto` no longer exists (forwards to the interactive CLI);
  `codex exec --json` is one-shot with no mid-run input or interrupt.
  app-server is the interface IDE integrations use and the only one
  with the busy/steer/interrupt semantics ConvoBox needs.
- Handshake: `initialize` request -> `initialized` notification ->
  `thread/start` -> per-utterance `turn/start`.
- `turn/steer` is REAL steering of the in-flight turn (unlike Claude
  Code's queue-only semantics); it requires `expectedTurnId` and fails
  if that turn is no longer active.
- `turn/interrupt {threadId, turnId}` cancels the TURN for real
  (confirmed live: interrupted turn emits `turn/completed`, and the
  same thread serves subsequent turns fine) -- but NOT necessarily an
  already-dispatched `commandExecution` tool call's underlying shell
  subprocess. Live-caught 2026-08-09 (real UAT session, docs/field-notes
  /2026-08-09-hard-stop-does-not-cancel-an-in-flight-tool-call.md): five
  separate incidents where `turn/interrupt` succeeded (zero RPC errors
  logged) and ConvoBox's own state reset immediately, but the real
  command's `tool_result` still arrived 16-48+ seconds later, on the
  underlying command's own schedule. The turn-level guarantee above is
  real and still holds; it just doesn't extend as far down as this
  docstring previously implied.
- Text arrives as `item/completed` notifications whose item has
  `type: "agentMessage"` and the full `text` (deltas exist too;
  ignored, same policy as OpenCode's text.ended-not-text.delta).
- **ARTIFACT wiring (2026-08-07, schema-only -- not yet live-probed).**
  Re-ran `codex app-server generate-json-schema` against codex-cli
  0.146.1 specifically to check the artifact-pane gap
  docs/KNOWN-ISSUES.md flagged ("codex hasn't been looked at"): a
  completed `FileChangeThreadItem` (`item.type == "fileChange"`, one of
  `_TOOL_ITEM_TYPES` below) is `{type, id, status, changes: [{path,
  kind, diff}]}` -- every changed file's final path in one
  `item/completed` notification, confirmed from the schema bundle, NOT
  from a live authenticated session (unlike everything else in this
  docstring). See `_resolve_artifact_writes` for the wiring and its own
  status caveat.
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
import re
import shutil
import signal
import subprocess  # nosec B404 -- see _kill_by_command_text()'s own use for why
import sys
import time
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any

from convobox.adapters import _windows_job_object
from convobox.adapters.base import (
    ARTIFACT_MEDIA_TYPES,
    BackendAdapter,
    BackendEvent,
    BackendEventType,
    BackgroundJob,
    JobState,
    readline_with_stall_diagnostic,
)

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
#
# "approve" has NO working entry here -- see _CODEX_APPROVE_MODE_ERROR
# below for why, and docs/KNOWN-ISSUES.md's "Codex permission_mode:
# approve" entry for the full live-verified writeup (superseded
# 2026-09-02; originally diagnosed 2026-08-30 as "crashes", now confirmed
# more precisely).
_PERMISSION_CODEX_OVERRIDES: dict[str, tuple[str, str]] = {
    # (approval_policy, sandbox_mode)
    "plan": ("never", "read-only"),          # investigate; cannot write; no prompts
    "permissive": ("never", "workspace-write"),   # writes freely, no prompts
}

# 2026-09-02: JP reported live (macOS) that `approve` mode "didn't
# actually push approvals". Root-caused with a raw JSON-RPC probe against
# the real codex-cli 0.152.1 app-server, bypassing this adapter entirely
# -- NOT a ConvoBox protocol-usage bug, an upstream behavior:
#
# - `approval_policy=untrusted` (this dict's value until this fix): codex
#   REJECTS it outright at spawn -- "approval_policy = 'untrusted' is no
#   longer supported; remove this setting" -- confirmed unchanged from
#   the 2026-08-30 diagnosis (docs/KNOWN-ISSUES.md), now against a newer
#   codex-cli version too.
# - `approval_policy=on-request` (or `on-failure`), `sandbox_mode=
#   read-only` -- the two schema-valid candidates that dict's own comment
#   floated as unverified -- BOTH silently bypass the sandbox instead of
#   escalating to approval: a real write went through with ZERO
#   `item/fileChange/requestApproval` (or `commandExecution/...`)
#   ever sent, live-confirmed via a raw JSON-RPC probe with NO ConvoBox
#   code in the loop at all (rules out an adapter-side detection bug).
# - `approval_policy=never`, `sandbox_mode=read-only` (this is `plan`
#   mode's own mapping) DOES correctly block the same write -- model
#   response: "I can't create the file because this workspace is
#   currently mounted read-only." Isolated A/B, changing ONLY
#   approval_policy with sandbox_mode held constant at read-only: `never`
#   blocks, `on-request` silently allows. This looks like an upstream
#   codex-cli behavior change/regression (the module docstring's own
#   2026-07-14 probe against 0.144.1 DID observe real
#   `item/fileChange/requestApproval` escalation under the
#   then-current `untrusted` policy) rather than anything fixable from
#   ConvoBox's side of the protocol.
#
# No approval_policy value tried delivers real "escalate to a voice-gated
# decision" behavior on this codex-cli version -- the ONLY combination
# confirmed to actually prevent an unreviewed write is plan mode's own
# (never/read-only). Rather than ship a config that silently provides
# ZERO protection while claiming to gate writes (worse than the old
# crash, which at least failed loudly), CodexAdapter refuses to start
# under `approve` at all -- see its __init__.
_CODEX_APPROVE_MODE_ERROR = (
    "backend.permission_mode 'approve' is not currently safe to use with "
    "the codex backend -- live-verified 2026-09-02 (docs/KNOWN-ISSUES.md) "
    "that no codex-cli approval_policy value both starts successfully AND "
    "actually escalates a write to a voice-gated approval on this "
    "codex-cli version; the schema-valid candidates silently let the "
    "write through with NO approval prompt at all, which is worse than "
    "failing here. Use 'plan' (read-only, cannot write) if you want a "
    "safe default, or 'permissive' if you specifically want codex to act "
    "without asking. This is an upstream codex-cli gap, not a ConvoBox "
    "config choice -- see docs/KNOWN-ISSUES.md for the full writeup and "
    "to check whether it's since been resolved."
)


def _permission_config_args(permission_mode: str) -> list[str]:
    if permission_mode == "approve":
        raise RuntimeError(_CODEX_APPROVE_MODE_ERROR)
    override = _PERMISSION_CODEX_OVERRIDES.get(permission_mode)
    if override is None:
        return []
    approval_policy, sandbox_mode = override
    return [
        "-c", f"approval_policy={approval_policy}",
        "-c", f"sandbox_mode={sandbox_mode}",
    ]


def _strip_shell_quotes(text: str) -> str:
    """Drop ' and " characters. codex reports commandExecution items as
    the ORIGINAL shell-quoted invocation text (e.g. `/bin/zsh -lc "sh -c
    'echo x; sleep 20'"`), but the actual live process's argv -- what
    `ps` shows -- has already had that quoting consumed by the
    intermediate shell layers (confirmed live, 2026-08-15: the real `ps`
    line was `sh -c echo x; sleep 20`, with neither the outer `/bin/zsh
    -lc` wrapper's quotes nor the inner `'...'` quotes surviving). Quote
    characters are the only structural difference between the two
    representations in every case observed so far -- stripping them
    from both sides before comparing is what makes the substring match
    below actually succeed.
    """
    return text.replace("'", "").replace('"', "")


def _normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace (including a real embedded
    newline/tab) to a single space, and strip the ends.

    Added 2026-08-25, live-verified on Linux: BSD `ps` (macOS) octal-
    escapes an embedded newline as the literal text `\\012` (see
    `_unescape_ps_octal` below), but Linux's `procps` `ps` does something
    different -- it renders the SAME embedded newline as a plain space,
    with no escape sequence at all. Confirmed directly: spawning a real
    multi-line `python3 -c "line1\\nline2"` process and reading its own
    `ps -eo pid,ppid,command` entry back showed the two lines joined by
    an ordinary space, not `\\012`. `_unescape_ps_octal` alone is a no-op
    against that rendering (there's no octal escape to reverse), so the
    substring match below -- comparing against codex's own reported
    `command` text, which contains REAL newline bytes parsed from JSON --
    failed to match at all: `_kill_by_command_text` returned an empty
    list against a real, still-running multi-line process on this
    platform. Collapsing whitespace on BOTH sides of the comparison
    (after `_unescape_ps_octal` has already turned any macOS-style octal
    escape back into a real newline, so this collapses that too) fixes
    both renderings with one normalization instead of chasing each
    platform's own `ps` quirk individually.
    """
    return " ".join(text.split())


_PS_OCTAL_ESCAPE_RE = re.compile(r"\\([0-3][0-7]{2})")


def _unescape_ps_octal(text: str) -> str:
    """Reverse BSD `ps`'s own octal-escaping of non-printable bytes in its
    COMMAND column (the `strvis`-family encoding it uses internally --
    confirmed live, not just documented, since `man ps` on macOS doesn't
    spell it out explicitly): a real embedded newline (0x0A) inside a
    multi-line shell invocation (e.g. `python3 -c "line1\\nline2"`, a
    real Python multi-line -c script) is rendered by `ps` as the four
    literal ASCII characters `\\012`, not an actual newline byte.

    Without reversing this, `_kill_by_command_text()`'s substring match
    against codex's own reported `command` text (which DOES contain real
    newline bytes -- parsed from JSON, where a `\\n` escape decodes to
    the real byte) can never succeed for ANY multi-line command,
    independent of _is_bare_generic_shell()'s own fix above. Confirmed
    live, 2026-08-23: a 90-second multi-line `python3 -c` write loop
    (hashing a counter with SHA-256 in a loop, ~2.9GB written) survived
    `force_kill()` completely untouched -- orphaned to launchd (ppid 1),
    still running its own independent timer well after kill_phrase
    matched and the whole ConvoBox session had already exited. The
    short, single-line `sleep 90` case the 2026-08-19 length-guard fix
    covers has no embedded control characters, so `ps` renders it
    verbatim and this bug never surfaced there.
    """
    return _PS_OCTAL_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 8)), text)


_GENERIC_SHELL_NAMES = {"sh", "zsh", "bash", "ksh", "csh", "tcsh", "dash", "fish"}


def _is_bare_generic_shell(command_line: str) -> bool:
    """True only for a ps command line that is NOTHING but a shell's
    own bare name (e.g. "zsh", "-zsh" for a login shell, "/bin/sh"),
    with no arguments at all. See _kill_by_command_text()'s own comment
    for why this specific, narrow case -- and only this case -- needs
    excluding.
    """
    parts = command_line.split()
    if len(parts) != 1:
        return False
    return os.path.basename(parts[0]).lstrip("-") in _GENERIC_SHELL_NAMES


def _kill_by_command_text(command: str) -> list[int]:
    """Best-effort SIGKILL of every live process whose (quote-stripped)
    full command line appears within `command`'s own quote-stripped
    text (or vice versa), AND every live descendant of each such
    process. See CodexAdapter.force_kill()'s own comment for why this
    exists and its known fragility. Uses `ps -eo pid,ppid,command` + a
    literal Python substring check -- deliberately NOT `pgrep -f`
    (which interprets its pattern as POSIX extended regex; `command`
    here is untrusted-ish real shell text that can contain regex
    metacharacters, so a literal check avoids both false negatives from
    a broken pattern and the smaller but real risk of a regex matching
    more broadly than the literal text did).

    Descendant-killing is NOT optional/defensive -- it's required for
    correctness. Confirmed live, 2026-08-15: a multi-statement shell
    script (`sh -c 'echo marker; sleep 90'`) reports command text
    (matched here) for the `sh -c` WRAPPER process, but `sleep 90`
    itself runs as a SEPARATE forked child (only a script's tail
    command can be exec'd in-place; anything before a `;` forks) --
    killing only the matched wrapper left `sleep 90` alive, reparented
    to pid 1, invisible to a survivor check that (reasonably) only
    re-searches for the ORIGINAL marker text, which never appears in
    the orphaned child's own command line. This was caught by manually
    inspecting leftover processes after what this fallback's own tests
    had reported as "clean" -- the automated survivor check's blind
    spot, not something the test runs themselves revealed.

    POSIX-only by construction, not just by validation: `ps -eo
    pid,ppid,command` and `signal.SIGKILL` are both POSIX-specific --
    `signal.SIGKILL` does not exist as an attribute on Windows'
    `signal` module at all (an `AttributeError`, not a graceful
    failure, if this were ever reached there), and `ps` isn't a
    standard Windows command either. Callers MUST check
    `sys.platform != "win32"` before calling this -- it does not guard
    itself, so it stays a plain, easily-testable function rather than
    needing its own internal no-op-elsewhere branch.

    macOS has been LIVE-VALIDATED (20/20, 2026-08-15/18/23 field notes).
    Linux is now ALSO confirmed, not just expected -- live-tested
    2026-08-25 (tests/test_real_process_tree_kill.py, real spawned
    process trees, no mocked `ps`) -- but that same pass found two real
    Linux-specific gaps in THIS function, both fixed: (1) Linux's
    `procps` `ps` renders an embedded newline as a plain space, not
    macOS's `\012` octal escape, which `_unescape_ps_octal` alone didn't
    reverse -- fixed by `_normalize_whitespace` collapsing whitespace on
    both sides of the comparison, handling either rendering; (2) this
    function's own `ps` call had no `COLUMNS` override, so its COMMAND
    column truncated to terminal width whenever `ps`'s stdout wasn't a
    wide/real tty (true for this call every time, since
    `capture_output=True` always pipes it) -- the same real process,
    same code, matched in one calling context and was silently missed in
    another, purely from ambient terminal-width detection this function
    never controlled. Both are now covered by `tests/
    test_real_process_tree_kill.py` and `tests/test_codex_adapter.py`'s
    own regression tests, not just this docstring's word.
    """
    stripped_command = _normalize_whitespace(_strip_shell_quotes(command))
    try:
        # Fixed argv list (shell=True deliberately NOT used, so there's no
        # shell-injection surface); "ps" resolves via $PATH rather than an
        # absolute path because this needs to work identically across
        # macOS's /bin/ps and Linux distros' varying ps locations (see
        # this function's own docstring on Linux support) -- a hardcoded
        # /bin/ps would silently break there. A PATH-hijacked "ps" binary
        # implies the attacker already has arbitrary local code
        # execution, at which point this specific call is not the weak
        # link.
        #
        # env=... with a huge COLUMNS is load-bearing, not decorative:
        # live-verified 2026-08-25 while building this project's process-
        # kill test matrix. `ps`'s COMMAND column truncates to terminal
        # width whenever its own stdout isn't a wide/real tty -- true here
        # regardless of platform, since capture_output=True always pipes
        # ps's stdout. Confirmed directly: the SAME real spawned process,
        # same code, same machine, matched and was killed correctly when
        # this call's calling process had a wide terminal context, but
        # failed to match at all (silently -- this whole function just
        # returns an empty list, "nothing to kill") when run from a
        # context where terminal-width detection came back small (e.g.
        # under a test runner). ConvoBox itself has no control over what
        # terminal context it's launched from in practice (a real
        # terminal, a service manager, a headless script) -- COLUMNS is
        # the one thing THIS call can pin regardless, so the safety-
        # critical kill fallback doesn't silently depend on ambient
        # terminal state it never asked for.
        out = subprocess.run(  # nosec B603 B607
            ["ps", "-eo", "pid,ppid,command"],
            capture_output=True, text=True, timeout=5, check=False,
            env={**os.environ, "COLUMNS": "10000"},
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children_by_ppid: dict[int, list[int]] = {}
    matched_pids: set[int] = set()
    for line in out.splitlines()[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        pid_str, _, rest = line.partition(" ")
        ppid_str, _, cmd_rest = rest.strip().partition(" ")
        try:
            pid, ppid = int(pid_str), int(ppid_str)
        except ValueError:
            continue
        children_by_ppid.setdefault(ppid, []).append(pid)
        # Unescape BEFORE quote-stripping: ps's octal escapes for a real
        # embedded newline/tab render as literal backslash-digit ASCII
        # text (e.g. "\012"), which _strip_shell_quotes' quote-character
        # removal doesn't touch either way, but must be reversed before
        # the substring comparison below -- see _unescape_ps_octal()'s
        # own docstring for why (a multi-line commandExecution's reported
        # text, parsed from JSON, contains REAL newline bytes).
        stripped_line_command = _normalize_whitespace(
            _strip_shell_quotes(_unescape_ps_octal(cmd_rest.strip()))
        )
        # A guard against one specific coincidental false-positive: a
        # BARE generic shell name with no arguments (e.g. ps reporting
        # just "zsh", nothing else). codex's own reported invocation
        # text always wraps the real command in a shell (e.g. "/bin/zsh
        # -lc ..."), so a bare "zsh" is a substring of `stripped_command`
        # on essentially every call -- without this guard, every
        # unrelated live bare shell process on the box would match and
        # get killed. A blanket minimum-length guard (any line under 15
        # chars) was tried first, but it silently excluded real,
        # legitimate short commands too -- "sleep 90" (8 chars) went
        # unprotected in live voice testing. See docs/field-notes/
        # 2026-08-18-kill-phrase-live-voice-test-finds-two-real-gaps.md.
        # Only the specific bare-shell-name case needs excluding, not
        # "any short line."
        if _is_bare_generic_shell(stripped_line_command):
            continue
        if (
            stripped_line_command not in stripped_command
            and stripped_command not in stripped_line_command
        ):
            continue
        matched_pids.add(pid)

    # Expand to every live descendant of each matched pid (BFS over the
    # ppid map) -- see docstring for why this is required, not optional.
    to_kill: set[int] = set()
    frontier = list(matched_pids)
    while frontier:
        pid = frontier.pop()
        if pid in to_kill:
            continue
        to_kill.add(pid)
        frontier.extend(children_by_ppid.get(pid, []))

    killed: list[int] = []
    for pid in to_kill:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
    return killed


class CodexAdapter(BackendAdapter):
    def __init__(
        self,
        command: Sequence[str] | None = None,
        permission_mode: str = "plan",
        working_dir: str | None = None,
    ) -> None:
        self._command = _resolve_command(command)
        # Injected before the `app-server` subcommand at spawn -- see
        # _permission_config_args and _ensure_thread's create_subprocess_exec.
        self._permission_args = _permission_config_args(permission_mode)
        # Where the spawned agent reads/writes files. None -> inherit
        # ConvoBox's cwd (which may be its own source repo); an explicit
        # path isolates edits to a chosen workspace. See BackendConfig.
        self._working_dir = working_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()  # guards spawn + handshake (see _ensure_thread)
        self._reader_task: asyncio.Task[None] | None = None
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._busy = False
        # Best-effort, fragile fallback for force_kill() -- see that
        # method's own comment for why. Set on every commandExecution
        # item/started, cleared on its matching item/completed (so a
        # force_kill() called after the tool call already finished
        # normally has nothing stale to act on -- force_kill() also only
        # ever uses this while self._busy is True, as a second guard
        # against acting on a leftover value from an earlier, unrelated
        # turn).
        self._last_command_text: str | None = None
        self._request_seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[BackendEvent | object] = asyncio.Queue()
        self._interactive_approvals = False
        self._pending_approval: tuple[int, str] | None = None
        # Windows-only observation Job Object (see _windows_job_object.py --
        # OBSERVATION ONLY, never used to kill). Created lazily on first
        # spawn, reused across respawns within this adapter's lifetime so a
        # detached descendant of an earlier, already-dead codex process
        # stays visible even after a respawn -- not recreated per spawn.
        self._windows_job: int | None = None

    def set_interactive_approvals(self, enabled: bool) -> None:
        self._interactive_approvals = enabled

    async def resolve_pending_approval(self, approved: bool) -> bool:
        """Answer one operator-held current-protocol approval request.

        The app-server leaves the originating turn blocked until this JSON-RPC
        response arrives.  There is deliberately no auto-approve fallback:
        an unexpected/missing request is reported as ``False`` to the caller.

        The approve payload is ``{"decision": "accept"}``, NOT ``"approve"``
        -- bug found live, 2026-07-20 (UAT session: every voice-approved
        write was still rejected by Codex, e.g. "the temporary write probe
        was declined before it ran" immediately after ConvoBox logged
        "approved pending Codex approval"). `codex app-server
        generate-json-schema` (codex-cli 0.144.6) shows
        CommandExecutionApprovalDecision/FileChangeApprovalDecision have NO
        "approve" enum member at all -- only "accept"/"acceptForSession"/
        "decline"/"cancel" (plus amendment objects) -- so the old value was
        simply invalid and Codex treated it as a decline. The module
        docstring's live verification only ever covered the DECLINE path;
        the approve path was schema-read but never actually tested, which
        is exactly how this went unnoticed.
        """
        pending = self._pending_approval
        if pending is None:
            return False
        request_id, method = pending
        deny_payload = _APPROVAL_DENY_PAYLOADS[method]
        payload = {"decision": "accept"} if approved else deny_payload
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
                    cwd=self._working_dir,
                )
                self._thread_id = None
                self._active_turn_id = None
                self._busy = False
                self._pending = {}
                self._pending_approval = None
                self._observe_via_windows_job_object()
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

    def _observe_via_windows_job_object(self) -> None:
        """Best-effort: assign the freshly-spawned codex process to this
        adapter's observation Job Object (created lazily, reused across
        respawns). A no-op, logged-and-swallowed failure on any non-Windows
        platform or if the Win32 calls themselves fail -- this must never
        prevent codex from actually starting, since it's an observability
        enhancement, not a requirement of the adapter working at all.
        """
        if sys.platform != "win32" or self._proc is None or self._proc.pid is None:
            return
        if self._windows_job is None:
            self._windows_job = _windows_job_object.create_job()
        if self._windows_job is not None:
            _windows_job_object.assign_to_job(self._windows_job, self._proc.pid)

    def background_jobs(self) -> Sequence[BackgroundJob]:
        """Codex's own protocol has no background-job concept to observe
        (see docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md's API audit) -- the
        only visibility here is OS-level, via the Windows Job Object above.
        Every PID it reports besides codex's own top-level process is
        UNKNOWN state and "os-scan" source: this module can confirm a
        process is alive, not what it's doing or whether it will ever
        finish, which is the honest answer for something codex never told
        ConvoBox about in the first place.
        """
        if sys.platform != "win32" or self._windows_job is None:
            return ()
        own_pid = self._proc.pid if self._proc is not None else None
        now = time.time()
        return tuple(
            BackgroundJob(
                id=str(pid),
                state=JobState.UNKNOWN,
                label=f"process {pid} (spawned by codex, not otherwise identified)",
                pid=pid,
                observed_at=now,
                source="os-scan",
            )
            for pid in _windows_job_object.enumerate_job_pids(self._windows_job)
            if pid != own_pid
        )

    async def aclose(self) -> None:
        # Terminate the codex app-server subprocess and await it here, while
        # the loop is alive, so its pipe transports close cleanly instead of
        # being GC'd after the loop closes (which prints "Event loop is
        # closed" / "unclosed transport" tracebacks on Windows). Idempotent.
        await self._terminate_and_kill_process()
        # Releases OUR reference to the observation Job Object -- does NOT
        # terminate any member process (see _windows_job_object.py's module
        # docstring: this module never sets JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE).
        # A detached descendant that's still alive stays alive; ConvoBox
        # simply stops being able to observe it after this point. Reset to
        # None (not just closed) so the rare case of this adapter instance
        # being reused after aclose() creates a fresh job on next spawn
        # instead of silently failing every assign_to_job() against an
        # already-closed handle.
        if self._windows_job is not None:
            _windows_job_object.close_job(self._windows_job)
            self._windows_job = None

    async def force_kill(self) -> None:
        # Unlike aclose(), does NOT reset self._thread_id -- see
        # BackendAdapter.force_kill()'s own docstring on why that's left
        # for a future caller to decide (Phase 2, not built here). Sharing
        # _terminate_and_kill_process() with aclose() is safe regardless:
        # that helper only ever touches self._proc/self._reader_task.
        #
        # Terminating/killing self._proc (below) only ever reaches codex's
        # own app-server process -- confirmed live, 2026-08-15 field notes
        # (docs/field-notes/2026-08-15-force-kill-macos-fix-attempts-
        # killpg-and-processid-both-fail.md): on macOS, codex's real
        # spawned shell child is its own process-group leader
        # (os.getpgid(child) == child's own pid, independent of the
        # app-server's group and of sandboxing), so no signal ConvoBox
        # sends to self._proc's group can ever reach it. codex's own
        # protocol-reported `processId` for a commandExecution item was
        # also tested and does not correspond to any live process by the
        # time it would matter (see the same field note).
        #
        # This fallback is the last resort those notes left untested:
        # best-effort match the REAL child by its own command line (which
        # codex DOES report accurately, unlike the processId field) via
        # `ps`, and kill whatever matches directly. Fragile by
        # construction -- literal substring matching against `ps` output
        # can, in principle, match an unrelated process that happens to
        # share the same command text, and a command already reaped
        # between item/started and this call simply won't be found (not a
        # failure, just nothing left to do). Gated on self._busy: only
        # attempted while a turn is believed to still be in flight, to
        # reduce (not eliminate) the chance of acting on a stale command
        # string from an earlier, already-finished turn. Validated live:
        # 15/15 clean across two real scenarios (shell_sleep,
        # file_write_progressive) where the normal path alone was 0/15 --
        # see docs/field-notes/2026-08-15-force-kill-pgrep-fallback-*.md.
        #
        # POSIX-only, not Windows (see _kill_by_command_text()'s own
        # docstring): `ps`/`signal.SIGKILL` don't exist/work on Windows,
        # so this is unconditionally excluded there rather than left to
        # fail loudly (an uncaught AttributeError on signal.SIGKILL) or
        # silently on a platform that was never tested. Linux is
        # included on the strength of the same POSIX mechanisms macOS
        # was validated against, NOT because it has been independently
        # confirmed live -- see the docstring's own caveat.
        command, was_busy = self._last_command_text, self._busy
        await self._terminate_and_kill_process()
        if was_busy and command and sys.platform != "win32":
            _kill_by_command_text(command)

    async def _terminate_and_kill_process(self) -> None:
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
        except TimeoutError:
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
                line = await readline_with_stall_diagnostic(
                    proc.stdout, proc, "codex app-server _read_loop",
                    busy=lambda: self._busy,
                )
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
                    if item_type == "commandExecution":
                        command = item.get("command")
                        if isinstance(command, str) and command:
                            self._last_command_text = command
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
                    if item_type == "commandExecution":
                        self._last_command_text = None
                    self._events.put_nowait(
                        BackendEvent(
                            type=BackendEventType.TOOL_RESULT,
                            tool_output=json.dumps(
                                item.get("aggregatedOutput") or item.get("status") or ""
                            )[:500],
                        )
                    )
                    if item_type == "fileChange":
                        for artifact in self._resolve_artifact_writes(item):
                            self._events.put_nowait(artifact)
        # Everything else (reasoning items, deltas, token usage, MCP
        # startup, rate limits, ...) is deliberately unmapped -- same
        # narrow-on-purpose policy as the other two adapters.

    def _resolve_artifact_writes(self, item: dict[str, Any]) -> list[BackendEvent]:
        """codex/ARTIFACT wiring, closing the gap docs/KNOWN-ISSUES.md
        flagged ("codex hasn't been looked at"). Schema confirmed via
        `codex app-server generate-json-schema` (codex-cli 0.146.1, not
        live-probed against a real running session -- see this method's
        own status note in the accompanying field note): a completed
        `FileChangeThreadItem` is `{type: "fileChange", id, status,
        changes: [{path, kind, diff}]}` -- every changed path's FINAL
        diff/status in one `item/completed` notification, unlike
        claude_code.py's Write/Edit tool_use, which only names a path
        and needs a separate tool_result to confirm success. So there's
        nothing to stage across two messages here -- just filter the
        completed changes down to renderable paths and resolve them.
        `status` is one of inProgress/completed/failed/declined per the
        schema; only "completed" changes actually landed on disk.
        """
        if item.get("status") != "completed":
            return []
        events: list[BackendEvent] = []
        for change in item.get("changes") or []:
            if not isinstance(change, dict):
                continue
            file_path = change.get("path")
            if not isinstance(file_path, str):
                continue
            if Path(file_path).suffix.lower() not in ARTIFACT_MEDIA_TYPES:
                continue
            artifact_path = self._resolve_artifact_path(file_path)
            if artifact_path is not None:
                events.append(
                    BackendEvent(type=BackendEventType.ARTIFACT, artifact_path=artifact_path)
                )
        return events

    def _resolve_artifact_path(self, file_path: str) -> str | None:
        """Same fencing as ClaudeCodeAdapter._resolve_artifact_path
        (src/convobox/adapters/claude_code.py): file_path -> a path
        relative to working_dir, the same base convobox.web.artifacts'
        serving route resolves against -- or None if working_dir isn't
        configured, or file_path doesn't actually resolve inside it.
        Independent, redundant fencing on top of that route's own check
        (defense in depth), duplicated per-adapter rather than shared
        since each adapter's own event shape feeding it differs enough
        that a shared helper would need its own indirection layer for a
        ~10-line method."""
        if self._working_dir is None:
            return None
        base = Path(self._working_dir).resolve()
        raw = Path(file_path)
        absolute = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
        try:
            return str(absolute.relative_to(base))
        except ValueError:
            return None


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
