# Status

The detailed, narrative status log behind the README's condensed support
matrix. This is where "what changed and why," live-verification detail,
and the security/performance audit findings live. For the at-a-glance
tested-vs-implemented table, see the README's [Status](../README.md#status)
section; for the formal per-release changelog, see
[../CHANGELOG.md](../CHANGELOG.md).

## Since 0.2.0

The interaction/safety bundle (`DESIGN-0.3.0-interaction-and-safety.md`) is
landing. Phase 1 (barge-in + a live conversation TUI) and Phase 2 (response
tiering) are both implemented and merged/merging into `main`; a version
bump to reflect this as a real release is a separate, deliberate step
(not yet done — package version tracks releases, not individual PRs),
so `pyproject.toml` still says `0.2.0` as of this writing even though
substantially more than that is now on `main`:

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
  10+ minutes) — now recovers with a bounded timeout instead. Not yet
  done: a live voice session with real speakers (verified
  programmatically against the real model so far, per the README
  support matrix), and individual Kokoro voice files' own licenses
  haven't been independently re-checked the way Piper's were.

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
(`pytest tests/`), mypy is clean across the tree, and `scripts/spike.py`'s
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
