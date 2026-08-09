---
title: A dropped-by-any-gate transcript showed in the TUI but was invisible in the web UI — forward_transcript() fired too late
status: validated-live
date: 2026-08-05
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main circa PR #212 (feat/stt-hotwords-bias branch, UAT part 10, hotwords OFF, temperature=0.0, max_utterance_s=30)
evidence:
  - convobox-tui.log 2026-08-05 20:07:44-20:08:03 (live incident: 3 dropped transcripts visible in TUI, invisible in web)
  - scripts/run_convobox.py (mic loop: tui_state.add_turn() vs. web_forwarder.forward_transcript() call-site timing — fix applied)
  - src/convobox/web/bridge.py (WebEventForwarder.forward_transcript(), unchanged — the gap was in the caller, not this method)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; caught the TUI/web mismatch live during #204 hotwords A/B testing)
    - Claude Code (Anthropic claude-sonnet-5) — live log investigation, code trace, writing, fix
  org: https://legionforge.org
  created: 2026-08-05T21:45:15-05:00
  revised: 2026-08-05T21:45:15-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A dropped-by-any-gate transcript showed in the TUI but was invisible in the web UI

**Context for outsiders.** ConvoBox has two live UIs over the same voice
session: a terminal TUI and a web UI, both meant to show the same
conversation. The mic loop maintains a transcript pane in each — the TUI's
directly, the web UI's via a forwarder that persists to history and
broadcasts over SSE. This note documents a live session where an
utterance ConvoBox genuinely heard (including one where it correctly
transcribed the literal word "Athena") appeared in the TUI's transcript
pane but never reached the web UI at all.

## Problem

During #204 (`stt.hotwords`) live A/B testing, the operator watched three
consecutive utterances render in the TUI transcript pane that never
appeared in the web UI's history/SSE feed at the same session.

## Evidence

```
20:07:44,807 INFO Processing audio with duration 00:02.112
20:07:45,500 INFO transcript='trying to say the hot word Athena.' lang=en (0.99) dec=0.72 busy=False
20:07:47,019 INFO response: Athena heard.                                    <- reached the backend, shown in BOTH UIs
...
20:07:52,926 INFO Processing audio with duration 00:01.184
20:07:53,270 INFO Detected language 'en' with probability 0.33
20:07:53,616 INFO dropped low-confidence transcript='Athena' lang=en (0.33 < 0.40) [ERROR-LADDER: tier 1]   <- TUI only
20:07:59,582 INFO Detected language 'pt' with probability 0.31
20:07:59,941 INFO dropped low-confidence transcript='Afina.' lang=pt (0.31 < 0.40) [ERROR-LADDER: tier 2]   <- TUI only
20:08:02,991 INFO Detected language 'en' with probability 0.28
20:08:03,347 INFO dropped low-confidence transcript="That's it." lang=en (0.28 < 0.40) [ERROR-LADDER: tier 3]  <- TUI only
```

The middle case is the sharpest example: STT correctly transcribed the
literal word "Athena" — no mishearing at all — but the utterance's overall
language-detection confidence (0.33) fell below `stt.min_language_probability`
(0.4, an ordinary short-utterance false-negative — very short single-word
utterances give the language-ID model little to work with). The
transcript was real and correct; it just never survived the quality gate,
so it never reached the backend, and — before this fix — never reached
the web UI either.

## Mechanism

`scripts/run_convobox.py`'s mic loop had two separate places that
recorded a heard utterance, at very different points in the gate chain:

- **TUI**: `tui_state.add_turn("user", text)`, called immediately after
  transcription, before the safeword check, the pause/resume gate, the
  confidence gate, or any other filter — deliberately, per its own
  comment: "Every utterance ConvoBox actually heard, even ones later
  dropped by a gate below."
- **Web**: `web_forwarder.forward_transcript(text)`, called only right
  before `orchestrator.handle_transcript(text)` — i.e. only for
  utterances that survived *every* gate.

Same root shape as the earlier 2026-08-05 finding
(`docs/field-notes/2026-08-05-web-resume-desyncs-tui-display.md`, PR
#212): a secondary UI surface reflecting a narrower slice of reality than
the primary one, silently. Different code path this time (raw transcript
logging vs. pause-state display), same underlying lesson.

## Fix applied

Moved `web_forwarder.forward_transcript(text)` to fire at the same point
as `tui_state.add_turn()` — immediately after transcription, unconditional
on any downstream gate, using the same raw heard text (not the
corrected/barge-in-marked text a *surviving* utterance later becomes).
Removed the original later call site, which would otherwise double up an
already-forwarded utterance in the web history/SSE feed for the surviving
case.

No new automated test: this is deep in `run_convobox.py`'s un-unit-tested
main mic loop (no existing harness mocks the full
transcriber/segmenter/orchestrator/gate chain together), matching this
project's existing pattern for that function — verified by full-suite
regression (1234 passed, unchanged) plus `ruff`/`mypy` clean, with live
re-verification as the natural next step, same as PR #212's own fix was
confirmed live rather than via a new unit test.

## What transfers

- **A UI surface that logs "what was heard" and one that logs "what
  survived" are different data, and callers who need parity must
  explicitly wire the display call at the SAME point in the pipeline as
  the reference UI, not just "somewhere before the backend call."**
  (validated-live)
- **Short single-word utterances are more likely to fail a
  language-probability confidence gate than longer ones, independent of
  whether the transcription itself was correct.** Worth remembering when
  reading any resume-word (or other short-phrase) live-UAT data: a
  "failure" in the log may be a correct hearing dropped by confidence,
  not a mis-transcription. (validated-live, this specific instance;
  general claim plausible from the language-ID mechanism but not
  measured across a larger sample)
- **Live re-verification not yet done** — the fix is unit-suite-clean
  but hasn't been confirmed against a real recurrence of a dropped
  utterance showing correctly in both UIs now.
