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
- **[L1] Agent replies are now in the log.** Live-confirmed gap during the
  2026-07-14 audio UAT: the `on_event` hook forwarded backend replies
  straight to TTS and logged none of them (and was only wired up under
  `--tui`, so a plain listening session never observed replies at all).
  Fixed in `scripts/run_convobox.py`: every backend TEXT reply is now
  logged as `response: <raw text>`, plus `response(spoken): <spoken text>`
  when the spoken form (`strip_code_for_speech`) differs from the raw
  reply. UAT: confirm that, in a plain (non-`--tui`) listening session,
  each assistant turn produces a `response:` log line, and that a reply
  containing markdown (e.g. `**bold**` / `` `code` ``) also emits a
  `response(spoken):` line with the decoration stripped. The hook is now
  installed unconditionally regardless of `--tui`.
- **[N5] Numbered lists keep their numbers** -- deliberate: spoken
  enumeration is natural, unlike asterisks.
- **[L2] Runtime stack is opencode + `hy3-free` (OpenCode Zen).** Verified
  live during the 2026-07-14/15 audio UAT by reading the opencode server's
  own session message records (two separate sessions, 35 and 7 assistant
  messages respectively -- 100% `model.id=hy3-free`, `providerID=opencode`).
  The provider list from the live server shows `opencode` -> "OpenCode Zen"
  (`https://opencode.ai/zen/v1`, `apiKey: "public"`); the local
  `~/.config/opencode/opencode.json` pins **no** default model, so
  `hy3-free` is being used as opencode's built-in default public model, not
  an explicitly configured one. NOT verified: whether the user set this up
  intentionally, or available OpenCode Zen usage/quota. `convobox.yaml`
  only names `backend: opencode` (no model field). Recorded for UAT
  provenance; do not assert a deliberate model choice from this evidence
  alone.
- **[L3] Headset UAT: AEC is ON but has no echo to cancel -- turn it OFF for
  headsets.** Live-confirmed during the 2026-07-14/15 audio UAT with a
  headset (mic does not hear the speaker): 54 of 59 responses logged
  `NO ECHO DETECTED: barely any speaker sound is reaching the mic`, i.e. AEC
  had essentially nothing to cancel. With AEC still running
  (`echo_cancellation: true`), the operator reports audible artifacts
  ("artifacting from automatic echo cancellation") on the mic path. This
  same no-echo condition directly caused the spoken-echo filter to drop
  genuine barge-in speech on no-echo responses (see [L1] context: the
  "Yeah, you got it." barge-in was dropped as self-echo because
  `NO ECHO DETECTED` was misread as "this speech is our own echo"). For
  headset use, AEC should be OFF -- it has nothing to cancel and risks
  artifacts plus dropped real barge-ins. AEC remains valuable for
  open-speaker/laptop use. Not yet changed in code; recorded for assessment.
- **[L4] Heartbeat coloring for the silent-busy indicator.** Live-confirmed
  gap during the same 2026-07-14/15 headset UAT, continued into the
  overnight session: the "backend still working" heartbeat (`WorkingIndicator`)
  is the only feedback during a silent-busy stretch, but it's log-only --
  invisible when interacting through a backend's own chat UI rather than
  watching this terminal, so a long stall (one observed run: 618s / over
  10 minutes) reads as "is it broken?" rather than "still thinking." Fixed
  in `scripts/run_convobox.py`: the SAME log line is now color-coded
  (green < 10s, yellow 10-60s, red > 60s) when connected to a real
  terminal (`sys.stderr.isatty()`, also correctly OFF for `--tui` mode's
  file-redirected log and for the UAT crib's own
  `2>&1 | Tee-Object -Append uat-echo.log` pattern -- piping makes
  `isatty()` false, so the diffable log file stays plain-text automatically,
  no separate "am I being redirected" check needed). UAT: run a session,
  provoke a long silent-busy stretch (a real multi-step tool-calling
  response works well), and confirm the heartbeat line visibly shifts
  green -> yellow -> red as it ages, in a real unpiped terminal; then
  confirm running under the `2>&1 | Tee-Object` crib pattern produces a
  plain, uncolored log file.
