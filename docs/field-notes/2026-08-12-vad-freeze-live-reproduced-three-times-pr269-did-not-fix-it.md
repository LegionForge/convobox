---
title: The VAD/mic-loop freeze still reproduces after PR #269's dedicated-executor fix -- three clean live repros, and the new stall diagnostic never fired
status: validated-live
date: 2026-08-12
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 51e325f (PR #269 merged); stt.device=cpu, stt.model=base; backend=codex, permission_mode=permissive
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, --tui --web -v, real codex backend, working_dir _artifact-test-scratch
  - convobox-tui.log timestamps quoted verbatim below (not paraphrased)
  - src/convobox/vad/segmenter.py feed_async() (the instrumentation that did not fire)
  - src/convobox/audio/capture.py MicrophoneStream.stream() (the leading new suspect, not yet instrumented)
  - docs/KNOWN-ISSUES.md "VAD segmenter's per-window model call..." entry (prior status)
  - docs/field-notes/2026-08-06-resume-word-hallucination-and-runaway-repetition.md (original incident)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; deliberately drove a rapid-fire hotword-spam stress protocol while paused, live-reported each freeze in real time, tested recovery via both voice and the web UI)
    - Claude Code (Anthropic claude-sonnet-5) -- protocol design, live log analysis and timing extraction while the session ran, writing
  org: https://legionforge.org
  created: 2026-08-12T18:58:00-05:00
  revised: 2026-08-12T18:58:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The VAD/mic-loop freeze still reproduces after PR #269 -- three clean live repros, new diagnostic silent throughout

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents. `docs/KNOWN-ISSUES.md` has tracked a real, safety-relevant
bug since 2026-08-06/07: under rapid-fire speech stress, the mic
pipeline can go totally silent for a couple of minutes, taking every
safety-relevant stop control down with it in the worst recorded case.
PR #269 (merged 2026-08-12, same day as this session) gave a concrete
mechanism and a fix -- dedicated thread-pool executors for two
long-lived blockers that were starving the shared pool. This note is a
same-day live re-test of that fix, run specifically because "plausible
mechanism, unverified fix" was flagged as the one real gap standing
between the current `main` and a release candidate.

## Problem

The freeze reproduced three times in about 15 minutes of deliberate
rapid-fire stress testing (short hotword-heavy phrases spoken while
paused, matching the original incident's own trigger pattern). PR #269
did not close it. Worse for the original diagnosis: the new
queued-vs-running stall warning PR #269 added specifically to catch this
never fired once, across all three real occurrences -- meaning the
stall isn't happening where #269's fix and its diagnostic both targeted.

## Evidence

Stress protocol: `stt.device: cpu` (matches the original incidents),
hotwords configured include `brake` (the exact word that triggered the
worst original recurrence). JP paused via voice, then rapid-fired short
phrases and safewords (`brake brake brake`, `halt halt halt`, `abort
abort abort`, `mayday mayday mayday`, etc.) for a sustained burst before
attempting to resume.

**Repro 1** -- last real activity, then total silence, then recovery:
```
18:39:14,010 INFO dropped (paused, not the resume word): 'stop brake'
                                    [52.8s of silence]
18:40:06,786 INFO resumed listening (web UI)
```

**Repro 2** -- longest of the three:
```
18:42:58,207 INFO AEC stats for last response: ...
                                    [72.7s of silence]
18:44:10,910 INFO resumed listening (web UI)
```

**Repro 3** -- this time JP also tried the spoken resume word *during*
the stall; it produced no log line at all (not even a rejected-transcript
line), confirming total pipeline silence rather than a confidence-gate
rejection:
```
18:45:29,029 INFO AEC stats for last response: ...
                                    [60.7s of silence -- spoken "resume
                                     listening" attempted in here,
                                     produced zero log output]
18:46:29,688 INFO resumed listening (web UI)
```

**Consistent pattern across all three:**
- Duration clustered 53-73s (mean ~62s) -- shorter than the original
  2026-08-07 incident's 2m9.4s, but the same shape.
- The web UI's Resume Listening button broke the stall **every single
  time**, immediately, on the first click. This matches and reconfirms
  the "contained" behavior the 2026-08-07 partial fix (`feed_async`
  offload) already established: the rest of the event loop (HTTP
  routes) stays alive even while the mic pipeline itself is stuck.
- The spoken resume word did **not** work during any of the three
  stalls (confirmed directly in repro 3; repros 1-2 were recovered via
  web before a voice attempt was tried).
- `feed_async()`'s own stall warning (`segmenter.py:279-301`, the thing
  PR #269 added specifically to distinguish "still queued" from
  "genuinely running long") **never logged once**, in any of the three
  occurrences, despite `-v` verbose logging being active for the whole
  session.

