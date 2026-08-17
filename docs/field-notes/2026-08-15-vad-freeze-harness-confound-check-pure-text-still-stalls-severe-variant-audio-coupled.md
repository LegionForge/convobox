---
title: The audio test harness is exonerated for short stalls (they reproduce identically via pure text, no mic/speakers/STT at all) but the severe multi-minute freeze specifically appears coupled to real audio-pipeline activity -- 0/10 severe in a pure-text batch vs. 1/10 and 1/5 in matched audio batches
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=codex, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - A new scratch harness, _test_freeze_no_audio_confound.py (not committed), constructing Orchestrator + CodexAdapter + SafewordDetector directly and calling handle_transcript() with the exact same phrase sequence/cadence as the audio-based VAD stress harness -- but as pure text, zero mic/speakers/STT/Piper/AEC involved
  - Direct comparison against this session's own two prior audio-based batches: docs/field-notes/2026-08-15-vad-mic-freeze-live-reproduced-on-macos.md (5 cycles, 1 severe) and docs/field-notes/2026-08-15-vad-freeze-second-severe-instance-plus-a-self-resolving-66s-stall.md (10 cycles, 1 severe)
  - Full raw session output (/tmp/no_audio_confound_test.log, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; explicitly asked to "eliminate the possibility that the test or the test harness is adversely affecting the results")
    - Claude Code (Anthropic claude-sonnet-5) -- harness design/implementation, live testing, writing
  org: https://legionforge.org
  created: 2026-08-15T02:55:00-05:00
  revised: 2026-08-15T02:55:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The audio harness is exonerated for short stalls; the severe freeze looks coupled to real audio-pipeline activity

**Context.** This session's audio-based VAD stress harness (Piper TTS
through real speakers into the real mic, `audio.echo_cancellation:
false`) caught two independent severe freezes across two batches (5
cycles, then 10 cycles). Before trusting that data further, JP asked to
rule out the harness itself -- self-echo, AEC-off artifacts, Piper's
synthetic voice, or the mic/speaker loopback generally -- as the actual
cause, rather than a genuine backend-subprocess issue.

## Method: the same stress pattern, with the entire audio pipeline removed

Built a new scratch harness that constructs `Orchestrator` +
`CodexAdapter` + `SafewordDetector` directly (no `run_convobox.py`
process, no mic, no speakers, no STT, no TTS, no AEC-relevant code path
at all) and calls `Orchestrator.handle_transcript()` with the exact same
phrase sequence and cadence as the audio harness's own cycles (3x
`"stop stop stop"` at 0.5s spacing, a 1s gap, then a followup utterance,
repeated for N cycles) -- but as literal Python strings, bypassing
`ListeningGate`/mic/STT entirely. This isolates one specific question:
does rapid repeated backend-turn cycling ALONE (no audio pipeline
running concurrently) reproduce the stalls and the severe freeze?

## Result: short stalls reproduce identically; the severe freeze did not appear in this batch

A 10-cycle pure-text run produced the exact same *shape* of stall as
every audio-based run -- `codex app-server _read_loop: readline()
still pending after 0.5s` warnings firing repeatedly, each one
self-resolving within 0.6-2.5s. **This alone rules out the audio
harness as the cause of the short-stall pattern** -- it reproduces
identically with zero audio pipeline involved, so it cannot be an
artifact of self-echo, Piper's voice, AEC being off, or the speaker/mic
loopback specifically. It's a real characteristic of rapid backend-turn
cycling against the codex app-server.

**The severe multi-minute freeze did NOT appear in this 10-cycle pure-
text batch** -- every single stall self-resolved, max observed 2.5s.
Compare against the two audio-based batches run earlier the same
session, same cycle counts:

| Batch | Cycles | Audio pipeline? | Severe freeze? |
|---|---|---|---|
| Pure text (this note) | 10 | No | 0/10 |
| Audio batch 1 | 5 | Yes (Piper->speakers->mic->STT) | 1/5 |
| Audio batch 2 | 10 | Yes (same) | 1/10 |

Small sample (n=1 for the pure-text condition), so this is a real signal
worth taking seriously, not proof of causation. But the direction is
consistent with the leading hypothesis this whole investigation thread
has carried since the 2026-08-12 Windows notes: a synchronous VAD/audio-
capture call sharing the same single-threaded event loop as the
backend-subprocess I/O is a plausible root cause, and this result is
exactly what that hypothesis would predict -- remove the concurrent
audio-pipeline load, and the severe variant (which needs that
contention to actually starve the readline() task) stops appearing,
while the short stalls (a separate, already-partially-understood
characteristic of the backend I/O itself) persist unchanged.

## What transfers

- **A methodology point worth keeping for future harness work**: when a
  test rig combines multiple subsystems (here: audio capture + STT + VAD
  + backend I/O, all sharing one event loop), isolate them before
  trusting which one a symptom belongs to. The pure-text variant took
  under an hour to build and immediately clarified which part of the
  original finding was real backend behavior vs. which part needed the
  full pipeline to manifest. (validated-live)
- **Ruling out a harness as the SOLE cause is not the same as ruling out
  every possible harness-specific detail** -- this note only removes
  "the audio pipeline generally" as a confound for the short-stall
  pattern. It does NOT confirm the exact mechanism (still open: which
  specific concurrent audio-thread work is contending with the read
  loop), and does not rule out that some other harness detail (Piper's
  specific voice, sample rate, the exact stress cadence) shapes how
  OFTEN the severe variant appears, only that removing the pipeline
  ENTIRELY appears to remove it in this sample.

## Not done here

- A larger pure-text sample (this note has n=1 batch) to build real
  confidence that 0/10 wasn't just this batch's luck.
- Running the audio pipeline WITHOUT the stress harness's own deliberate
  bursts -- i.e., real mic capture + VAD + STT running idle/quiet, no
  backend turns being sent at all -- to isolate whether it's audio-
  pipeline presence alone, or audio-pipeline PLUS backend turn cycling,
  that's required to trigger the severe variant. This is the natural
  next experiment given today's other finding that both severe catches
  happened at the tail, after deliberate stress had already stopped.
- Instrumenting the actual thread/task scheduling during a live severe
  freeze to directly observe what's contending for the event loop --
  the natural way to confirm the mechanism rather than just the
  correlation, not attempted this session.