- **[L6] Headset barge-in test PASSED under AEC-off config (session #81,
  2026-07-15).** NOTE on provenance: [L3] is **recorded for assessment, not
  changed in code** (authored by jp-cruz, not applied). The running
  `convobox.yaml` is **untracked** and sets `echo_cancellation: false` with
  a comment citing [L3]'s reasoning (54/59 NO ECHO DETECTED) — so it likely
  reflects the [L3] recommendation, but this is an operator runtime choice,
  not a committed code change, and causation is inferred not proven.
  Under that config (`echo_cancellation: false` + headphones +
  `interrupt_preset: conversational`: `on_current_turn=mute`,
  `on_new_words=now`, `barge_in_min_speech_ms: 250`), a deliberate barge-in
  test produced 8 user speech events during interruption windows — every
  one transcribed AND forwarded to the backend (transcript lines each
  immediately followed by a `POST …/prompt`). Playback-mute barge-ins
  (`barge-in: sustained speech during playback -- stopping audio` →
  replacement utterance) at 01:57:40/43, 01:57:54/55, 01:58:49/51;
  while-busy (`busy=True`, `on_new_words=now`) barge-ins at 01:57:46,
  01:57:50, 01:57:53, 01:57:55, 01:58:51. **Zero `NO ECHO DETECTED` lines**
  in the whole test — the exact condition that armed the [L1] self-echo
  drop path never occurred, so the [L1] failure mode did NOT recur here.
  Marginal-confidence speech was still kept and tagged `[BARGE-IN]` ("Don't
  okay?" at dec=0.48, "just barged in." at dec=0.52) — the 250 ms min-speech
  threshold is not over-filtering. Scope: this confirms barge-in works under
  the AEC-off headset config; it is evidence *consistent with* [L3] but is
  NOT a validation that [L3] is correct (no AEC-on contrast test was run to
  confirm [L3]'s predicted artifact/drop downside actually recurs). [L1]
  regression is closed **for this AEC-off path only**. Non-blocking note: a
  ~54 s backend "still working" stretch (01:58:02→38) overlapped several
  barge-ins with no stacking problem — latency observation only.
- **[L7] AEC-on + headset contrast: [L3]'s predicted drop did NOT recur
  (session #82, 2026-07-15).** This is the "before" half of the [L3]
  before/after contrast (`docs/UAT-L3-contrast.md`, config
  `convobox.uat-aec-on.yaml`), finally run. With the headset and
  `audio.echo_cancellation: true` (forced via `settings_tui.py` into the
  default `convobox.yaml`, live session pid 27000 → `uat-echo3-aec-on.log`),
  AEC logged `NO ECHO DETECTED` on essentially every response (the exact
  [L3] "nothing to cancel" premise held — mic barely hears the speaker).
  **8/8 genuine *user* barge-ins were preserved and forwarded** (mix of
  mid-playback `barge-in: sustained speech during playback -- stopping
  audio`, tagged `[BARGE-IN]`, and while-busy `busy=True` / `on_new_words=now`,
  not tagged but still forwarded via `POST …/prompt`); lowest-decision-score
  utterances (dec=0.43, 0.46, 0.44, 0.37) survived — the 250 ms min-speech
  threshold is not over-filtering. **So [L3]'s specific predicted failure
  mode — AEC-on + headset dropping *genuine user* barge-ins as self-echo —
  did NOT occur.** However, the run was NOT drop-free: one real
  `dropped (` event fired (03:00:43) — `dropped (overlap gate,
  echo-cancellation active): 'AAC could be left on with a headset. We can
  try to AAC off as an option, but I think the default should be owned.'`
  That dropped text is a *mis-transcription of the assistant's own prior
  spoken response* (not user speech), caught by the overlap/echo gate as if
  it were echoed playback — i.e. the [L1] self-echo drop path DID re-arm
  here, but it dropped ConvoBox's own words, not the user's. This is the
  opposite of [L3]'s concern (which was about dropping the *user*), and is
  harmless to the conversation (it just suppresses a repeated spoken phrase),
  but it confirms the [L1] overlap-gate can misfire in the AEC-on/NO-ECHO
  regime — the exact mechanism [L3] flagged, just pointed at the wrong
  speaker. **Conclusion:** [L3]'s *user-barge-in* downside is **not
  reproduced** on this hardware/config, so "AEC OFF is required for headsets"
  is wrong — AEC (AAC) is a fine default and can stay ON. But [L3]'s root
  mechanism (NO-ECHO → overlap gate misfires) is alive; it bit the assistant's
  own speech rather than the user's this time. Resolution: **[L3] is
  overstated for the user-barge-in case, but its underlying drop mechanism is
  real and should be tracked** (see [L8]). The open-speaker caveat in [L3]
  still stands. Caveat: the *subjective* "mic artifacts" half of [L3] is
  operator-perceived only and was not re-raised during this run; if artifacts
  recur, reopen. Evidence artifacts: `uat-echo3-aec-on.log` (AEC-on) and
  `uat-echo2-aec-on.log`/`uat-echo.log` (the AEC-off baseline, session #81)
  — kept as diffable proof. The still-unexplained operator "artifacting"
  report from [L3] remains a known unknown (possible external to AEC, e.g.
  mic/OS processing); flagged for follow-up, not blocking.
- **[L8] Overlap/echo gate dropped the assistant's OWN speech (AEC-on,
  NO-ECHO regime), not the user's (found 2026-07-15, session #82,
  `uat-echo3-aec-on.log`).** During the [L7] AEC-on headset run, one genuine
  `dropped (` event fired: `dropped (overlap gate, echo-cancellation
  active): 'AAC could be left on with a headset. We can try to AAC off as an
  option, but I think the default should be owned.'` (03:00:43). The dropped
  text is a mis-transcription of ConvoBox's *own prior spoken response*,
  caught by the overlap gate as if it were echoed playback — the inverse of
  the [L1] user-barge-in drop failure mode. Harmless to the conversation (it
  suppresses a repeated spoken phrase, not user input), but it proves the
  [L1] overlap-gate misfire mechanism [L3] warned about is live in the
  AEC-on/NO-ECHO condition; it happened to land on ConvoBox's words this
  time. Follow-up: investigate whether the overlap/`SpokenEchoFilter` should
  exclude the assistant's own just-spoken text from the drop decision (or
  require token overlap with the *currently* playing segment, not any recent
  response), so a re-spoken assistant phrase can't be suppressed. Severity:
  low (no user input lost). Not blocking.
- **[L9] Backend interactive `question` tool deadlocks a voice session
  (session #5, 2026-07-18, `uat-echo.log`).** Asked "can you help me test?",
  the opencode build agent called its interactive `question` tool
  (multiple-choice, "What kind of testing do you want to run...") at
  18:53:32 and blocked in `status: running` for 5+ minutes waiting for an
  answer that has no voice path. Compounding chain, each verified live:
  (1) the user's barge-in at 18:53:40 muted playback mid-announcement, so
  the question was never heard; (2) all ~15 subsequent utterances were
  steered (`on_new_words=now`), got HTTP 200 "admitted", and queued
  invisibly behind the blocked tool -- none materialized into the session's
  message list, so "can you repeat the question?" can NEVER work as an LLM
  prompt in this state; (3) the heartbeat said "thinking or running a
  tool" for 126s when the truth was "waiting for YOUR answer" -- an honest
  status was available the whole time (`GET /api/session/{id}/question`
  returns the full pending question + options). The server exposes a
  complete reply API (`POST .../question/{requestID}/reply` / `/reject`),
  so a voice answer loop is buildable -- design: docs/DESIGN-backend-questions.md.
  Safeword remains the only working exit today. Positive observations from
  the same session: barge-in captured + `[BARGE-IN]`-tagged cleanly again,
  AEC delay auto-estimate stable at 222ms, and the RecognitionErrorLadder's
  FIRST live firing (`'Hallo?' lang=de 0.32 < 0.40 -> [ERROR-LADDER: tier 1]`,
  working as built).
- Echo layers' live scorecard: overlap window caught ~30 echo utterances
  with zero false drops and zero echo reaching the backend; the text
  filter never had to fire (it remains the backstop).
- **[L5] Backend event stream could die silently mid-session, losing the
  LLM's response from the log for over a minute (fixed 2026-07-15).**
  Note: numbered against `main`'s current `[L1]`-`[L4]` -- if JP's own
  `[L5]`/`[L6]` findings from earlier this session are still uncommitted,
  renumber whichever lands second, same as PR #83's precedent. JP
  reported "I am not always seeing the LLM output in the logs" and
  pasted a live UAT log that showed the real mechanism: 74 seconds into
  a silently-busy turn, `OpenCodeAdapter.events()` raised
  `httpx.ReadTimeout` from inside `_ensure_session()`'s session-creation
  POST (no explicit timeout set on that call, unlike the prompt POST --
  a busy/cold opencode server took longer than httpx's bare 5s default
  to respond). `Orchestrator._consume_events()` had no exception
  handling at all, so this silently killed the whole event-consuming
  task with only asyncio's own generic `"Task exception was never
  retrieved"` warning -- not a clear log line. Nothing re-created the
  task until the NEXT unrelated utterance's `handle_transcript()` call
  happened to notice `_events_task` was done and started a fresh one --
  in the live log, the user's first real question sat completely
  unlogged for over a minute, only surfacing (all at once, in a burst)
  once that second, unrelated utterance incidentally triggered a fresh
  subscription. Two fixes: `_ensure_session()`'s session-creation POST
  now gets the same generous read timeout the prompt POST already had
  (`src/convobox/adapters/opencode.py`), and
  `Orchestrator._consume_events()` now resubscribes immediately on any
  exception instead of dying silently, with a clear
  `"backend event stream failed; resubscribing"` warning log line
  (`src/convobox/orchestrator/orchestrator.py`). Deliberately does
  **not** retry when `events()` ends normally without an exception --
  that's each adapter's own documented lazy-respawn contract for a dead
  subprocess (claude-code/codex), preserved unchanged. UAT: provoke a
  long busy stretch on a loaded/slow backend and confirm responses now
  appear in the log promptly even if the connection hiccups mid-session;
  if a `ReadTimeout` (or similar) does occur, confirm the new warning
  line appears immediately, not a silent gap.

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
- **[E7] Multi-segment tool-calling responses don't spuriously kill the
  overlap gate.** A real bug, live-confirmed 2026-07-14 and fixed in
  `Orchestrator._on_event`/`speak_more()`: a single backend turn with
  multiple TEXT segments (text interleaved with tool calls -- "let me
  check that file" ... [tool work] ... "found it, fixing now") used to
  leave the PREVIOUS segment's `_speak_task` running uncancelled, which
  kept advancing `EchoAwarePlayer.playback_ended_at`
  (`scripts/run_convobox.py`) for audio that was never actually played
  (`play_stream()` already replaces the audio thread/stream regardless).
  Observed live as an entire multi-minute session where nearly every
  utterance got dropped by the overlap gate as echo -- reported as "AEC
  seems to be misfiring," though AEC itself was never the mechanism
  doing the dropping. Ask a coding-agent backend to do real multi-step
  work (read a file, then explain what it found, then make an edit) so
  it emits several TEXT segments in one turn, and confirm: (a) only the
  LAST segment's text is actually heard (matches existing behavior,
  unaffected by this fix), (b) speaking normally a few seconds after
  the full response finishes is NOT dropped as overlap -- the
  regression case this fix specifically targets. Unit-tested
  (`tests/test_orchestrator.py::test_second_text_event_cancels_the_first_speak_task_before_it_completes`,
  verified to fail without the fix -- hangs forever, confirming it
  detects the real bug) but not live-mic re-verified against a fresh
  session, to avoid interfering with an in-progress UAT session on a
  shared local backend server when this was found and fixed.
- **[E8] AEC delay hint: a stale fixed value causes near-total
  under-cancellation, and it could get silently re-baked on every
  Settings TUI save (fixed 2026-07-15).** Live-confirmed root cause of a
  session where mic+speakers (not headphones) self-triggered barge-in on
  nearly every response: `convobox.yaml` had `aec_delay_ms: 100`
  explicit, but the real measured render-to-capture delay on that
  machine was ~222ms -- WebRTC AEC3 can't converge with a hint that far
  off, so attenuation stayed at 0.2-4dB (`UNDER-CANCELLING`) instead of
  the 6-16dB actually available, and the assistant's own TTS output kept
  tripping the overlap gate. Root cause of the stale value itself: the
  Settings TUI's save function used to write EVERY field on every save
  (not just ones you changed), so opening and saving the TUI even once
  silently locked in whatever `aec_delay_ms` happened to be at the time.
  Two fixes: `aec_delay_ms` is now `None` by default (auto-tune, the
  recommended state) instead of a literal `100`, and saves now only
  write fields that actually differ from their default
  (`exclude_defaults=True` -- see `docs/UAT-settings-tui.md`'s matching
  section for the save-behavior UAT steps). Re-run the mic+speaker
  self-barge-in scenario with `aec_delay_ms` left unset and confirm the
  log shows `FLOOR-LIMITED` or genuine `UNDER-CANCELLING` with a
  MUCH smaller headroom gap, not the same near-total failure -- this is
  the live validation the original incident couldn't get to.

  **Follow-up, verified against WebRTC's own source (2026-07-15):** read
  the real `set_stream_delay_ms` documentation in
  `webrtc.googlesource.com/src/+/refs/heads/main/api/audio/audio_processing.h`
  (not a secondhand summary) -- confirms ConvoBox's existing delay
  semantics are exactly right: "the delay in ms between
  ProcessReverseStream() receiving a far-end frame and ProcessStream()
  receiving a near-end frame containing the corresponding echo,"
  `delay = (t_render - t_analyze) + (t_process - t_capture)`, matching
  `EchoCanceller.__init__`'s own docstring. Also found (via the real
  `modules/audio_processing/aec3/` source tree, specifically
  `echo_path_delay_estimator_unittest.cc`/`render_delay_buffer.cc`, and
  WebRTC's own changelogs) that AEC3 has its OWN internal delay
  estimator that continuously detects/adapts the true delay from the
  audio itself -- `set_stream_delay_ms()`'s hint is used to seed the
  INITIAL alignment "before the AEC has been able to detect the delay"
  itself, not as a permanent fixed value AEC3 blindly trusts forever.
  This explains something the original incident didn't: why a
  122ms-off hint caused *total* non-convergence for an entire
  10+-minute session rather than just a slow initial ramp-up --
  `EchoCanceller`'s AEC3 instance persists for the whole process
  lifetime (constructed once in `run()`, never rebuilt per-response;
  `reset_stats()` only clears ConvoBox's own telemetry deques, not
  AEC3's filter state), so it had ample time to self-correct if a bad
  initial seed only cost convergence speed. A stale-enough initial
  hint most likely placed the true echo path outside the delay
  estimator's effective search window, blocking convergence entirely
  rather than just delaying it -- consistent with, and a stronger
  validation of, the fix already shipped above (a genuinely accurate
  initial estimate matters more than "AEC3 will sort it out
  eventually").
- **[E9] Overlap gate's grace window now extends after an
  UNDER-CANCELLING response (2026-07-15, candidate -- needs live
  tuning).** The `[E8]` incident's log stayed `UNDER-CANCELLING` for
  nearly the whole session even accounting for the delay-hint bug --
  same-room mic+speaker echo may genuinely be a harder acoustic problem
  than a wrong delay hint alone explains. `grace_s_for_last_response()`
  (`scripts/run_convobox.py`) now widens `ECHO_GRACE_S` (the window
  after playback ends that still counts as "overlapping," protecting
  against reverb-tail false positives) proportionally to the JUST-
  finished response's remaining echo headroom, capped at `_MAX_GRACE_S`
  (1.0s) -- a `FLOOR-LIMITED` or `NO ECHO DETECTED` response leaves the
  window unchanged. **The exact constants
  (`_GRACE_EXTENSION_PER_DB=0.05`, cap `1.0s`) are derived from the
  `[E8]` log's own headroom numbers (8-14dB -> ~0.4-0.7s extra), NOT
  live-tuned** -- unit-tested for correctness of the logic (pure
  function, `tests/test_run_convobox_echo.py`), but whether these
  specific numbers feel right in practice needs a real mic+speaker UAT
  pass. Watch the new `overlap-gate grace window: Xs -> Ys` log line
  after each response; confirm it widens during a genuinely bad
  `UNDER-CANCELLING` stretch and settles back to `0.30s` once AEC
  recovers, and that the wider window doesn't make the assistant feel
  sluggish to respond to real speech right after it stops talking.

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
- **[V5] `was_forced` distinguishes a cap-triggered cutoff from a natural
  pause.** Set `vad.max_utterance_s` to something short (e.g. 5) and talk
  continuously past it. Confirm the main loop's transcript log line grows a
  `[FORCED: cut at max_utterance_s, still your turn]` marker for the capped
  utterance (`UtteranceSegmenter.was_forced`, `scripts/run_convobox.py`),
  and that the marker does NOT appear on a normal utterance that ends via a
  silence pause instead. This is purely a log-line signal for now (no
  spoken/TUI notification) -- note during UAT whether that's sufficient or
  whether a spoken cue (`docs/CONVERSATION-DESIGN-REFERENCES.md`'s
  LiveKit-research gap) would actually be needed in practice.
- **[V6] Pre-speech padding prevents onset clipping**
  (`UtteranceSegmenter`'s `_PREFIX_PADDING_WINDOWS`, per
  `docs/CONVERSATION-DESIGN-REFERENCES.md`'s Gemini Live API
  `prefix_padding_ms` finding). Hard to A/B by ear directly, but worth a
  specific listen during safeword UAT ([S1]-[S3]): say the safeword
  crisply, right after a pause (cold start, no vocal warm-up into it --
  the scenario most likely to clip an onset before this fix). If a hard
  stop is ever missed or mis-transcribed with a clean, unambiguous
  "stop stop stop" clearly spoken, note whether the transcript looks
  truncated at the start (e.g. "top stop stop") -- that specific failure
  signature would mean 64ms isn't enough padding and needs revisiting,
  as opposed to an unrelated STT/echo issue.

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
- **[T5] Multi-speaker voice selection.** Real, not hypothetical: several
  Piper voices already downloaded in this repo are genuinely multi-speaker
  (`en_GB-semaine-medium`: 4 named speakers -- prudence/spike/obadiah/poppy
  -- `en_GB-aru-medium`: 12, `en_GB-vctk-medium`: 109,
  `en_US-libritts-high`: 904). Set `tts.voice: en_GB-semaine-medium` and
  `tts.speaker: spike`, confirm it synthesizes without error and *sounds*
  different from `tts.speaker: poppy` (this needs a real ear -- the
  automated verification only confirmed the two produced different sample
  counts for similar text, not that they're audibly distinct). Then set
  `tts.speaker: nobody` (a name that doesn't exist) and confirm `[t]` on
  the TTS section reports a clear error naming the real available speakers
  for that voice, not a raw traceback.

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

Updated 2026-07-14 -- the original 8-step list below predated barge-in
presets, pause/resume, the conversation TUI, response tiering, and the
STT recovery fix, so it silently stopped covering roughly half the
document. Re-derived from the doc's own current section list rather than
patched piecemeal, to catch anything else that had drifted (nothing else
did).

1. Happy path: idle → speak → response spoken (N1-N4, T2).
2. Hard stop safety: S1-S5.
3. Echo / half-duplex: E1-E5 (speakers ON).
4. Barge-in (`interrupt_preset` != `do-not-disturb`/`halt`, requires AEC
   or headphones): G1-G7 -- barge-in itself is fully built now, this is
   no longer "document the gap," it's "verify the real behavior."
5. Edge VAD: V1-V4.
6. Pause/resume listening: P1-P8 (P5 is the one most likely to reveal a
   priority-ordering bug -- do not skip it).
7. Conversation TUI (`--tui`): U1-U10.
8. Response tiering (`interaction.tier_responses: true`): R1-R7.
9. STT native-allocator recovery (long session, 20+ min): ST1-ST3.
10. Scriptable/cleanup: M1-M3, X1-X2.
11. Settings UI: see [UAT-settings-tui.md](UAT-settings-tui.md).

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
- **[G7] Backchannels don't count as a real interrupt.** Say a bare
  backchannel token or short phrase built from one (e.g. "yeah", "right",
  "okay"/"ok", "sure", "wow", "really", "gotcha", "mm-hmm"/"uh-huh" --
  the exact whole-utterance token set is `_BACKCHANNEL_TOKENS` in
  `scripts/run_convobox.py`: `mm`, `mhm`, `mmhmm`, `uh`, `huh`, `uhhuh`,
  `hmm`, `yeah`, `yep`, `yup`, `right`, `oh`, `ok`, `okay`, `sure`, `wow`,
  `really`, `gotcha`) during playback under a preset where
  `BargeInMonitor` can fire (`conversational`/`halt`/`take-over`). Audio
  STILL stops (`BargeInMonitor` decides from raw audio timing alone,
  before STT can know the content -- this is expected, not a bug), but
  the utterance itself must NOT be forwarded to the backend -- log line
  `"dropped (backchannel, not a real interrupt attempt)"`
  (`is_backchannel(text)` in `scripts/run_convobox.py`). Research-grounded
  default behavior (Schegloff 1982; Ward & Tsukahara 2000; independently
  validated in production by Pipecat, LiveKit Agents, and Vocode -- see
  `docs/CONVERSATION-DESIGN-REFERENCES.md` section 2/4), never live-mic
  verified until now. Note whether the audio-stops-anyway part feels
  like a real UX problem in practice (a backchannel currently always
  costs the rest of the response, even though it's correctly not
  forwarded as a command) -- that gap is the false-interruption-recovery
  item flagged in `docs/DESIGN-barge-in.md`'s open questions, not yet
  built.

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
- **[U7] Diagnostics line (backend/AEC/heartbeat), added 2026-07-15 per
  JP's direct request for "voice status information... back-end
  interpreter... any other information you deem necessary."** A second
  header line now shows `backend: <name>` (from `config.backend.name`,
  static for the session), `AEC: on/off` (+ the last response's compact
  verdict tag -- `FLOOR-LIMITED`/`UNDER-CANCELLING`/`NO ECHO DETECTED`
  -- once at least one response has finished), and, only while the
  backend is silently busy, a color-coded `still working: Ns` (same
  green/yellow/red thresholds as the log-line heartbeat from PR #83,
  duplicated intentionally in `src/convobox/tui/render.py` to keep
  package layering clean -- `src/convobox` must not import from
  `scripts/`). Unit-tested (`tests/test_conversation_tui.py`,
  `tests/test_barge_in.py`'s new `WorkingIndicator.silent_busy_s`
  tests) and a real rendered-frame smoke test confirmed the layout
  looks right, but never watched update live frame-by-frame during an
  actual session. Confirm during a live `--tui` run: the backend name
  is right immediately at startup, the AEC tag appears/changes after
  each response finishes (matching the log's own "AEC stats for last
  response" line), and the heartbeat color/countdown tracks a real
  silently-busy stretch (appears after ~10s, turns yellow at 10s, red
  at 60s, disappears the instant audio starts playing or the backend
  goes idle) without visibly lagging the 0.1s redraw.
- **[U8] Live mic level (dBFS), added to the same diagnostics line
  (2026-07-15).** `mic: -XXdBFS`, updated per mic chunk (post-AEC if
  echo cancellation is on -- the same signal VAD/STT sees), reusing
  `audio_devices.level_meter()`'s existing RMS math. Deliberately NOT
  smoothed -- unit-tested and a real rendered-frame smoke test confirm
  the number appears/formats correctly, but the raw per-chunk value has
  never been watched live. If it reads as too flickery to be useful in
  practice, that's the first improvement to make (a decay-based VU-meter
  smoothing, same idea `audio_devices.py --setup`'s own live meter
  already uses) -- not something to guess at blind here. Speaker-side
  live level was deliberately NOT built this pass: it would need a
  cross-thread write from `AudioPlayer.on_block_played` (the playback
  THREAD, not the async mic loop), more care than this same-thread
  update needed -- noted as a follow-up candidate, not attempted
  half-verified. Confirm during a live run: the number moves with real
  speech/silence, tracks roughly what `audio_devices.py --test-input`
  reports for the same device, and reads AEC-cancelled (much quieter)
  during the assistant's own playback when AEC is on and converged.
- **[U9] "Who's expected to act?" ambiguity during the dead-time
  (found live during the AEC/barge-in UAT, 2026-07-18).** During a
  test the user could not tell whether ConvoBox was still processing on
  the backend or waiting for the user to say something -- there was no
  indicator for which party the session was blocked on. Root cause:
  the watchdog loop in `scripts/run_convobox.py` fell through to
  `status = "listening"` during the tiered-response continue-window
  (when `continue_gate.is_waiting`), so the "ball is in your court"
  wait looked identical to idle LISTENING. Fixed by adding a distinct
  `waiting` `TuiStatus` -- header now shows bold magenta
  `WAITING FOR YOU` (distinct from the calm cyan LISTENING), driven
  from `continue_gate.is_waiting` in the watchdog loop
  (`src/convobox/tui/state.py`, `src/convobox/tui/render.py`,
  `scripts/run_convobox.py`). Unit-tested (`tests/test_conversation_tui.py`
  `test_status_label_reflects_state` now covers `WAITING FOR YOU`).
  Still TODO for a live confirm: watch the header flip to `WAITING FOR
  YOU` the instant a tiered response finishes speaking and hold there
  until "continue"/timeout, and confirm it reads as obviously different
  from LISTENING. The phase-3 approval gate's wait is a separate
  candidate to surface the same way once that gate is wired live.
- **[U10] Scrollable panes, added 2026-07-20.** Reported broken (no PgUp/
  PgDn/other shortcuts worked at all) -- traced end-to-end before fixing
  per this repo's "verify a bug before proposing a fix" rule: the
  transcript and full-response panes always rendered just the tail of
  their content with zero keyboard input handling anywhere in
  `_tui_render_loop` -- this was never-implemented, not regressed.
  `Tab` switches focus between the Transcript and Full response panes
  (the focused one gets a `▸` marker); `Up`/`Down` scroll the focused
  pane one line, `PgUp`/`PgDn` one page (10 lines), `Home` jumps to the
  oldest content, `End` snaps back to live/latest. A scrolled pane's
  header shows `(scrolled -- End for latest)`. Unit-tested (`_handle_tui_key`
  in `tests/test_conversation_tui_keys.py`; render-side windowing/
  clamping in `tests/test_conversation_tui.py`) but never driven by a
  REAL keypress in a real terminal -- confirm live: PgUp/PgDn/Home/End/
  Tab/arrows all work as described on both the tested platform (Windows,
  `msvcrt`) and, if you get to it, a POSIX terminal (the CSI-sequence
  path -- `ESC [ 5 ~` / `ESC [ 6 ~` for PgUp/PgDn -- is implemented but
  unvalidated live, matching this project's existing Linux/macOS
  validation gap); that Ctrl+C still exits cleanly now that POSIX raw
  mode (`tty.setcbreak`) is active for the whole `--tui` session, not
  just at the moments a key is read; and that the terminal is left in a
  normal (echo on, line-buffered) state after exit even if the session
  ends via an exception, not just Ctrl+C. **Mouse scroll wheel is
  deliberately NOT implemented this pass** -- it would need real
  terminal mouse-tracking mode (SGR `ESC[?1000h`/`ESC[?1006h` + parsing
  `ESC[<64;...M`/`ESC[<65;...M` wheel events) on POSIX, and the Windows
  Console API's `ReadConsoleInput`/`ENABLE_MOUSE_INPUT` (msvcrt's
  `getwch()` cannot see mouse events at all) on Windows -- two
  substantially different, untestable-without-a-real-terminal
  mechanisms, for a platform (Windows) that's also the only tested one.
  Keyboard scrolling covers the reported problem; flagged in
  `docs/ROADMAP.md` as a scoped follow-up rather than bundled in here.

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
- **[ST4] A failed reload doesn't crash the process either.** Live-confirmed
  2026-07-14: JP hit a real session where the RELOAD itself (not just the
  original `transcribe()` call) hit the same native-allocator failure --
  an unhandled `RuntimeError` from `WhisperModel.__init__`/`ctranslate2.
  models.Whisper.__init__`, which crashed the whole process before this
  fix. If the log ever shows `"STT model reload ALSO failed -- staying
  unavailable, will retry on the next utterance..."`, confirm the app
  keeps running (doesn't crash) and that a LATER utterance (not
  necessarily the very next one -- retries on every call while
  unavailable) eventually transcribes normally again once the underlying
  pressure eases. Also check the log line's memory diagnostic (e.g.
  `"30208MB RAM available -- likely the known ctranslate2/MKL allocator
  quirk, not a real memory shortage"`) reads sane against what Task
  Manager / `Get-CimInstance Win32_OperatingSystem` actually shows at
  the time.

## 12. OpenCode model selection (`backend.model`, `src/convobox/adapters/opencode.py`)

JP asked directly, 2026-07-14/15: opencode picked a hosted free-tier
model (`hy3-free`, OpenCode Zen) rather than his own configured provider,
with no error or indication either way. Root-caused and fixed: `POST
/api/session`'s optional `model: {providerID, id}` field was never sent
(the adapter posted an empty body unconditionally) -- see
`OPENCODE_API_NOTES.md`'s "Session creation supports pinning a model"
section for the full investigation, including why a CLI flag (`opencode
-m ...`) doesn't work for this project's use case (`opencode serve` has
no `-m` option at all).

**Verification gap, explicitly not closed by unit tests alone**: the
request SHAPE is confirmed correct against a live server's own OpenAPI
spec (read-only `GET /doc`, no session actually created -- respecting
the standing "no test traffic on JP's live server" boundary), and the
adapter's construction/request-building logic is fully unit-tested
against a fake server. What's NOT verified: whether opencode's real
`POST /api/session` genuinely accepts a live request with this shape and
actually honors the pinned model for generation, rather than silently
falling back again for some other reason.

- **[BM1] A configured model actually gets used.** Settings TUI ->
  Backend -> Model, set e.g. `openai/gpt-5.6-sol` (or another real
  `provider/model-id` from `opencode models`), save, and start a real
  mic session. Confirm the response is genuinely generated by that
  model, not silently falling back to opencode's own default -- opencode
  itself may report which model answered (check its own logs/session
  export, `opencode export <sessionID>`), or ask the agent directly
  which model it is.
- **[BM2] An invalid model is a clear, early error, not a silent
  fallback.** Set `backend.model` to a real `provider/` prefix but a
  bogus model id (e.g. `openai/does-not-exist`). Confirm `[t]` on the
  Backend section (or the first real utterance) surfaces a clear error
  from the real `POST /api/session` call, rather than opencode silently
  substituting a different model with no signal.
- **[BM3] Leaving Model unset behaves exactly as before.** With
  `backend.model` unset (the default), confirm behavior is unchanged
  from before this feature existed -- opencode picks its own default,
  no `model` field appears in the session-creation request at all.
- **[BM4] Switching backends and back preserves the configured model.**
  In the Settings TUI, set a model on opencode, switch to `codex` or
  `claude-code`, then switch back to `opencode`. Confirm the model is
  still there (per-backend memory, `backend_profiles`), not reset to
  unset.