**One negative data point, for calibration**: a real runaway-repetition
hallucination (`mayday` repeated ~35 times, matching the known pattern
from the 2026-08-06 field note) occurred later in the session and did
**not** produce a freeze -- it hard-stopped cleanly via safeword match
and the session continued normally. Confirms the freeze is a real but
intermittent failure mode under this stress pattern, not a guaranteed
consequence of every hallucination/hard-stop cluster -- consistent with
JP's own 2026-08-07 qualitative read ("significantly more reliable...
assuming I don't pound the paused client").

## Mechanism

**Ruled out by this session's own evidence**: `feed_async()`'s VAD
window calls sitting queued-or-running on the shared thread pool. If
that were the active mechanism, the warning built specifically to catch
it would have fired at least once across three real occurrences. It
didn't.

**Leading new candidate, not yet confirmed**: `MicrophoneStream.stream()`'s
own blocking queue read (`src/convobox/audio/capture.py`). PR #269 gave
this its own dedicated single-worker executor specifically to stop it
competing with the shared pool for workers -- but that fix only
addresses *contention*, not any other reason this specific call might
not return (a stuck `sounddevice` callback, an internal queue that never
gets a new item enqueued, some other stall entirely). Critically,
**this call has no equivalent stall-diagnostic** -- unlike `feed_async()`,
nothing logs if `MicrophoneStream.stream()`'s own wait is taking
unusually long. If the freeze is actually happening here, it would
produce exactly what was observed: zero `Processing audio` lines (no
audio chunk ever reaches the VAD/STT layer to log anything), and zero
diagnostic output (nothing is watching this specific wait the way
`feed_async()`'s is watched).

This is a plausible, evidence-consistent hypothesis, not a confirmed
diagnosis -- structurally consistent with the observed symptom, not yet
proven by direct instrumentation of the actual stalled call.

## What transfers

- **A fix for one plausible mechanism of an intermittent bug is not
  confirmed by "the mechanism sounds right" -- it needs the bug to
  actually stop reproducing under the same stress that produced it
  originally.** This session's live re-test is exactly why that
  distinction was flagged before considering this a release candidate;
  the mechanism lined up cleanly on paper, and reproduced live anyway.
  (validated-live)
- **When you add a diagnostic specifically to distinguish two
  hypotheses, a live recurrence where it stays silent is itself strong
  evidence against the hypothesis it was built to catch** -- not just
  an inconclusive result. Three real occurrences with zero warnings is
  a meaningfully different outcome than zero occurrences at all would
  have been. (validated-live)
- **Recovery-path asymmetry is real and repeatable, not a one-off**:
  three-for-three, the web control path recovered a stalled mic
  pipeline; the voice path never did during an active stall. Anyone
  hitting this in practice has a real, working recovery option (click
  Resume Listening) even before the underlying cause is fixed.
  (validated-live)

## Next step, not done this session

Add the same queued-vs-running timing instrumentation `feed_async()`
already has to `MicrophoneStream.stream()`'s own queue-read wait, so the
next recurrence -- live or via a repeatable synthetic stress harness --
actually shows where execution is stuck instead of producing silence
either way.
