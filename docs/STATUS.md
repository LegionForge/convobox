# Status

The detailed, narrative status log behind the README's condensed support
matrix. This is where "what changed and why," live-verification detail,
and the security/performance audit findings live. For the at-a-glance
tested-vs-implemented table, see the README's [Status](../README.md#status)
section; for the formal per-release changelog, see
[../CHANGELOG.md](../CHANGELOG.md).

## Since 0.3.1 (in progress, not yet tagged)

**First macOS live-hardware UAT pass, 2026-08-10.** Everything below
this note through 0.3.1's Windows-only history predates any live
hardware testing on macOS; this pass closes a meaningful chunk of that
gap on a Mac mini (Apple Silicon), AIRHUG 28 USB mic, Mac mini
Speakers. Full evidence for every finding lives in
`docs/field-notes/2026-08-10-*.md`, not repeated here.

- **Signal-level AEC confirmed working on real macOS hardware** — 41
  live trials across the session (baseline + a 10-run batch + a 29-run
  volume-escalation batch at 1.5x/2.0x/3.0x playback volume): mean
  attenuation 5-8dB depending on volume, **zero false barge-ins in
  every single trial**, AEC on or off. An initial 2-trial read got the
  attenuation-vs-ceiling comparison backwards ("at or below" when the
  real numbers were the opposite) — caught and corrected in place once
  a larger batch made the arithmetic error obvious; see the
  10-runs field note for the full self-correction writeup.
- **Claude Code and Codex backends confirmed live via `--text` mode**
  (real TTS through real speakers); **opencode untested on this
  machine** — no provider credentials configured there, not something
  set up unattended.
- **A real crash was found and fixed in `run_convobox.py --text`
  mode**: a `NameError` on the very first backend event, from a
  closure over a variable only assigned in the mic-loop setup path.
  Fixed (`ce413f9`) and reverified live against both backends.
