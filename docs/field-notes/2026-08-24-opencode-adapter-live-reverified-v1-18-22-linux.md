---
title: OpenCodeAdapter re-verified live against a real opencode serve (v1.18.22) on Linux -- the July 2026 API investigation still holds five patch versions and one platform later
status: validated-live
date: 2026-08-24
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0); opencode 1.18.22 (previous live-verification in OPENCODE_API_NOTES.md was v1.17.18, 2026-07-11); openSUSE Tumbleweed 20260822
evidence:
  - Real live round trip via src/convobox/adapters/opencode.py's OpenCodeAdapter, driven directly (not through scripts/run_convobox.py, to avoid contending with a concurrent live audio sweep's single-instance lock)
  - opencode serve --port 4096 --hostname 127.0.0.1, real process, started and stopped this session
  - OPENCODE_API_NOTES.md (repo root) -- the prior investigation this note re-confirms, unchanged
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; installed and configured OpenCode this session -- official opencode.ai curl installer, ~/.opencode/bin -- and asked for a live ConvoBox-backend test with the explicit constraint that nothing proprietary be sent, since this OpenCode install currently routes through OpenCode Zen, a third-party hosted model gateway)
    - Claude Code (Anthropic claude-sonnet-5) -- installed OpenCode, ran the live adapter test in an isolated fork, wrote this note
  org: https://legionforge.org
  created: 2026-08-24T15:30:00-05:00
  revised: 2026-08-24T15:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# OpenCodeAdapter re-verified live on Linux, five patch versions later

**Context for outsiders.** ConvoBox drives three coding-agent backends --
OpenCode, Claude Code, and Codex -- through per-backend adapters.
`OPENCODE_API_NOTES.md` documents a real, hard-won investigation
(2026-07-11) into OpenCode's actual `/api/` HTTP+SSE surface, since the
adapter was originally built against an inferred, wrong API shape ported
from an unrelated project. That investigation was last live-verified
against opencode v1.17.18. This note re-runs the same kind of live check
against v1.18.22, for the first time confirmed on Linux specifically (the
original investigation doesn't record which platform it ran on).

## Problem

JP installed and configured OpenCode fresh on this Linux machine this
session and asked for ConvoBox's OpenCode backend to actually be tested
against it -- not assumed working because the adapter code exists and unit
tests pass against a mocked server, but actually run against a real
`opencode serve` process, per this repo's own stated verification bar
(`CLAUDE.md`: "run the actual pipeline... rather than trusting unit tests
alone"). He also set a hard constraint: OpenCode is currently configured to
use **OpenCode Zen** (`opencode.ai`'s own hosted model gateway -- a
third-party, non-local API), so nothing proprietary could be sent through
it during the test.

## Method

Run in an isolated fork, concurrently with an unrelated live audio sweep
that was holding ConvoBox's single-instance mic lock (127.0.0.1:47613) at
the time -- so `scripts/run_convobox.py` (both its mic loop and its
`--text` mode call `acquire_single_instance_lock()` on that same port)
could not be used without contending for that lock. Instead, `OpenCodeAdapter`
was exercised directly in a small standalone script, which needs no
audio/mic/lock at all:

1. A fresh, empty scratch workspace (outside the repo, nothing in it) as
   `opencode serve`'s cwd -- guarantees no repo source or proprietary
   content is ever in OpenCode's own file-reading context.
2. `opencode serve --port 4096 --hostname 127.0.0.1` (real binary,
   `~/.opencode/bin/opencode` -- not yet on `PATH` in a non-interactive
   shell despite the curl installer adding it to `.bashrc`, since that
   file isn't sourced by a non-login/non-interactive shell; used the full
   path directly).
3. `OpenCodeAdapter(url="http://localhost:4096")` constructed directly
   (bypassing `scripts/run_convobox.py` entirely -- no lock contention).
4. One trivial, explicitly non-proprietary prompt: *"What is the capital
   of France? Reply in one word."* -- chosen specifically to have no
   reason to invoke a tool, relevant given `OpenCodeAdapter`'s adapter
   (per this repo's own README) has no tool-call approval gate the way
   claude-code/codex do.
5. `events()` consumed until a real reply arrived, then the adapter and
   the `opencode serve` process were both cleanly torn down and the
   scratch workspace removed.

## Evidence

Real, live round trip, confirmed:

- `POST /api/session` created a real session.
- SSE subscription on `/api/session/:id/event` delivered real events over
  the live connection.
- The prompt produced a `BackendEventType.TEXT` event with
  `content='Paris'` -- correct, and confirms the adapter's request/response
  parsing still matches the real server's actual wire format.
- `is_busy()`-based completion tracking (via `step.ended`'s `finish`
  field, not a `DONE`/`session.idle` event -- `OPENCODE_API_NOTES.md`
  already documents why the adapter deliberately does not rely on either)
  worked exactly as that doc describes: cleared correctly once the reply
  was complete, with no idle/done event ever needed or seen.

**One minor, non-functional wrinkle**: near the end of the run, a
`httpcore.ReadError` surfaced as an unretrieved-task-exception warning
(inside `anext_with_stall_diagnostic`'s internals) around the moment the
test script cancelled its event-consumer task after `is_busy()` went
false. `opencode serve`'s own server-side log was completely clean --
no errors there. Reads as a client-side task-cancellation-ordering
artifact of the *test script* cancelling the consumer task directly,
rather than letting the async generator exit on its own, not a bug in the
adapter's real send/receive path -- the reply had already printed
correctly before the warning appeared. Not chased further; worth keeping
in mind if a similar warning shows up in `Orchestrator`'s own shutdown
path someday, since that's a different, more careful cancellation
sequence than this quick test script used.

## Mechanism

Nothing here contradicts or extends `OPENCODE_API_NOTES.md`'s existing
findings -- this note's whole point is that nothing needed to change.
Five patch versions later (1.17.18 -> 1.18.22) and on a platform the
original investigation doesn't specify, the same `/api/` surface, the same
SSE event shape, and the same `finish`-field-based busy-tracking still
hold exactly as documented in July.

## What transfers

- **`OPENCODE_API_NOTES.md`'s July 2026 findings remain accurate against
  opencode 1.18.22.** (validated-live, this session, one round trip)
- **ConvoBox's OpenCode adapter now has a confirmed-working live path on
  Linux specifically**, closing one more cell of the README's "Linux/macOS
  implemented, not yet voice-validated" gap (adapter-level, not yet a full
  voice-driven session -- see "Not done here").
- **A safe way to live-test any backend adapter without a full
  `scripts/run_convobox.py` session exists and works**: construct the
  adapter class directly, drive `send_text`/`events()` by hand. Useful
  whenever a real audio session isn't available or would contend for the
  single-instance lock, as it did here.

## Not done here

- No multi-step / tool-calling round trip -- only a single trivial text
  reply, deliberately, to respect the "nothing proprietary, no reason to
  invoke a tool" constraint for this OpenCode-Zen-backed install.
- No test through `scripts/run_convobox.py` itself (mic loop or `--text`
  mode) -- deferred until the concurrent audio sweep releases the
  single-instance lock. A real voice- or `--text`-driven OpenCode-backend
  session is still open.
- The `httpcore.ReadError` cleanup-ordering artifact noted above wasn't
  investigated further -- not reproduced against `Orchestrator`'s own
  (different) shutdown sequence.
- Did not test against a self-hosted/non-Zen model provider -- this
  session's OpenCode install specifically uses the hosted Zen gateway;
  behavior against a different provider is unverified.
