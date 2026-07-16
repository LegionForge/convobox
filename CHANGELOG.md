# Changelog

All notable changes to ConvoBox are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0, so
minor versions carry feature and behavior changes.

## [Unreleased]

> **Attribution:** Changes in this Unreleased section were authored by the
> **ConvoBox** AI coding agent during live audio UAT on 2026-07-14/15
> (submitted via the `jp-cruz` account, PR #78). ConvoBox is the product
> under test; its own agent made these modifications. The agent was observed
> running on opencode's `hy3-free` model (OpenCode Zen provider) — verified
> from the live backend's session records, not assumed. See `docs/UAT-checklist.md`
> **[L2]**.

### Added
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

### Fixed
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
