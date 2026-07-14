# Design: 0.3.0 — interaction, response tiering, approvals, and the TUI

> **Scope: the 0.3.0 release.** Decided by JP, 2026-07-13, consolidating four
> previously-separate threads (barge-in, response tiering, approvals, and a
> terminal UI) into one bundle, because they turned out to share one
> underlying primitive and render into the same surface. This doc is the
> authoritative scope + priority order for 0.3.0; it does not re-derive
> barge-in's own design (see [DESIGN-barge-in.md](DESIGN-barge-in.md) for the
> full grid/preset/trigger/backchannel design — referenced, not repeated,
> here) and it builds on research already catalogued in
> [CONVERSATION-DESIGN-REFERENCES.md](CONVERSATION-DESIGN-REFERENCES.md).

## Why these four things are one bundle, not four

Barge-in, "want more detail?", and "approve this action?" all look like
different features, but they're the same shape underneath: **ConvoBox
sometimes needs to enter a "pending special-listening" state, where the
next utterance is interpreted against a small fixed vocabulary instead of
routed as a normal command.**

| Feature | The special vocabulary |
|---|---|
| Barge-in | interrupt / not (sustained speech during playback) |
| Response tiering | continue / not (after a tiered response) |
| Approvals | approve / deny / discuss (after a gated action) |

Building this once, generalized, is the right foundation rather than three
ad-hoc state machines that each reinvent silence-timing and word-matching
slightly differently — and it's why this is one design pass, not three.

The fourth piece — a real terminal UI — isn't a separate feature either.
It's the **presentation surface** the other three render into: barge-in's
status, the always-visible full-detail text, and approval warnings all need
somewhere persistent to live that isn't scrolling log lines.

## The shared primitive: `PendingPrompt`

A small, generalized state that the orchestrator can be "in," analogous to
how `BargeInMonitor` already tracks sustained speech during playback:

- **What it's waiting for**: a closed vocabulary of intents (`interrupt`,
  `continue`, `more_detail`, `approve`, `deny`, `discuss`), not open text.
- **Timeout behavior**: each prompt type declares its own timeout-implies
  behavior (barge-in: no timeout, it's edge-triggered; response tiering:
  silence for 1-4s implies "no, don't elaborate"; approvals: **no
  auto-timeout-implies-approve, ever** — silence on an approval prompt must
  never be treated as consent, only as "still waiting" or an explicit
  timeout-implies-**decline**).
- **Detector reuse, NOT detector sharing**: each vocabulary gets its own
  detector instance, because the *safety bar* differs per vocabulary.
  `ContinueDetector` (new, response tiering) is deliberately lightweight —
  a bare "yes" is fine, since misrecognition just means hearing more detail
  you didn't need. `ConfirmwordDetector` (existing, PR #29) is deliberately
  strict — a bare "yes" is banned by design, since misrecognition there
  could approve something destructive. **Never let a `PendingPrompt` for an
  approval reuse the low-stakes continue/barge-in vocabulary matching.**
  This is the single most important safety invariant in this doc.

## Phase 1 — TUI skeleton + barge-in

**Barge-in** ships per the existing design: the two-axis grid, named
presets (`conversational` default), the `speech`/`push-word` trigger split,
`WakewordDetector`, and backchannel filtering. See
[DESIGN-barge-in.md](DESIGN-barge-in.md) for the full spec — nothing here
changes it, this phase just implements it on top of `PendingPrompt` instead
of a bespoke `BargeInMonitor`-only mechanism.

**The TUI** (new) is a full-screen terminal surface, same rendering
discipline as `scripts/settings_tui.py` (terminal-size-aware, ANSI, no
special fonts, unit-tested layout) and `scripts/voice_tui.py` (live,
continuously redrawn). Scope for this phase — deliberately minimal, built
to be *extended* by phases 2-3, not rebuilt:

