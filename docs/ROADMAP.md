# ConvoBox Roadmap

> Direction set by JP, 2026-07-12, at the close of the first full
> voice-UAT marathon. This is the durable version of those decisions --
> scope arguments end here until JP revises it. Mission framing:
> **voice is a first-class communications channel for driving coding
> agents; the screen becomes the secondary display.** Free. Easy.
> User friction is what kills adoption. Do one thing well first:
> voice-operate any coding agent -- before "frontend any LLM anywhere."

## The end-state picture (v1.0+)

The user speaks; the agent talks ABOUT the work while the screen SHOWS
the work. "See line 35? That's the critical line." / "Show me where
this is erroring and let's figure out what broke." Voice + visual
together drive development. Editor integration (VS Code / VSCodium
plugin) makes the agent able to navigate the user's view -- voice as
channel, editor as canvas.

## Near-term (0.x)

### Pluggable STT/TTS engines (decided)
- Engines are plugins selected in config and INSTALLED AT SETUP TIME --
  ConvoBox never bundles an engine. "We support piper" means the user
  can choose to download/install it during setup, not that we ship it
  (also cleanly sidesteps the piper GPL-vs-MIT packaging question --
  see DEPENDENCY_LICENSE_AUDIT.md).
- Packaging: optional extras per engine (convobox[piper],
  convobox[kokoro], convobox[aec] already exists); the TTSEngine ABC
  already anticipates this (its docstring named Kokoro years... hours
  in advance). STT needs the same ABC treatment as TTS.
