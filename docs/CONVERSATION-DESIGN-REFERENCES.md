# Conversation-design references

Research grounding for ConvoBox's turn-taking, barge-in, interrupt, and
backchannel behavior. The point isn't scholarship for its own sake — it's
that "how should a voice assistant handle interruption" is a question with
50 years of empirical answers, and we'd rather adopt findings than
re-derive them from vibes.

Each entry: the **finding**, then **Adopt →** what it means for ConvoBox.

> Provenance: the modern / less-canonical entries (Skantze 2021, VAP 2022,
> dGSLM 2023, Moshi 2024, Stivers 2009, Ward & Tsukahara 2000, Pipecat,
> LiveKit Agents, Deepgram Flux, Vocode, ElevenLabs Conversational AI,
> Google Conversation Design) were web-verified July 2026 by reading real
> primary-source pages/code, not secondhand summaries. The foundational
> conversation-analysis and pragmatics classics are cited from the
> standard literature; confirm
> against the primary source before any
> formal/public citation. The Alexa Design Guide entry is explicitly
> flagged as NOT primary-source-verified this pass (see that entry) --
> don't treat it as equally solid.

---

## 1. Turn-taking: when does the floor change hands?

**Sacks, Schegloff & Jefferson (1974). "A Simplest Systematics for the
Organization of Turn-Taking for Conversation." *Language* 50(4):696–735.**
The founding paper. Turns are built from **turn-constructional units
(TCUs)**; the floor becomes available at **transition-relevance places
(TRPs)** — the boundaries between units — not continuously. Humans achieve
fluent exchange with very small gaps and little overlap.
**Adopt →** Interruption isn't binary noise; there are natural yield points.
A graceful barge-in yields at TRPs. v1 approximates that with a
duration + content threshold; TRP-aware yielding is a later upgrade.

**Skantze (2021). "Turn-taking in Conversational Systems and Human-Robot
Interaction: A Review." *Computer Speech & Language* 67.**
The best single entry point for putting turn-taking into a *machine* —
surveys endpointing, overlap, backchannels, and continuous vs. turn-based
models.
**Adopt →** Read-this-first for the 0.3.0 barge-in design; use its taxonomy
as our vocabulary (endpointing, overlap-management, backchannel handling).

---

## 2. Backchannels / continuers: the sounds that must NOT interrupt

**Yngve (1970). "On getting a word in edgewise." *Papers from the 6th
Regional Meeting, Chicago Linguistic Society*, 567–578.**
Coined "back-channel": the listener's channel of "mm-hmm / uh-huh / yeah"
running alongside the speaker's turn.

**Schegloff (1982). "Discourse as an interactional achievement: Some uses
of 'uh huh' and other things that come between sentences." In *Analyzing
Discourse: Text and Talk* (Georgetown Round Table), D. Tannen (ed.).**
Establishes these tokens as **continuers** — they signal "I'm following,
keep going," and are explicitly *not* bids for the floor.

**Ward & Tsukahara (2000). "Prosodic features which cue back-channel
responses in English and Japanese." *Journal of Pragmatics* 32(8):
1177–1207.**
Backchannels are prosodically cued — listeners drop them in after a
**region of low pitch (~110 ms)** late in the speaker's unit.
**Adopt →** A `natural`-mode barge-in must **filter backchannels**: a short,
affirmation-class token ("mm-hmm / yeah / uh-huh / right / oh") should not
count as an interrupt. This is the single most important finding for
matching user expectations — it's exactly the instinct behind the grid's
"don't interrupt on backchannels." (Bonus: the same cue model tells us how
to *produce* backchannels later — the assistant murmuring "mm-hmm" while the
user talks is a large naturalness win.)

---

## 3. Timing: the human-calibrated latency target

**Stivers et al. (2009). "Universals and cultural variation in turn-taking
in conversation." *PNAS* 106(26):10587–10592.**
Across 10 languages, the median between-turn gap is **~200 ms**, strikingly
universal (ranging from near 0 ms in Japanese to ~469 ms in Danish); longer
gaps start signaling "trouble" / a dispreferred response.

