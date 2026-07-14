# UAT checklist

Per-subsystem live-testing matrix for the full voice loop. Each item
names the module that implements the behavior so a pass/fail pins to a
place. Derived from agent-assisted code review during the 2026-07-11
Windows UAT, corrected and extended after log analysis (see
DESIGN-echo-and-barge-in.md for the design rationale behind items
marked as deliberate behavior).

Additions from the 2026-07-11 live log:

- **[E6] Whisper hallucination loops on far-field echo.** Observed live
  (one transcript repeated a clause five times). Currently caught by the
  overlap window; any future gate reordering must keep these out.
- **[N5] Numbered lists keep their numbers** -- deliberate: spoken
  enumeration is natural, unlike asterisks.
- Echo layers' live scorecard: overlap window caught ~30 echo utterances
  with zero false drops and zero echo reaching the backend; the text
  filter never had to fire (it remains the backstop).

---
## 1. Echo / half-duplex overlap handling

Implements in `scripts/run_convobox.py`: `SpokenEchoFilter`, `EchoAwarePlayer`,
`utterance_overlapped_playback()`, and the drop branch in the main loop.

- **[E1] Same-room echo arriving AFTER playback ends.** Speak a command right
  as the assistant finishes. The overlap window (`ECHO_GRACE_S = 0.3` plus the
  math in `utterance_overlapped_playback`) must catch echo that lands just
  after `playback_ended_at`. Confirm such a transcript is dropped with the log
  `"dropped (overlapped response playback ...)"` rather than looped back.
- **[E2] Real short confirmation is NOT dropped.** `SpokenEchoFilter.MIN_TOKENS
  = 3`: a genuine `"yes run it"` (3 tokens) that happens to appear in the
  spoken response could be falsely flagged as echo. Craft a response whose
  wording contains a likely short reply, then say that reply, and confirm it is
  forwarded (not swallowed). This is the explicit false-positive risk in the
  filter's docstring.
