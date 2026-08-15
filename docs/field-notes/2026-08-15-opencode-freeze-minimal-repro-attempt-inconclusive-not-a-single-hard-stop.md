---
title: A minimal, targeted repro of the previous round's opencode freeze (one hard-stop, one follow-up turn) did NOT reproduce it across 3 separate hard-stop/resubscribe cycles in one muted session -- narrows the hypothesis (a single clean hard-stop-then-turn is not sufficient on its own) without disproving it; the freeze may need the original harness's tighter multi-cycle cadence, or may simply be a rarer race than one attempt can rule out
status: inconclusive
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch docs/opencode-freeze-live-repro-2026-08-15 (off main), backend=opencode, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), output volume confirmed 65%, --mute (to eliminate the TTS/echo feedback confound hit early in this round)
evidence:
  - First attempt (non-muted) hit an UNRELATED confound immediately: with echo_cancellation disabled and playback audible, ConvoBox's own spoken replies were picked up by the mic as new "utterances," cascading into repeated self-triggered turns -- a known self-barge-in issue (see 2026-08-11 self-barge-in field notes), not what this round was testing. Restarted muted to remove it.
  - Cycle 1 (muted): one question -> one hard-stop (safeword correctly matched, `hard stop matched safeword 'stop stop stop'`, POST /interrupt 204, one POST body CancelledError) -> waited 13s with no follow-up turn. No SSE resubscribe happened during this window at all (matches opencode.py's documented "hard_stop() deliberately does NOT tear down the SSE subscription" behavior) -- Silero trace stayed alive throughout, no freeze.
  - Cycle 2 (muted): one question -> one hard-stop -> one follow-up turn (a genuinely NEW turn, not just idle waiting). This produced a fresh `GET .../event` 200 OK (the resubscribe this round's hypothesis expected) -- and the follow-up turn's transcript, processing, and response all completed normally. No freeze.
  - Cycle 3 (muted): one question -> a rapid 3x-repeated safeword burst (0.2s gaps, matching the original harness's burst shape -- the segmenter merged all 3 plays into ONE utterance since the gaps were shorter than min_silence_ms, and faster-whisper's repetition-hallucination kicked in, transcribing it as ~100 repeated "stop"s, but the safeword match still fired correctly on the resulting text) -> resume word -> a follow-up utterance. THREE total hard-stop-adjacent SSE reconnects across this session, still no freeze; 7 utterances processed cleanly by the end.
  - Total elapsed: ~2.5 minutes of active hard-stop/resubscribe cycling in one continuous session, zero freezes, versus the previous round's freeze which hit on the very FIRST cycle of an unmodified 6-cycle harness run.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop running this investigation, following up on the previous round's opencode freeze finding)
    - Claude Code (Anthropic claude-sonnet-5) -- test design, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T09:06:00-05:00
  revised: 2026-08-15T09:06:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The minimal hard-stop/resubscribe repro did not reproduce the freeze

**Context.** The previous round found a real, live, non-recovering
opencode-backend freeze (main event loop parked in kevent, 0% CPU,
mic capture continuing with nothing consuming it) that hit on the
first cycle of the unmodified 6-cycle stress harness. That note's
"likely mechanism" hypothesis was that `hard_stop()`'s documented
decision to leave the old SSE subscription open on interrupt,
combined with the orchestrator's exception-only resubscribe logic,
could leave an orphaned subscriber that starves a subsequent
connection. This round set out to isolate that specific mechanism
with a smaller, controlled repro -- one hard-stop, one new turn --
rather than the full stress harness.

## What was tried

Three progressively closer approximations of the original trigger, all
in one continuous muted session (volume confirmed 65%, `--mute` used
specifically to eliminate an unrelated self-barge-in confound hit on
the first attempt):

1. A single hard-stop with no follow-up turn (establishes that
   `hard_stop()` alone does not reopen the SSE subscription -- confirmed,
   matches the adapter's own documented comment).
2. A single hard-stop followed by exactly one new turn (the specific
   mechanism hypothesized last round -- a fresh `GET .../event`
   reconnect right after an interrupt).
3. The closer rapid 3x-burst safeword shape (matching the original
   harness's stress pattern) followed by resume + a follow-up turn.

None of the three produced a freeze. The session stayed fully
responsive through 3 separate hard-stop-adjacent SSE reconnects and 7
total processed utterances.

## What this means

**This does not disprove last round's hypothesis** -- it narrows it. A
single, cleanly-isolated hard-stop-then-turn is evidently not
sufficient on its own to trigger the freeze, at least not reliably.
Plausible explanations, none confirmed:

- **Cadence-sensitive race**: the original harness runs its cycles
  back-to-back with fixed short sleeps (1.5s, 0.2s, 1.0s, 1.5s, 3.0s)
  across SIX repeated cycles, not the more relaxed, manually-timed
  single cycles used here. If the actual bug is a connection-pool or
  server-side-subscriber race that depends on tight timing or
  repetition count, three manually-paced single cycles may simply not
  hit the window.
- **Rare/non-deterministic**: the original freeze happened on the very
  first cycle of ITS run, which argues against "needs many
  repetitions" -- but a race condition can easily be more likely under
  one specific pacing than another without being strictly
  repetition-dependent.
- **A different trigger entirely**: something about the ORIGINAL
  session's specific history (it was a fresh session, first-ever
  interaction, vs. this round's session which itself had already
  survived 2 clean hard-stop cycles before the burst) could matter in
  a way not yet identified.

## What transfers

- **A single successful negative-repro attempt narrows a hypothesis;
  it does not retire it.** This session's own recurring lesson
  tonight (busy-state, volume-confound) has been "the first plausible
  mechanism is often incomplete" -- this note applies that same
  discipline to its own most recent finding rather than treating the
  previous round's diagnosis as settled. (inconclusive)
- **Isolating a self-barge-in confound (audible TTS + no echo
  cancellation) BEFORE it contaminates a targeted test** cost real
  time this round (the first attempt's transcript came back garbled
  and the safeword never matched) -- muting output for any test that
  doesn't need to verify actual audio playback is now the safer
  default for future repro attempts on this thread. (validated-live)

## Not done here

- Running the UNMODIFIED, unmuted 6-cycle harness again against
  opencode to confirm the freeze is still reproducible at all (only
  the previous round's single run has ever shown it) -- this is the
  most important unresolved check: does the freeze reproduce reliably
  on a fresh session with the original harness's exact pacing, or was
  even THAT a rarer event than assumed?
- Any code-level instrumentation of opencode.py's SSE
  connect/reconnect path (e.g. logging every `aconnect_sse` call and
  its outcome) that would make this mechanism directly observable
  instead of inferred from timing and HTTP request logs.
- Capturing opencode server-side logs during a future freeze attempt,
  which would directly show whether the server considers the new
  connection an active subscriber or not.