- **Kokoro TTS confirmed working live on macOS for the first time**
  (previously "not yet a live voice session with real speakers" on any
  platform, per this doc's own TTS section below).
- **A TTS→speaker→mic→Whisper round-trip reproduced the known
  `[E6]` far-field-echo hallucination pattern** (see
  `docs/UAT-checklist.md`), then correctly isolated it: feeding the
  same synthesized audio directly to the transcriber (no speaker/mic
  path) scored 100% word accuracy for both Piper and Kokoro — the STT
  model itself is fine; the far-field acoustic path is the hard part,
  already known and already mitigated in real sessions by the overlap
  gate.
- **First live confirmation of the real mic loop AND the safeword
  hard-stop on macOS** — via a synthetic-speech-injection harness (TTS
  played through the real speaker, picked up by a live, non-`--text`
  session's own mic; no human speaker was available). Full
  VAD→STT→backend→TTS loop confirmed working end to end; the safeword
  ("stop stop stop") correctly hard-stopped a live session twice, no
  crash, clean recovery. One caveat recorded honestly: both trials had
  `busy=False` at the moment of the stop (the backend turn had already
  finished), so this doesn't yet confirm the `was_busy=True` branch a
  same-session hard-stop fix (below) added.
- **`Orchestrator.hard_stop()` "honesty fix" shipped**: it now reports
  whether a turn was actually busy when it fired, and the web/voice
  pause paths use that to stop implying a tool call fully stopped when
  it may still be finishing in the background — closes an
  explicitly-named, previously-unbuilt option from the 2026-08-09
  hard-stop-doesn't-cancel-a-tool-call finding.
- **Still open after this pass**: real human voice input on macOS
  (everything above used synthetic TTS injection, not a person
  speaking), Chrome/browser-driven web UI testing (tooling unavailable
  this session), and opencode (credentials).

**First real human-speech demo on macOS, 2026-08-11** — closes the
"real human voice input" gap the pass above left open. JP demoed
ConvoBox live to his son. **Safeword confirmed working with real
speech, 3 times** (`stop stop stop` x2, `abort abort abort` x1).
**Barge-in confirmed working** after switching
`interaction.interrupt_preset` to `conversational` — then a **real
self-triggered barge-in loop** appeared under rapid-fire conditions
(20 barge-ins in ~90s, 18/19 with a following AEC reading showing
`UNDER-CANCELLING`). Diagnosed live: attenuation stayed near this
session's steady-state baseline (6.54dB vs. 6.75dB) while the measured
echo-to-ambient ceiling spiked (14.22dB vs. ~0.53dB) — rapid
back-to-back short turns leave proportionally more residual echo for a
fixed amount of real cancellation to remove. `do-not-disturb` mode
(the config's original default) isn't subject to this, since ordinary
speech can't trigger anything mid-playback there. Also reconfirmed
`[E6]`'s far-field hallucination pattern with real speech (not
synthetic), including one instance where a hallucinated transcript
happened to contain "stop listening" as a substring and paused the
session — a real, organic occurrence of an already-documented risk
category. No fix built this pass; full writeup:
`docs/field-notes/2026-08-11-macos-live-human-demo-safeword-bargein-and-self-echo-loop.md`.

A day-long live-UAT + infra-hardening pass, 2026-08-05/06. Four PRs
merged (#212, #202, #204, #205), two open and ready (#206, #213), plus
cross-repo GitHub/PyPI publishing hardening still in progress. Full
evidence for every finding below lives in `docs/field-notes/` (dated
2026-08-05) rather than repeated here.

- **`stt.hotwords` (#204, merged) — live-UAT'd properly, not just shipped.**
  faster-whisper's own prompt-bias, added to fight the recurring
  short-resume-word ("Athena") mis-transcription pattern. First attempts
  at a same-day A/B were genuinely inconclusive (the same hotwords-active
  branch produced both the best and worst runs of the day before
  `stt.temperature` was pinned to remove the STT's own decode
  randomness as a confound) — see
  `docs/field-notes/2026-08-05-stt-hotwords-athena-resume-inconclusive.md`
  for the full self-correcting methodology story. Once properly
  controlled (temperature pinned, same mic, back-to-back), hotwords ON
  did show a real advantage (2/2 vs 0/2 voice-resolved). Bigger finding
  from the same pass: **microphone hardware swung the resume-success
  rate far more than hotwords did** (0/2 on a Lavalier to 14/14 on
  another mic, all else held constant) — worth remembering before
  crediting any future STT-config change without also controlling for
  mic.
- **VAD segmenter could go silent indefinitely (bundled into #204's
  branch).** Two related live incidents, same day:
  1. With `vad.max_utterance_s` unset, `UtteranceSegmenter` could lock
     up permanently — mic capture and AEC processing stayed alive
     (confirmed via AEC-dump frame-count arithmetic matching wall-clock
     time), but zero utterances were ever completed, zero log output,
     for 3+ minutes until the operator gave up and quit.
  2. Setting `vad.max_utterance_s: 30` (the pre-existing mitigation for
     exactly this class of bug) stopped the *permanent* lockup but
     exposed a second, subtler gap: a forced-cap run that never
     accumulated `min_speech_ms` of confidently-classified speech
     (audio sitting in the VAD's exit-hysteresis band) was silently
     discarded — no utterance, no log line, indistinguishable from
     genuine silence, repeating every 30s cap cycle. Fixed with a new
     `UtteranceSegmenter.discarded_forced_runs` counter and a
     `_working_watchdog` heartbeat `WARNING` on increase, so this state
     is now visible instead of silent. Full write-up:
     `docs/field-notes/2026-08-05-vad-segmenter-silent-unbounded-lockup.md`.
- **Web UI showed less than the TUI, twice, in two different ways —
  both real bugs, one fixed, one open:**
  - **Fixed (#212, merged):** a web-triggered pause/resume (the
    Stop/Resume-listening button) correctly flipped the shared
    `ListeningGate` but never logged or touched the TUI's
    `ConversationTuiState` — only the voice path did. A session that
    was genuinely working (resumed via the button after voice kept
    missing the resume word) read as permanently hung because the TUI
    kept showing the stale "paused" banner forever.
    `docs/field-notes/2026-08-05-web-resume-desyncs-tui-display.md`.
  - **Found, fix ready but not yet merged (#213):** any utterance
    dropped by *any* gate (paused/not-the-resume-word, low-confidence,
    etc.) showed in the TUI's transcript pane (which logs everything
    heard, by design) but never reached the web UI at all —
    `web_forwarder.forward_transcript()` used to fire only for
    utterances that survived every gate and reached the backend, not
    at the same point the TUI logs from. Live-caught example: STT
    correctly transcribed the literal word "Athena," but it failed the
    confidence gate (0.33 < 0.40, a short-utterance false negative) and
    silently vanished from the web view.
    `docs/field-notes/2026-08-05-web-transcript-forwarding-parity.md`.
- **Web status line now shows what the backend is doing, not just that
  it's busy (#205, merged, live-verified in-browser)**: `working
  (thinking)` / `working (<tool name>)` instead of a bare `working`,
  closing a gap the TUI's own heartbeat tag has had since PR #190.
- **PyPI packaging fix (#206, open, CI green, ready to merge) grew into
  a real publishing-infra pass**, not just the original entry-point bug:
  - The original fix (`pip install convobox` shipped with every CLI
    entry point broken — `scripts/` present in the sdist but dropped
    from the wheel) is unchanged from when it first shipped.
  - **PyPI distribution renamed to `legionforge-convobox`**, matching
    this org's own naming convention (PyPI has no namespace scoping;
    every LegionForge package is prefixed to avoid squatting/ambiguity
    — `legionforge-guardian`/`legionforge-llm-valet` already exist
    under the same pattern). Confirmed unclaimed and live-verified via
    a real `uv build` → clean-venv install → `convobox --help` /
    `convobox-settings --help` cycle, not just edited and assumed.
  - **New `publish.yml`** (OIDC Trusted Publishing, no stored PyPI
    token), dormant until a `vX.Y.Z` tag is actually pushed — every
    action SHA-pinned (a self-caught inconsistency: `ci.yml` already
    pins `dev-rig` this way with a comment explaining why; the first
    draft of `publish.yml` didn't carry the same discipline over).
  - **The `pypi` GitHub environment is now gated**: restricted to
    deploys from `main` only, plus a required human reviewer
    (jp-cruz) before the actual publish job runs — deliberately added
    ahead of multiple AI agents (not just this one) gaining push access
    to this and sibling repos. The same environment protection was
    also brought up to this bar on `LegionForge/guardian` and
    `LegionForge/llm-valet` (both already live on PyPI, neither had any
    environment protection before) — see those repos' own settings for
    detail, not duplicated here since it's cross-repo, not
    convobox-specific.
  - **Still open, blocked on the operator, not code**: an org-level
    GitHub Ruleset (tag-creation restricted to `v*`, required PR review
    count ≥1, scoped to `convobox`/`guardian`/`llm-valet`) is designed
    and ready but not yet created — the automation token lacks the
    `admin:org` scope, which requires an interactive browser
    authorization only a human can grant (`gh auth refresh -h
    github.com -s admin:org`). Also found in the same pass: `guardian`'s
    own CI is more hardened than convobox's (`step-security/harden-
    runner`, build-provenance attestation, Dependabot-driven action-pin
    bumps) — worth treating as the reference template going forward,
    not convobox; `llm-valet`'s `publish.yml` has the same
    floating-action-tag issue convobox's did before this pass, not yet
    fixed.

## Since 0.3.0

27 PRs, `0.3.1` (2026-08-01, see [../CHANGELOG.md](../CHANGELOG.md) for the
formal entry) -- mostly a stability/UX-polish pass on the interaction and
web UI work `0.3.0` shipped, plus one genuine security fix and one
safety-critical timing bug, both found and fixed via live UAT rather than
code review alone. No config schema breaks.

- **Real bugs found and fixed via live UAT, not speculative hardening:**
  - **Safety-critical: pause/hard-stop could let a stale in-flight turn's
    response play 1-10+ seconds late**, with no resume in between. Root
    cause: `send_hard_stop()` only ever reset the local busy counter --
    it never stopped the event-consumption task from reading and
    speaking a trailing event from the turn that had just been aborted.
    The same bug existed at both call sites that hard-stop (the pause
    handler and the safeword's own branch in `Orchestrator
    .handle_transcript()`); fixed at both once found, not left as a known
    duplicate. Live-verified across a 24-minute re-test with repeated
    pause/hard-stop cycling, zero leaks.
  - **Security: `backend.permission_mode: permissive` didn't actually
    bypass all permissions.** It silently mapped to the same Claude Code
    flag as `approve` (`acceptEdits`), which only auto-approves file
    edits -- every other tool (Bash, WebFetch, WebSearch, Read, ...)
    still generated a real approval request that headless mode has no
    channel to answer, quietly stalling those calls in a mode whose
    entire contract is "act without asking." Found by cross-referencing
    an independent sandboxed session's findings (WebFetch/Read/Bash all
    failing with "permissions... haven't granted it yet" in a session
    believed to be permissive) against the actual flag-resolution code.
    Now correctly maps to `bypassPermissions`; live-verified against a
    real `WebFetch` call succeeding with no approval-hang.
  - **Barge-in false-positive tag**: utterances spoken in the gap *after*
    a response finished (not during it) were incorrectly tagged as
    interrupting it, because `EchoAwarePlayer.audible` was only ever
    reset at the start of the *next* response, never on the current
    one's own natural completion.
  - **Barge-in interrupts near the end of playback could be dropped
    entirely.** An interruption starting ~1s before natural completion
    never accumulated enough sustained-speech time to cross the barge-in
    threshold before playback ended, so it fell through to the overlap
    gate and was silently discarded as presumed echo -- live-reproduced
    reliably (every time at ~1s before the end, never at ~5s before).
    Notably, this bug had been masked by the false-positive-tag bug
    above until that one was fixed.
  - **TTS synthesis/playback failures were silently swallowed.** The
    fire-and-forget speak task had no exception handling anywhere in its
    call chain; a failure (Kokoro's own ~510-phoneme hard limit is the
    confirmed live trigger) previously vanished with no log, no error,
    no indication anything went wrong beyond "the response stopped after
    the first paragraph." Now logged and surfaced as a real error event
    through the same path every other backend event already flows
    through -- live-confirmed 3/3 attempts against the real phoneme
    limit. Closed out a `--tui`-specific gap in a follow-up once found:
    the error reached the log and web UI correctly but `--tui` showed
    nothing on-screen, since `_on_backend_event` only special-cased
    `APPROVAL_REQUEST`/`TEXT`.
  - **Backend reconnect retried every ~1s forever against a dead
    `backend.url`**, with no distinction between a genuine transient
    hiccup and a permanently misconfigured backend -- ~90 identical
    tracebacks logged in under 5 minutes during a safe isolated-instance
    test. Now backs off exponentially (capped at 30s) on consecutive
    failures, resetting to fast-retry the instant a real event arrives,
    so recovery from an actual transient failure is exactly as fast as
    before.
  - **The web UI's Quit button and a real terminal Ctrl+C only ever
    stopped the embedded web server**, not the mic loop or backend
    adapter underneath, while `--web` was active. Root cause: uvicorn
    installs its own OS signal handler for as long as it runs, so the
    existing signal-based shutdown path silently never fired. Fixed by
    cancelling the main task directly instead of round-tripping through
    a now-unreliable OS signal.
  - Smaller fixes: `.md` file writes now open the artifact pane (the
    extension was simply missing from the content-type allowlist);
    `--tui` now shows `ERROR` events in the transcript, not just the log
    and web UI; a second instance's mic-lock refusal now shuts down
    cleanly instead of leaving a noisy uvicorn traceback.
- **UX polish, all live-verified:** the barge-in interrupt marker
  reworded from parenthetical prose to `[User interrupted AI response]`;
  interrupt-preset descriptions rewritten from internal jargon into a
  plain-language sentence plus a concrete "you say X, it does Y" example
  per preset (one shared `FieldSpec.help_text` covers both the TUI and
  web UI, so this one edit updated both surfaces); the web UI's paused
  status now shows which word resumes listening instead of just
  `paused`; **Safeword folded into the Interaction tab** in both
  Settings surfaces (display grouping only -- `config.safeword
  .hard_stop_phrases` and every real reference to it are unchanged).
- **New: `interaction.pause_resume_ack` pause/resume tone**, resolving
  `DESIGN-barge-in.md`'s long-open [P8] question. `none` (default,
  silent) or `tone` -- a synthesized 3-note earcon (A-major triad,
  150ms/note after live UAT found an initial 300ms/note too slow),
  ascending on resume and descending on pause. No bundled audio asset;
  generated on the fly and fed through the existing `AudioPlayer.play()`
  path. See [../CHANGELOG.md](../CHANGELOG.md) for the full entry.
- **Product direction: decided to pursue [ACP](https://agentclientprotocol.com)**
  as the standard backend-adapter protocol going forward, after
  comparing ConvoBox against [katipally/openlive](https://github.com/katipally/openlive)
  (closest prior art found so far) at JP's request. Scoping follow-up
  found only **OpenCode** exposes an ACP server natively today; Claude
  Code and Codex each only have third-party bridges of unverified
  maturity, so no adapter migration has started yet.
- **New `docs/TROUBLESHOOTING.md`**: why pause/resume/safeword
  recognition is a deterministic normalized-substring match (checked
  before `stt.corrections`, deliberately -- a hard-stop-class control
  can't depend on a rewritable glossary), and how to diagnose/verify a
  candidate phrase against your own voice, including the "Athena" story
  (the original `ConvoBox` default was confidently mis-heard as "Control
  Box" every time).
- **Two live-UAT findings documented, neither a blocker:** a hard-stopped
  in-flight backend call can surface the CLI's own interrupt-confirmation
  as a generic `error_during_execution` turn (cosmetic, never
  logged/spoken, the hard-stop itself works correctly); a misheard
  safeword can land on the pause phrase instead of the safeword itself
  (not a safety gap -- pause calls the identical `send_hard_stop()` the
  safeword does -- but leaves the session paused rather than returning to
  normal listening immediately). Both in [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
- Also fixed: a real, reproducible test-suite flake (`OpenCodeServer
  .event_gate`, an `asyncio.Event` that could lose a wakeup under
  full-suite scheduling contention -- fixed by switching to
  `asyncio.Semaphore(0)`, which queues releases properly regardless of
  scheduling order); a docs-merge conflict that had accidentally dropped
  a `KNOWN-ISSUES.md` section still referenced by `run_convobox.py`'s own
  clean-exit message, restored verbatim.

## Since 0.2.0

The interaction/safety bundle (`DESIGN-0.3.0-interaction-and-safety.md`) --
Phase 1 (barge-in + a live conversation TUI) and Phase 2 (response
tiering) -- plus web UI v2 (Settings editor, control-plane trio, artifact
pane) shipped together as `0.3.0` (2026-07-28, see
[../CHANGELOG.md](../CHANGELOG.md)):

- **Barge-in, migrated to a two-axis preset system**
  (`interaction.interrupt_preset`): `conversational`/`patient`/
  `do-not-disturb`/`halt`/`take-over`, replacing the old three-value
  `interrupt_mode`. Default (`do-not-disturb`) is behaviorally identical
  to the pre-migration default — no surprise behavior change for
  existing configs.
- **"Stop listening" / "pause listening"** puts ConvoBox into a
  resume-word-only state (default resume word: `Athena` — round-trip
  STT-verified, unlike the original `ConvoBox` default, which
  Whisper confidently mis-heard as "Control Box" every time).
- **Backchannel filtering** ("mm-hmm", "yeah", "right", ...) so a
  listener's continuers never falsely trigger a barge-in.
- **A live conversation TUI** (`--tui`): transcript pane, full-detail
  response pane, and a status/barge-in indicator, alongside the
  already-shipped Settings TUI (`scripts/settings_tui.py`, config
  editing — a separate tool from the conversation view).
- **Response tiering** (`interaction.tier_responses`): voice speaks only
  the first paragraph of a multi-paragraph response by default when
  enabled; saying "continue"/"go on" within `continue_timeout_s` speaks
  the rest, already in hand, no backend round-trip. Off by default.
- **A real safety bug found and fixed in the Codex adapter**: the
  auto-decline approval path sent a schema-invalid response for 3 of 5
  approval methods (only 2 were correct) — live-verified against a real
  `codex app-server` that the auto-decline now actually works for every
  reachable method, not just the one that happened to be tested first.
- **A real concurrency bug found and fixed from a live UAT log**: a
  single backend turn emitting multiple TEXT segments (text interleaved
  with tool calls, exactly what a coding agent doing real multi-step
  work looks like) used to leave the previous segment's speak task
  running uncancelled, corrupting the overlap gate's echo-detection
  timing for the rest of the session — reported live as "AEC seems to
  be misfiring," though AEC itself was never the actual cause. Fixed by
  cancelling any in-flight speak task before starting a new one.
- **faster-whisper's known, unresolved native-allocator failure**
  (ctranslate2/MKL leaking memory across repeated calls in a long-lived
  process — `SYSTRAN/faster-whisper#660`) is now recovered from instead
  of crashing the session: one lost utterance instead of a dead
  process, with the model reload preferring the local cache instead of
  making a network call on every recovery — see [KNOWN-ISSUES.md](KNOWN-ISSUES.md)
  for the full writeup.
- **Settings TUI gained a real audio device picker**
  (`scripts/settings_tui.py`): cycle through actually-discovered,
  deduped input/output devices (the same logic `python
  scripts/audio_devices.py --setup` uses) instead of typing a device
  name blind, plus an in-TUI test that plays a real tone and reports a
  real mic level reading.
- **The onset of an utterance is no longer clipped.** `UtteranceSegmenter`
  already padded the trailing silence of an utterance to avoid cutting
  off the last phoneme; it now pads the START the same way, so the
  first phoneme of a phrase — including the safeword — isn't lost while
  the VAD is still building confidence to trigger.
- **Kokoro (Apache 2.0) shipped as the default TTS engine, 2026-07-24**
  (PRs #141, #144) — the second TTS engine this section previously
  listed as "on the roadmap" (see the 2026-07-12 entry below) is done,
  not pending. Piper moved to an explicit opt-in extra
  (`uv sync --extra piper`) rather than a main dependency, resolving the
  GPL-encumbrance concern `DEPENDENCY_LICENSE_AUDIT.md` raised — a
  default ConvoBox install/distribution is now cleanly MIT. Also
  shipped: a real per-voice picker in the Settings TUI (cycles the 54
  actual voices in the downloaded voices file, not free text), per-engine
  profile memory (switching `tts.engine` no longer loses the other
  engine's settings), a side-by-side Kokoro/Piper compare action (`[c]`,
  speaks the same test phrase through both so you can actually hear the
  difference — the existing `[t]` test never played anything, only
  confirmed synthesis succeeded), and a forced voice-refresh action
  (`[d]`, for when kokoro-onnx's upstream release adds voices to an
  already-downloaded file). Real end-to-end testing against the actual
  model files (not mocks) also found and fixed a genuine bug upstream in
  `kokoro-onnx` itself: a single unpunctuated run of text exceeding the
  model's ~510-phoneme batch limit could hang synthesis forever (a
  detached background task dying silently, confirmed via 0% CPU for
  10+ minutes) — now recovers with a bounded timeout instead. **Live
  voice session with real speakers: done, 2026-08-10** (macOS/Apple
  Silicon, see the "Since 0.3.1" macOS pass above) — auto-downloaded
  its voice asset and played correctly through real hardware; not yet
  independently confirmed on Windows/Linux. Individual Kokoro voice
  files' own licenses still haven't been independently re-checked the
  way Piper's were.
- **A local web UI, 2026-07-25/26** (`--web` / `web.enabled`, opt-in,
  off by default) — see `docs/WEB-UI-ARCHITECTURE.md` for the full
  design and build history, `docs/WEB-UI-USAGE.md`/`WEB-UI-DEV.md` for
  the user/contributor-facing versions. A browser view of a live
  session (transcripts, backend responses, tool calls, pending
  approvals) streamed over SSE to every connected tab, plus optional
  SQLite-persisted history (`web.history_tracking_enabled`, a
  deliberately separate opt-in from `enabled` — viewing a live session
  and writing it to disk are different privacy decisions). Built and
  live-verified across six independent slices: config schema, SQLite
  storage, a FastAPI app (REST + SSE), wiring into `Orchestrator`'s
  existing `on_event` hook (found and fixed a real gap here: the first
  wiring pass only forwarded backend events, not the user's own
  transcripts — a captured demo session showed replies with no visible
  prompt until that was fixed), a real `uvicorn` server started
  alongside the voice loop, and a dependency-free HTML/JS frontend
  (no React/Vite — deliberately minimal per the design doc's own
  guidance). `fastapi`/`uvicorn` are the new `web` extra, lazily
  imported only when `web.enabled` — no dependency cost for anyone who
  doesn't use it. Not yet built: approving/denying a tool call from the
  browser (voice/TUI remain the only channels — this needs a real
  design decision about how a browser decision should interact with a
  simultaneous voice answer, not just an endpoint) and any
  remote-access/authentication story (loopback-only by design; a
  `bind_address` validator rejects a specific non-loopback address,
  though `0.0.0.0` is still allowed as an explicit choice).

Fully wired and config-driven, all with real-pipeline verification where
a live microphone session was possible; several items (the TUI's full
utterance-to-response render cycle, response tiering's spoken "continue"
reply, the `patient` preset's queue-and-deliver behavior) are unit- and
integration-tested but still need a live-mic UAT pass — see
[UAT-checklist.md](UAT-checklist.md)'s Conversation TUI, Response tiering, and
Barge-in sections for the specific checklist items (named, not numbered,
there on purpose -- section numbers have already drifted once as new
sections were added).

## Claude Code permission mode

Headless (`--print`) mode has no way to answer a tool-permission prompt at
runtime — a gated tool call would hang the session forever with no signal
(see `src/convobox/adapters/claude_code.py`'s module docstring for the
live-probed root cause). ConvoBox therefore defaults Claude Code to
`--permission-mode plan`: it can read, explore, and explain, but never
edit files or run commands on its own. For full write/execute access, set
your own `--permission-mode bypassPermissions` (or the equivalent
`--dangerously-skip-permissions`) in `backend.command` —
**this bypasses every permission check**, which is risky on a
voice-driven channel (misheard words, no per-action confirmation yet);
only use it in a context you'd trust an unsupervised agent with. An
explicit `--permission-mode` you set always wins over ConvoBox's default.
Per-action voice approval is on the roadmap ([ROADMAP.md](ROADMAP.md)'s
"Safety tiers for destructive actions").

## Progress log

**As of 2026-07-12, the full voice loop runs end to end**
(`scripts/run_convobox.py`: mic → VAD → local STT → orchestrator → backend
adapter → streaming Piper TTS → playback), verified live on Windows across
many conversation rounds. All three backend adapters are implemented and
verified against live instances (OpenCode, Claude Code, Codex). Streaming
TTS (audio starts on the first synthesized sentence), acoustic echo
cancellation (optional `[aec]` extra, WebRTC AEC3), open barge-in
(`interaction.interrupt_preset`, defaults to `do-not-disturb` -- off), a
single-instance mic lock, and a documented, validated `convobox.yaml`
(see `convobox.example.yaml` and [QUICKSTART.md](QUICKSTART.md)) are all
in. ~500 automated tests, mypy/ruff/bandit clean. A Settings TUI
(`scripts/settings_tui.py`, config editing) and a live conversation TUI
(`--tui`, see the "Since 0.2.0" section above) are both shipped, not
roadmap items anymore. Still open: Linux/macOS aren't validated yet, and
a second TTS/STT engine (Kokoro) is on the roadmap ([ROADMAP.md](ROADMAP.md))
-- kept as-written for history; Kokoro has since shipped as the default
TTS engine, 2026-07-24, see the "Since 0.2.0" section above.

The rest of this section is the earlier progress log, kept for history.

Scaffolding stage — an initial implementation of every pipeline stage
exists (`src/convobox/`: audio capture/playback, VAD segmenter, local STT,
safeword detector, TTS + Piper engine (streaming), an orchestrator, and an
OpenCode adapter), plus a first real end-to-end validation:
`scripts/roundtrip_smoketest.py` runs text → Piper TTS → faster-whisper STT
with no mic involved, and `scripts/spike.py` is the originally-planned
mic → VAD → local STT → logged-transcript spike. The orchestrator now
drives TTS itself — a backend TEXT event is stripped of code
(`strip_code_for_speech`) and spoken via whatever `TTSEngine`/`AudioPlayer`
it was constructed with (both optional; omitting them keeps the
routing-only behavior from before), fired as a background task so a slow
synthesis doesn't stall draining the next backend event, and a hard stop
now also stops in-progress TTS/playback. 98 automated tests pass
(`pytest tests/`), mypy is clean across the tree -- kept as-written for
history; this meant CI's own `mypy src/convobox` invocation, which never
actually covered `scripts/` at all until the 2026-08-08 review's D1 fix,
not literally the whole repo -- and `scripts/spike.py`'s
own async wiring (not just its components) has been run end-to-end with a
faked mic feed of real synthesized speech. Playback has also now run
against real speaker hardware, not just a mocked `OutputStream` — including
barge-in genuinely cutting off in-progress audio (see
[../TESTING.md](../TESTING.md) for the measured stop-latency number).

**Windows is now verified end to end** (2026-07-09, Windows 11: full
suite, mypy, TTS/STT round trip, both smoke tests, real speaker playback
with 240ms barge-in stop latency), and that run also closed the last
hardware gap on any platform: **live microphone capture through
`scripts/spike.py` works**, including a real spoken-safeword exit. The
same session produced a set of pipeline improvements now in the tree: an
empty-transcript guard in the orchestrator (background noise can
VAD-trigger and transcribe to nothing; that must never reach the backend
as an empty command), a `vad.max_utterance_s` cap (continuous speech
otherwise buffers unboundedly and yields no transcript until the speaker
pauses), an `stt.min_language_probability` confidence gate (auto language
detection hallucinates below ~0.4 on accented or ambiguous audio; the
safeword is always checked before the gate so a quality filter can never
swallow a hard stop), and `scripts/voice_tui.py`, a stdlib-only live
dashboard showing input level, capture state, and a per-utterance clarity
verdict (see [../TESTING.md](../TESTING.md) → "Live clarity dashboard").
`LanguageTracker` followed from further live testing: it flags when an
utterance's detected language breaks from the session's established one,
without ever pinning what language STT is asked to assume — auto-detect
stays real auto-detect always, since pinning was tried and found worse
(it decodes non-matching speech as confident-sounding nonsense in the
pinned language rather than surfacing the mismatch).

`TTSConfig.voice`/`rate`/`volume` are wired up now too — every script
constructed `PiperTTSEngine` by hand with a hardcoded voice before;
`convobox.tts.create_tts_engine()` is the missing factory, and 98 tests
pass with it in place. `scripts/voice_picker.py` browses, downloads, and
auditions any of Piper's 163 voices (44 languages) through real speakers,
interactively or via flags, and prints the `convobox.yaml` snippet for
whichever one you land on; `scripts/roundtrip_smoketest.py --voice KEY`
runs the same TTS→STT intelligibility check as before against any
installed voice, not just the original hardcoded one. See
[../TESTING.md](../TESTING.md) → "Picking a voice". Linux hasn't been attempted
at all.
(At that 2026-07-09 point nothing was stable — no Claude Code/Codex
adapters yet, config not threaded through a CLI, and the orchestrator→TTS
wiring used `synthesize()` (whole-utterance) rather than streaming. All
three have since been implemented; see the current-status summary at the
top of this document.)

## Security + performance audit

A security + performance pass (8 independent finder angles, each claim
verified against the actual code before acting) found and fixed 7 real
bugs — worth knowing about even though they're fixed, since a couple were
subtle:

- **VAD could hang indefinitely.** `UtteranceSegmenter`'s hysteresis band
  (`[threshold-0.15, threshold)`, ambiguous — neither confidently speech
  nor silence) was treated as speech, resetting the silence timer on every
  ambiguous frame. A speaker trailing off gradually, or noise sitting near
  threshold, could keep an utterance open forever — it would only end via
  an external `flush()`, never the segmenter's own silence detection.
- **`OpenCodeAdapter.is_busy()` could latch `True` forever.** It was only
  ever cleared inside `events()` on an observed DONE/ERROR — a dropped
  connection, an exception, or the consumer simply not running left every
  later transcript silently routed to `send_interject` instead of
  `send_text`, with no error surfaced. Now cleared on any exit from
  `events()`, and `Orchestrator.handle_transcript` starts the event-drain
  loop itself instead of relying on a caller to remember a separate wiring
  step.
- **A safeword phrase could silently do nothing.** A configured hard-stop
  phrase that normalizes to an empty string (pure punctuation, etc.) was
  dropped with no warning — an operator could believe their abort word was
  active when it wasn't. Now raises at construction instead.
- **TTS buffered the entire response before returning any audio.**
  `PiperTTSEngine` collected every chunk into a list before returning —
  full synthesis time was added to time-to-first-audio. Now streams
  (`synthesize_stream`, bridging piper's blocking generator through a
  background thread, same pattern as `MicrophoneStream`); measured ~11x
  improvement in time-to-first-audio on a 20-sentence passage (143ms vs.
  1574ms total). `synthesize()` still exists as a concatenating
  convenience on top of the stream.
- **A misconfigured backend URL could silently bypass the plaintext-HTTP
  warning.** A schemeless `"host:port"` URL makes `urlparse` mistake the
  host for the scheme, so the `scheme == "http"` check never fired —
  confirmed both that this parse behavior is real and that `httpx` accepts
  such a URL without complaint. Now warns on any non-http/https scheme too.
- **`MicrophoneStream.read()` and `.stream()` disagreed on end-of-stream.**
  After `close()`, `.stream()`'s async generator ended cleanly but `.read()`
  raised `RuntimeError` — and since it re-enqueues the close-sentinel before
  raising, every call after `close()` raises again rather than reaching a
  quiet terminal state. Both now documented/behave consistently (clean
  return for the async path, an explicit `RuntimeError` for the sync path
  — a deliberate difference, not an oversight, since a sync consumer can't
  just "stop iterating" the way an async-for can).
- Two small cleanups: an unused `MicrophoneStream.chunks()` method and a
  redundant `OpenCodeAdapter._sse_source` instance field (only ever used
  immediately after assignment) were removed.

One finding came back **PLAUSIBLE rather than cleanly refuted**, and an
earlier draft of this document overstated it as refuted — corrected here:
whether a real audio chunk could land in the queue *after*
`MicrophoneStream.close()`'s sentinel (because `_callback` has no lock
against `close()`) rests entirely on `sounddevice`/PortAudio's documented
guarantee that `stop()` blocks until pending callbacks finish — a
guarantee this code trusts but does not itself enforce with any lock or
flag. If that external contract ever doesn't hold, a stray chunk could be
stranded behind the sentinel (harmless — it's just never read, not a
correctness hazard beyond that). Not fixed: adding internal synchronization
to guard against a well-established, actively-relied-upon PortAudio
guarantee breaking would be defending against a scenario with no evidence
it occurs, at the cost of real complexity.

**Confirmed but deliberately not fixed, low practical impact:**
`UtteranceSegmenter` runs Silero inference on every 32ms window regardless
of triggered state (verified: `_process_window`'s model call happens before
the triggered check) — but this is inherent to how VAD works, not
avoidable waste: the model has to run continuously to detect speech onset
in the first place, and Silero's per-window cost is small enough that it
hasn't shown up as a bottleneck in any measurement so far. Separately, the
`np.concatenate` of ~32ms window slices at utterance end happens
synchronously on the STT hand-off path — real, but the absolute data size
involved (hundreds of KB for a several-second utterance) makes this a
sub-millisecond operation, not a meaningful latency contributor next to
STT's ~150–200ms. Worth revisiting with actual profiling data if latency
ever becomes a measured problem, not worth speculatively optimizing now.

Known, deliberately deferred (not wrong, just lower-value-per-effort right
now): `AudioPlayer.play()` opens a fresh `OutputStream` per call instead of
reusing one — real but modest overhead (tens of ms device-open latency per
spoken response, not a hot per-window cost), and fixing it would require
reworking a test suite that deliberately asserts today's open/close-per-call
contract. Revisit once real latency numbers from the now-wired
orchestrator→TTS path are available to justify the rework.
