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
    - Claude Code (Anthropic claude-sonnet-5) — transcription, writing, follow-up API research
  org: https://legionforge.org
  created: 2026-07-21T01:15:00-05:00
  revised: 2026-07-21T03:35:00-05:00
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
- **Status: hypothesis, API-confirmed, still not integration-tested.**

## Follow-up research (2026-07-21, same session, later cycle)

Installed the real `sherpa-onnx` package (PyPI, latest 1.13.4) into an
isolated throwaway venv (not this project's own) to check real
feasibility claims rather than taking the recommendation at face value:

- **Real Windows wheels confirmed**: `sherpa_onnx-1.13.4-cp312-win_amd64.whl`
  exists on PyPI, matching this project's Python 3.12 — not a
  Linux/macOS-only claim.
- **The wheel itself is small (2.1MB)** — supports the "cheaper than a
  second full transcriber" claim at the library level, but this is
  bindings only. A real pretrained keyword-spotting model (encoder/
  decoder/joiner ONNX files, typically tens-to-hundreds of MB, hosted
  separately on the k2-fsa model releases/Hugging Face) is a SEPARATE
  download this note's original write-up didn't make explicit — same
  "downloads on first use, not bundled" pattern as faster-whisper/Piper,
  but worth stating plainly rather than implying the 2.1MB wheel is the
  whole cost.
- **`sherpa_onnx.KeywordSpotter` is real and matches Codex's claim**,
  confirmed via `inspect.signature`:
  `KeywordSpotter(tokens, encoder, decoder, joiner, keywords_file, ...,
  provider='cpu', ...)` — a streaming transducer-based spotter, CPU
  provider available (no GPU requirement, as claimed).
- **Initial concern, then resolved**: `keywords_file` looked like it would
  need the target phrase pre-converted into the specific pretrained
  model's own token/BPE vocabulary -- a real integration tax if it
  required hand-building. Checked further: the package ships
  `sherpa_onnx.text2token()` (confirmed via `inspect.signature` +
  docstring) plus `SentencePieceTokenizer`, which does exactly this
  conversion given the model's own `tokens.txt`/BPE model files. Not
  custom tooling ConvoBox would need to write -- the library already
  handles it.

**Net assessment**: the recommendation holds up under real API
inspection, more tractable than the original write-up assumed (the
keyword-file generation concern is a solved problem in the library, not
an open one). Still genuinely unevaluated for actual USE: no pretrained
model has been downloaded or tested against ConvoBox's real phrases
("stop stop stop", "juliette papa charlie", "Athena") for either
accuracy or false-positive rate. Deliberately did not download a
pretrained model this cycle (same reasoning as declining to
pre-download `large-v3` for Whisper earlier tonight: a genuine,
possibly-largish unsupervised download isn't a good use of this
autonomous window). Concrete next step if pursued: pick one of k2-fsa's
published small English streaming-transducer keyword models, download
it once (with JP's awareness of the size), and run a real recognition
test against the three phrases above before writing any integration
code.
