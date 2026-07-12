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

### ConvoBox Settings TUI (decided)
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

### Spoken-response contract (decided: user-selectable, later)
- User-settable response length target (word budget) and per-response
  routing: VERBALIZE vs DISPLAY (spoken summary + full text on screen).
- For now: ride with backend defaults; this lands with the settings
  TUI. This is the #2 UX lever after barge-in.

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

### Wake word (decided: post-0.5, designed now)
- Optional "listening" states with an activation wake word
  ("Computer!"-class), trained on THE USER'S OWN VOICE like a
  biometric enrollment: multiple passes -- high/low pitch, fast, slow,
  excited, sleepy -- so other speakers don't trigger it.
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
