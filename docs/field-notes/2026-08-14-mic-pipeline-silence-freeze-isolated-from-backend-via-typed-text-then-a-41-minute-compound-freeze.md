---
title: A structurally distinct mic-pipeline freeze (zero diagnostic output) isolated from the backend for the first time via typed text, followed by a hallucination-triggered false-emergency response and an unnoticed ~41-minute compound freeze discovered only in forensic log review
status: validated-live
date: 2026-08-14
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 4f1c58b; backend=codex, permission_mode=permissive; working_dir D:/LegionForge/convobox-UAT
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, --web -v, real codex backend (background task output `bdtav6q7d.output`, full session, 21:52:57-22:56:xx)
  - convobox-tui.log timestamps quoted verbatim below
  - docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-and-a-12-minute-unrecoverable-one.md ("this looks like two distinct bugs" hypothesis, directly confirmed here)
  - docs/field-notes/2026-08-14-vad-freeze-harness-live-catches-two-more-readline-stalls-with-real-telemetry.md (companion note, the OTHER freeze mechanism from the same evening)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; typed into the web text box as a live diagnostic move, reported the self-barge-in and the "no resume listening in the last 10 seconds" symptom in real time)
    - Claude Code (Anthropic claude-sonnet-5) -- live session driving, and (for the 41-minute compound freeze specifically) after-the-fact forensic log discovery, correlation, writing
  org: https://legionforge.org
  created: 2026-08-15T01:10:00-05:00
  revised: 2026-08-15T01:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A structurally distinct mic-freeze, isolated from the backend via typed text -- then an unnoticed ~41-minute compound freeze found only in forensic review

**Context for outsiders.** Same evening, same investigation as the
companion readline()-freeze note, but this is a genuinely different
finding: a freeze with NO diagnostic output at all (ruling out the
readline()-on-the-backend-pipe mechanism), isolated for the first time
by a clean natural experiment (typed text bypasses the mic entirely), and
then -- discovered only afterward, by accident -- the same abandoned
session sitting almost continuously frozen for the better part of an
hour with nobody watching.

## Problem, part 1: total silence, then a clean isolation

After fixing the pause-phrase clipping bug (a filler-prefix fix, "Testing,
stop listening" spoken as one utterance -- see the companion note), a
single stress cycle correctly registered the pause:

```
21:53:23,988 INFO paused listening (matched 'testing stop listening') -- hard-stopped in-flight work; say 'resume listening' to resume
```

One utterance was processed just after (dropped, no input recognized).
Then the log went **completely silent** -- not a single line, of any
kind -- for over a minute, while the harness kept playing its burst for
another ~11+ seconds past that point and JP was independently speaking
live:

```
21:53:25,974 INFO dropped (no input, STT heard nothing recognizable) [ERROR-LADDER: tier 1]
[nothing at all until:]
21:54:32,513 INFO resumed listening (web UI)
```

Critically, **no `readline() still pending` warning ever fired during
this gap** -- the diagnostic that caught both freezes in the companion
note was silent here, meaning this is NOT the same mechanism. The web
UI's Resume Listening button call succeeded and logged "resumed
listening," but real mic activity did not actually follow: JP reported
live, "no 'resume listening' in the last 10 seconds" while speaking
directly into a session that claimed to be listening. This exactly
matches the 2026-08-12 exhaustive-batch finding: *"Web /api/listening
resume... succeeded as an API call, produced zero new mic-pipeline
activity."*

**The isolation:** JP then typed a message into the web text box --
`WebTextInputBridge`'s path, which bypasses the mic/VAD/STT pipeline
entirely. At 21:59:09, over 4.5 minutes after the mic pipeline last did
anything, the typed message reached the backend and got a normal
response within seconds:

```
21:59:09,573 WARNING codex app-server _read_loop: readline() still pending after 0.5s ...
21:59:10,580 WARNING ... finally returned after 1.5s total ...
21:59:16,803 INFO backend still working (6s, no audio yet) [THINKING] -- say the safeword to abort
21:59:18,435 INFO response: Yep, I'm okay—ready when you are.
```

This is the first clean, direct proof in this investigation that a
mic-pipeline freeze and a backend freeze are separable: the backend was
fully healthy and responsive the whole time (readline() calls resolving
in 0.5-1.5s, completely normal), while the mic/VAD layer -- a totally
different code path `WebTextInputBridge` never touches -- was the thing
actually stuck. Directly confirms the "this looks like two distinct
bugs" suspicion from the 2026-08-12 note, this time with a real
controlled comparison rather than inference.

Right after that response's playback started, the mic pipeline resumed
on its own:

```
21:59:34,269 INFO playback: first audio block reached output device
21:59:36,314 INFO Processing audio with duration 00:01.600
```

Coincidence in timing with a new playback cycle starting is noted, not
claimed as causal -- no mechanism is proposed for why a new response
would unstick the mic capture layer.

## Problem, part 2: a hallucination triggers a false emergency response

Once the mic recovered, a rapid self-barge-in loop followed (`UNDER-
CANCELLING` AEC readings degrading from 12.5dB to under 1dB across the
sequence -- the assistant's own TTS output leaking back into the mic and
triggering fresh turns). One of these hallucinated transcripts produced
a genuinely concerning response, not just a nonsense one:

```
22:14:54,169 INFO transcript='you run in front of your car and you run in front of your car and you run in front' lang=en (0.88) dec=0.36 busy=False  [BARGE-IN]
22:14:58,398 INFO transcript="that's okay you're barging in on yourself don't worry about it" lang=en (0.99) dec=0.74 busy=True
22:14:58,534 INFO response: Are you saying someone is about to run in front of a car? If yes, move away from the road and car immediately, warn them if safe, and call 911 (or your local emergency number). Are you safe right now?
```

Nothing dangerous happened -- JP was not in any actual danger, and the
model's response is the RIGHT failure mode if it had been real (erring
toward caution on an ambiguous safety-sounding phrase). But this is a
real instance of a runaway-repetition STT hallucination (matching the
already-documented 2026-08-06 pattern) producing safety-adjacent output
in response to fabricated content, worth naming as its own data point
rather than folding silently into "another hallucination."

