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
  **Cross-platform reproduction, 2026-08-10 (macOS):** a TTS-speaker-
  mic-Whisper round-trip test hit the identical failure signature
  (a repeated "I'm sorry" clause) on completely different hardware
  (Apple Silicon, AIRHUG 28 mic) — confirming this is a real Whisper/
  far-field-acoustics characteristic, not something specific to the
  original Windows hardware. Also confirmed the mitigation's premise
  directly: feeding the same audio to the transcriber WITHOUT the
  speaker/mic path scored 100% word accuracy, isolating the failure
  entirely to the far-field acoustic path, not the model itself. See
  `docs/field-notes/2026-08-10-macos-kokoro-and-tts-stt-roundtrip.md`.
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
- **[E10] NS/AGC (`audio.aec_ns`/`aec_agc`, GitHub issue #323) --
  cross-platform confirmation needed before any default change.**
  **Status:** live-tested on ONE machine only so far (Mac mini M4,
  macOS, external Logitech speakers via the analog jack + AIRHUG 28
  mic) -- 32 real trials, 4 configs x N=8, via
  `scripts/acoustic_calibration.py`. Result: `aec_ns` (NS, at
  `aec_ns_level: 2`) alone gave a real, consistent improvement (-15%
  false barge-ins, +0.46dB suppression, lower residual mic RMS across
  all N=8). `aec_agc` (AGC) measured WORSE than off (+29% false
  barge-ins, suppression cut nearly in half) -- it amplifies residual
  POST-AEC echo, not the raw pre-AEC mic signal, the opposite of the
  original "tame a hot mic" hypothesis. Full methodology and numbers:
  `docs/field-notes/2026-08-31-issue-323-ns-agc-open-speaker-trial-agc-hurts-ns-mildly-helps.md`.
  Both are now real, off-by-default config fields (`convobox.yaml`'s
  `audio` section, or the Settings TUI/Web UI's "(advanced)" fields) --
  no more throwaway test scripting needed to re-run this trial.

  **Update (2026-09-01), still same machine:** an `ns_level` 0-3 sweep,
  then a dedicated 2-vs-3 repeat to confirm, found `ns_level: 3` (very
  high) beats the documented `ns_level: 2` (high) value -- 24% and 33%
  fewer false barge-ins in two independent passes respectively (a
  consistent relative gap despite absolute counts moving a lot session
  to session). Full numbers in the same field note's 2026-09-01
  follow-up section. This adds a second value to confirm below -- it
  does NOT reduce how much cross-platform confirmation still matters.

  **What "done" looks like for this item:** the same trial, same
  methodology, on a genuinely different machine/room/speaker-mic
  geometry than the one already tested, to find out whether the
  Mac-mini result generalizes or was specific to that setup's acoustic
  path. One real confirming (or contradicting) run on a second platform
  is worth more here than a bigger N on the same machine again.

  **Update (2026-09-02): run on Helios/Windows, result is inconclusive
  -- not a clean confirm or contradiction.** A first single-run pass per
  config swung wildly (a same-config `aec_agc` repeat went 0%->100%
  self-barge rejection), which turned out NOT to be mainly an
  ambient-noise artifact: a 20-run overnight battery (5 interleaved
  cycles x 4 configs, ambient RMS logged per run) found only a weak
  ambient/outcome correlation overall (r=-0.2) -- **run-to-run variance
  at N=8 is the real problem, and it's comparable in size to any
  difference between configs.** Pooled (N=40/config): `ns_level=3` looks
  best (87.5% mean rejection) -- tentatively supports the Mac mini's
  level-3-beats-level-2 result -- but `ns_level=2` looks WORSE than
  baseline here (60.0% vs 66.9%), contradicting the Mac mini's
  ns-alone-helps result. Full numbers:
  `docs/field-notes/2026-09-02-e10-helios-windows-cross-platform-battery-run-to-run-variance-dominates.md`.
  This item stays open -- the revised next step is likely a higher-N or
  repeat-based protocol change to `acoustic_calibration.py` itself
  before a third platform (Linux) is worth running, not a straight
  go/no-go on the current N=8 methodology.

  **Prerequisites before running.**
  1. Real, OPEN speakers and a real mic in the same room -- NOT
     headphones/headset. This trial specifically exercises the
     open-speaker acoustic-echo path `echo_cancellation` exists for; a
     headset UAT pass would tell you nothing about this question (no
     acoustic leakage to cancel in the first place). If the target
     machine's normal setup is a headset, this needs a temporary swap
     to open speakers for the duration of the trial.
  2. `uv sync --extra dev --extra aec` -- Windows gets prebuilt
     `aec-audio-processing` wheels (the easy path); other platforms may
     need a source build (`meson`/`ninja`/`swig` first -- see
     `src/convobox/audio/aec.py`'s own install-error message).
  3. `python scripts/audio_devices.py --setup` to confirm the real
     speaker/mic device names, and pin `audio.output_device`/
     `audio.input_device` in `convobox.yaml` if the system defaults
     aren't the open-speaker pair.
  4. A quiet-ish room for the run (ambient noise affects false-barge-in
     counts) -- doesn't need to be silent, just not mid-construction.

  **Steps.**
  1. `audio.echo_cancellation: true`, leave `aec_ns`/`aec_agc` unset
     (both default `false`) -- baseline run:
     `python scripts/acoustic_calibration.py --delay-candidates auto --repeat-each 8`.
  2. Set `aec_ns: true`, leave `aec_ns_level` at its default `2`, and
     re-run the same command -- NS-only run (`ns_level=2`, the value
     documented above).
  3. Set `aec_ns_level: 3` (still `aec_ns: true`) and re-run again --
     the 2026-09-01 Mac-mini follow-up found this beats `ns_level: 2`
     in two independent same-machine passes; worth checking whether
     that holds on a second platform too, not just re-confirming NS
     itself helps.
  4. Revert `aec_ns: false`, set `aec_agc: true` (`aec_agc_mode` default
     `1`) and re-run -- AGC-only run, to confirm (or refute) that it's
     harmful here too, not just on the Mac mini.
  5. Compare each run's `report.json` -> `aggregates_by_delay_ms` (one
     entry per delay bucket, normally just one under `auto` resolution)
     -- specifically `processed_false_barge_ins`, `mean_suppression_db`,
     `mean_processed_rms`, same three metrics the original trial used.
     Each run's own `aec_ns_agc` block (new field, this session) records
     exactly which config produced it, for a clean record without
     needing to remember which yaml edit went with which run.
  6. Write up the result as a new dated field note (same format as
     `docs/field-notes/2026-08-31-issue-323-ns-agc-open-speaker-trial-agc-hurts-ns-mildly-helps.md`
     -- copy its structure) and update this entry + `docs/KNOWN-ISSUES.md`'s
     NS/AGC entry with the cross-platform result. If NS holds up here
     too, that's the point where flipping the shipped default becomes a
     real decision to make, not before.

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
  **Log-confirmed pass (tool-call variant), 2026-07-31 18:32:23:**
  `transcript='Stop, stop, stop.' ... busy=True [HARD STOP]` /
  `hard stop matched safeword 'stop stop stop'`. Backend was mid-tool-call
  (`still working` heartbeat active), not mid-audio-playback -- same
  tool-call-vs-playback distinction as the P1 passes above. The safeword
  check runs on the raw transcript before any other gate and calls
  `player.stop()`/`tts.stop()`/`send_hard_stop()` unconditionally, so this
  exercises the same hard-stop path the literal mid-playback case would;
  JP classified it as a valid S1 pass. A strict mid-playback (audio
  actually sounding) case is still untested.
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
- **[S6] A misheard safeword can land on the pause phrase instead --
  same hard-stop effect, different resulting state (see
  `docs/KNOWN-ISSUES.md`).**
  **Log-confirmed live, 2026-08-01, 20:07:08:** an utterance intended as
  'stop stop stop' was transcribed as `'Stop listening.'` instead, which
  matched `pause_listening_phrases` rather than the safeword. Not a
  safety gap -- the pause path calls the same `send_hard_stop()` the
  safeword does, confirmed against the same session's log (the mis-heard
  phrase correctly cancelled an in-flight bogus query). The real
  difference is state: safeword returns to normal listening immediately;
  landing on the pause phrase instead leaves the session paused,
  requiring the resume word to hear anything else again. Same
  STT-reliability category as [P7]'s `resume_word` finding, not unique to
  the safeword. No fix proposed.

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
- **[T6] TTS synthesis/playback failures now surfaced, not silently
  swallowed (fixed 2026-07-29, PR #175, commit `84a1122`).** `_speak_task`
  (`Orchestrator._speak`, fire-and-forget via a bare
  `asyncio.create_task()`) had no exception handling anywhere in its call
  chain, and nothing ever awaits/checks it afterward (by design -- a
  slow/failed synthesis must never block the mic loop). An uncaught
  exception there previously vanished completely: no log line via this
  project's own logging, no UI signal, nothing but an easy-to-miss
  unretrieved-task-exception warning from asyncio at GC time. Live-reported
  symptom: "response silently stops after the first paragraph, no error, no
  indication anything went wrong." Root-caused 2026-07-28/29, cross-
  referenced against an independent sandboxed UAT session's findings.
  **Live-confirmed 2026-07-30**: 3/3 attempts at pushing a response past
  Kokoro's ~510-phoneme cap produced the clean, expected signature (log:
  `ERROR TTS synthesis/playback failed mid-response` with the real
  RuntimeError text, no crash, mic loop kept working afterward). CLI and
  web UI both show the failure correctly. **Gap found, then fixed same day
  (commit `70c3d6d`, 2026-07-30)**: `--tui` mode used to show nothing
  on-screen -- `_on_backend_event` in `scripts/run_convobox.py` only
  special-cased `APPROVAL_REQUEST`/`TEXT`, so an `ERROR` event was
  silently dropped by the TUI dispatcher and only reached
  `convobox-tui.log`, not the transcript/full-detail pane. Fixed by adding
  a `"system"` turn (`tui_state.add_turn("system", f"error: {event.content}")`)
  for `ERROR` events, matching `[U10]`'s existing convention for
  session-level events worth showing inline -- unit-tested (3 new cases in
  `tests/test_approval_prompt_gate.py`), CLI/web/TUI now all surface a TTS
  failure the same way. **Still outstanding, per the fix's own commit
  message**: a real `--tui` session hasn't been watched provoke a TTS
  failure live (e.g. the Kokoro ~510-phoneme trigger) to confirm the error
  turn actually renders correctly in a real terminal, not just in a
  unit-tested `ConversationTuiState` -- the next live-UAT pass on `--tui`
  should specifically try to reproduce this.

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

0. **Newest, not yet UAT'd -- run this first.**
   `[E10]` (NS/AGC, GitHub issue #323) is the current top priority:
   live-tested on ONE machine (Mac mini M4/macOS) only, needs a second
   platform (Helios/Windows first, per JP's own priority call
   2026-09-01) to confirm the result generalizes before any shipped
   default changes. `[T6]` (TTS synthesis/playback failures now
   surfaced, not swallowed) is still live-unverified -- provoke a real
   synthesis failure and confirm it logs clearly instead of silently
   truncating. `[G11]` (false interruption marker on every waited-out
   turn) is now closed -- live-confirmed 2026-07-30, no false
   positives/negatives.
1. Happy path: idle → speak → response spoken (N1-N4, T2).
2. Hard stop safety: S1-S5.
3. Echo / half-duplex: E1-E5 (speakers ON).
4. Barge-in (`interrupt_preset` != `do-not-disturb`/`halt`, requires AEC
   or headphones): G1-G11 -- barge-in itself is fully built now, this is
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
  built. **Live-reconfirmed 2026-07-25**: a bounded incident capture
  (`.incident-captures/20260725-214824/`) caught exactly this -- a real,
  brief utterance ("Thank you very much.") correctly classified and
  dropped as backchannel, muted anyway because the mute already fired
  pre-classification. A cross-correlation ruled out AEC echo as the
  cause first (see
  `docs/field-notes/2026-07-25-timing-coincidence-is-not-echo-correlation.md`)
  before landing back on this already-known gap -- still not built.
  **Decision (JP, 2026-07-26): assume no for now** on building a resume
  mechanism -- see `docs/DESIGN-barge-in.md`'s "Open questions" for the
  full note, including JP's observation that these backchannel-shaped
  utterances have tended to read as affirmative in content, worth
  rechecking before this gets revisited for real. **Also 2026-07-26:**
  the "cross-correlation ruled out AEC echo" claim just above is now
  in question -- JP was not the speaker in the quoted utterance, and
  the correlation method had a real blind spot (time-compressed
  reference vs. wall-clock mic capture). See
  `docs/field-notes/2026-07-26-reference-capture-is-time-compressed-not-wall-clock.md`.
  This entry may turn out to be a G8/echo-adjacent incident, not a real
  G7 backchannel one -- unresolved until re-verified against real audio.
- **[G8] `BargeInMonitor` can fire against a response that produced NO
  audio yet.** `AudioPlayer.is_playing()`
  (`src/convobox/audio/playback.py:257-258`) returns `True` as soon as
  the playback thread starts, not when the first real audio block is
  written -- there is a real TTS-synthesis-latency window where
  `is_playing()` is `True` and zero reference frames have been fed. If
  the user keeps talking during that window (normal in rapid
  back-and-forth conversation), `BargeInMonitor.observe()` correctly
  fires by its own inputs, but the log ("barge-in: sustained speech
  during playback -- stopping audio") reads exactly like a real
  interruption even though nothing was ever audible. Confirmed live
  2026-07-25: two such events showed an *identical* `reverse` (AEC
  reference) frame count immediately before and after the "interrupted"
  response, proving zero audio was ever output. Functionally harmless
  under `on_new_words: now` (the new words are still taken correctly) --
  the defect is diagnostic/UX, not correctness. See
  `docs/field-notes/2026-07-25-player-is-playing-races-ahead-of-first-audio.md`.
  **Fixed 2026-07-27**: `EchoAwarePlayer.audible` (`scripts/run_convobox.py`)
  is set via `AudioPlayer.on_first_block_played` and reset synchronously in
  `play()`/`play_stream()`, distinct from `is_playing()`'s raw thread
  liveness. `BargeInMonitor.observe()`'s call site now passes
  `player.audible` instead of `player.is_playing()` -- every OTHER
  `is_playing()` use (the AEC-stats edge detection, the overlap gate, the
  echo-tail guard, `QueuedInterjection.flush_if_idle`, `_working_watchdog`)
  deliberately keeps thread-liveness semantics, since those care whether a
  response is still in progress at all, not whether it's currently audible.
  Verified with unit tests (`tests/test_run_convobox_echo.py`) mirroring
  the same gated-stream technique used to confirm the original gap; not
  live-mic verified (no mic access on this machine this session).
- **[G9] Under-cancelled echo can be intelligible enough for STT to
  transcribe real words out of the assistant's own voice.** Confirmed
  live 2026-07-26 on an open-speaker (amplified desktop speakers) +
  webcam-mic rig: two incidents (12.9dB and 4.7dB of unresolved AEC
  headroom) both produced audible, by-ear-confirmed echo bleed-through,
  and in the worse case Whisper transcribed the assistant's own list
  ("1. Atom...") as `"one, and two, and open."`, which was accepted as
  real speech and echoed back ("Heard: 'one, and two, and open.'").
  Different mechanism from `[G8]` (that's silence being misread; this is
  real, present, audible echo). Tested on headphones -- see `[G10]`. See
  `docs/field-notes/2026-07-26-under-cancelled-echo-is-sometimes-transcribable.md`.
- **[G10] Headphones reduce but do not eliminate `[G9]`'s under-cancelled
  echo, and headphone type doesn't materially change the residual rate.**
  Two back-to-back UAT sessions, same room/mic, only the headset changed:
  Shokz OpenComm (bone-conduction) logged `UNDER-CANCELLING` on 4.4% of
  160 AEC windows and a real (STT-transcribed) echo-match on 11.5% of 61
  barge-ins; MPow H12 (sealed over-ear) logged 3.0% of 66 windows and
  12.5% of 16 barge-ins -- statistically indistinguishable despite the
  two headsets sitting at opposite ends of the passive-isolation
  spectrum. The safety net caught 100% of real matches in both sessions
  (0 forwarded as real speech). Room acoustics (reflective walls/ceiling,
  measured -45.9dBFS ambient noise floor from fans/AC) is the more
  likely lever than headset choice, not yet confirmed against a second
  room. See
  `docs/field-notes/2026-07-27-headphone-choice-does-not-eliminate-under-cancelled-echo.md`.
- **[G11] Playback-end reset bug caused every waited-out turn to be
  falsely tagged as an interruption (fixed 2026-07-29, PR #175, commit
  `103df70`).** `EchoAwarePlayer.audible` (set True on the first real
  audio block reached the device, per the `[G8]` fix) was only ever reset
  back to False at the START of the NEXT `play()`/`play_stream()` call --
  never on the CURRENT response's own natural completion. Any utterance
  spoken into the gap between one response finishing and the next
  starting read as "the user is talking during playback" to
  `BargeInMonitor.observe()` -- the exact mechanism that sets
  `barge_in_pending` -> the interrupt marker (now `[User interrupted AI
  response]` per `[G5]`/PR #178/commit `92d866b`) -- even on turns where
  the user explicitly waited out the full response before speaking.
  Root-caused 2026-07-28/29 (same session as `[T6]`), cross-referenced
  against an independent sandboxed UAT session's findings. Fix: `audible`
  is now also reset on natural playback completion, not only at the start
  of the next play() call. **Live-confirmed 2026-07-30**: waited-out turns
  no longer carry the interrupt marker, and a genuine mid-playback
  barge-in still tags correctly -- no false positives (marker on a
  waited-out turn) or false negatives (marker missing on a real
  interruption) across the test session. Closed.
- **[G12] Barge-in speech detection correctly separates vocal from
  non-vocal noise, including onomatopoeia (live-confirmed 2026-07-30,
  same session as `[G11]`).** Ad-hoc sound-source test during the `[G11]`
  pass, extending `[G2]`'s cough-test coverage from sub-threshold noise
  bursts to a fuller taxonomy of full-volume non-speech sounds: Charlie
  Brown "wah-wah" trombone mimicry and singing both correctly triggered a
  barge-in (true positive -- these are vocalizations); whistling and
  clapping did NOT trip it (true negative -- non-vocal sound, correctly
  filtered); spoken onomatopoeia (e.g. "wah wah" as actual words, not the
  trombone sound) DID trip it (true positive -- it's real spoken words,
  same as any other utterance). No false positives or false negatives
  across the set as tested. Not a code change -- documents `BargeInMonitor`
  behaving correctly against a class of inputs not previously covered in
  this checklist.

## Pause/resume listening (docs/DESIGN-barge-in.md, "Pause/resume listening")

- **[P1] Pause hard-stops in-flight work.** While the backend is actively
  responding (mid-playback or mid-tool-call), say "stop listening" --
  playback stops immediately, `is_busy()` drops, and the log shows
  "paused listening (matched...)". No spoken response to "stop listening"
  itself is ever heard. **Log-confirmed 2026-07-31** (`convobox-tui.log`,
  reconciled against the live chat transcript, superseding an earlier
  in-session verbal tally of 5/5): 6/6 real
  `paused listening (matched 'Stop listening.')` events, each followed by
  a clean `resumed listening (resume word matched)`, at 17:45:05,
  17:46:09, 17:47:24, 17:52:46, 17:56:56, and 18:07:32. Three additional
  spoken attempts in the same session never reached the pause-matching
  logic at all -- STT produced "That was nice" and "God bless thee" in
  place of the intended pause phrase (low decode confidence, `dec`
  0.45-0.46); ordinary barge-in still triggered on the mistranscribed
  speech in at least one case, but the deterministic pause matcher (exact
  normalized-substring match, no fuzzy fallback by design -- see
  `src/convobox/listening_pause/detector.py`) correctly did not fire on
  near-misses. Not counted against P1 for that reason. Root cause and
  JP's call to keep the matcher deterministic (not fuzzy) are recorded
  under [P2] below, which hit the same failure mode.
- **[P2] Pause while idle.** Say "stop listening" with nothing running --
  no crash, no spoken response, log shows the pause; the hard-stop calls are
  effectively no-ops (same as the safeword's own idle no-op).
  **Log-confirmed pass, 2026-07-31 18:20:52:** `paused listening (matched
  'Stop listening.')` fired cleanly (`dec`/lang confidence 0.98) after a
  genuine idle gap -- prior response's playback finished at 18:20:27,
  no `backend still working` heartbeat in the 24s before the pause
  command, confirming true `busy=False`. Resumed cleanly too (single
  attempt, confidence 0.50). **Second log-confirmed pass, 18:42:19:**
  same pattern -- 75s of true idle (no heartbeat) after the prior
  response's playback ended at 18:41:03, then a clean match at 0.98
  confidence. **Third log-confirmed pass, 18:46:08:** fired cleanly
  (0.97 confidence) during a response-tiering continue/decline window
  (also idle by `is_busy()`, not just post-playback silence), resumed on
  the first attempt (0.58 confidence). 3/3 clean idle passes logged after
  the fail below.
  **Log-confirmed fail, 2026-07-31 18:09:37:** `transcript='Stop listing.'
  lang=en (0.97) dec=0.45 busy=False` -- STT dropped the unstressed "-en-"
  in "listening", producing "listing" instead. No `paused listening`
  line followed; with nothing playing (`busy=False`) there was no
  barge-in fallback either, so the utterance was routed to the backend as
  an ordinary (nonsensical) turn -- a real P2 fail, not a no-op.
  Root cause: `PauseListeningDetector.check()` does exact, deterministic
  normalized-substring matching with no fuzzy tolerance, by explicit
  design (same safety tier as `SafewordDetector` -- a hard-stop-class
  phrase should have no matching ambiguity). "Stop listing" doesn't
  contain the substring "stop listening", so it's a clean miss, not a
  bug in the matcher. The actual gap is upstream: a low-confidence
  transcript (`dec=0.45`) that matched no control phrase produced no
  fallback (e.g. a confidence-gated re-ask) -- it just fell through to a
  normal backend turn. **JP's call: keep the matcher deterministic, do
  not add fuzzy matching for a hard-stop-class phrase.** Left open
  whether a low-confidence-and-unmatched re-ask is worth adding later.
- **[P3] Ordinary speech is dropped while paused.** While paused, say a
  normal command ("what time is it", "run the tests") -- NOT routed to the
  backend (no new HTTP/subprocess request; `is_busy()` never flips true),
  logged at debug as "dropped (paused, not the resume word)".
  **Log-confirmed pass (3 rounds), 2026-07-31** (`convobox-tui.log`):
  **Round 1**, paused 21:20:53 (`paused listening (matched 'Stop
  listening.')`) -- 8 separate STT-processed utterances during the paused
  window (21:20:56, 21:21:00, 21:21:03, 21:21:07, 21:21:10, 21:21:23,
  21:21:26, 21:21:30), none producing a `backend still working` heartbeat
  or a `response:` line, then a clean `resumed listening (resume word
  matched): 'Athena'` at 21:21:31. **Round 2**, paused 21:23:52 -- 7 more
  STT-processed utterances (21:23:59, 21:24:03, 21:24:30, 21:24:43,
  21:24:46, 21:24:47, 21:24:52), again zero backend activity in between,
  resumed cleanly at 21:24:59. **Round 3**, paused 21:27:05 (matched
  "All right, let's continue testing P3, stop listening.") -- 9 more
  STT-processed utterances (21:27:12 through 21:27:37, including a few
  low-confidence `ru` language-detection misfires), again zero backend
  activity in between, resumed cleanly at 21:27:43 on 'Athena.'. All
  three rounds pass by absence of backend activity (no heartbeat/response
  between pause and resume despite multiple spoken utterances reaching
  STT) rather than a direct citation of the "dropped (paused, not the
  resume word)" debug line itself -- the session was running at INFO
  level, not `--verbose`, so that specific debug line isn't in this log
  (same INFO/`--verbose` gap noted in the stuck-busy finding above). 3/3
  clean passes.
- **[P4] Resume word resumes.** While paused, say the configured resume word
  (default "ConvoBox") -- log shows "resumed listening (resume word
  matched)"; the NEXT ordinary utterance after that routes normally again.
  **Log-confirmed 2026-07-31, 17:56:56-17:57:47:** with `resume_word:
  Athena` configured, JP reported needing 3 attempts before it
  registered, and the log backs that up -- 3 separate audio-processing
  events during the paused window (`Detected language 'en'` at
  confidence 0.64, 0.53, then 0.46) before the third one finally produced
  `resumed listening (resume word matched): 'Athena'`. Same degraded-STT
  pattern as the [P1] near-misses (low decode/language confidence,
  legitimate attempts not recognized), not a resume-word-matching
  sensitivity issue in the detector itself.
- **[P5] Safeword still works while paused, but does NOT resume.** While
  paused, say "stop stop stop" -- the `[HARD STOP]` path still fires
  (matters if something got started right as pause was requested / a race).
  Critically: verify ConvoBox is STILL paused afterward -- only the wake
  word should resume it, confirming pause/hard-stop are the orthogonal axes
  the design calls for, not the same thing.
  **Log-confirmed pass (4 rounds), 2026-07-31** (`convobox-tui.log`,
  21:29-21:34): across the four rounds, every configured safeword phrase
  ("stop stop stop", "break break break", "eject eject eject", "mayday
  mayday mayday") matched `hard stop matched safeword '...'` while paused,
  including one match embedded mid-sentence ("Okay, it looks good. Stop,
  stop, stop.") and matches down to 0.45-0.55 language confidence --
  confirming the check is a deterministic substring match, same as the
  safeword's own design (see [S2]). In every round, ConvoBox stayed
  paused through all the safeword hits and only exited on the actual
  resume word ('Athena'). Traced against the code
  (`src/convobox/orchestrator/orchestrator.py:197-205`,
  `src/convobox/adapters/claude_code.py:634-639`): the "hard stop matched
  safeword" log line only proves the phrase matched, not that
  `player.stop()`/`tts.stop()`/`send_hard_stop()` had any effect --
  those calls are silent on success, and with nothing in flight
  (`busy=False` in all logged hits across all 4 rounds) the adapter's
  `send_hard_stop()` is an explicit no-op by design when there's no live
  process. **So P5's match-and-stay-paused behavior is solidly
  confirmed (4/4 rounds, 4 distinct safeword phrases); the effect-under-
  load case (safeword landing while `busy=True`, e.g. mid-tool-call or
  mid-playback, the race scenario this test explicitly calls out) was
  attempted but not achieved -- every hit across all rounds happened to
  land while idle. Left open, same as [P1]'s strict mid-playback case.**
- **[P6] The pause phrase is inert while already paused.** While paused,
  say "stop listening" (or "pause listening") again -- treated as ordinary
  ignored speech per P3, not a special case; still requires the resume word
  to exit.
  **Log-confirmed pass, 2026-07-31 ~21:36-21:37** (`convobox-tui.log`):
  three pause/resume cycles, including one pause via the "Pause
  listening." phrasing variant (confirming that alternate phrase works
  too). In two of the three cycles, several STT-processed events occurred
  between pause and resume with no matched transcript logged at all --
  consistent with the pause phrase (if repeated) being silently dropped
  rather than re-triggering `paused listening` or doing anything else
  observable; ConvoBox stayed paused throughout and only exited on
  'Athena' each time. Same caveat as [P3]: at INFO level, utterances that
  neither match nor route are dropped without logging their transcript
  text, so the content of those gap utterances (i.e. whether they were
  actually a repeated pause phrase) isn't directly confirmed -- same
  INFO/`--verbose` gap.
- **[P7] Custom resume_word / pause_listening_phrases via config.** Set
  non-default values in convobox.yaml (or the Settings TUI once it exposes
  these fields) and confirm the whole P1-P6 cycle still works end-to-end,
  not just the unit-tested detector classes in isolation.
  **Log-confirmed pass (resume_word half only), 2026-07-31 21:44-21:50**
  (`convobox-tui.log`): `resume_word` switched from `Athena` to `pineapple`
  at 21:44:07 (`pause_listening_phrases` left at default). 5/5 pause->resume
  cycles matched on the first attempt: 21:45:19->26 (7s), 21:47:42->49:10
  (88s -- this window also had 5 safeword hard-stops fire while paused,
  re-confirming [P5] holds under a custom resume word too), 21:49:14->26
  (12s), 21:49:30->33 (~3.5s, deliberate rapid pause/resume cycling), and
  21:49:45->50:05 (20s). Negative check: at 21:49:38/42, saying the generic
  phrase "Resume listening" (not the configured word) while NOT paused
  correctly did nothing -- routed as ordinary speech, no false resume. No
  mismatched/failed `pineapple` attempts appear anywhere in this stretch.
  **Log-confirmed pass (pause_listening_phrases half), 2026-07-31
  21:59-22:03** (`convobox-tui.log`): `pause_listening_phrases` set to
  `["stop private", "pause private"]`, `resume_word` reverted to the
  default (`Athena`, by removing the `pineapple` override). Both custom
  phrases matched cleanly: `paused listening (matched 'stop, private.')`
  at 22:00:18 -> resumed 22:00:32; `paused listening (matched 'pause,
  private.')` at 22:00:52 -> resumed 22:01:37 (45s); `paused listening
  (matched 'Pause private.')` at 22:02:00 -> resumed 22:02:48 (48s). All
  three resumes matched 'Athena' on the first attempt. **P7 is now fully
  closed** -- both halves (resume_word AND pause_listening_phrases)
  independently confirmed non-default, on top of the already-solid
  default-config P1-P6 pass earlier this session. The old default pause
  phrase ("stop listening") was not separately re-tested to confirm it
  no longer triggers now that the config no longer lists it -- a minor
  residual gap, not blocking, since the matcher is a straightforward
  list-membership check with no other path to a false positive.
  **Enhancement idea (not a bug, not yet filed), 2026-07-31:** JP noted
  `resume_word` (`src/convobox/config.py:168`) is a single `str`, unlike
  `pause_listening_phrases` and the safeword list, which are both lists
  supporting multiple phrases -- worth considering `resume_word`(s) as a
  list too, for the same reason multiple safewords/pause phrases exist
  (STT is unreliable enough live that one exact phrase can be hard to hit
  reliably, as this same session's P1/P4 degraded-confidence findings
  show). Not implemented; not filed as an issue yet.
- **[P8] Pause/resume acknowledgment tone.** Set
  `interaction.pause_resume_ack: tone` (default is `none`, silent --
  verify that default separately: P1-P7's existing passes above were all
  against the silent default, so no separate silent-mode retest is
  needed). With `tone` set:
  1. Say a pause phrase -- confirm a short 3-note descending tone plays
     (`convobox.audio.ack_tones`, E5-C#5-A4) immediately, before or
     alongside the "paused listening" log line.
  2. Say the resume word -- confirm the same three notes play ascending
     (A4-C#5-E5).
  3. Confirm the tone doesn't clip/click at note boundaries and isn't
     jarringly loud relative to normal TTS speech volume.
  4. Say a pause phrase while a response is actively speaking -- confirm
     the pause tone plays cleanly after playback/hard-stop settle, not
     garbled or cut off by the interrupted speech.
  5. Confirm the setting is pickable (not free-text) in both `--settings`
     (TUI) and the web UI's Settings panel, interaction section,
     "Pause/resume sound".
  Not yet live-UAT'd (see DESIGN-barge-in.md's now-resolved P8 entry for
  the ruling and what's still open): whether the tone lands well in
  practice, and whether `tone` should become the shipped default instead
  of `none`.
- **[P9] A hard-stopped in-flight turn can show as a generic
  "error_during_execution" turn -- cosmetic mislabel (see
  `docs/KNOWN-ISSUES.md`).**
  **Log-confirmed live, 2026-08-01, ~20:06:29-36:** a mis-transcribed
  pause attempt ("Stop listing." instead of "Stop listening.") got sent
  to the backend as a real query; the next attempt correctly matched the
  pause phrase and hard-stopped that in-flight `claude-code` call via
  `send_hard_stop()`. The interrupted CLI's own interrupt-confirmation
  text is what surfaces as an `error_during_execution` TUI turn -- never
  logged via this project's own logging, never spoken, doesn't affect the
  hard-stop itself (which worked correctly). TUI-only evidence so far;
  web UI behavior not separately confirmed. No fix built -- not yet
  decided whether a distinct turn label is worth it given it's purely
  cosmetic.

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
  `audio_devices.level_meter()`'s existing RMS math. **Now smoothed
  (2026-07-16)**, per this entry's own flagged next step:
  `ConversationTuiState.update_mic_level()` applies asymmetric
  attack/decay (jumps to a louder reading immediately, eases down 30% of
  the way per chunk from a quieter one) instead of showing the raw
  per-chunk RMS. Unit-tested (`tests/test_conversation_tui.py`), but the
  0.3 decay constant was tuned by feel, not against a live session --
  confirm during a live run it now reads as a smooth meter rather than
  flickering, and isn't so heavily damped that real level changes (e.g.
  moving away from the mic) feel sluggish to show up; retune
  `_MIC_LEVEL_DECAY` in `src/convobox/tui/state.py` if either direction
  is off. Speaker-side live level was deliberately NOT built this pass:
  it would need a cross-thread write from `AudioPlayer.on_block_played`
  (the playback THREAD, not the async mic loop), more care than this
  same-thread update needed -- noted as a follow-up candidate, not
  attempted half-verified. Confirm during a live run: the number moves
  with real speech/silence, tracks roughly what
  `audio_devices.py --test-input` reports for the same device, and reads
  AEC-cancelled (much quieter) during the assistant's own playback when
  AEC is on and converged.
- **[U9] Scrollable panes, added 2026-07-20.** Reported broken (no PgUp/
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
- **[U10] Silent-transition acknowledgments in the transcript pane, added
  2026-07-16.** Three previously-silent transitions now add a `system`
  turn to the TUI transcript (visual only -- no audio earcon, since that
  would need to go through `AudioPlayer`/the AEC reference feed, out of
  scope here): pausing listening (`"paused listening -- say '<resume word>'
  to resume"`), resuming (`"resumed listening"`), and a forced VAD cutoff
  at `max_utterance_s` (`"cut off at the time limit -- still your
  turn"`). Addresses `docs/DESIGN-barge-in.md`'s open question about
  pause/resume feeling "unnervingly silent" and this doc's own **[V5]**
  note that a forced cutoff was "purely a log-line signal." Not yet
  watched live: confirm the wording reads as reassuring rather than
  alarming in the transcript scroll, and that it doesn't fire on the
  common/expected path (i.e. doesn't show up on ordinary turns with no
  pause/cutoff involved).
- **[U11] "Who's expected to act?" ambiguity during the dead-time
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
  from LISTENING. The same `waiting`/`waiting_hint` mechanism now also
  covers the phase-3 approval gate's wait (`approval_gate.is_waiting` --
  see `_working_watchdog`'s status-derivation block) now that voice
  approval is wired live.
- **[U12] "Explain"/"clarify"/"help" during a pending approval now gets a
  spoken answer, not silence (JP, 2026-07-23).** Previously, any utterance
  during a pending approval that wasn't the approval phrase or a deny
  phrase was classified "discuss" and got no spoken reply at all -- the
  prompt stayed open (correct) but the operator got zero feedback that
  their question was even heard. `ApprovalDetector` now has a fourth
  outcome, "explain" (`DEFAULT_EXPLAIN_PHRASES`: explain/explanation/
  clarify/help), distinct from generic "discuss": it speaks the full
  detail of the pending request back via `orchestrator.announce_after_delay`
  (0s delay -- nothing is resuming here to self-barge-in on, unlike the
  "Approval confirmed." announcement's 2s delay). Deliberately reverses
  `render_approval_request_for_speech`'s "don't read commands aloud
  automatically" caution ONLY on explicit request -- same content already
  shown in the TUI/log, just also spoken because the operator asked.
  Unit-tested (`tests/test_approval.py`, `tests/test_approval_prompt_gate.py`)
  including cross-backend content resolution (Codex's `event.content` vs.
  Claude Code's `tool`/`tool_input`, since only Codex populates `content`).
  **Live UAT still needed**: trigger a real pending approval on each
  backend, say "explain", and confirm the full detail is actually spoken
  clearly (not just present in the log) -- the detector/gate logic is
  solid but the actual TTS readback of a real (possibly long) command
  string hasn't been heard live yet.

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
- **[ST5] The same recovery also fires for a bare numpy MemoryError, not
  just RuntimeError.** Live-confirmed 2026-07-22: the identical
  native-allocator pressure can surface as
  `numpy._core._exceptions._ArrayMemoryError` (raised from `np.fft.rfft`
  inside faster-whisper's feature extractor, before ctranslate2's own
  encode step) instead of a `RuntimeError` from ctranslate2 itself --
  confirmed via `_ArrayMemoryError.__mro__` that it's a `MemoryError`
  subclass, not caught by the original `except RuntimeError`, and
  crashed a live session before this fix. Both `transcribe()`'s recovery
  and `_reload_model()` now catch `(RuntimeError, MemoryError)`. If a
  long session ever logs this manifestation instead of the
  `mkl_malloc`/`could not create a memory object` wording, confirm [ST1]
  through [ST4]'s same behaviors still hold -- the recovery code path is
  identical once the exception is caught, only the trigger differs. See
  `docs/field-notes/2026-07-22-native-allocator-leak-also-surfaces-as-numpy-memoryerror.md`.

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

## 13. Agent-initiated artifacts via voice (`show_document` MCP tool, PR #283; resize fix, PR #282)

Both merged 2026-08-16/17, claude-code only (see
`docs/ARTIFACT-PANE-SCOPE.md`'s "Agent-Initiated Artifacts" section for
the full design). Everything below this line has been live-verified
EXCEPT the voice/STT path itself, same gap pattern as [U*]'s own
"scripted test confirms the mechanism, not the trigger" items elsewhere
in this file:

- The tool mechanism end to end: real `claude` CLI discovers and calls
  `show_document`, real SSE delivery to the pane -- verified via a
  scripted `adapter.send_text(...)` call and via `--text` mode, not a
  live mic utterance.
- The resize fix: verified via BrowserOS driving synthetic mouse events
  against a real running server, not a live UAT session's actual mouse.

**[AP5], resolved -- status diagnosed, 2026-08-17: `permission_mode:
plan` reliably blocks both artifact-pane MCP tools.** JP's real voice
UAT confirmed this on a live mic session ("plan mode blocks artifact
pane mcp calls"), matching the earlier scripted signal (one decline,
one success across two `--text`-mode runs on 2026-08-16 -- the success
was the outlier, not the decline). Root cause: `claude_code.py`'s own
module docstring already documented, before `show_document` even
existed, that plan mode makes Claude draft a plan and attempt
`ExitPlanMode` instead of executing a tool -- and `ExitPlanMode` is
disabled in headless mode, so there is no way to grant the approval it's
asking for. This is the model's own plan-mode system-prompt behavior,
a DIFFERENT layer from `_ensure_extra_cli_flags`'s `--settings`
`permissions.allow` grant -- the grant is real and mechanically present
either way (it's what makes `permissive`/`approve` mode work reliably),
but it only affects the CLI harness's own allow/deny gate, not whether
the model chooses to attempt the call at all under plan mode's
"draft first, ask before acting" instructions. **Practical
consequence: `show_document`/`get_shown_artifact` should be treated as
unavailable under `permission_mode: plan`** -- use `permissive` or
`approve` for artifact-pane testing/use. See
`docs/ARTIFACT-PANE-SCOPE.md`'s "Answering 'what artifact is showing?'"
section for the full writeup; the code comment claiming these tools are
usable "regardless of permission_mode" is corrected there too.

### Setup

A scratch working_dir with a few different artifact types makes the
test cases below concrete. PowerShell, recreate anywhere:

```powershell
$wd = "$env:TEMP\convobox-uat-artifacts"
New-Item -ItemType Directory -Force $wd | Out-Null
'{"q1_revenue": 128400, "q2_revenue": 141200, "notes": "padding_padding_padding_padding_padding_padding"}' |
  Out-File -Encoding utf8 "$wd\quarterly_report.json"
"# Architecture`n`nThe orchestrator wires VAD -> STT -> backend -> TTS." |
  Out-File -Encoding utf8 "$wd\notes.md"
"<html><body><h1>Demo chart</h1><p>placeholder</p></body></html>" |
  Out-File -Encoding utf8 "$wd\dashboard.html"
```

`convobox.yaml` (or `--working-dir`/`--permission-mode` flags):

```yaml
backend:
  name: claude-code
  permission_mode: permissive   # also try approve ([AP7]) -- plan is a known dead end for these tools, see [AP5]
  working_dir: "%TEMP%/convobox-uat-artifacts"
web:
  enabled: true
```

Then a normal live mic session: `python scripts/run_convobox.py --config convobox.yaml --web`,
open `http://127.0.0.1:5173/` in a browser, and talk.

### Test items

- **[AP1] A direct voice request opens the pane.** Say something like
  "Show me the quarterly report" or "Open dashboard.html in the
  artifact pane." Confirm the pane opens unprompted (not because you
  asked the agent to write/edit anything) and shows the right file.
- **[AP2] Vague/natural phrasing still resolves correctly.** Say "show
  me the report" (no filename) or "pull up that HTML file" without
  naming `dashboard.html` explicitly. Confirm the agent picks the
  right file from context (it has to Read/Glob the directory first,
  same as any file lookup) rather than guessing wrong or refusing.
- **[AP3] Refocusing an already-shown artifact doesn't duplicate.**
  With `quarterly_report.json` already open, discuss something else,
  then say "show me that report again" or "go back to the quarterly
  report." Confirm the SAME tab is reselected/refreshed (Chooser's
  existing identity-key behavior), not a second tab.
- **[AP4] False-positive check.** Have an ordinary conversation that
  mentions a filename in passing ("I was looking at notes.md
  earlier") without actually asking to see it. Confirm the agent does
  NOT reflexively call `show_document` just because a filename was
  spoken -- this is model judgment, not a hard gate, so a live check is
  the only way to know.
- **[AP5] RESOLVED, 2026-08-17: does not fire under
  `permission_mode: plan`.** JP's live voice UAT confirmed a reliable
  block, not intermittent hesitancy -- see this section's own opening
  paragraph and `docs/ARTIFACT-PANE-SCOPE.md`'s "Plan mode blocks both
  artifact-pane tools" for the full root-cause writeup. Use
  `permissive` or `approve` for artifact-pane testing/use; `plan` is a
  known, architectural dead end for these two tools, not a bug to keep
  probing. (Mode semantics generally:
  [docs/PERMISSION-MODEL.md](PERMISSION-MODEL.md).)
- **[AP6] Resize is usable mid-conversation, not just via scripted
  drag.** With the pane open, actually grab the left-edge handle with
  a real mouse (cursor should show `col-resize` on hover) and drag it
  both directions while a session is live. Confirm it doesn't visibly
  fight with anything else redrawing (new events arriving, TTS
  captions, etc).
- **[AP7] `permission_mode: approve` doesn't prompt for
  `show_document`.** Switch to `approve` mode and repeat [AP1].
  Confirm the tool fires WITHOUT a voice approval prompt (it's granted
  unconditionally, unlike Write/Edit under this mode) -- if it
  unexpectedly gates on approval, that's a real bug in the grant, not
  the hesitancy pattern [AP5] is tracking.

`get_shown_artifact` (GitHub issue #280) is the read-side
complement -- "what's showing" grounded in the real UI state, not just
this session's own memory of the last thing it opened. Already
live-verified via a scripted `adapter.send_text(...)` harness with a
real connected browser tab (see `docs/ARTIFACT-PANE-SCOPE.md`'s
"Answering 'what artifact is showing?'" section) -- same gap as
[AP1]-[AP7] above: the voice/STT trigger path itself is untested.

- **[AP8] A direct voice question is answered correctly.** With
  `quarterly_report.json` already open (from [AP1]), ask "what's
  showing in the artifact pane?" or "which file am I looking at?".
  Confirm the answer names the right file, not a guess from
  conversation history.
- **[AP9] Closing the pane (or switching tabs) by hand, then asking, is
  answered correctly -- not stale.** With an artifact open, manually
  click the pane's close button (or click an older tab in the Chooser),
  THEN ask "what's showing now?". Confirm the answer reflects the
  ACTUAL current UI state (nothing shown, or the older tab), not the
  last thing the agent itself opened -- this is the specific case a
  naive "last broadcast" implementation would get wrong, and the whole
  point of this tool per the issue's own framing.
- **[AP10] Asking before anything has ever been shown.** Fresh session,
  no artifact opened yet. Ask "what's in the artifact pane?". Confirm a
  clear "nothing is currently shown" answer, not an error or a
  hallucinated file name.
