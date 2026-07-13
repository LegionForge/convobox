# Conversation-design references

Research grounding for ConvoBox's turn-taking, barge-in, interrupt, and
backchannel behavior. The point isn't scholarship for its own sake — it's
that "how should a voice assistant handle interruption" is a question with
50 years of empirical answers, and we'd rather adopt findings than
re-derive them from vibes.

Each entry: the **finding**, then **Adopt →** what it means for ConvoBox.

> Provenance: the modern / less-canonical entries (Skantze 2021, VAP 2022,
> dGSLM 2023, Moshi 2024, Stivers 2009, Ward & Tsukahara 2000) were
> web-verified July 2026. The foundational conversation-analysis and
> pragmatics classics are cited from the standard literature; confirm
> against the primary source before any formal/public citation.

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
**Adopt →** Read-this-first for the 0.2.1 barge-in design; use its taxonomy
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

**Industry design guides: Amazon Alexa Design Guide; Google Conversation
Design.**
The codified, shipped-at-scale barge-in and confirmation patterns.
**Adopt →** Sanity-check our preset behaviors against what hundreds of
millions of users are already trained on (wake-word interrupt = Alexa;
open barge-in = modern LLM voice modes).

---

## What we're adopting for the 0.2.1 barge-in cycle

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