**Levinson & Torreira (2015). "Timing in turn-taking and its implications
for processing models of language." *Frontiers in Psychology* 6:731.**
Because gaps are so short, listeners must *predict* turn-ends and prepare
their response before the current turn finishes.
**Adopt →** Gives our "stop fast" instinct a *number*: interrupt-stop and
response-start latency should target **~200 ms**, with sub-second as the
ceiling. Instrument it as a tracked metric (same template as the AEC
telemetry). Prediction (§4) is how you beat a pure silence timer.

---

## 4. Machine turn-taking: endpointing and prediction

**Raux & Eskenazi (2009). "A Finite-State Turn-Taking Model for Spoken
Dialog Systems." NAACL-HLT.** *(representative of the endpointing line of
work; not re-verified this pass)*
Dynamic endpointing — deciding the user has finished — beats a fixed silence
timeout.

**Ekstedt & Skantze (2022). "Voice Activity Projection: Self-supervised
Learning of Turn-taking Events." *Interspeech 2022*, 5190–5194.
arXiv:2205.09812.**
A self-supervised model that **predicts** upcoming turn-shifts *and*
backchannels directly from raw audio — outperforming silence-timer VAD, with
public code.
**Adopt →** This is the principled version of our "semantic VAD" idea and
the upgrade path beyond Silero's silence-timer endpointing: predict
turn-shift-vs-backchannel instead of waiting out a silence. A concrete
roadmap target once the preset/grid control surface is in.

**Pipecat (pipecat-ai/pipecat, Apache-2.0, 13.4k★, verified 2026-07-14 by
reading real source, not just docs).** A production, Python, voice-agent
*framework* (not a single app) — the closest architectural cousin to
ConvoBox surveyed so far. Four concrete, source-verified findings:

- **Pluggable turn-start *strategies***, not one hardcoded rule:
  `BaseUserTurnStartStrategy` with swappable implementations —
  `MinWordsUserTurnStartStrategy` (N words to interrupt while the bot is
  speaking, 1 word when idle — read the actual class:
  `src/pipecat/turns/user_start/min_words_user_turn_start_strategy.py`),
  a transcription-based one, an external-signal one, even a commercial
  voice-isolation vendor integration (Krisp VIVA). **Adopt →** Word-*count*
  is a simpler, complementary backstop to our word-*list* backchannel
  filter (`is_backchannel()`): our list precisely excludes known continuers
  by name but says nothing about a short, garbled, off-list STT scrap
  during playback. A future refinement: treat "short AND low-confidence
  AND bot-speaking" as backchannel-shaped too, not just an exact word-list
  hit. Also validates a pluggable-strategy shape as the eventual home for
  our `trigger: speech | push-word` axis, if it grows a third option later.
- **`AlwaysUserMuteStrategy`** — a named, first-class "the bot always
  finishes talking, user input is suppressed until then" mode. **Adopt →**
  Independent, real-production validation that our `do-not-disturb` preset
  (`let-finish` + `drop`) is a legitimate, wanted mode, not
  over-engineering a rarely-used cell.
- **"Filter Incomplete Turns" example** — the framework asks the *LLM
  itself* to classify the user's utterance as complete / cut-off-short /
  needs-time-to-think (3 outcomes, each with its own reprompt delay) before
  generating a real response, using the same model already in the loop, no
  extra classifier needed. **Adopt →** A different mechanism than VAP
  (semantic judgment of the *transcript* vs. VAP's acoustic prediction from
  raw audio) solving a related-but-distinct problem (was the user's own
  utterance cut off, not whether *our* response should be interrupted).
  Doesn't transplant directly — ConvoBox is a thin client over external
  coding-agent CLIs we don't control the system prompt of, unlike
  Pipecat's direct LLM access — but a *cheap local heuristic* version
  (trailing conjunction / no terminal punctuation → "sounds cut off") is a
  candidate for the VAD/segmenter layer, distinct from `ContinueDetector`
  (which is about the *assistant's* response, not the user's utterance).
- **Frame-priority queue**: `SystemFrame`s (start/end/interruption
  signals) preempt `DataFrame`/`ControlFrame`s via a dedicated
  `asyncio.PriorityQueue`, guaranteeing an interrupt signal cuts ahead of
  pipeline backlog regardless of load. **Adopt →** Different mechanism
  (their whole architecture is frame-based; ours is procedural), same
  *principle* already in our code: the safeword check runs unconditionally
  before every other gate, and `BargeInMonitor` operates at the raw-audio
  level specifically so it doesn't wait behind STT. Real-production
  confirmation the principle is right, not something to restructure our
  code to imitate mechanically.