- Kokoro (Apache-2.0) is the first second-engine, proving the plug
  points. The voice-picker TUI experience is KEPT AS-IS conceptually
  (JP's explicit call) and adapted per engine -- note Kokoro's voice
  model differs from piper's per-voice-ONNX HuggingFace catalog
  (built-in voice set, no download-per-voice), so the picker's
  browse/audition/choose/persist flow stays while its
  catalog/download mechanics become engine-specific.
- **Auto-download-on-first-use, shipped for Piper 2026-07-20** (see
  `create_tts_engine`/`resolve_voice_paths` in
  `src/convobox/tts/factory.py`): a voice named in config that isn't
  cached yet is downloaded automatically instead of raising with a
  "run voice_picker.py yourself" hint -- mirrors the STT model's
  existing one-time-download-then-offline pattern
  (`convobox.stt.transcriber`). Carry the same convention to Kokoro's
  engine factory once it lands, not just the picker UX noted above.

### ConvoBox Settings TUI (decided; shipped 0.2.0-cycle)
One full-screen ASCII TUI (same rendering discipline as the voice
picker: terminal-size-aware, no special fonts, unit-tested layout)
that manages:
- input/output device selection (with live test-tone + mic-level
  check; host-API disambiguation handled for the user -- nobody should
  ever see "Multiple output devices found" raw);
- STT/TTS engine selection, including install/uninstall of engine
  plugins (guided download at setup time, never bundled);
- backend/LLM-provider connection setup (opencode/claude/codex today;
  provider URLs, health checks);
- the spoken-response contract and audio tunables (below).

Not to be confused with the **live conversation TUI** that
[DESIGN-0.3.0-interaction-and-safety.md](DESIGN-0.3.0-interaction-and-safety.md)'s
Phase 1 adds -- this one edits `convobox.yaml` before/between sessions; the
0.3.0 one runs *alongside* `run_convobox.py` showing the live transcript,
full-detail response pane, and barge-in/approval status while talking.

### Conversation TUI mouse-wheel scrolling (deferred, scoped)
Keyboard scrolling (Tab/Up/Down/PgUp/PgDn/Home/End) shipped 2026-07-20
(`docs/UAT-checklist.md`'s **[U9]**). Mouse wheel support was
deliberately left out of that pass: it needs two unrelated mechanisms,
not one small addition --
- POSIX: enable SGR mouse-tracking mode (`ESC[?1000h` + `ESC[?1006h`)
  and parse `ESC[<64;COL;ROWM`/`ESC[<65;COL;ROWM`  wheel-up/down events.
- Windows: msvcrt's `getwch()` (what the conversation TUI already reads
  keys through) cannot see mouse events at all -- would need the Win32
  Console API directly (`ReadConsoleInput` + `ENABLE_MOUSE_INPUT`/
  `ENABLE_EXTENDED_FLAGS` via ctypes), a different code path from
  everything else the TUI does today.

Windows is also the only tested platform (README support matrix), so
this is real, non-trivial work for the one platform where it's hardest
to get right, with no CI/automated way to exercise real mouse events
either way. Worth doing once the keyboard controls have had a live UAT
pass and mouse support is still wanted -- not blocking today's fix.

### Spoken-response contract (decided: user-selectable, later)
- User-settable response length target (word budget) and per-response
  routing: VERBALIZE vs DISPLAY (spoken summary + full text on screen).
- For now: ride with backend defaults; this lands with the settings
  TUI. This is the #2 UX lever after barge-in.
- **0.3.0 concrete design:** [DESIGN-0.3.0-interaction-and-safety.md](DESIGN-0.3.0-interaction-and-safety.md)'s
  Phase 2 -- voice always gives the tiered/short version, a new TUI's
  full-detail pane always shows the untruncated response, and a
  `ContinueDetector` is the eyes-free "tell me more" escape hatch.

### Safety tiers for destructive actions (decided; design sketch)
When the agent is about to do something destructive-classed and the
instruction arrived BY VOICE (where mishearing is a real input mode):
- The agent must clarify and require an APPROVAL WORD -- a
  user-chosen word, deliberately NOT a common affirmation (no yes/yup/
  uh-huh/oui/da/ja), so casual speech can never approve anything.
- Approvals are recorded and timestamped; options explored later:
  crypto signature over the approval record, or retaining the actual
  audio snippet of the spoken approval.
- Architecture note: this is the inverse of the safeword -- the
  SafewordDetector pattern (deterministic, normalized, checked on raw
  transcript) is the right foundation for a ConfirmwordDetector.
- **0.3.0 concrete design:** [DESIGN-0.3.0-interaction-and-safety.md](DESIGN-0.3.0-interaction-and-safety.md)'s
  Phase 3 -- built for Codex first (it has a real live approval channel);
  Claude Code's headless mode has none, so it gets an `--allowedTools`
  investigation instead, with the PTY/interactive-mode rework explicitly
  deferred past 0.3.0.

### Wake word (decided: post-0.5, designed now)
- Optional "listening" states with an activation wake word
  ("Computer!"-class), trained on THE USER'S OWN VOICE like a
  biometric enrollment: multiple passes -- high/low pitch, fast, slow,
  excited, sleepy -- so other speakers don't trigger it. This is the
  Alexa/Google-Home-style "wake from idle/asleep" engine (openWakeWord
  etc.) -- a genuinely different feature from `interaction.resume_word`
  (which resumes from an already-listening-but-paused state, not from
  asleep); deliberately kept named "wake word" for that reason.
- Research pointers when we get there: openWakeWord / microWakeWord
  (local, trainable); speaker-conditioned wake filtering.
- Explicit deferral (JP, 2026-07-12): open mic WITHOUT speaker
  rejection is acceptable for 0.5/1.0; wake word + enrollment is the
  path to closing the open-mic trust boundary, not speaker-ID on
  every utterance.

### Session persistence (decided: configurable, tiered)
Whether a conversation survives restart depends on what the user has:
nothing -> flat files -> sqlite -> postgres/pgvector, user-configurable,
with ConvoBox suggesting optional installs as needs grow. (Same
install-at-setup philosophy as engines.)

### Tone-of-voice / prosody perception (proposed, not yet decided)
JP, 2026-07-23: a read-only prototype that gives the response layer a
sense of HOW something was said, not just what was said.

1. Keep the existing microphone audio long enough to analyze a rolling
   2-3-second window.
2. Run that window through a local prosody model.
3. Emit modest cues such as `energy: high`, `valence: positive`,
   `uncertainty: possible`, plus a confidence score -- not a single
   "mood" label.
4. Give those cues to the response layer as optional context ("the
   speaker sounds excited," not "the speaker is excited") -- a hint the
   backend prompt may use for pacing/warmth, never a fact asserted back
   to the user.
5. Log and evaluate during UAT, with an easy on/off config switch.

Start with arousal/valence, not sarcasm detection -- more reliable,
easier to validate against your own impressions in a live UAT session,
and directly useful for pacing/warmth without needing to get a much
harder classification problem right first.

**First milestone: read-only.** Observes and logs cues, changes no
behavior at all. Only if those cues consistently feel useful over a
short live test does the dialogue prompt get to see them -- same
"prove it's real before it affects behavior" bar this project already
holds itself to elsewhere (e.g. AEC telemetry before AEC defaults).

### "Works well" budgets (decided: tunable, not yet numbered)
Time-to-first-audio, interrupt latency, echo drops/minute become
tracked numbers with per-user tunable targets -- auditory processing
differs per person, so the bar is a setting, not a constant. Current
bar is honest: "a feeling." Instrument first (AEC telemetry was the
template), then set defaults from data.

## Mid-term
- VS Code / VSCodium extension: voice channel + editor-navigation
  actions (agent can point at lines/files; user can ask to be taken
  to the error).
- Apple Silicon validation (Mac Mini awaits; first second-environment
  test).
- macOS/Linux UAT parity; second-voice, second-room validation.

## Long-term
- Frontend any LLM/provider, cloud and local; desktop/web surfaces.
  Deliberately AFTER the coding-agent niche is nailed (JP's own rule:
  do one thing well; the general-voice-frontend space is crowded,
  the conversational-coding-agent-operation space is not -- see
  docs/DESIGN-echo-and-barge-in.md's competitive notes and the
  2026-07-12 landscape review: existing tools are dictation;
  ConvoBox is conversation).

## Deployment phases (client/server packaging)

Rough phased direction, not commitments — captured to keep design
decisions from painting the architecture into a corner, not as a
schedule.

1. **Native desktop client** (macOS, Windows, Linux). Audio capture,
   listening-state indicators, and TTS playback as a lightweight native
   process per platform, talking to a local server process over
   localhost.
2. **Browser client + networked server.** The server component —
   VAD/STT/TTS/orchestrator/backend adapters — runs the same regardless
   of who's talking to it. A browser tab becomes just another thin client
   (mic in, indicators + audio out) pointed at that server over your own
   private network (e.g. Tailscale) instead of localhost. Exposing
   agent-execution access this way needs real auth, not just "reachable
   on the network" — scoping to a private tailnet, the way other services
   here already are, is the likely default rather than open LAN access.
3. **Mobile — deprioritized, not designed away.** Not being built now,
   but the client/server split above means a native mobile client is
   "just another client" against the same server API later, not a
   re-architecture, as long as that protocol stays platform-agnostic.
   Some phones already do on-device STT/TTS well; the likely mobile shape
   is a hybrid — local STT/TTS for responsiveness/privacy, still calling
   the server (over Tailscale, SSH, or similar) for the actual agent
   execution, since the CLI backends themselves can't run on a phone.

**Cross-platform packaging: Docker for the server, not the client.** The
server-side component (orchestrator, STT/TTS, backend adapters) is a good
fit for a single Docker image that runs identically on Mac/Windows/Linux
hosts — the same container serves the Phase 1 localhost client and the
Phase 2 browser client. The audio-capture/indicator client can't move
into the container the same way: microphone and speaker access don't
pass through Docker cleanly on any of the three platforms (especially
macOS/Windows, where Docker Desktop runs in a VM with no direct hardware
audio access), so that piece stays a thin native process per platform
regardless of how the server is packaged.