- a live transcript pane (what was heard, what's being said)
- a full-detail pane (see phase 2 — the untruncated response text)
- a status/warning area (barge-in state now; approvals in phase 3)

This is **not** the Settings TUI (config editing) — a separate, already-shipped
tool. This is the live *conversation* surface, run alongside
`run_convobox.py`, not instead of it. Open question: same process (a
second render thread/task inside `run_convobox.py`) or a separate process
reading a shared state file/socket? Lean toward same-process for phase 1
(simpler, matches the working-indicator/heartbeat pattern already in
`run_convobox.py`); revisit if that proves awkward.

**Rendering layer + live wiring shipped (2026-07-14).**
`src/convobox/tui/` — `state.py` (`ConversationTuiState`, `TranscriptTurn`,
pure dataclasses, no terminal I/O) and `render.py`
(`render_conversation_frame(state, width, height, now) -> list[str]`, pure
function, no stdout writes), split the exact way `settings_tui.py`
separates `render_modal()` (pure, tested) from `_draw_modal()` (resolves
the real terminal and writes). All three panes from the scope above exist
and are covered by 18 unit tests: transcript (chronological, scrolls to
most-recent-visible on overflow, ANSI-safe word wrapping that preserves
every word — verified against a real bug caught while building this: a
naive `len()`-based fit/truncate helper overcounted color-escape bytes as
visible text and truncated lines that actually fit; fixed to measure
visible length, ANSI codes included but not counted), full-detail pane
(paragraph breaks preserved, not flattened by a naive wrapper), and the
warning banner (phase 3 -- reserves zero space when unset, bordered
top/bottom with `!` so it can't be mistaken for an ordinary line once
set).

**Wiring** (`run_convobox.py --tui`): required a small prerequisite --
`Orchestrator` gained an optional `on_event` observer hook (the class
consumed backend events internally and only ever acted on TEXT by
speaking it; nothing exposed the stream to a caller wanting to *see* the
real response). With that hook, transcript turns and the full-detail pane
populate from real backend events; status (`listening`/`capturing`/
`speaking`/`working`/`paused`) is derived by the existing
working-watchdog's 1s poll rather than threaded through every call site
in the main loop -- a deliberate simplification, not laziness:
`transcriber.transcribe()` blocks the event loop synchronously today (no
`asyncio.to_thread` offload), so a render task can't redraw *during* that
decode regardless of whether status updates are poll- or event-driven;
polling is the lower-risk mechanism for a label that's inherently
"close enough," not frame-perfect, either way. Log output moves to a file
(`convobox-tui.log`) when `--tui` is active -- interleaving ordinary log
lines with the alt-screen redraw would garble the display, same reasoning
`voice_tui.py`/`settings_tui.py` already avoid the general logging module
entirely for.

**Live-verified against the real pipeline** (proper subprocess lifecycle
management, not a shell `timeout` -- that sends SIGTERM on Windows, which
Python doesn't map to a graceful `KeyboardInterrupt`, and produced a
misleading "no output" false alarm before this was caught): real config
load, real STT model check, real single-instance lock, real mic device
open, a real session against a running opencode server, correct
`LISTENING` status with accurate elapsed time, clean alt-screen
entry/exit, no traceback, log correctly redirected to file (stderr
empty). **Honest limitation**: this verifies startup/idle/shutdown, not
an actual spoken utterance flowing all the way to a rendered transcript
turn + response -- that needs a live mic UAT pass with a real voice,
which is queued, not done.

## Phase 2 — Response tiering

Implements the roadmap's already-decided "Spoken-response contract" (
`docs/ROADMAP.md`: *"User-settable response length target... per-response
routing: VERBALIZE vs DISPLAY (spoken summary + full text on screen)"*) —
this phase is the concrete design for that item, informed by the TUI now
existing:

- **Voice always gives the tiered/short version.** Not a per-response
  negotiation — a standing policy setting (tier A/B/C, user-configurable,
  home: the Settings TUI's existing Interaction section once it grows this
  field).
- **The TUI always shows the full, untruncated response**, live, in the
  full-detail pane from phase 1. A user with eyes on the screen never needs
  to ask for more — it's already there.
- **`ContinueDetector` is the eyes-free escape hatch**, not the primary
  mechanism: a user without the TUI open (or who just doesn't want to look)
  can say "tell me more" / "go on" / a bare "yes" after a tiered response,
  and ConvoBox speaks progressively more of the *already-in-hand* text — no
  backend round-trip, since the full response was already received.
- **v1 is pure client-side truncation** (first paragraph/sentence vs. full
  text) on the text ConvoBox already gets from every backend — no prompt
  injection, no backend-specific system-prompt hacking, works identically
  across opencode/Claude Code/Codex from day one. Semantically-compressed
  (LLM-generated) summaries are a v2 upgrade, not a prerequisite.
- **Silence-timeout-implies-no** (1-4s, configurable) reuses the same
  silence-timing machinery as barge-in's sustained-speech threshold —
  intentionally, per the shared-primitive section above.

**Core tiering logic shipped (2026-07-14), wiring not yet started.**
`src/convobox/response_tiering/tiering.py` — `split_tiers(text) ->
list[str]` (paragraph-boundary split, pure function) and
`ResponseTierState` (`start(full_text) -> str` returns tier 0 to actually
speak; `reveal_more() -> str | None` is the `ContinueDetector`-triggered
action, `None` once nothing's left; `has_more()` for callers that need to
know before deciding whether to even listen for "continue"). Same
"primitive first, review it, wire it later" pacing as the TUI work
(#54/#55/#56): no `Orchestrator`/`run_convobox.py` changes in this PR.

Picked **paragraph**, not sentence, as the v1 split unit (the design
above says "first paragraph/sentence," left open) — reliable sentence-
boundary detection has to handle abbreviations, decimals, ellipses, and
code fragments correctly, which is genuinely hard to get right; paragraph
splitting (blank line) is simple, robust, and already the boundary
`Orchestrator.strip_code_for_speech` collapses onto. It also degrades
correctly for the common case: most coding-agent replies are a single
paragraph with nothing to hide, so tier 0 *is* the whole response and
there's nothing to offer "more" of -- `has_more()` is `False`
immediately, no dangling "want more detail?" prompt for a two-sentence
answer. 13 new tests, including the reset-on-new-response semantics (an
old response's remaining tiers are moot once a new one exists, same
principle as the TUI's full-detail pane resetting per-turn).

**`Orchestrator` wiring shipped (2026-07-14), main-loop gate not yet
started.** `Orchestrator(..., tier_responses: bool = False)`: off by
default (zero behavior change for existing callers -- full text spoken
exactly as before). When on, each `TEXT` event tiers the
*already-stripped* speech text (not raw markdown -- `strip_code_for_speech`
already collapses 3+ newlines to exactly `"\n\n"`, so tiering after
stripping matches `split_tiers()`'s expected boundary, and avoids
splitting mid-code-block on a blank line stripping was about to remove
anyway) and speaks only tier 0. `has_more_to_reveal()` and
`speak_more()` (the `ContinueDetector`-triggered action) expose the rest.
The `on_event` observer hook (#55, so the TUI's full-detail pane) always
sees the full, untiered raw content -- fires before tiering, by design,
matching "the TUI always shows the full, untruncated response." 15 new
tests, including the reset-on-new-response and stripped-vs-raw-boundary
cases explicitly.

**Main-loop wiring + config field shipped (2026-07-14) -- Phase 2 complete
end to end.** `ContinuePromptGate` (`scripts/run_convobox.py`, same
pure-state-machine shape as `ListeningGate`/`BargeInMonitor`): the
watchdog's existing 1s poll starts the wait the instant a response
finishes speaking (`was_playing and not playing`) *and*
`orchestrator.has_more_to_reveal()` -- no wait started for a response
that already said everything -- and expires it silently after
`continue_timeout_s` (1-4s range, 2.5s default) with no reply. The main
loop checks `continue_gate.is_waiting` in the same early position as the
pause/resume gate: a `"continue"` reply calls `speak_more()`, a
`"decline"` reply just ends the wait, and critically an *unrelated* reply
(`"pass"`) is NOT dropped the way a paused session drops everything --
it falls through to normal processing, since the user just moved on to a
new topic rather than still answering. Continue/decline replies bypass
the overlap gate the same way a real barge-in does (they're *expected*
right after a response, not suspect echo); a "pass" reply does not
bypass it -- unknown whether it's genuine new speech or residual echo
until the normal gates check it.

`InteractionConfig` gained `tier_responses: bool = False` (off by
default, zero behavior change for existing `convobox.yaml` files) and
`continue_timeout_s: float = 2.5` (not yet live-UAT-tuned against a real
"did that feel laggy or naggy" pass -- flagged as an open question
below).

**Live-verified against the real pipeline**: drove `Orchestrator`
directly with `tier_responses=True` against a real running `opencode`
server, asked for a genuine two-paragraph response -- confirmed only
paragraph 1 was spoken first, `has_more_to_reveal()` correctly reported
`True`, and `speak_more()` correctly delivered paragraph 2. **Honest
limitation, same class as the TUI's**: this verifies the `Orchestrator`
tiering logic against a real backend, not the full watchdog-trigger +
main-loop `ContinuePromptGate` wiring through an actual spoken "continue"
-- `ContinuePromptGate`'s pure state-machine logic (10 tests) and the
full script's syntax/imports are verified, but saying "continue" into a
real microphone after a real tiered response needs a live-mic UAT pass,
which isn't possible in this environment.

## Phase 3 — Approvals

**Codex (built now — it has a real channel).** `codex.py`'s
`_APPROVAL_METHODS` already intercepts every approval-shaped JSON-RPC
request; today it hardcodes `decision: "decline"`. This phase replaces that
hardcode with a real `PendingPrompt(approve/deny/discuss)`:

- **Approve** — a dedicated `ConfirmwordDetector`-shaped approval word
  (never a common affirmation — see `ConfirmwordDetector`'s existing
  construction-time guard, PR #29, and `docs/ROADMAP.md`'s Safety-tiers
  sketch: *"NOT a common affirmation... so casual speech can never approve
  anything"*).
- **Deny** — an explicit word (or timeout — silence safely implies decline,
  never approve).
- **Discuss** — the interesting one: the user asks a question about the
  pending action instead of deciding. Needs the *same* approval request to
  still be answerable after the exchange — **confirmed live, 2026-07-14**:
  spawned a real `codex app-server`, captured a genuine pending
  `item/commandExecution/requestApproval` request and deliberately left it
  unanswered for 20s (simulating time spent on a voice exchange), sent a
  completely unrelated request on the *same* connection in the meantime
  (a second, independent `thread/start` — got a normal response, proving
  the pipe isn't blocked/serialized behind the pending approval), **then**
  answered the *original* request's id with `decline` — it resolved
  normally (`"exec command rejected by user"`, `turn/completed` with
  `status: "completed"`, no error). Codex's app-server does not time out
  or invalidate a pending approval across an intervening exchange, at
  least at this scale (one 20s delay, one interleaved request) — not
  tested for much longer delays or heavier interleaved traffic, but
  enough to unblock building "discuss" without a preservation workaround.
- Every decision is recorded + timestamped (per the roadmap sketch); crypto
  signing / audio-snippet retention stays a later option, not phase-3 scope.
- Rendered in the TUI as a loud, unmissable **WARNING** block — visible
  regardless of whether the user answers by voice or types in the TUI.

**Claude Code (investigate, don't rework).** Headless (`--print`) mode has
**no runtime-answerable channel at all** (confirmed live, see
`claude_code.py`'s module docstring and PR #37) — the TUI can *display*
"approval needed" but there is still nothing to tell the hung subprocess
"approved." Two tiers, explicitly separated:

1. **Cheap, in-scope for 0.3.0 — probed live, 2026-07-14, confirmed
   safe.** `--disallowedTools Bash Write Edit` makes those tools
   **genuinely unavailable** to the model, not gated-behind-approval: a
   real spawned `claude --print ... --disallowedTools Bash Write Edit`
   asked to run a shell command never attempted a `Bash` tool_use at
   all — it searched for one (`ToolSearch: 'select:Bash,PowerShell'` →
   `'No matching deferred tools found'`), then reported back in plain
   text that it has no shell tool available and stopped. Terminal
   `result` message: `is_error=False, subtype=success` — a clean,
   speakable turn, **not** the permission-gate's silent-forever hang
   (same class of bug `--permission-mode plan` already fixes, see
   `claude_code.py`'s docstring). Read-only tools (Read/Glob) were
   confirmed to keep working normally under the same flags in a
   companion probe.

   **Relationship to the shipped `--permission-mode plan` fix**: plan
   mode already avoids the hang for *any* gated tool by never executing
   writes/exec at all (blanket, zero-config). `--disallowedTools` is a
   different, more granular knob — name specific tools (or, per
   `claude --help`, specific command patterns like `"Bash(git *)"`) to
   remove entirely, rather than accepting plan mode's blanket
   research-only stance. Whether ConvoBox should expose this (a new
   config field, a default deny-list, or leave `command:` overrides as
   the escape hatch it already is) is a real feature-scoping decision,
   not a mechanical follow-up — deliberately not rushed into this probe;
   tracked as an open question below.
2. **Out of scope for 0.3.0**: switching Claude Code off headless mode
   entirely to drive its interactive TTY via injected keystrokes (the
   README's own named fallback: *"a PTY/keystroke fallback where nothing
   better exists"*). Not proven to be the last resort yet given (1) is
   unexplored, and it trades a clean, documented protocol for something
   adjacent to the terminal-scraping anti-pattern the README explicitly
   says to avoid except as a last resort. Track as its own future
   initiative if (1) proves insufficient.

## Phase 4 — Critical-output polish

Not a separate deliverable — the WARNING/critical-moment rendering in the
TUI's status area matures as phases 1-3 land (barge-in state, response-tier
indicator, approval prompts). Revisit at the end of 0.3.0 whether anything
here needs to be its own pass.

## Explicitly out of scope for 0.3.0

- Wake-word *engine* (openWakeWord etc.) for a low-power idle/asleep mode —
  `WakewordDetector` (transcript-match, phase 1) is in scope; a dedicated
  spotter model is not (per `docs/ROADMAP.md`'s existing post-0.5 deferral).
- The Claude Code PTY/interactive-mode rework (see phase 3).
- Voice Activity Projection / semantic endpointing (still the deferred
  upgrade path noted in `CONVERSATION-DESIGN-REFERENCES.md`; `openlive`'s
  "Smart-Turn" is further evidence this is real and shippable, but it's a
  bigger lift than 0.3.0's scope).
- Crypto-signed / audio-retained approval records (roadmap sketch, later).
- Full Claude/Codex backend "support" claims beyond what's already
  voice-validated — that's the 0.4.0 line, not this one.

## Open questions to resolve during implementation

- TUI process model: in-process render task vs. separate process (see
  phase 1).
- Exact silence-timeout durations for response-tiering (1-4s range given;
  needs live-UAT tuning, same as barge-in's `barge_in_min_speech_ms`).
- ~~Whether Codex's app-server preserves a pending approval request across a
  "discuss" exchange~~ — **confirmed yes**, see phase 2 above. "Discuss" is
  unblocked to build.
- Whether to actually wire `--disallowedTools` into ConvoBox's Claude Code
  adapter (confirmed safe, see phase 3 above) — and if so, what the default
  deny-list should be and whether it's user-configurable. Not started.