## Problem, part 3: an unnoticed ~41-minute compound freeze, found only afterward

This part was NOT observed live by anyone -- both JP and the operator
had moved on to other work (JP asked to prioritize building the
force-kill feature; this session was left running, unwatched, in the
background). It was discovered only during after-the-fact forensic log
review while writing this note, roughly an hour after it happened.

The last real activity before it began was a correctly-dropped echo:

```
22:15:09,958 WARNING dropped (spoken-echo filter, barge-in was our own echo): 'yes i can stable' [echo-match: 0.75 of tokens in last response]
```

No new user turn was sent immediately after this -- meaning whatever
triggered the stall below was not a fresh burst of rapid input, unlike
both freezes in the companion note. What follows is, as closely as the
log allows reconstruction, a nearly continuous stuck period:

| Phase | Duration | Window (approx, local time) |
|---|---|---|
| Stall 1 | 843.7s (~14.1 min) | 22:15:09 -> 22:29:10 |
| Brief return | ~10.0s | 22:29:10 -> 22:29:20 |
| Stall 2 | 472.2s (~7.9 min) | 22:29:20 -> 22:37:12 |
| Brief return | ~10.0s | 22:37:12 -> 22:37:22 |
| Stall 3 | **never returned on its own** -- still climbing past 1130.5s (~18.8 min) when the process was force-killed | 22:37:23 -> ~22:56 |

```
22:29:10,591 WARNING codex app-server _read_loop: readline() finally returned after 843.7s total (proc.returncode=None)
22:37:12,775 WARNING codex app-server _read_loop: readline() finally returned after 472.2s total (proc.returncode=None)
22:54:53,276 WARNING codex app-server _read_loop: readline() still pending after 1050.5s ...
22:56:13,277 WARNING codex app-server _read_loop: readline() still pending after 1130.5s ...
[the process was killed here, investigating an unrelated orphaned-process cleanup, without realizing this session was also mid-freeze at that exact moment]
```

Total elapsed from the last real activity to the kill: **~41 minutes**,
of which at most ~20 seconds (the two brief "returned" gaps, themselves
not confirmed as genuine recovery -- just one `readline()` call each
resolving) was not actively stuck. The final segment alone, 1130.5+
seconds, already exceeds the previous longest directly-observed freeze
in this project's history (the 2026-08-12 note's "12+ minutes").

**This session was never deliberately killed for being frozen** -- it
was terminated as a side effect of cleaning up unrelated orphaned
processes discovered while investigating a completely different test
(the force_kill() reliability harness). Nobody was watching this
session's own health for the entire ~41-minute window.

## Mechanism

Silence (part 1) and the readline() pattern (part 3) are, by the
diagnostic evidence, two different things happening in the SAME
abandoned session at different times -- not necessarily the same root
cause. Part 1 had zero readline() warnings at all (consistent with the
mic/VAD layer being the stuck component, not backend I/O). Part 3 shows
the EXACT SAME readline() signature as the companion note's freezes, but
triggered without a rapid-fire input burst -- the last thing that
happened before it was a single correctly-dropped echo, not a stress
condition. This is the most concerning data point in this note: it
suggests the readline() freeze is not exclusively a stress-test
artifact and can occur under what looks like ordinary, low-volume
operation.

## What transfers

- **Typed text is a genuine, clean diagnostic tool for separating
  mic/VAD freezes from backend freezes** -- it bypasses the mic pipeline
  entirely and reached a healthy backend within seconds while the mic
  layer stayed dead, the cleanest isolation this investigation has
  produced. Worth using deliberately, not just incidentally, next time
  this class of freeze is suspected. (validated-live)
- **An abandoned/unwatched session can be silently frozen for a very
  long time with nobody noticing** -- this is as much a process lesson as
  a technical one: a background session left running during other work
  needs its own periodic health check, not just trust that "it's probably
  fine." (validated-live, and a real methodology gap this session itself
  fell into)
- **A hallucinated repeated phrase can produce a plausible, safety-
  adjacent response, not just nonsense** -- worth including in any future
  hallucination sampling specifically for what KIND of false content gets
  generated, not just whether a hallucination occurred. (validated-live,
  single instance)
- **The readline() freeze is not confirmed to require rapid-fire stress
  conditions** -- the ~41-minute compound freeze started right after
  ordinary single-utterance activity, not a burst. This should update
  how "when should I expect this" is described going forward: as a
  freeze that CAN be triggered by stress but is not proven to REQUIRE it.
  (hypothesis -- single instance, not yet deliberately reproduced under
  low-volume conditions)

## Not done here

- Confirming whether the ~41-minute freeze's trigger (ordinary,
  non-bursty activity) reliably reproduces the freeze, or whether this
  was still some residual effect of the earlier bursty stress in the
  same long-lived, never-restarted session.
- Any investigation into whether periodic automated health-checking of
  a running ConvoBox session (a "is this session still making progress"
  watchdog, distinct from the per-call stall diagnostics that already
  exist) would have caught this sooner than forensic log review did.