- **[E3] Token-overlap threshold.** `OVERLAP_THRESHOLD = 0.7`: partial overlap
  (<70% of the transcript's words) should pass; >=70% should drop. Test with a
  transcript that shares most-but-not-all words with a recent response.
- **[E4] Echo filter age bound.** `MAX_AGE_S = 30.0`: after 30s the spoken
  history is ignored, so old responses must no longer cause drops. Speak a
  phrase identical to something said >30s ago and confirm it is NOT dropped.
- **[E5] Mute mode disables echo-drop by design.** `--mute` uses `MutePlayer`
  (is_playing always False, `playback_ended_at` stays 0). Echo/overlap
  suppression is therefore OFF in `--mute`. UAT echo behavior MUST be run with
  speakers on; `--mute` runs validate the non-audio path only.

## 2. VAD segmentation

Implements in `src/convobox/vad/segmenter.py`. Config: `threshold=0.5`,
`min_silence_ms=500`, `min_speech_ms=250`, `max_utterance_s=None` (uncapped).

- **[V1] Short utterance floor.** Speech shorter than `min_speech_ms=250` is
  discarded as noise. Test a very short command (e.g. "go", "no") and confirm
  it may be dropped — decide if that's acceptable for UAT or needs lowering.
- **[V2] Inter-utterance pause.** `min_silence_ms=500`: two phrases separated
  by >500ms silence must become two utterances; <500ms must merge. Time the
  pauses.
- **[V3] Uncapped utterance (current config default).** `max_utterance_s=None`
  means a long uninterrupted monologue yields NO transcript until the speaker
  pauses (observed live as a 30.5s single utterance). UAT a 30s+ monologue and
  confirm the transcript only arrives at the end. If real use needs mid-speech
  transcripts, set `max_utterance_s` (e.g. 20) and re-test that it force-emits.
- **[V4] `in_speech` signal** (exposed for UIs / future barge-in) flips True on
  first speech window and back to False at utterance end. Verify with a harness
  if a listening indicator depends on it.

## 3. Safeword / hard stop

Implements in `src/convobox/safeword/detector.py` + `orchestrator.py:50-57`.
Config phrases: `stop stop stop`, `break break break`.

- **[S1] Hard stop mid-playback.** While the assistant is speaking, say the
  safeword. Confirm playback stops IMMEDIATELY (`player.stop()` +
  `tts.stop()` + `send_hard_stop()`), and the app stays listening (safeword does
  NOT exit, per the run_convobox.py docstring).
- **[S2] Safeword cannot be swallowed.** The check runs on the RAW transcript
  before the language-probability gate and before the echo/overlap drop. Test
  a hard stop phrased with low-confidence/garbled audio (e.g. accented, quiet)
  and confirm it still fires.
- **[S3] Substring / boundary matching.** Detector pads with spaces
  (`" stop stop stop "` in `" ... stop stop stop"`), so the phrase at the
  start/end of an utterance still matches. Test "... please stop stop stop" and
  "stop stop stop now".
- **[S4] Empty-phrase guard at startup.** A configured phrase that normalizes to
  nothing (pure punctuation) must raise `ValueError` at construction. Negative
  test: set `hard_stop_phrases: ["!!!"]` and confirm a loud startup failure.
- **[S5] Hard stop while idle.** Saying the safeword when the backend is not
  busy should be a safe no-op (OpenCode's interrupt is documented idle-no-op).
  Confirm no error and continued listening.

## 4. Busy / interject routing

Implements in `orchestrator.py:handle_transcript` + adapters.

- **[B1] Talk while busy → interject, not new turn.** With the backend mid-
  response, speak a command. Confirm `send_interject` is used (routed via
  `is_busy()`) rather than `send_text`. Verify on the chosen backend --
  CORRECTED (the endpoints originally listed here were the hard-stop
  calls, not interjects): opencode interject = `POST .../prompt` with
  `delivery: "steer"`; claude-code interject = a queued user message (no
  true steering on that backend); codex interject = `turn/steer`.
- **[B2] Talk while idle → new turn.** Confirm `send_text` path.
- **[B3] Interject blocked by overlap drop.** Because ordinary speech during
  playback is dropped (half-duplex), an interject only fires AFTER playback
  ends. Confirm a command spoken during playback is NOT forwarded as an
  interject, and that the same command spoken after playback IS.
- **[B4] `wait_listening()` ordering.** `handle_transcript` awaits
  `adapter.wait_listening()` before routing (except on hard stop). Confirm a
  command issued immediately after first send is not lost to unsubscribed SSE
  events.

## 5. Speech normalization (separate file: speechnormalization.md)

- **[N1] Asterisks not spoken** — `**bold**`, `*italic*`, `* bullet` stripped.
- **[N2] Slashes preserved** — `path/to/file` spoken as-is (per UAT decision).
- **[N3] Code blocks not spoken** — fenced ``` and inline `code` already
  stripped; confirm a long code block produces no speech.
- **[N4] `snake_case` / identifiers preserved** — no spurious stripping.

## 6. TTS config & playback

Implements in `src/convobox/tts/piper.py`, `audio/playback.py`.

- **[T1] Rate/volume apply only when != default.** `SynthesisConfig` is built
  only if `rate != 1.0` or `volume != 1.0`. Current config is 1.0/1.0, so
  `syn_config=None` → voice default. Set `rate: 1.5` and confirm faster output;
  set `volume: 0.5` and confirm quieter.
- **[T2] Streamed first-audio latency.** `play_stream` starts audio on the
  first chunk. Measure time-to-first-audio for a long response; confirm it's
  ~one sentence, not the whole response.
- **[T3] Stop mid-stream.** Hard stop / barge-in calls `player.stop()` which
  joins the playback thread. Confirm no audio after stop and no thread leak
  (check `is_playing()` returns False promptly).
- **[T4] Replacing playback.** Calling play/play_stream while something is
  playing must replace it cleanly (AudioPlayer.play calls stop() first). Test
  rapid successive responses.

## 7. Scriptable / non-mic modes

- **[M1] `--text` single-shot.** `python scripts/run_convobox.py --text "run
  the tests"` exercises Orchestrator + backend + TTS with no mic. Confirm it
  responds, drains until idle, waits for playback, and exits.
- **[M2] `--text --mute`** confirms the no-speaker path.
- **[M3] Device resolution.** `--device N` numeric → int device; name → string.
  Test an invalid device fails gracefully (not a hang).

## 8. Cross-cutting

- **[X1] Ctrl+C cleanup.** Confirm `stop_event_loop()` cancels `_speak_task`
  and `_events_task` and the process exits cleanly (no orphaned threads /
  backend sessions).
- **[X2] Config defaults vs file.** With no `convobox.yaml`, `load_config`
  returns `AppConfig()` defaults; with the file, all sections load. Confirm
  `language` unset → detection active (language_probability gate meaningful);
  pinned language → probability 1.0 (gate inert).

---

### Suggested UAT matrix ordering
1. Happy path: idle → speak → response spoken (N1-N4, T2).
2. Hard stop safety: S1-S5.
3. Echo / half-duplex: E1-E5 (speakers ON).
4. Barge-in gap: B3 (document current "drop, don't cut" behavior; see
   bargein_suggestions.md for the fix).
5. Edge VAD: V1-V3.
6. Scriptable/cleanup: M1-M3, X1-X2.
7. Settings UI: see [UAT-settings-tui.md](UAT-settings-tui.md).
8. Pause/resume listening: P1-P8 (P5 is the one most likely to reveal a
   priority-ordering bug -- do not skip it).

---

---

## Operational gotchas (from live UAT incidents)

- **[O1] Exactly one runner instance -- but COUNT CORRECTLY.**
  CORRECTED DIAGNOSIS (late 2026-07-11): on Windows, a uv-created
  venv's `.venv\Scripts\python.exe` is a launcher trampoline that
  spawns the real interpreter (the uv-managed base python) as a CHILD
  process. **One launch therefore always shows as TWO python processes**
  -- an idle parent and a busy worker -- and both match a command-line
  grep for run_convobox. The 2026-07-11 "double-launch incidents" were
  this pair misread as duplicates (verified by ParentProcessId: the
  "second instance" was the first one's child). Count LOGICAL instances:
  `Get-CimInstance Win32_Process | ? { $_.CommandLine -match "run_convobox" } |
   Select ProcessId, ParentProcessId` -- a parent-child pair is ONE
  instance; two processes with unrelated parents are two.
  True duplicates are still harmful (mic contention, split
  conversation), and since the second same-evening scare, mic mode
  takes a single-instance lock (localhost port bind, auto-released on
  any kind of process death): a genuine duplicate exits immediately
  with an explanatory error. The startup banner now logs its PID and
  lock acquisition so the log itself disambiguates.
- **[O2] Output device pinning.** `audio.output_device` unset means the
  system default output, which on a multi-device Windows box (onboard
  Realtek headphone/speaker endpoints, monitor audio, VR headset
  virtual devices) may not be where the user is listening. If a single
  clean instance is silent, pin `audio.output_device` in convobox.yaml
  to the device actually wired to the speakers.
- **[O3] "Two opencode instances" is usually one.** `opencode serve`
  runs as a launcher process plus the server it spawns -- two PIDs, one
  server. Verify by port, not by process count.

## Barge-in items (interrupt_preset != "do-not-disturb"/"halt"; requires AEC or headphones)

- **[G1] Sustained speech during playback stops audio** within
  ~barge_in_min_speech_ms + one chunk (preset `conversational` or
  `take-over`); the utterance is forwarded with the interruption marker
  and `[BARGE-IN]` in its transcript log line.
- **[G2] Cough test.** Sub-threshold noise bursts during playback must
  NOT stop audio (the monitor resets between speech episodes).
- **[G3] Echo-triggered barge-in is contained.** If self-echo trips the
  barge-in (AEC not converged), the utterance matches the spoken-text
  filter and is dropped with a WARNING log -- playback stops (annoying)
  but the echo is never forwarded to the backend (safe). Persistent
  occurrences mean AEC needs tuning or interrupt_preset should be
  "do-not-disturb".
- **[G4] `halt`/`take-over` presets** also interrupt the backend turn
  (safeword-equivalent) -- verify against each backend.
- **[G5] Marker delivery.** The forwarded barge-in text carries
  BARGE_IN_MARKER so the backend knows its response wasn't fully heard
  ("the truncation problem", DESIGN-echo-and-barge-in.md).
- **[G6] `patient` preset queues, doesn't drop or deliver immediately.**
  Talk over a response under preset `patient`: audio keeps playing
  (`on_current_turn: let-finish`, unlike G1); the utterance is neither
  forwarded immediately nor silently dropped -- once the response is
  FULLY done (backend idle AND audio finished), the queued utterance is
  delivered automatically (log line: "delivering queued interjection now
  that the turn is idle"). Say a second thing while still queued before
  the first flushes: only the most recent one should be delivered
  (most-recent-wins, not both) -- log line: "queued interjection replaced
  by a newer one".

## Pause/resume listening (docs/DESIGN-barge-in.md, "Pause/resume listening")

- **[P1] Pause hard-stops in-flight work.** While the backend is actively
  responding (mid-playback or mid-tool-call), say "stop listening" --
  playback stops immediately, `is_busy()` drops, and the log shows
  "paused listening (matched...)". No spoken response to "stop listening"
  itself is ever heard.
- **[P2] Pause while idle.** Say "stop listening" with nothing running --
  no crash, no spoken response, log shows the pause; the hard-stop calls are
  effectively no-ops (same as the safeword's own idle no-op).
- **[P3] Ordinary speech is dropped while paused.** While paused, say a
  normal command ("what time is it", "run the tests") -- NOT routed to the
  backend (no new HTTP/subprocess request; `is_busy()` never flips true),
  logged at debug as "dropped (paused, not the wake word)".
- **[P4] Wake word resumes.** While paused, say the configured wake word
  (default "ConvoBox") -- log shows "resumed listening (wake word
  matched)"; the NEXT ordinary utterance after that routes normally again.
- **[P5] Safeword still works while paused, but does NOT resume.** While
  paused, say "stop stop stop" -- the `[HARD STOP]` path still fires
  (matters if something got started right as pause was requested / a race).
  Critically: verify ConvoBox is STILL paused afterward -- only the wake
  word should resume it, confirming pause/hard-stop are the orthogonal axes
  the design calls for, not the same thing.
- **[P6] The pause phrase is inert while already paused.** While paused,
  say "stop listening" (or "pause listening") again -- treated as ordinary
  ignored speech per P3, not a special case; still requires the wake word
  to exit.
- **[P7] Custom wake_word / pause_listening_phrases via config.** Set
  non-default values in convobox.yaml (or the Settings TUI once it exposes
  these fields) and confirm the whole P1-P6 cycle still works end-to-end,
  not just the unit-tested detector classes in isolation.
- **[P8] Resume acknowledgment (open question).** Currently silent on
  resume -- no tone/spoken confirmation. Note whether this feels
  unnervingly silent in practice; see DESIGN-barge-in.md's open question on
  this.

## 9. Conversation TUI (`--tui`, `src/convobox/tui/`)

Only startup/idle/shutdown against a real backend+mic is automation-
verified so far (no scripted way to "speak" into this loop) -- this
section is the live-mic pass that closes the gap.

- **[U1] A real spoken utterance appears in the transcript pane** as a
  "you:" turn, and the assistant's response appears as an "assistant:"
  turn once it arrives -- confirms the `Orchestrator.on_event` wiring
  actually threads real backend text through, not just the placeholder
  states already verified.
- **[U2] Full-detail pane shows the untruncated response**, and clears
  when the NEXT utterance starts a fresh turn (not accumulating across
  unrelated turns, not blanking on a gate-dropped/echo utterance that
  never reaches the backend).
- **[U3] Status label tracks reality closely, not frame-perfectly.**
  Watch it cycle through listening/capturing/working/speaking/paused
  during a real conversation. Since it's derived from the existing 1s
  watchdog poll (not threaded through every call site), very brief states
  may be skipped -- note whether that reads as "a little laggy" (expected,
  documented) or "wrong" (a real bug) in practice.
- **[U4] Barge-in flag appears/clears correctly** during a real barge-in
  (requires a non-`none` `interaction.interrupt_mode` + AEC or
  headphones).
- **[U5] Log output doesn't corrupt the display.** Confirm ordinary log
  lines (info/debug) never appear inside the alt-screen while `--tui` is
  active -- they should be going to `convobox-tui.log` instead. Tail that
  file during the session to confirm nothing is silently lost.
- **[U6] Clean exit restores the terminal.** Ctrl+C during a `--tui`
  session must leave the terminal in its normal (non-alt-screen, cursor
  visible) state afterward -- no leftover garbled screen requiring a
  manual `reset`/`cls`.

## 10. Response tiering (`interaction.tier_responses: true`)

Only the `Orchestrator`-level tiering logic is automation-verified so far
(a real multi-paragraph response through a real backend, confirmed
correctly speaking paragraph 1 first and delivering paragraph 2 via
`speak_more()`) -- the watchdog-trigger + main-loop `ContinuePromptGate`
wiring is unit-tested at the pure-logic level only. This section is the
live-mic pass that closes that gap.

- **[R1] A multi-paragraph response speaks only the first paragraph**,
  then goes quiet -- confirm nothing extra is spoken automatically.
- **[R2] Saying "continue" (or "go on"/a bare "yes") within
  `continue_timeout_s` speaks the rest** of the already-received
  response, with no perceptible round-trip delay to the backend (it's
  already in hand -- this should feel instant, not like a fresh request).
- **[R3] Silence past `continue_timeout_s` implies "no"** -- say nothing
  after a tiered response and confirm ConvoBox does NOT prompt again,
  re-speak, or otherwise nag; it should simply go back to normal
  listening.
- **[R4] Saying something unrelated instead of continue/decline is
  forwarded normally**, not dropped and not misread as either outcome --
  e.g. after a tiered response, say a completely different command and
  confirm it's treated as a fresh instruction, not swallowed by the
  continue-prompt gate.
- **[R5] A single-paragraph response never triggers the prompt at all**
  -- `has_more_to_reveal()` is `False` immediately for a short reply, so
  there should be no wait, no timeout, no "say continue for more"
  anywhere in the logs.
- **[R6] Barge-in still works normally during/after a tiered response**
  -- the continue-prompt gate and barge-in are independent axes; talking
  over the FIRST paragraph while it's still playing should barge in as
  usual, not get misrouted through the continue-prompt logic (which only
  activates once playback has already ended).
- **[R7] `continue_timeout_s` tuning.** Default is 2.5s (the 1-4s range
  from the design doc, not yet live-tuned). Note whether it feels laggy
  (too long) or naggy/cut-off (too short) in practice; adjust the config
  default if a clear preference emerges.

## 11. STT native-allocator recovery (`src/convobox/stt/transcriber.py`, PR #65)

Implements in `LocalTranscriber.transcribe()`. Mitigates a known, unresolved
upstream ctranslate2/faster-whisper issue (`SYSTRAN/faster-whisper#660`,
`#390`): the native (MKL on Windows) allocator can fail after enough
repeated `transcribe()` calls in one long-lived process. Live-confirmed
2026-07-14, crashing a real session at ~13 minutes / ~20 transcriptions --
see `docs/KNOWN-ISSUES.md` for the full writeup. `tests/test_transcriber.py`
verifies the recovery logic against a fake model that fails on command --
the real native failure can't be triggered deterministically, so a live
long session is the only way to confirm the recovery actually fires
correctly when the real bug recurs.

- **[ST1] Long-session survival.** Run a real mic session for 20+ minutes
  with regular speech (aim for 30+ transcriptions -- roughly 50% more than
  the ~20 that crashed the pre-fix session, to have margin). Confirm either
  (a) no failure occurs at all, or (b) if the log shows `"faster-whisper
  native transcribe() failure -- reloading the STT model..."`, the app does
  **not** crash: it logs the warning, keeps listening, and the very next
  utterance transcribes normally again.
- **[ST2] A recovered failure doesn't corrupt state.** If [ST1]'s failure
  case fires, confirm the failed utterance is silently dropped the same way
  an ordinary low-confidence transcript is (check for the immediately-
  following `"dropped low-confidence transcript=''"` line) -- not forwarded
  to the backend as an empty command, and not leaving any gate
  (barge-in/pause/continue-prompt) stuck waiting.
- **[ST3] Reload preserves configured STT settings.** After a recovery,
  confirm subsequent transcriptions still use the same `stt.model`/
  `stt.language`/etc. as before -- the reload rebuilds from the original
  `STTConfig` via `model_factory`, not a fresh-defaults model.
