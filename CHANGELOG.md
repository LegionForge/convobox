# Changelog

All notable changes to ConvoBox are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0, so
minor versions carry feature and behavior changes.

## [Unreleased]

### Fixed
- **macOS: `safeword.kill_phrase`/`force_kill()` now actually reaches a
  spawned codex tool-call child, not just the top-level app-server
  process.** `codex` was 0/10 on macOS at `0.3.1`'s release (disclosed
  in `docs/KNOWN-ISSUES.md`) -- the real spawned shell child is its own
  process-group leader regardless of sandboxing, so no signal to the
  app-server's process group could ever reach it, and `os.killpg()`
  (the previously-disclosed "candidate fix") was tested live and
  confirmed to fail for the same reason. Fixed with a `ps`-based
  command-line-matching fallback plus recursive descendant-kill (a
  multi-statement shell script forks its later commands as separate
  child processes, which the naive matched-PID-only kill orphaned
  otherwise). Re-verified live 20/20 clean against real spawned
  processes on current main. `claude-code` was already reliable
  (10/10) and is unaffected.

### Known issues
- **Windows: `kill_phrase` does not reach a process the agent
  deliberately detached.** Found 2026-08-19 in live voice UAT against a
  `codex` backend; not yet tested against `claude-code` or `opencode`.
  The kill reliably ends the ConvoBox session and takes down whatever
  the backend still has structurally attached, but a child the agent
  backgrounds on purpose (e.g. via PowerShell's `Start-Process`)
  survives indefinitely -- reproduced live 5/5, including one case where
  the detachment confused codex's own PID tracking badly enough that it
  launched a duplicate copy of the same background process. The
  `ps`-based fallback that closes the analogous macOS gap does not apply
  here (`signal.SIGKILL` doesn't exist on Windows). **An automated
  harness driving the identical scenario has NOT reproduced this (8/8
  passed)**, so the automated suite should not be treated as a stand-in
  for live verification of this gap. See `docs/KNOWN-ISSUES.md`'s
  force-kill entry and `docs/field-notes/2026-08-19-kill-phrase-windows-
  orphaned-descendant-survives-force-kill.md`.

## [0.3.1] — 2026-08-17

Patch release: 119 PRs since `0.3.0`. First cycle with live macOS
hardware coverage alongside Windows (Apple Silicon Mac mini, AIRHUG 28
USB mic) -- full evidence in `docs/field-notes/` and summarized in
`docs/STATUS.md`. No config schema breaks -- every existing
`convobox.yaml` keeps behaving exactly as before unless it opts into a
new setting below.

### Added
- **Pause/resume acknowledgment tone** (`interaction.pause_resume_ack`,
  `src/convobox/audio/ack_tones.py`). Resolves DESIGN-barge-in.md's [P8]
  open question. `none` (default, silent, matches every prior release) or
  `tone`: a short synthesized 3-note earcon, A-major triad A4/C#5/E5 at
  150ms/note (tightened from 300ms/note after live UAT found the first
  pass too slow), ascending on resume and descending on pause -- no
  external audio asset, generated on the fly. Pickable (not free-text) in
  both the TUI settings editor and the web UI's Settings panel,
  interaction section. A `file` (user-supplied sound) option is
  intentionally deferred and not offered anywhere yet. (#192)
- **Web UI: paused status shows which word resumes listening** -- the
  activity ribbon now reads `paused (say "Athena" to resume)` instead of
  just `paused`. (#170)
- **`stt.hotwords`** -- faster-whisper's own prompt-bias, to fight the
  recurring short-resume-word ("Athena") mis-transcription pattern.
  Live-UAT'd with STT decode randomness controlled for (`stt.temperature`
  pinned); note microphone hardware swung resume-success rate far more
  than hotwords did in the same testing, worth remembering before
  crediting any future STT-config change without also controlling for
  mic. (#204)
- **`stt.compute_type` is now a picker** with real tradeoff hints instead
  of free text. (#197)
- **Async STT model download** with a spinner and elapsed-time feedback
  in the settings TUI, instead of blocking silently. (#196)
- **Two new default safewords**, `"abort abort abort"` and `"halt halt
  halt"`, alongside the existing `"stop stop stop"`. (#251)
- **Web UI: text entry box**, sent through the same path as `--text`
  mode. (#202)
- **Web UI: Stop button** that mirrors the spoken safeword hard-stop.
  (#209)
- **Web UI: `tool_call`/`tool_result` events render collapsed**, click to
  expand. (#201)
- **Web UI: drag-and-drop file upload**, written into
  `backend.working_dir`. (#246)
- **Web UI: Artifact Chooser tab strip** plus a filtered
  working-directory file browser. (#261)
- **Web UI: syntax-highlighted code in the artifact pane**, plus an
  "Open in editor" action. (#228)
- **Web UI: accessibility + feedback pass** on the ribbon, composer, and
  settings chrome, including a pause/resume icon with screen-reader-safe
  labeling and live-reloading display settings with a restart-required
  indicator. (#227, #229, #233)
- **`safeword.kill_phrase`** (opt-in, unset by default) -- a genuine
  OS-level `terminate()`/`kill()` of the backend process, no RPC
  round-trip, for when the polite `send_hard_stop()` path is itself
  wedged (it rides the same stdin/stdout pipe a frozen backend can't
  answer). Must be one of `hard_stop_phrases`; JP's own config sets it
  to `"eject eject eject"`. Live-verified through the real mic pipeline
  on Windows/codex during an actual freeze, not a staged one --
  `docs/field-notes/2026-08-15-kill-phrase-live-verified-during-a-
  genuine-freeze-resume-word-stt-unreliable.md`. **macOS gap found the
  same evening: unreliable for `codex` there** (Apple Seatbelt sandboxing
  reparents the real child process to `launchd` before the kill signal
  can reach it, and macOS signals don't cascade to children the way
  Windows' `TerminateProcess()` does) -- `claude-code` stays reliable on
  both platforms. See `docs/KNOWN-ISSUES.md`'s force-kill entry before
  relying on this on macOS with a codex backend. (#277)
- **Web UI: agent-initiated artifact-pane tools.** The backend LLM gets
  two new tools over a small local MCP server ConvoBox now hosts
  alongside its existing FastAPI web UI (`src/convobox/web/mcp_server.py`,
  bearer-token-authenticated, loopback-only): `show_document(path)` to
  push/refocus a specific file in the artifact pane, and
  `get_shown_artifact()` to answer "what's currently showing?" grounded
  in real UI state (closes #280) rather than the model's own guess.
  Live-verified end to end against a real `claude` CLI and a real
  browser, including the edge case where the user manually closes the
  pane (which would defeat a naive "last broadcast" implementation).
  **Known limitation:** `backend.permission_mode: plan` (the default)
  reliably blocks both tools headless, since the model's own
  `ExitPlanMode` approval mechanism doesn't work in `--print` mode --
  see `docs/ARTIFACT-PANE-SCOPE.md`. (#283, #285)
- **Web UI: a distinct "session ended" status** for when Quit or the
  kill phrase ends the whole ConvoBox process, instead of the composer
  and buttons just spinning in an endless "reconnecting…" state
  indistinguishable from an ordinary dropped connection. (#288)

### Changed
- **Safeword folded into the Interaction tab**, both the Settings TUI and
  the web UI -- display grouping only, `config.safeword.hard_stop_phrases`
  and every real reference to it (`SafewordDetector`, incident capture)
  are unchanged. (#193)
- **Interrupt preset descriptions rewritten** with a plain-language
  sentence and a concrete "you say X, it does Y" example per preset,
  replacing terse internal jargon -- one shared `FieldSpec.help_text`
  covers both the TUI and web UI. (#183)
- **TUI heartbeat and web status line now show what the backend is
  doing**, not just that it's busy -- `working (thinking)` / `working
  (<tool name>)` instead of a bare `working`. (#190, #205)
- **Settings TUI's input-line editor shows the end of a long value**
  instead of the start when opened. (#253)
- **Config backups now go into `.convobox-backups/`**, not scattered
  across the repo root. (#268)
- **Barge-in interrupt marker reworded** from "(I interrupted your spoken
  response midway)" to "[User interrupted AI response]", shown in the
  transcript and forwarded to the backend as conversational context.
  (#178)

### Fixed
- **Pause/hard-stop could let a stale in-flight turn's response play
  1-10+ seconds late.** `send_hard_stop()` only reset the local busy
  counter -- it never stopped the event-consumption task from reading and
  speaking a trailing response from the turn that was just aborted. Fixed
  at both call sites (the pause handler and the safeword's own hard-stop
  branch) by also cancelling event consumption, not just the speak task.
  Live-verified across a 24-minute re-test, zero leaks. (#191)
- **Barge-in false-positive tag.** Utterances spoken in the gap *after* a
  response finished (not during it) were incorrectly tagged as
  interrupting it -- `EchoAwarePlayer.audible` was only ever reset at the
  start of the *next* response, never on the current one's own natural
  completion. (#174)
- **Barge-in interrupts landing near the end of playback could be dropped
  entirely** instead of registering -- an interruption starting ~1s
  before natural completion never had enough runway to cross the
  sustained-speech threshold, so it fell through to the overlap gate and
  was silently discarded as presumed echo. (#179)
- **TTS synthesis/playback failures are now surfaced, not silently
  swallowed** -- a fire-and-forget speak task had no exception handling
  anywhere in its chain, so a failure (e.g. Kokoro's ~510-phoneme hard
  limit) previously vanished with no log, no error, no indication
  anything went wrong; now logged and shown as a real error in both the
  TUI and web UI. (#175)
- **`--tui` now shows ERROR events in the transcript**, not just the log
  and web UI, closing the gap #175's fix left on that one surface. (#185)
- **`.md` file writes now open the artifact pane** -- the extension was
  simply missing from the content-type allowlist, rejected before an
  artifact event was ever staged. (#176)
- **Backend reconnect now backs off exponentially** instead of retrying
  every ~1s forever against a misconfigured or unreachable
  `backend.url` -- first retry stays fast (preserving quick recovery from
  a genuine transient hiccup), consecutive failures double the wait up to
  30s, and it resets to fast-retry the instant a real event arrives.
  (#177)
- **Clean shutdown (no noisy traceback) when a second instance's mic lock
  is correctly refused** -- the refusal itself was already correct, it
  just didn't tear down the adapter/web server cleanly on the way out.
  (#173)
- **Web UI Quit button and Ctrl+C now actually stop the process while
  `--web` is active** -- previously they only stopped the embedded web
  server; the mic loop and backend adapter kept running underneath.
  Root cause: uvicorn installs its own OS signal handler for as long as
  it runs, so the existing signal-based shutdown path never fired. (#168)
- **A real crash in `run_convobox.py --text` mode**: a `NameError` on the
  very first backend event, from a closure over a variable only assigned
  in the mic-loop setup path. Live-verified against both backends after
  the fix. (#256)
- **VAD segmenter could go silent indefinitely.** With
  `vad.max_utterance_s` unset, `UtteranceSegmenter` could lock up
  permanently -- mic capture and AEC stayed alive but zero utterances
  ever completed, no log output. Setting the cap stopped the permanent
  lockup but exposed a second, subtler gap: a forced-cap run that never
  accumulated `min_speech_ms` of confidently-classified speech was
  silently discarded, indistinguishable from genuine silence. Fixed with
  a new `UtteranceSegmenter.discarded_forced_runs` counter and a
  `_working_watchdog` heartbeat `WARNING` on increase. (#204)
- **`Orchestrator.hard_stop()` now reports whether a turn was actually
  busy when it fired** -- the web/voice pause paths use that to stop
  implying a tool call fully stopped when it may still be finishing in
  the background. (#255)
- **`hard_stop()` now cancels a pending approval-gate wait**, not just
  the speak/event-consumption tasks. (#240)
- **Web UI: a web-triggered pause/resume now syncs to the TUI's
  transcript pane** -- previously only the voice path touched
  `ConversationTuiState`, so a session resumed via the web Stop/Resume
  button could read as permanently hung in the TUI. (#212)
- **Web UI: every heard transcript is now forwarded**, not just ones
  that survive every gate -- an utterance dropped by any gate
  (paused/not-the-resume-word, low-confidence, etc.) used to show in the
  TUI's transcript pane but never reach the web UI at all. (#213)
- **Packaging: the built wheel left every CLI entry point broken**
  (`scripts/` was present in the sdist but dropped from the wheel).
  Distribution renamed to `legionforge-convobox` on PyPI (matching the
  org's existing naming convention), and a new OIDC Trusted-Publishing
  `publish.yml` added, dormant until a `vX.Y.Z` tag is pushed. (#206)
- **STT: reject an incompatible `compute_type`/`device` pairing at
  config load** instead of failing later. (#210)
- **Settings TUI recovers from an invalid on-disk config** instead of
  crashing. (#215)
- **Settings TUI: confirming a picker without cycling it no longer
  claims the value was "updated."** (#200)
- **Settings TUI: opening `backend.command` and pressing Enter unchanged
  no longer corrupts it.** (#254)
- **`run_convobox`: removed a dead duplicate approval-gate
  construction.** (#239)
- **Interaction: paused-listening drops now log at INFO, not DEBUG.**
  (#198)
- **Web UI: settings-save confirmation now matches the actual restart
  requirement** instead of always claiming one is needed. (#248)
- **Web UI: "Open in editor" `vscode://` URI used Windows backslashes**,
  not valid URI syntax. (#249)
- **Web UI: "Open in editor" guarded against a stale-fetch race.** (#260)
- **STT `transcribe()` offloaded to a thread with an optional timeout**,
  and the **VAD segmenter's per-window Silero call offloaded to a
  thread** -- both were synchronous on the event loop and could
  plausibly freeze the whole app. (#217, #231)
- **Dedicated executors for indefinite blocking calls**, distinguishing
  queue-wait time from execution time in VAD stall warnings. A follow-up
  stall diagnostic for `MicrophoneStream.stream()` live-confirmed this
  pass did not fully close the underlying VAD-freeze issue -- tracked as
  a known issue below, not yet root-caused. (#269, #271)
- **Web UI: SSE broadcast tasks are now tracked** instead of
  fire-and-forget (a dropped reference meant CPython's weak-reference GC
  could cancel one mid-broadcast, silently dropping frames). (#238)
- **Web UI: SSE subscriber queues are now bounded**, evicting the oldest
  entry and signaling drops instead of growing unbounded. (#241)
- **Web UI: history DB writes offloaded off the event loop.** (#242)
- **A safeword match in a transcript no longer skips checking that same
  transcript for a pause phrase.** A long, rapid-fire safeword utterance
  fired the hard stop correctly but silently dropped a "stop listening"
  pause intent present in the same transcript, since the entire
  pause/resume check lived inside the hard-stop branch's `if not
  is_hard_stop:` guard. `listening_gate.observe()` now runs
  unconditionally; on a hard-stop transcript only its state change is
  applied, not its own redundant stop-sequence side effects. Never a
  safety gap -- the hard stop itself always fired correctly regardless.
  Live-verified: both the hard stop and the paused state now register
  from the same chained utterance --
  `docs/field-notes/2026-08-15-pause-phrase-fix-live-verified-hard-stop-
  and-pause-both-register.md`. (#276)
- **Web UI: artifact pane wide content was permanently half-clipped, and
  the pane couldn't be widened at all.** The native CSS `resize:
  horizontal` handle had zero drag room since the pane sits flush
  against the browser window edge, and `align-items:
  center`/`justify-content: center` on the pane body was silently
  swallowing overflow instead of scrolling to it. Replaced with a custom
  drag handle (mouse + arrow-key resizable, clamped 240px-85vw) and
  `margin: auto` centering, which scrolls correctly once content
  overflows. (#282)
- **Web UI: approval-action buttons now grey out immediately once a
  voice approve/deny decision resolves**, instead of staying clickable
  indefinitely; the "explain" action stays active as intended. (#287)

### Security
- **`claude-code` backend approval-gate gaps fixed** (issue #235, A1+A2).
  (#236)
- **CSRF-protected every mutating web route**, and restricted the
  settings-API write surface. (#237)
- **Approval hook token comparison now uses `secrets.compare_digest`**
  instead of `!=`, closing a timing-attack surface. (#266)
- **The `pypi` GitHub publishing environment is now gated**: restricted
  to deploys from `main` only, plus a required human reviewer, ahead of
  multiple AI agents having push access to this and sibling repos. Same
  protection brought up to this bar on `LegionForge/guardian` and
  `LegionForge/llm-valet`. (cross-repo, alongside #206)
- **`backend.permission_mode: permissive` now genuinely bypasses every
  tool permission**, not just file edits. It previously mapped to the
  same Claude Code flag as `approve` (`acceptEdits`), which only
  auto-approves Write/Edit/NotebookEdit -- every other tool (Bash,
  WebFetch, WebSearch, Read, ...) still generated a real approval
  request that headless mode has no channel to answer, silently stalling
  those calls in a mode whose entire point is "act without asking."
  `permissive` now correctly maps to `bypassPermissions`; `approve` is
  unaffected. (#182)

### Known issues
- **A self-triggered barge-in loop under rapid-fire conditions on
  macOS** (issue #119): 20 barge-ins in ~90s during a real live-speech
  demo at high playback volume, 18/19 with a following AEC reading
  showing `UNDER-CANCELLING`. No code fix shipped -- `do-not-disturb`
  mode (the config's original default) isn't subject to this, since
  ordinary speech can't trigger anything mid-playback there. For
  `conversational` mode at high volume, a documented config-level
  mitigation (`aec_delay_ms: 400` + `barge_in_min_speech_ms: 1200`)
  brought false triggers from 8-13 down to a mean of 1.25 across 4 live
  trials; likely root cause is the Mac mini's single built-in speaker
  distorting acoustically at volume, which a linear AEC structurally
  can't fully cancel. See `docs/KNOWN-ISSUES.md` and
  `docs/field-notes/2026-08-11-self-barge-in-combined-mitigation-and-
  hardware-notes.md`.
- **The safety-relevant freeze flagged in the previous cycle is now
  mostly understood, and the opencode-specific mechanism is mitigated.**
  Escalated 2026-08-12 as likely two distinct bugs; a 2026-08-15
  investigation (`docs/KNOWN-ISSUES.md`'s VAD-freeze entry, "2026-08-15
  investigation, final status") found most of what looked like a
  widespread freeze was either (a) a test-harness volume confound and
  harmless idle time misread as a hang by diagnostics that didn't yet
  distinguish the two, or (b) codex blocking on its own stdin -- not
  ConvoBox's event loop. For (b), `Orchestrator.stop_event_loop()` now
  retries `cancel()` up to 3 times (3s timeout each) instead of once,
  turning what used to be an indefinite hang into a ~9s-bounded one;
  validated live against 143 automated hard-stops on Windows, zero
  timeouts. **One genuinely new, still-open variant was also caught the
  same evening**: a mic-layer-only freeze with no codex subprocess
  involved, 6+ minutes, the first time this shape self-resolved rather
  than requiring intervention -- root cause not established, rare (one
  occurrence to date), tracked in `docs/KNOWN-ISSUES.md`. (#289)
- **`safeword.kill_phrase` is unreliable on macOS with a `codex`
  backend.** Apple's Seatbelt sandboxing reparents the real spawned
  child process to `launchd` almost immediately, detaching it from the
  process tree before `force_kill()` can reach it, and macOS signals
  don't cascade to children the way Windows' `TerminateProcess()` does
  -- codex was 0/10 in live testing. `claude-code` stays reliable (10/10)
  on both platforms. A process-group kill (`os.killpg()`) is a candidate
  fix, not yet built or confirmed. See `docs/KNOWN-ISSUES.md`'s
  force-kill entry.

## [0.3.0] — 2026-07-28

### Added
- **Web UI v2: full Settings editor, a real control-plane (approve/deny/
  explain, stop/resume listening, quit), and a live artifact pane**
  (`src/convobox/web/`, `src/convobox/web/static/index.html`).
  `Attribution: Claude Code; Provider: Anthropic; Model: claude-sonnet-5;
  Scope: src/convobox/web/, scripts/run_convobox.py's web wiring,
  docs/WEB-UI-*.md.` Builds on Phase 1's read-only view (below) with real
  mutating capability, each a deliberate, discussed extension of the
  no-auth loopback trust model, not silent scope creep -- see
  `docs/WEB-UI-USAGE.md`'s "Security posture" section.
  - **Settings editor** (`web/settings_api.py`, `GET /api/settings`,
    `POST /api/settings/schema`/`/validate`/`/save`/`/test`): full feature
    parity with `scripts/settings_tui.py`, reusing that file's
    `SECTION_SPECS`/`validate_config`/`save_with_backup`/`probe_*`
    directly rather than a second copy, so the TUI and web UI can never
    silently drift on what counts as valid or how a save is written.
  - **Approve/Deny/Explain buttons** (`WebApprovalBridge`) answer the same
    pending backend approval a spoken phrase would; **Stop/Resume
    listening** (`WebListeningBridge`) drives the same hard-stop path
    barge-in/the safeword use; **Quit** ends the whole session (mic loop,
    backend, and the web server itself) -- arms on first click, fires on a
    second within a few seconds. Voice and the browser can both answer a
    pending approval or pause; whichever gets there first wins.
  - **Live activity-status indicator**: the mic loop's own
    `listening`/`capturing`/`speaking`/`working`/`waiting`/`paused` state,
    over the same SSE stream, updated only when it actually changes.
  - **Artifact pane** (`web/artifacts.py`, `GET /api/artifacts/{path}`):
    opens when a tool call writes something worth looking at (image or
    HTML page) -- Claude Code only today (a confirmed successful
    `Write`/`Edit` tool call), opencode/codex not yet wired. Served only
    from `backend.working_dir`, fenced by resolving the real path rather
    than a string-prefix match; resizable, with the chat pane adjusting
    alongside it.
  - Bubble-chat layout with a branded top ribbon and configurable
    per-role bubble colors; configurable user/assistant display names
    (`DisplayConfig.user_name`/`assistant_name`); PWA installability
    (`manifest.json`, `sw.js`).
  - A field note on other Claude Code/coding-agent web UIs
    (`docs/field-notes/2026-07-28-other-claude-code-web-uis-dont-transfer-
    much.md`): most of their scope (file tree/Git/terminal/multi-session)
    doesn't transfer, since ConvoBox's web UI is a voice-session
    companion, not an IDE -- PWA install-ability was the one portable
    idea, now built.

> **Attribution:** Changes in the remainder of this section were authored by the
> **ConvoBox** AI coding agent during live audio UAT on 2026-07-14/15
> (submitted via the `jp-cruz` account, PR #78). ConvoBox is the product
> under test; its own agent made these modifications. The agent was observed
> running on opencode's `hy3-free` model (OpenCode Zen provider) — verified
> from the live backend's session records, not assumed. See `docs/UAT-checklist.md`
> **[L2]**.

### Fixed
- **A live-UAT AEC diagnosis was wrong, and the resulting config edit was
  reverted** (`convobox.yaml` in the UAT working tree; no repo code
  changed). `Attribution: Claude Code; Provider: Anthropic; Model:
  claude-fable-5; Scope: this entry.` 47 `UNDER-CANCELLING` verdicts in a
  live session were diagnosed as caused by a stale `aec_delay_ms: 309`
  fighting a ~222ms auto-tune estimate, and the explicit value was
  removed. That was wrong: `uat-acoustic-calibration/`'s real,
  on-hardware delay-sweep reports (already in the repo from 2026-07-16)
  rank 309ms the empirical best of the values tested, and 222ms (the
  auto-estimate) the *worst*. Restored the explicit value with a comment
  pointing at the evidence. See `docs/DESIGN-echo-and-barge-in.md`'s
  2026-07-20 correction for the full account, including why this
  doesn't fully contradict the same-day synthetic/WebRTC-source research
  into whether AEC3 needs a precise delay hint (it's flagged as an
  honest, unresolved tension, not papered over).
- **`uv`'s local build cache can cross-contaminate editable installs
  between two same-named/same-version local clones of this repo** --
  confirmed by direct reproduction while investigating the above:
  running `uv sync` in one clone silently repointed the OTHER clone's
  `convobox` import at the wrong `src/`, undetected until an import
  error surfaced it. `Attribution: Claude Code; Provider: Anthropic;
  Model: claude-fable-5; Scope: this entry.` No code fix applies (this
  is `uv` cache behavior, not a ConvoBox bug); documented as a
  practical rule -- re-run `uv sync --reinstall-package convobox
  --no-cache` and verify `convobox.__file__` before trusting a test run
  -- in `TESTING.md` → "Keeping local, CI, and UAT environments in
  sync".
- **Python version floor didn't match what CI or local dev actually run**
  (`pyproject.toml`, `uv.lock`, `README.md`, `docs/QUICKSTART.md`).
  `Attribution: Claude Code; Provider: Anthropic; Model: claude-fable-5;
  Scope: this entry.` `requires-python` and the docs claimed "3.11+", but
  `ci.yml` pins every job to `python-version: "3.12"` and this machine's
  own dev `.venv` is 3.12 -- 3.11 was never actually exercised anywhere.
  Raised the floor to `>=3.12` to match reality instead of adding
  untested 3.11 CI coverage. Two related environment-drift risks (pip
  vs. uv tool-version resolution having no shared lockfile; `scripts/*.py`
  having zero CI lint/type-check coverage) were investigated and
  documented as accepted, named gaps rather than silently fixed --
  see `TESTING.md` → "Keeping local, CI, and UAT environments in sync".

### Added
- **`--aec-dump` captures a live incident's real AEC audio for offline
  replay** (`src/convobox/audio/aec.py`'s new `AecDumpWriter`,
  `scripts/run_convobox.py`, `src/convobox/tui/`). `Attribution: Claude
  Code; Provider: Anthropic; Model: claude-fable-5; Scope: this entry.`
  Complements `scripts/acoustic_calibration.py`'s controlled, scripted
  calibration sweeps with the ability to capture what actually happens
  during a REAL conversation with a real coding-agent backend --
  something the calibration script deliberately doesn't do. Writes
  `reference.wav`/`mic-raw.wav`/`mic-processed.wav` (WebRTC's own
  "aecdump" methodology: capture once, replay against any hypothesis
  offline, no repeat live sessions needed) to a timestamped subdirectory
  of `.aec-dumps/` (gitignored). The `--tui` conversation view shows a
  `REC <n>s` tag on the diagnostics line while active; verbose log lines
  mark start, per-response progress, and a final after-action summary
  at shutdown (finalized even on Ctrl+C, so the WAV headers stay valid).
  See `docs/DESIGN-echo-and-barge-in.md` → "Capturing a live incident
  for offline analysis".
- **Conversation TUI panes are now keyboard-scrollable** (`src/convobox/tui/`,
  `scripts/run_convobox.py`). `Attribution: Claude Code; Provider:
  Anthropic; Model: claude-fable-5; Scope: this entry.` Reported as
  "PgUp/PgDn and other shortcut keys aren't in the TUI" — traced first
  (per this repo's "verify before fixing" rule) and confirmed there was
  never any keyboard input handling in `_tui_render_loop` at all: both
  panes always rendered just their tail, a missing feature rather than a
  regression. `Tab` now switches focus between the Transcript and Full
  response panes; `Up`/`Down`/`PgUp`/`PgDn`/`Home`/`End` scroll whichever
  pane has focus, clamped fresh every frame so a stale offset (terminal
  resize, shorter content) can never blank a pane. Windows (`msvcrt`) and
  POSIX (raw/cbreak `termios` mode for the session, CSI-sequence
  decoding for PgUp/PgDn) are both implemented; see
  `docs/UAT-checklist.md` **[U9]** for what's unit-tested versus still
  needing a live-terminal pass, and `docs/ROADMAP.md` for why mouse-wheel
  support was scoped out of this pass rather than bundled in.
- **Backend questions are announced out loud** (`src/convobox/orchestrator/`):
  when the backend calls opencode's blocking interactive `question` tool,
  ConvoBox speaks the question with numbered option labels and logs
  "backend is waiting for YOUR answer" -- previously the session silently
  deadlocked (UAT finding [L9]; slice 1 of
  `docs/DESIGN-backend-questions.md`). `Attribution: Claude Code;
  Provider: Anthropic; Model: claude-fable-5; Scope: this entry.`
- **Resume word is configurable from the Settings TUI**, validated by the
  real `ResumeWordDetector` at save time, with a warning for words the real
  TTS->STT round-trip has proven unreliable (`ROUNDTRIP_REJECTED_RESUME_WORDS`
  -- 'ConvoBox' itself mis-heard as 'Control Box' every time).
  `Attribution: Claude Code; Provider: Anthropic; Model: claude-fable-5;
  Scope: this entry.`
- **Operator-maintained STT corrections glossary** (`stt.corrections`,
  `src/convobox/stt/corrections.py`): deterministic word-boundary fixes
  for recurring mishears ('bargain' -> 'barge-in'), applied only after
  every safety-critical raw-transcript check -- a correction can never
  manufacture a hard stop, wake/pause action, or approval decision.
  `Attribution: OpenAI Codex; Provider: OpenAI; Model: gpt-5.6-terra;
  Scope: this entry.`
- **Conversation TUI now shows backend name, AEC status, and a
  color-coded working heartbeat** (`src/convobox/tui/state.py`,
  `src/convobox/tui/render.py`, `scripts/run_convobox.py`). `Attribution:
  Claude Code; Provider: Anthropic; Model: claude-opus-4-8; Scope: this
  entry.` Per JP's direct request for "voice status information...
  back-end interpreter... any other information you deem necessary": a
  new diagnostics line shows `backend: <name>`, `AEC: on/off` (+ the
  last response's compact verdict tag once available), and, only while
  silently busy, a green/yellow/red `still working: Ns` heartbeat
  (`WorkingIndicator.silent_busy_s`, a new continuous counterpart to
  `observe()`'s sparse notification-tick return value). Also shows a
  live mic level in dBFS (post-AEC, reusing `audio_devices.level_meter()`'s
  existing math) on the same line -- speaker-side level deliberately
  deferred (would need a cross-thread write from the playback callback).
  See `docs/UAT-checklist.md` **[U7]**/**[U8]**.
- **Overlap gate's grace window now widens after a poorly-cancelled response**
  (`scripts/run_convobox.py`). `Attribution: Claude Code; Provider:
  Anthropic; Model: claude-opus-4-8; Scope: this entry.` The `[E8]`
  self-barge-in incident's log stayed `UNDER-CANCELLING` for nearly the
  whole session even after fixing the delay hint -- same-room mic+speaker
  echo can leave real, uncancelled energy that leaks through as apparent
  "new speech" right after playback ends. `grace_s_for_last_response()`
  widens the overlap gate's protected window (`ECHO_GRACE_S`)
  proportionally to the just-finished response's remaining echo headroom,
  capped at 1.0s; a `FLOOR-LIMITED` or `NO ECHO DETECTED` response leaves
  it unchanged. The exact constants are derived from the `[E8]` log's own
  numbers, not live-tuned -- see `docs/UAT-checklist.md` **[E9]** for the
  live validation this still needs.
- **AEC delay auto-tune is now the real default, and Settings TUI saves only
  write fields you actually changed** (`src/convobox/config.py`,
  `scripts/run_convobox.py`, `scripts/settings_tui.py`). `Attribution: Claude
  Code; Provider: Anthropic; Model: claude-opus-4-8; Scope: this entry.`
  `audio.aec_delay_ms` defaults to `None` (auto-tune from real measured
  stream latencies) instead of a literal `100`. Root-caused a real live
  incident: the Settings TUI's save used to write every field on every
  save, so opening and saving it even once silently baked a stale
  `aec_delay_ms: 100` into `convobox.yaml`, permanently disabling
  auto-tuning -- explaining a mic+speaker session where the real delay
  was ~222ms and AEC could never converge, so the assistant kept
  self-triggering barge-in on its own TTS output. Saves now use
  `exclude_defaults=True`. The field is also user-editable in the
  Settings TUI (`optional_int`, `-` clears it back to auto-tune) and its
  help panel shows the last real auto-detected value, read from a
  diagnostic sidecar file `run_convobox.py` writes (`<config>.aec-estimate.json`,
  never `convobox.yaml` itself). See `docs/UAT-checklist.md` **[E8]** and
  `docs/UAT-settings-tui.md`.
- **Repo-wide AI attribution convention**: `docs/AI-ATTRIBUTION.md` now
  defines how to record Codex, Claude Code, and opencode edits in PRs,
  changelog entries, commit trailers, or file-level notes when those notes
  are genuinely useful.
- **Agent response logging in the UAT/echo log** (`scripts/run_convobox.py`):
  the orchestrator's `on_event` hook now records every backend reply, not
  just the user's transcript. Each reply is logged as `response: <raw text>`
  and, when the spoken form differs from the raw reply, `response(spoken):
  <spoken text>` — so the log now shows what the agent said back (and what
  was actually spoken aloud, making markdown-readout bugs like Piper saying
  "asterisk asterisk" visible). Live-confirmed during the 2026-07-14 audio
  UAT: agent replies were previously forwarded straight to TTS and captured
  nowhere, leaving the most useful lines of an audio test invisible in the
  log. See `docs/UAT-checklist.md` **[L1]**.
- **Local web UI** (`--web` / `web.enabled` in `convobox.yaml`, opt-in and
  off by default): a browser view of a live session -- transcripts,
  backend responses, tool calls, and pending approvals streamed over
  Server-Sent Events, plus optional SQLite history
  (`web.history_tracking_enabled`, a separate opt-in from `enabled`
  since viewing a live session and persisting it to disk are different
  privacy decisions). `Attribution: Claude Code; Provider: Anthropic;
  Model: claude-sonnet-5; Scope: src/convobox/web/, scripts/run_convobox.py's
  web wiring, docs/WEB-UI-*.md.` Built across several independently
  committed and live-verified slices: `WebConfig` (loopback-only by
  default, `bind_address` rejects a specific non-loopback address since
  this server has no authentication); `HistoryDB`
  (`src/convobox/web/history.py`); a FastAPI app
  (`src/convobox/web/app.py`) serving `/api/sessions`,
  `/api/sessions/{id}/events`, `/clear`, `/export`, and the SSE stream,
  with `EventBroadcaster` (`stream.py`) fanning events out to every
  connected browser tab rather than just one; `WebEventForwarder`
  (`bridge.py`) plugging into `Orchestrator`'s existing `on_event` hook
  (no change to `Orchestrator` itself needed) and into every
  `handle_transcript()` call site, so both backend events and the
  user's own recognized speech reach the browser; a real `uvicorn`
  server started as a background task alongside the voice loop; and a
  minimal, dependency-free HTML/JS frontend
  (`src/convobox/web/static/index.html`), mounted last so it can never
  shadow the API routes. `fastapi`/`uvicorn` are the new `web` extra
  (`uv sync --extra web`), lazily imported only when actually enabled --
  zero added dependency weight for CLI/TUI-only use. See
  `docs/WEB-UI-USAGE.md` (end users) and `docs/WEB-UI-DEV.md`
  (contributors).

### Fixed
- **`BargeInMonitor` could fire against a response that produced no
  audible output yet** (`src/convobox/audio/playback.py`). `Attribution:
  Claude Code; Provider: Anthropic; Model: claude-sonnet-5; Scope: this
  entry.` `AudioPlayer.is_playing()` reports `True` the instant the
  playback thread starts and the device stream opens, before a single
  sample has actually reached it -- a real TTS-synthesis-latency window
  during which a live session showed a barge-in log line ("stopping
  audio") for a response that was never audible. Confirmed live:
  matching AEC reference frame counts immediately before/after proved
  zero audio was ever output. Functionally harmless (routing was already
  correct) -- diagnostic/UX only. Added `AudioPlayer.has_played_audio`,
  a flag set the first time a real block reaches the device, distinct
  from thread liveness; not yet wired into the barge-in log message
  itself (deliberately left for a live mic session to confirm the
  corrected wording reads right -- see `docs/UAT-checklist.md` **[G8]**).
- **Backend event stream could die silently mid-session, losing the LLM's
  response from the log for over a minute** (`src/convobox/orchestrator/orchestrator.py`,
  `src/convobox/adapters/opencode.py`). `Attribution: Claude Code; Provider:
  Anthropic; Model: claude-opus-4-8; Scope: this entry.` JP reported "I am
  not always seeing the LLM output in the logs" and pasted a live UAT log
  showing the real cause: `_ensure_session()`'s session-creation POST had
  no explicit timeout (unlike the prompt POST), so a busy/cold opencode
  server exceeded httpx's bare 5s default and raised `ReadTimeout` --
  which `Orchestrator._consume_events()` had no exception handling for at
  all, silently killing the whole event-consuming task. Nothing re-created
  it until an unrelated later utterance happened to trigger a fresh
  subscription; in the live log, an entire real response sat unlogged for
  over a minute. Fixed both: the session-creation POST now gets the same
  generous read timeout as the prompt POST, and `_consume_events()` now
  resubscribes immediately on any exception (clearly logged), while
  deliberately preserving each adapter's existing lazy-respawn contract
  for a normal (non-exception) end. See `docs/UAT-checklist.md` **[L5]**.
- **Response hook was not wired outside `--tui` mode**: `Orchestrator`'s
  `on_event` was passed `None` unless `--tui` was set, so a plain
  listening/UAT session never observed assistant replies at all. The hook is
  now installed unconditionally (it safely handles `tui_state=None`).
- **Accidental duplicate definitions** of `_on_backend_event`,
  `_draw_conversation_tui`, and `_tui_render_loop` in `scripts/run_convobox.py`
  (the second copy silently overrode the first). Collapsed to a single copy.

## [0.2.0] — 2026-07-12

The first release where the **whole product loop works end-to-end**: speak,
and a real coding agent responds by voice. 0.1.0 was the front-half spike
(mic → transcript); 0.2.0 closes the loop through a backend and back out to
speech, hardened across a full day of live voice UAT on Windows 11.

**Tested configuration:** Windows 11 · opencode backend · faster-whisper STT
· Piper TTS. Other backends/platforms are implemented but not yet
voice-validated — see the README support matrix and `docs/KNOWN-ISSUES.md`.

### Added
- **Full voice loop** (`scripts/run_convobox.py`): mic → Silero VAD
  utterance segmentation → faster-whisper STT → orchestrator → backend
  adapter → Piper TTS → speakers, run against a live opencode server.
- **Acoustic echo cancellation** (opt-in, `audio.echo_cancellation`): WebRTC
  AEC fed the playback as a far-end reference, with auto-estimated
  render-to-capture delay and a floor-aware three-way verdict.
- **Streaming TTS**: audio starts on the first synthesized sentence, so
  time-to-first-audio is proportional to one sentence, not the whole reply.
- **Deterministic safeword hard-stop**: `stop/break/brake/eject/mayday`
  (×3), matched on the raw transcript and honored mid-playback.
- **Soft interject vs. hard stop** as distinct backend semantics
  (opencode: steer vs. interrupt).
- **Guided audio setup** (`scripts/audio_devices.py --setup`): default-first
  device testing, continuous test tone + live mic-level meter, mic
  record/playback with replay & re-record, a chooser deduped to one entry
  per physical device (hiding Windows' host-API duplicates and unopenable
  WDM-KS/meta devices), and a warning if no mic/speaker was selected.
- **Pluggable STT/TTS engines**: `STTEngine`/`TTSEngine` ABCs with
  `create_stt_engine`/`create_tts_engine` factories (installed at setup
  time, never bundled).
- **`ConfirmwordDetector`** — safety-tier primitive (the inverse of the
  safeword) that refuses to be armed with a common affirmation. Library
  primitive; orchestrator wiring deferred.
- **Single-instance mic lock** and a **working-indicator heartbeat** for a
  silently-busy backend.

### Fixed
- **Playback across arbitrary devices/host APIs**: open the output stream at
  the device's *native* rate and resample to match, instead of forcing
  Piper's 22050 Hz (which WASAPI rejected outright and DirectSound
  mis-resampled to silence).
- **WASAPI garbled static**: a phase-continuous streaming resampler
  (`_StreamResampler`) eliminates the per-chunk boundary clicks that were
  inaudible at integer device ratios (MME) but garbled at non-integer ones
  (48 kHz WASAPI).
- **Backend timeout no longer crashes the session**: the prompt POST gets a
  generous read timeout, and the run loop guards each utterance so a backend
  error is logged and listening continues instead of taking down the app.

### Known issues
- **WASAPI output plays speech an octave too high** (mono-on-stereo channel
  handling in PortAudio's WASAPI path). Use an **MME** output device.
  Documented in `docs/KNOWN-ISSUES.md`.

## [0.1.0]

Initial spike: microphone capture, VAD, and local transcription — the
front-half of the loop (mic → transcript), before any backend or TTS.