**LiveKit Agents (livekit/agents, Apache-2.0, verified 2026-07-14 by
reading the real docs page (docs.livekit.io/agents/build/turns/) and the
real turn-detector plugin source
(livekit-plugins-turn-detector/livekit/plugins/turn_detector/base.py),
not secondhand summaries).** A production, Python, real-time voice-agent
framework — the other close architectural cousin to ConvoBox besides
Pipecat, but built around a genuinely different endpointing mechanism
worth contrasting. Three concrete, source-verified findings:

- **A small, local, text-based end-of-turn classifier, not audio-based
  VAP.** The shipped "Turn Detector Model" is an ONNX transformer that
  takes the last 6 turns of CHAT TEXT (not raw audio), tokenizes up to
  128 tokens, and outputs a single `eou_probability` float, compared
  against a **language-specific** threshold loaded from `languages.json`.
  Docs describe it as predicting end-of-turn "from both the meaning of
  speech and its acoustic properties, on top of VAD" — i.e. explicitly
  layered ON TOP of Silero VAD, not a replacement for it. **Adopt →** A
  second, real, shipped answer to "beat a fixed silence timer" alongside
  VAP (§4 above) — but semantic-from-transcript rather than
  acoustic-from-audio, and notably *language-specific thresholds*, which
  ConvoBox doesn't have anywhere today (our `min_silence_ms`/
  `min_language_probability` gates are single global numbers regardless
  of detected language). Not proposing to build a transformer classifier
  for ConvoBox — the point is architectural validation that a lightweight
  local *text* classifier over recent turns is a legitimate, shipped
  complement to acoustic VAD, distinct from the heavier VAP/full-duplex
  end of the spectrum (§5).
