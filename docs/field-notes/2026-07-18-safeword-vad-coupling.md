---
title: Your abort phrase is only as reliable as your VAD threshold — safety and tuning are coupled
status: validated-live
date: 2026-07-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: Silero VAD; faster-whisper base; ConvoBox main circa PR #96
evidence:
  - docs/UAT-checklist.md (safeword findings, session logs 2026-07-18)
  - AGENTS.md rule 4 (one config knob at a time; re-test the safeword after any vad/interaction change)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the session that went deaf)
    - Claude Code (Anthropic claude-fable-5) — log analysis, root cause, writing
  org: https://legionforge.org
  created: 2026-07-19T12:55:00-05:00
  revised: 2026-07-19T12:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Your abort phrase is only as reliable as your VAD threshold

**Context for outsiders.** A voice assistant that can be interrupted needs an
abort phrase — a safeword — that reliably stops it. It is a safety control:
the user's guaranteed way out. This note is about a non-obvious way that
guarantee silently fails.

## Problem

During live testing the operator repeatedly said the abort phrases ("stop
stop stop", "break break break") and the assistant would not stop. It looked
like the safeword logic was broken.

## Evidence

- The safeword logic was **not** broken. Earlier the same session, at
  22:30:24, "Stop, stop, stop." was transcribed, tagged `[HARD STOP]`, and
  immediately followed by `hard stop matched safeword` — dispatch working.
- In the failing window (21:58–22:00) the log contained **zero transcripts
  of any kind** — not mis-heard safewords, nothing. The operator's speech
  never reached the safeword check.
- Just before that window, VAD had been tuned mid-session to
  `threshold: 0.6`, `min_speech_ms: 400`. The last utterances that *did* get
  through were scoring `dec` (decision confidence) 0.35–0.43 — a quieter,
  late-night voice sitting right at the raised bar.

## Mechanism

The safeword is checked on the transcript. But the transcript only exists if
the utterance clears the **front-end gates first**: voice-activity detection
and any confidence threshold. Raising the VAD threshold to reduce false
barge-ins also raised the floor for *hearing the user at all* — including
the one phrase that must never be missed. A tired voice at 0.6 threshold
produced no transcript, so the safeword had nothing to match.

A second, independent failure mode was ruled *in* as a separate lesson: an
AI agent inspecting the code claimed the safeword was "logged but never
dispatched" and offered to fix it. The claim was false (dispatch verified in
code and in the same session's log), and the "fix" would have modified
working safety-critical code. Verifying a bug end-to-end before proposing a
fix is now a standing rule.

## What transfers

- **An abort/safety phrase inherits the reliability of every gate upstream
  of where it is checked** — VAD, confidence thresholds, language filters.
  Tuning any of them tunes your safety control, usually invisibly.
  (validated-live)
- **Re-test the abort path after every front-end tuning change**, at
  realistic input levels (a tired/quiet voice, not a clear test phrase).
  A control you only verify at full volume is untested for when it matters.
  (validated-live)
- **Separate the knobs by role.** Interruption-sensitivity gates
  (barge-in min-speech) should be the tuning target for false positives —
  not the hearing gate (VAD threshold) that the abort phrase depends on.
  (diagnosed)
- **Never accept a fix for a safety-critical "bug" that has not been
  reproduced end-to-end.** An agent's plausible code-reading is not
  evidence. (validated-live — this incident)
