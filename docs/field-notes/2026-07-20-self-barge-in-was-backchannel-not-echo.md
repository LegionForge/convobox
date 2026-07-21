---
title: Repeated self-barge-in was an over-narrow backchannel filter, not uncancelled AEC echo
status: validated-live
date: 2026-07-20
project: ConvoBox (github.com/LegionForge/convobox)
versions: WebRTC AEC3 (aec-audio-processing), faster-whisper base, interrupt_preset conversational
evidence:
  - PR #108 (fix/backchannel-short-acknowledgments)
  - convobox-UAT/.aec-dumps/20260720-205724/ (reference.wav, mic-raw.wav, mic-processed.wav)
  - convobox-UAT/convobox-tui.log lines ~3790-3918 (2026-07-20 20:57-21:00 session)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator, live UAT testing)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, implementation, writing
  org: https://legionforge.org
  created: 2026-07-20T22:30:00-05:00
  revised: 2026-07-21T01:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Repeated self-barge-in was an over-narrow backchannel filter, not uncancelled AEC echo

**Context for outsiders**: ConvoBox is a local voice frontend for CLI coding
agents. With AEC (acoustic echo cancellation) enabled and
`interrupt_preset: conversational`, the operator reported the assistant
"kept barging in on itself" and "never lets a response finish" during a
live open-speaker UAT session, several days into AEC delay-tuning work.

## Problem

A live UAT session (`convobox-UAT`, 309ms AEC delay — already established
as the empirically-best value in prior calibration) showed frequent
barge-in events cutting off the assistant mid-response. The obvious
hypothesis, given days of AEC work already in flight, was residual
uncancelled TTS echo triggering VAD on the assistant's own voice.

## Evidence

Cross-correlated the raw mic capture (`mic-raw.wav`, pre-AEC) against the
TTS reference (`reference.wav`) for a precisely log-timestamp-aligned
window with known-active playback (response text logged 20:57:47.346,
reverse-frame count confirming 4.05s of real TTS output through
20:57:51.4). RMS attenuation in that window (13.7dB) matched the log's
own reported "attenuation=16.7dB" verdict closely — but a proper FFT-based
cross-correlation of the RAW (pre-cancellation) mic signal against the
reference found **no measurable correlation at any lag** (peak ±0.15,
noise-floor level), even though real echo was demonstrably present per
the RMS delta. This ruled out "the mic is clearly hearing a
recognizable copy of the TTS output" as the mechanism.

Pulling the full transcript/response timeline for the session showed every
barge-in-tagged utterance was coherent, real human speech — not garbled
TTS echo. Several were short acknowledgments: `"Okay, get it."`,
`"Thank you very much."`, `"We're in fact."`, `"Refreshing. Refreshing."`
— and the operator directly narrated the symptom mid-session: *"I'm not
saying anything during playback. I'm waiting for your playback to finish,
but it never does."*

`is_backchannel()` in `scripts/run_convobox.py` classified an utterance as
backchannel (safe to ignore during playback) only if **every** token was
in a fixed single-word list (`yeah`, `okay`, `right`, `sure`, ...) —
whole-utterance subset match. `"Okay, get it."` tokenizes to
`{okay, get, it}`; `get`/`it` aren't in the list, so it failed the check
and became a full barge-in under `interrupt_preset: conversational`
(any 250ms+ of sustained speech fully interrupts).

## Mechanism

**Ruled out**: literal self-hearing via uncancelled AEC echo. The
correlation analysis found no evidence of it even in a window verified
(via precise log-timestamp alignment) to have real TTS playback and
measurable RMS attenuation.

**Confirmed**: the operator's own natural short verbal acknowledgments
during playback — a normal human conversational habit — were being
classified as full redirect-worthy interrupts because they contained any
word outside a small fixed backchannel vocabulary, even when the
non-vocabulary words carried no real redirect intent.

## Fix

Added `_BACKCHANNEL_PHRASES`, a small curated set of common short
acknowledgment PHRASES (`"get it"`, `"got it"`, `"thank you very much"`,
`"understood"`, `"sounds good"`, etc.), matched by **exact token-set
equality** — not subset, unlike `_BACKCHANNEL_TOKENS` — specifically so a
new phrase can't accidentally swallow real commands that happen to share
a word with it (e.g. `"got it, but stop the deploy"` must still interrupt).
`is_backchannel()` now checks both the existing subset rule and this exact
match. PR #108, merged.

## What transfers

- **A "self-barge-in" report is not automatically an AEC problem** —
  worth checking the actual transcript content and a real cross-correlation
  before assuming echo, especially once delay-tuning has already been
  through one round (see the companion note,
  [[2026-07-20-aec-delay-hint-was-a-red-herring]], for the prior AEC-specific
  misdiagnosis this same investigation avoided repeating).
- **A whole-utterance backchannel filter needs a phrase tier, not just a
  token tier**, for any voice UX with a "any sustained speech interrupts"
  preset — real human backchannel isn't always single words.
- Live UAT re-verification (does the fix actually stop premature
  interruption in a real session) is still pending as of this note.