- **`max_words`/`max_duration` + `on_user_turn_exceeded` callback caps a
  user's turn and proactively intervenes**, rather than letting VAD buffer
  an unbounded monologue. **Correction (2026-07-14, caught re-reading the
  actual code rather than trusting this entry's own first draft): the
  force-flush half of this is NOT missing.** `docs/UAT-checklist.md`'s
  **[V3]** (2026-07-09) observed `vad.max_utterance_s=None` (uncapped)
  meant no transcript until a 30.5s monologue finally paused, and wrote
  "candidate upstream improvement: max-duration forced flush" as an open
  idea — but that was fixed the very next day, PR #1
  (`4aa61ac`, 2026-07-10, "Add UAT-derived input guards: max_utterance_s
  cap and STT confidence gate"). `UtteranceSegmenter._process_window`
  already force-emits at `max_utterance_s` when set, well before this
  research pass, verified via `tests/test_vad_segmenter.py`'s
  `test_max_utterance_cap_splits_continuous_speech` (a 70-window
  continuous run with a 1s/31-window cap correctly splits into two capped
  utterances plus an in-progress remainder). **Adopt →** What's genuinely
  still open, narrower than originally written here: (1) the *default* is
  still `None` (uncapped) — a real product decision about what default
  cap to ship, not missing code; (2) unlike LiveKit's named
  `on_user_turn_exceeded` callback, `UtteranceSegmenter.feed()` returns
  plain `list[np.ndarray]` with no signal distinguishing a forced-flush
  utterance from a naturally-silence-ended one, so the main loop can't
  (e.g.) log or announce "still listening, that was a checkpoint, not the
  end" differently — a real, narrow, still-scoped gap, just a much
  smaller one than "force-flush doesn't exist."
- **False-interruption recovery** (`resume_false_interruption`,
  `false_interruption_timeout`): when VAD-detected "speech" during agent
  playback produces an empty transcription, LiveKit treats the
  interruption as false and **resumes** the agent's speech rather than
  leaving it cut off. **Adopt →** Traced this against ConvoBox's own
  `BargeInMonitor` (`scripts/run_convobox.py`) and found the same gap,
  unflagged anywhere in `docs/DESIGN-barge-in.md` or
  `docs/DESIGN-echo-and-barge-in.md` until now: `BargeInMonitor.observe()`
  fires — stopping playback, and for `on_current_turn == "abort"`,
  hard-stopping the backend turn — purely from VAD-level sustained-speech
  duration, entirely BEFORE STT produces a transcript. When the transcript
  later arrives and turns out to be a backchannel (`is_backchannel(text)`)
  or presumably an empty/noise-triggered false positive, the main loop
  correctly DROPS it as "not a real interrupt attempt" — but the response
  is already gone; there is no resume path, matched or not. For
  `conversational`/`halt`/`take-over` presets (the only ones where
  `BargeInMonitor` can fire at all — `let-finish` short-circuits it), a
  false VAD trigger costs the user the rest of an in-progress answer for
  free. **Not built this cycle, deliberately**: an actual resume mechanism
  would need to reconstruct "how much of the response was already spoken"
  and re-enter TTS mid-stream, which is a real architectural question
  (how does this interact with `tier_responses`'s own tracked
  reveal-state? does a resumed response re-announce itself?) — exactly
  the kind of thing this session's discipline says to scope properly
  rather than rush, and it's audio-behavior-dependent in a way this
  environment can't live-verify. Flagged here and worth a line in
  `docs/DESIGN-barge-in.md`'s open questions the next time that doc is
  touched, not invented as unscoped code today.

**Deepgram Flux / Voice Agent API (developers.deepgram.com, verified
2026-07-14 by reading the real Flux agent doc, not a secondhand
summary).** A commercial STT vendor's own production voice-agent stack —
useful because Deepgram builds the ASR itself, so their endpointing
opinions are informed by owning that layer, not bolted on top of a
third-party transcript stream the way ConvoBox's Whisper-based pipeline
necessarily is.

- **"Eager" end-of-turn: a two-stage speculative response pipeline.**
  Flux emits `EagerEndOfTurn` at *medium* confidence — before final
  certainty — letting the caller start LLM generation early; a
  `TurnResumed` event cancels that speculative work if the user keeps
  talking; a final `EndOfTurn` confirms and the prepared response
  proceeds. Tunable via `eot_threshold` (final confidence),
  `eager_eot_threshold` (speculative-start confidence), and
  `eot_timeout_ms`. **Adopt →** A real, shipped instance of "predict
  before you're sure, cancel if wrong" — the same family of idea as
  VAP's turn-shift prediction (§4 above) and Pipecat's incomplete-turn
  LLM classification, but applied to *response latency* specifically
  rather than *when to yield the floor*. Doesn't transplant directly for
  the same reason Pipecat's LLM-classification example didn't: ConvoBox
  is a thin client over external coding-agent CLIs (opencode/Claude
  Code/Codex) it doesn't control the generation of, so there's no
  "start the LLM speculatively" lever to pull the way a framework with
  direct model access has. Worth remembering as the shape a *future*
  deeper backend integration could take, not something buildable at
  ConvoBox's current architectural layer.

**Vocode (docs.vocode.dev, open-source conversation framework, verified
2026-07-14 by reading the real conversation-mechanics doc).** Smaller
and older than Pipecat/LiveKit Agents, but two findings worth banking:

- **`interrupt_sensitivity`: an explicit low/high toggle, with backchannel
  filtering as the named "low" behavior.** Low sensitivity (their default)
  "makes the bot ignore backchannels (e.g. 'sure', 'uh-huh') while the bot
  is speaking"; high sensitivity "makes the agent treat any word from the
  human as an interruption." **Adopt →** A third independent, real-
  production validation (after Pipecat's word-count strategies and the
  backchannel-filtering literature in §2) that filtering backchannels by
  default — not treating every utterance as a bid for the floor — is the
  correct default behavior, not an ConvoBox-specific judgment call. Their
  binary low/high is coarser than ConvoBox's five-preset grid; the grid
  is still the right level of control, this just confirms the *direction*
  of the default.
- **`conversation_speed` / `speed_coefficient`: response latency scales
  with the CURRENT user's own observed speaking rate (words-per-minute),
  not a fixed number.** "The amount of time the bot waits inversely
  scales with the `conversation_speed` value," computed dynamically per
  session. **Adopt →** A genuinely new idea for ConvoBox, distinct from
  everything else in this doc: every timing knob here so far (`
  min_silence_ms`, `continue_timeout_s`, Deepgram's `eot_timeout_ms`,
  LiveKit's per-*language* thresholds) is static once configured — none
  scale to the *individual* speaker's pace within a session. A fast
  talker and a slow, deliberate talker get the same silence-timeout
  today. Not built this cycle (needs a real design for what "observed
  speaking rate" even means from VAD-only signal, and live-mic tuning to
  validate it doesn't feel erratic) — but worth naming as a concrete,
  distinct-from-everything-else-flagged roadmap candidate, since it's a
  different axis than the presets/thresholds already designed.
- **`utterance_cutoff_ms`/"mark final if no new words in X seconds"**
  confirms Vocode's own DEFAULT endpointing is still a plain silence
  timer, same family as ConvoBox's `min_silence_ms` — even a more
  sophisticated production framework ships a silence-timer baseline and
  treats semantic/adaptive endpointing as a layered addition, not a
  replacement. **Adopt →** Reassurance, not a gap: ConvoBox's baseline
  approach is the same starting point real frameworks use; VAP/semantic
  upgrades (§4 above) are genuinely optional upgrades, not table stakes
  ConvoBox is missing.

**ElevenLabs Conversational AI (elevenlabs.io/docs, commercial, verified
2026-07-14 by reading the real conversation-flow and skip-turn docs
pages).**

- **`turn_eagerness`: a three-level named knob (`patient`/`normal`/
  `eager`) for how quickly the assistant jumps in.** **Adopt →** Real,
  independent, production validation that ConvoBox's own naming
  choice — the `patient` preset (`let-finish` + `queue`,
  `docs/DESIGN-barge-in.md`) — lands on the exact same word a major
  commercial voice-agent platform uses for the same underlying axis.
  Not a new idea to adopt, a confirmation an existing one already named
  itself correctly.
- **Faster response generation: TTS starts on "enough words and a
  comma from the language model," not a complete sentence** — i.e.
  speaking begins from PARTIAL, still-generating LLM output. **Adopt →**
  Doesn't transplant to ConvoBox, and it's now the THIRD time this
  exact shape of limitation has come up (Deepgram Flux's eager-EOT
  speculative response, Pipecat's incomplete-turn LLM classification,
  now this) — worth naming as a real, recurring architectural
  boundary rather than three separate one-off caveats: **every
  commercial/framework voice-agent surveyed this session gets its most
  sophisticated latency tricks from direct access to LLM token
  generation, and ConvoBox deliberately doesn't have that.** ConvoBox
  is a thin client over external coding-agent CLIs (opencode/Claude
  Code/Codex) that only surface COMPLETE messages/blocks, checked per
  adapter rather than assumed: OpenCode and Codex both have real
  delta-style events on the wire that this codebase's own docstrings
  confirm are deliberately ignored in favor of the terminal
  `text.ended`/`item.completed`-equivalent event (`opencode.py`'s
  `_TEXT_ENDED` handling, `codex.py`'s module docstring: "deltas exist
  too; ignored, same policy as OpenCode's text.ended-not-text.delta").
  Claude Code's `stream-json` protocol, as consumed here, is
  block-based rather than delta-based in the first place (`assistant`
  messages arrive with complete `content` blocks) — no delta exists on
  that wire to ignore, but the outcome is the same: no adapter exposes
  a hook for "start speaking before the message is complete." This
  isn't a gap to close; it's the actual shape of the "**Backend-agnostic
  by design**" commitment `README.md` already states explicitly (a thin
  `send_text`/`send_interject`/`send_hard_stop`/`is_busy` adapter
  interface per backend, preferring each tool's native structured
  interface over scraping). Worth stating plainly so a future session
  doesn't mistake "we found this again" for "we should build it" — the
  fix would require abandoning backend-agnosticism, not adding a
  feature.
- **"Skip turn": a system tool letting the LLM itself decide to go
  silent** ("Give me a second," "let me think" -> the agent stops
  speaking and waits for the user, not a timeout). **Adopt →**
  Genuinely different from everything else in this doc: every other
  mechanism surveyed decides interruption/turn-taking from the
  CLIENT/audio side (VAD timing, backchannel word-lists, silence
  timers); this is the ASSISTANT voluntarily yielding the floor based
  on conversational content it detects. Also doesn't transplant for the
  same reason as the point above — ConvoBox doesn't control what tools
  the coding-agent CLIs it fronts expose, so there's no lever to add an
  agent-side "stay silent" tool call even if the idea is sound. Filed
  as an idea worth knowing exists, not a roadmap item; unlike the
  `turn_eagerness`/generation-access points above, this one has no
  ConvoBox-side equivalent to validate or contrast against (ConvoBox's
  `PauseListeningDetector` is user-initiated silence, a different
  direction entirely).

---

## 5. Full-duplex generative models (the frontier / ceiling)

**Nguyen et al. (2023). "Generative Spoken Dialogue Language Modeling."
*TACL* 11:250–266. arXiv:2203.16502 (dGSLM).**
Textless dual-channel model that generates two sides of a conversation
simultaneously — reproducing overlap, laughter, and naturalistic turn-taking
without segmenting into strict turns.

**Défossez et al. (2024, Kyutai). "Moshi: a speech-text foundation model for
real-time dialogue." arXiv:2410.00037.**
First real-time **full-duplex** spoken LLM (~200 ms practical latency),
modeling user and system speech as parallel streams — natively handling
overlap, interruptions, and interjections that a VAD→ASR→LLM→TTS pipeline
cannot.
**Adopt →** Not a v1 target — ConvoBox is deliberately a *pipeline* so it can
front *any* coding agent, not a single end-to-end speech model. But this is
where the ceiling is, and it validates the direction: strict turn
segmentation is the limitation; overlap/backchannel handling is the prize.
Worth tracking as the long-horizon comparison.

---

## 6. Practitioner / product design

**Grice (1975). "Logic and Conversation." In *Syntax and Semantics 3:
Speech Acts*, Cole & Morgan (eds.).**
The Cooperative Principle and maxims (quantity, quality, relation, manner).
**Adopt →** The maxim of quantity says: don't over-talk. The best way to
reduce the *need* to interrupt is to not say too much in the first place —
which ties directly into the spoken-response-contract (verbosity/length
control) roadmap item.

**Pearl (2016). *Designing Voice User Interfaces*. O'Reilly.**
The practitioner reference for VUI: barge-in, confirmations, error recovery,
discoverability.
**Adopt →** Use as the checklist for the non-turn-taking parts of the voice
UX (confirmation flows — cf. the ConfirmwordDetector — and error recovery).

**Google Conversation Design (developers.google.com/assistant/conversation-design,
verified 2026-07-14 by reading the real live pages, not a secondhand
summary — `/confirmations` and `/errors`).** Google's own shipped-at-scale
guidance for the Assistant/Actions platform. Two concrete, source-quoted
findings:

- **Confirmation policy matches `ConfirmwordDetector`'s existing design,
  independently**: *"Double-check with the user prior to performing an
  action that would be difficult to undo, for example, deleting user data,
  completing a transaction, etc."* paired with *"Don't confirm if the
  input is simple and typically recognized with high confidence, for
  example, yes/no grammars"* (their own stated anti-pattern: a redundant
  "Ok, yes"). **Adopt →** Real-production validation of the exact split
  ConvoBox already ships: `ConfirmwordDetector`'s strict ban on common
  affirmations for approval-class prompts (PR #29) vs. `ContinueDetector`
  and other low-stakes detectors allowing a bare "yes" — Google's
  reversible/self-evident vs. irreversible/destructive line is the same
  line ConvoBox already drew, not a new idea to import.
- **The No-Input / No-Match error-escalation ladder is a genuinely new,
  adoptable pattern — ConvoBox doesn't have this today.** *"Users should
  experience no more than 3 No Input or No Match errors in a row, after
  which your Action should play the appropriate max error prompt and
  exit."* The ladder itself: 1st attempt — brief rephrase, no
  over-explaining; 2nd attempt — escalate with examples/options (Google's
  own note: examples work better than instructions, since they model the
  expected response implicitly); max attempt (2-3) — end gracefully with
  a concrete next step, never a vague "try again later." **Adopt →**
  ConvoBox's `min_language_probability` gate today just silently drops a
  low-confidence transcript with no user-facing signal and no escalation
  state — a real gap next to this pattern. A future improvement: track
  consecutive low-confidence/empty-transcript counts per session, and
  after N in a row, speak something (not just log it) rather than sitting
  in silence indefinitely from the user's perspective. Not built this
  cycle — flagged as a concrete, scoped roadmap candidate, not vague
  "add error handling."

**Amazon Alexa Design Guide (alexa.design/guide, developer.amazon.com/.../design).**
Attempted the same live-read verification this cycle; the guide is
presented as an interactive, audio-example-driven experience rather than
static indexable pages, and didn't yield fetchable, quotable content the
way Google's docs did. Leaving the general, less-verified citation here
rather than overclaiming specifics: Alexa's wake-word-gated interaction
(no true open barge-in on-device) is the real-world validation for
ConvoBox's `push-word` trigger option, and its "no confirmation on
simple/reversible actions" convention is well-known industry practice
independent of this fetch attempt — but unlike the Google findings above,
this wasn't verified against primary-source text this pass.
**Adopt →** Revisit with a more targeted URL/search if Alexa-specific
detail becomes load-bearing for a future decision; don't cite it as
source-verified until it actually is.

---

## What we're adopting for the 0.3.0 barge-in cycle

1. **Backchannels are continuers — filter them, don't interrupt** (Schegloff;
   Ward & Tsukahara). Short affirmation-class tokens never trigger barge-in.
2. **~200 ms transition target** (Stivers) — a real, tracked latency metric
   for interrupt-stop and response-start, not a guess.
3. **TRPs exist** (Sacks et al.) — v1 uses a duration + content threshold;
   TRP-/prediction-aware yielding (VAP) is the upgrade path.
4. **Don't over-talk** (Grice) — verbosity control lowers interruption
   pressure at the source.
5. **Presets = the control surface; VAP / full-duplex = the engine upgrade**
   for later cycles. Match the mental models users already have (Alexa,
   Google, ChatGPT voice) rather than inventing a new one.
6. **Confirmation policy is validated, not new** (Google Conversation
   Design) — `ConfirmwordDetector`'s strict-vs-lightweight split already
   matches Google's irreversible/reversible line.
7. **Error-escalation ladder is a real, scoped gap** (Google Conversation
   Design) — low-confidence transcripts are silently dropped today with no
   escalating user-facing signal; a future candidate, not built this cycle.
8. **Corrected (2026-07-14): the monologue force-flush already exists**
   (`UtteranceSegmenter`'s `max_utterance_s` cap, PR #1/`4aa61ac`,
   2026-07-10) — this entry originally claimed it was still missing,
   which was wrong (see §4's LiveKit entry for the full correction). The
   genuinely-open remainder, independently validated by LiveKit Agents'
   `max_words`/`max_duration`/`on_user_turn_exceeded`: the default stays
   uncapped (a product decision) and forced-flush utterances carry no
   signal distinguishing them from naturally-ended ones.
9. **False-interruption recovery is a real, newly-identified gap**
   (LiveKit Agents' `resume_false_interruption`) — `BargeInMonitor` fires
   and stops playback purely from VAD timing, before STT can confirm the
   "interruption" wasn't a backchannel/noise false positive, and there's
   no resume path today; flagged for `docs/DESIGN-barge-in.md`, not built
   this cycle (needs real design work on how a resume interacts with
   response tiering, and can't be live-audio-verified in this
   environment).
10. **Backchannel filtering as the default, not the exception, is now
    triply validated** (Pipecat's word-count strategies, the §2
    literature, and Vocode's `interrupt_sensitivity` low/high split) —
    the direction of ConvoBox's default is confirmed by three independent
    production sources; no change needed.
11. **Per-speaker adaptive timing is a genuinely new, distinct roadmap
    candidate** (Vocode's `conversation_speed`) — every timing knob
    ConvoBox has today is static once configured; scaling to the current
    speaker's own observed pace is a different axis than the
    presets/thresholds already designed, not built this cycle (needs a
    real design for what "observed speaking rate" means from VAD-only
    signal).
12. **`patient` is validated, not new** (ElevenLabs' `turn_eagerness`) —
    a major commercial platform names the same axis with the same word.
13. **The "direct LLM access" limitation is a real, recurring
    architectural boundary, not three unrelated caveats** (Deepgram
    Flux's eager-EOT, Pipecat's incomplete-turn classification,
    ElevenLabs' partial-generation TTS start and skip-turn tool) — every
    framework surveyed gets its most sophisticated latency/turn-taking
    tricks from direct LLM token-generation access, which ConvoBox
    deliberately doesn't have (`README.md`'s "Backend-agnostic by
    design"). Not a gap; the actual shape of the architecture. Worth
    remembering so a future session doesn't propose building one of
    these without first proposing abandoning backend-agnosticism, which
    is the real trade being made.
