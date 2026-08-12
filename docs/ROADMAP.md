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
- **Kokoro (Apache-2.0) has landed and is now the default engine**
  (shipped 2026-07-24, PR #141) -- MIT + Apache-2.0 end to end, unlike
  Piper (GPL-3.0), which moved to an explicit opt-in extra
  (`uv sync --extra piper`) rather than a main dependency. See
  DEPENDENCY_LICENSE_AUDIT.md.
- **Auto-download-on-first-use, shipped for Piper 2026-07-20, carried
  to Kokoro 2026-07-24** (see `create_tts_engine`/`resolve_voice_paths`/
  `resolve_kokoro_model_paths` in `src/convobox/tts/factory.py`): a
  voice/model named in config that isn't cached yet is downloaded
  automatically. Kokoro also gained `refresh_kokoro_voices()` (`[d]` in
  the Settings TUI) to force a re-download when the file's already
  cached -- the auto-download path only fetches when missing, so it
  would never notice kokoro-onnx's upstream release adding voices to an
  already-downloaded file otherwise.
- **Settings TUI voice picker for Kokoro, shipped 2026-07-24 (PR #144)
  -- narrower than originally envisioned here, noted honestly.** This
  section originally assumed adapting `scripts/voice_picker_tui.py`'s
  full-screen browse-and-audition-before-picking experience for Kokoro
  ("the picker's browse/audition/choose/persist flow stays while its
  catalog/download mechanics become engine-specific"). What actually
  shipped instead: `tts.voice`
  in `scripts/settings_tui.py` becomes a real cycle-through-the-54-actual-
  voices field (`kind="kokoro_voice"`, reading the real downloaded voices
  file directly) when engine is kokoro -- naming and selection only, no
  audition-before-picking step. `voice_picker.py`/`voice_picker_tui.py`
  remain Piper-only (they browse Piper's per-voice HuggingFace catalog,
  which has no Kokoro equivalent -- Kokoro ships all 54 voices in one
  fixed bundle). A real "hear each candidate voice before choosing" flow
  for Kokoro -- arguably more valuable there than for Piper, since there's
  no per-voice download decision to make first -- remains unbuilt.
  `[c]` (side-by-side Kokoro/Piper compare, same PR) is a related but
  different feature: it speaks one fixed phrase through each engine's
  currently-configured voice, not a sampler across Kokoro's own 54 voices.

### Alternative local STT engines (watching, not decided)
Prompted by a 2026-07-25 look at [siddsachar/row-bot#152](https://github.com/siddsachar/row-bot/issues/152),
which pointed at [FunASR](https://github.com/modelscope/FunASR) (Alibaba/
ModelScope's speech toolkit, MIT, Python-native, actively released) and its
flagship model **SenseVoice-Small** as a possible alternative to
`faster-whisper`. Architecturally a non-autoregressive transformer encoder
(no decoder loop) rather than faster-whisper's encoder-decoder design --
that's the real source of its speed claim, not a tuning trick.

**Why watching, not prototyping yet:**
- The only benchmark offered ("~70ms for a 10s clip," i.e. RTF ~0.007 vs.
  faster-whisper's measured 0.05-0.13 here) has no hardware, dataset, or
  methodology attached, and came from an account that turned out to be a
  FunASR project insider (a co-author on FunASR's own release commits)
  promoting into an unrelated repo's issue tracker -- treat as marketing,
  not a community-verified number.
- Zero accuracy (WER) figures anywhere in that thread.
- The license story doesn't hold up as advertised: the toolkit itself is
  MIT, but SenseVoiceSmall's *weights* ship under a separate, more
  restrictive "FunASR Model Open Source License Agreement," and it
  actually covers 5 languages (Chinese, Cantonese, English, Japanese,
  Korean), not the "50+" claimed in the issue. A sibling model,
  Fun-ASR-Nano-2512, is genuinely Apache-2.0 and worth a look if this gets
  revisited.

**What would actually justify picking this up:** an independent side-by-
side WER + latency test on this project's own hardware/audio, not the
issue's own numbers taken on faith -- same "prove it's real before it
affects behavior" bar this roadmap already holds itself to elsewhere
(AEC telemetry, tone-of-voice prosody above). `funasr` is a real PyPI
package (Python-native, WebSocket streaming support), so a prototype
would be straightforward to wire up as a second STTEngine implementation
if the numbers ever check out.

**Second candidate, added 2026-08-03 (SOTA STT research pass, prompted by
a real live UAT finding -- see below):** **NVIDIA Parakeet TDT (0.6B v3)**
via the `onnx-asr` PyPI package (not NVIDIA's full NeMo toolkit --
heavy, Linux/CUDA-centric, painful on Windows; `onnx-asr` runs on plain
ONNX Runtime, closer in shape to how faster-whisper already gets used
here). Meaningfully more credible evidence than the FunASR thread above:
- Open ASR Leaderboard (a real, third-party benchmark, not a self-
  reported number from an interested party): WER 6.32% vs. large-v3's
  7.44%, and ~3,300x realtime throughput -- trivial headroom on an 8GB
  consumer GPU.
- Trained specifically on 36,000+ hours of *noisy and non-speech* audio,
  and multiple independent 2026 comparison sources report it rarely
  hallucinates on silence/low-signal input -- directly relevant to this
  project's own live finding (2026-08-02 UAT): a short `resume_word`
  ("Athena") got repeatedly hallucinated by faster-whisper as unrelated
  *fluent* sentences (and once as Cyrillic text) across every model/
  compute_type/device combination tried (base/large-v3, int8/float16/
  float32, cpu/cuda) -- the well-documented Whisper failure mode on
  short/low-signal clips, confirmed to sit upstream of model choice
  within the faster-whisper family specifically.
- Tradeoff: 25 languages vs. Whisper's 99 -- a real cost if ConvoBox
  ever needs more than English, a non-issue if it doesn't.
- A different runtime dependency than today's ctranslate2 (see the
  allocator-leak entry above this one in KNOWN-ISSUES.md) -- moving to
  ONNX Runtime would sidestep that whole class of bug, not just work
  around it, which is a real argument in its favor beyond raw accuracy.

**A third, architecturally different option, same research pass:**
rather than (or in addition to) swapping the general-purpose STT model,
route short/critical phrases (the resume word, safewords, approval
phrase) through a **dedicated wake-word classifier** in front of
faster-whisper -- the standard architecture elsewhere (Home Assistant's
Assist pipeline: mic -> wake-word engine -> full STT only after
activation). **openWakeWord** (Apache/MIT-family, trained on Google's
audio embedding model + Piper-synthesized data, ships a built-in
Silero-VAD gate) is a closed-set classifier, not a generative decoder --
it structurally cannot hallucinate a fluent unrelated sentence the way
Whisper can, sidestepping the failure mode above rather than tuning
around it. This is the same idea as the already-on-hold Sherpa-ONNX
keyword-spotting entry in the ConvoBox quickref's "Interesting Ideas"
(2026-08-01, JP's call: real accuracy against ConvoBox's actual phrases
unevaluated, `MicrophoneStream` is single-consumer so a parallel spotter
needs real broadcast/tee plumbing, and it's in tension with the safety
path's deliberate no-ML design) -- openWakeWord is a concrete alternative
*engine* for that same architectural idea, not a new idea on its own.
Same reasoning for staying on hold: revisit only if `stt.hotwords`
(shipped 2026-08-03, a much smaller change already in flight) turns out
not to be enough on its own.

**Cheaper, do-first candidates from the same research pass, already
shipped 2026-08-03** (not a roadmap item -- small enough to just build):
`stt.hotwords` (faster-whisper's own prompt-biasing param, direct
mitigation for the failure mode above), plus opt-in
`stt.condition_on_previous_text: false` and a pinned `stt.temperature`
-- see `STTConfig` in `src/convobox/config.py` for the live rationale on
each. Worth exhausting these first (near-zero cost, already built) before
spending real effort on either alternative-engine option above.

**One-month follow-up (2026-08-07, no live hardware -- a status check, not
a re-test):** the ctranslate2 allocator leak (see KNOWN-ISSUES.md) is
still open upstream with no fix, and one issue previously read as "closed,
unclear if it covers the leak" turns out to have closed over a live,
unaddressed new leak report -- read as no material change. `onnx-asr`
(the Parakeet TDT vehicle) looks healthier than it did a month ago:
active monthly releases, explicit Windows+CUDA+DirectML support, and a
previously-open GPU-slowness issue now closed -- strengthens the case for
a real prototype, still not started. `openWakeWord`'s maintenance has
visibly slowed (no tagged release since 2024-02) with an open, unresolved
Windows error on this project's own dev platform -- new reason for
caution beyond the already-known integration cost. New candidate spotted:
**Moonshine** (`moonshine-ai/moonshine`) -- very actively developed,
English models cleanly MIT-licensed, but its hallucination-control claims
are currently blog-sourced and unverified (same category of claim that
burned this project once already with FunASR); worth a real look later,
not acted on now. Full sourcing and reasoning:
`docs/field-notes/2026-08-07-stt-engine-continued-investment-research.md`.
Net recommendation: keep faster-whisper shipped, treat a real `onnx-asr`
prototype as "when, not if" rather than urgent.

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

### Web UI transcript timestamps, user-configurable on/off (proposed, scoped)
The TUI's transcript pane has always shown a per-line timestamp; the web
UI never has, in either its live SSE stream or its history replay --
raised directly by JP during a live UAT session (2026-08-06) after
needing timestamps to correlate what he said against the log while
diagnosing an unrelated STT freeze (docs/field-notes/
2026-08-06-resume-word-hallucination-and-runaway-repetition.md).

Scoped, not yet built:
- `history.py`'s `events` table already stores a `timestamp REAL`
  (epoch seconds) column per row, and `get_session_events()`'s
  `SELECT *` already returns it -- history replay has the data today,
  the frontend just never renders it.
- The **live** SSE stream is the actual gap: `WebEventForwarder.
  __call__`'s broadcast (via `event_to_dict()`) and `forward_transcript
  ()`'s `{"type": "transcript", ...}` payload carry no timestamp field
  at all today. Adding one (`time.time()`, matching the DB column's own
  epoch-seconds shape so both paths can share one frontend formatter)
  closes that gap.
- JP wants this **user-configurable on/off**, not always-on (unlike the
  TUI, which has no such toggle) -- needs a new `display.*` (or
  `web.*`) boolean setting, exposed in both Settings surfaces, not just
  a frontend-only visual tweak.

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

### Agent Client Protocol (ACP) support (decided: pursue; scoping in progress)
JP, 2026-07-29, after comparing ConvoBox to
[katipally/openlive](https://github.com/katipally/openlive) (see
[docs/field-notes/2026-07-29-openlive-comparison-and-acp-direction.md](field-notes/2026-07-29-openlive-comparison-and-acp-direction.md)):
reaffirmed the 2026-07-12 mission (voice-operate coding agents, not a
general voice-any-LLM product -- see this file's own intro), and decided
to pursue [ACP](https://agentclientprotocol.com) (JSON-RPC over stdio)
as the standard adapter protocol going forward, inspired by OpenLive's
use of it to support Claude Code/Codex/Cursor/OpenCode/Hermes uniformly.

- Today each backend adapter (`src/convobox/adapters/*.py`) hand-speaks
  that CLI's own native protocol (Claude Code's stream-json NDJSON,
  OpenCode's HTTP/SSE, Codex's app-server JSON-RPC) -- real, empirically
  live-verified engineering (see each adapter's own module docstring),
  but bespoke per backend and not extensible to a new agent without a
  new from-scratch adapter.
- **First scoping question answered, 2026-07-31** (see
  [docs/field-notes/2026-07-31-acp-scoping-only-opencode-native.md](field-notes/2026-07-31-acp-scoping-only-opencode-native.md),
  diagnosed from docs, not yet live-probed): only **OpenCode** exposes
  ACP as a first-party protocol (`opencode acp`, same maintainer,
  claimed permission parity with its terminal mode). **Claude Code**
  and **Codex** each only have third-party bridge processes of
  unverified maturity -- Claude Code's wraps the separate Claude Agent
  SDK rather than the `claude` CLI itself; Codex's just re-wraps the
  same app-server JSON-RPC protocol ConvoBox's own adapter already
  speaks directly, adding a process hop for no clear gain. ACP adoption
  is therefore at least three separate per-backend decisions, not one.
- **Revised next step**: if ACP work continues, OpenCode is the
  low-risk first candidate -- swap that one adapter, use it to verify
  the permission-parity claim against a real running session, and
  leave Claude Code/Codex on their bespoke adapters until a first-party
  ACP server exists for them (or the Claude Code bridge's approval
  semantics are confirmed equivalent to the PreToolUse-hook-based
  voice-gated approval channel ConvoBox's adapter already has
  live-verified -- still open, not yet scoped).
- This is the same mechanism the VS Code / VSCodium mid-term item below
  would likely ride on, if ACP (or a comparable editor-side protocol)
  turns out to cover that too.

## Mid-term
- VS Code / VSCodium extension: voice channel + editor-navigation
  actions (agent can point at lines/files; user can ask to be taken
  to the error).
- ~~Apple Silicon validation (Mac Mini awaits; first second-environment
  test).~~ **Substantially done, 2026-08-10/11** (see
  `docs/field-notes/2026-08-10-*.md` and `2026-08-11-*.md`):
  signal-level AEC, Claude Code + Codex backend connectivity, Kokoro +
  Piper TTS, the real mic loop and safeword hard-stop, and — closing
  what the 2026-08-10 pass left open — a real human-voice demo (safeword
  confirmed 3x live, barge-in confirmed, a genuine self-triggered
  barge-in loop diagnosed and largely mitigated) and opencode (initially
  blocked on provider credentials on that machine; 2026-08-11 got real
  credentials configured and, after a genuine multi-part investigation,
  confirmed opencode's *built-in* auth is broken in `serve` mode across
  three independent causes, but a manually-declared custom provider
  works completely end-to-end — including real tool-calling — through
  ConvoBox). **Still genuinely open**: Chrome/browser-driven web UI UAT
  (tooling unavailable across every session that's tried so far, not
  just this one).
- macOS/Linux UAT parity; second-voice, second-room validation. Linux
  still entirely untested; macOS's human-voice case is now closed
  (above) — what's left here is a genuinely different room/speaker/mic
  setup and a second human voice, not the same gap restated.

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
