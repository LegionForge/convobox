---
title: A dedicated keyword-spotting engine (Sherpa-ONNX) could fix fixed-phrase STT reliability generally, not phrase-by-phrase
status: hypothesis
date: 2026-07-21
project: ConvoBox (github.com/LegionForge/convobox)
versions: faster-whisper base (CPU), Codex CLI backend, ConvoBox interaction.approval_phrase
evidence:
  - convobox-UAT/convobox-tui.log, session 2026-07-21 00:06-00:25 (Codex conversation)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, posed the question live)
    - Codex CLI (OpenAI, model gpt-5.6-terra) — recommendation, made live in a voice session
    - Claude Code (Anthropic claude-sonnet-5) — transcription, writing
  org: https://legionforge.org
  created: 2026-07-21T01:15:00-05:00
  revised: 2026-07-21T01:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A dedicated keyword-spotting engine (Sherpa-ONNX) could fix fixed-phrase STT reliability generally, not phrase-by-phrase

**Context for outsiders**: ConvoBox uses one general-purpose STT engine
(faster-whisper) for everything — ordinary conversation AND a handful of
fixed safety-critical phrases (the approval phrase, the safeword, the
resume word). Whisper is tuned for open conversational transcription, not
a small fixed vocabulary, which turned out to matter in practice.

## Problem

The operator's chosen approval phrase failed round-trip STT verification
repeatedly and in different ways across two separate phrase choices:
`"whisky tango foxtrot"` was heard as `"Whiskey"` (a spelling mismatch
against the NATO-standard pronunciation) and, far more severely,
`"foxtrot"` was mis-transcribed nine different ways across two sessions
(`"Foxtrap"`, `"fox truck"`, `"Fox Tribe"`, `"FOXTRAD"`, `"FOX TROP"`,
...). This is the second phrase-reliability incident of the session (the
first being the original `wake_word` default `"ConvoBox"` → "Control Box"
finding from 2026-07-14, already banked in `convobox-design-decisions`).

## The recommendation

Mid-session, the operator asked Codex directly whether a different STT
approach might help. Codex's answer, from the live log:

> "**Sherpa-ONNX** is the most promising local candidate to evaluate
> first: it supports streaming ASR, keyword spotting, VAD, and speech
> enhancement behind a stable native API. That makes it a good fit for a
> dedicated safety/control path, while leaving normal conversational STT
> alone."

Supporting points made in the same conversation:

- **No GPU dependency**: CPU-only wheels for Windows x64 — sidesteps
  ConvoBox's current cuBLAS DLL-loading problem entirely for this path.
- **Cost**: "a narrow keyword-spotting control channel should be far
  lighter than running another full conversational transcriber" — it
  processes a continuous narrow task rather than decoding unrestricted
  speech per utterance.
- **Latency**: streaming keyword spotting means safety/approval detection
  "can be independent of full-transcript timing," rather than waiting on
  VAD-segmented Whisper output.
- **License**: Apache-2.0 — compatible with ConvoBox's licensing goals
  (relevant given Piper's GPL-3.0 status is an open, tracked concern in
  `DEPENDENCY_LICENSE_AUDIT.md`).

## What transfers

- **Reframes the phrase-reliability problem from "pick a better phrase"
  (done twice this session) to "use the right tool for a fixed small
  vocabulary."** A dedicated keyword spotter could catch the approval
  phrase, safeword, and resume word reliably regardless of phrasing,
  rather than hunting for STT-friendly words one at a time.
- **Overlaps with `docs/ROADMAP.md`'s existing "Wake word (post-0.5)"
  future engine** (an Alexa/Google-Home-style always-listening spotter for
  a low-power idle mode) — Sherpa-ONNX could plausibly serve both that
  future feature AND this safety-phrase-reliability problem in one
  implementation, rather than being two separate initiatives.
- **Status: hypothesis, not evaluated.** No prototype built, no real
  keyword-spotting model tested against ConvoBox's actual phrases. Next
  step if pursued: a small standalone spike testing Sherpa-ONNX's Windows
  CPU wheel against the actual approval phrase / safeword vocabulary,
  independent of the main pipeline.
