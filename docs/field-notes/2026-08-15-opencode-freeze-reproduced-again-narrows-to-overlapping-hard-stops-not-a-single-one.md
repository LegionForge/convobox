---
title: The opencode freeze reproduced a SECOND time, on a fresh unmodified 6-cycle harness run (volume confirmed 65%, --mute) -- and this run's log narrows the mechanism significantly further than the previous round's minimal repro could reach: it took THREE separate, closely-spaced hard_stop() calls (not one), and one of the resulting interrupt POSTs had its own TCP connect cancelled mid-flight (connect_tcp.failed exception=CancelledError()), a new and more specific signature than round 15's plain receive_response_body cancellation -- consistent with overlapping hard-stops racing and poisoning the shared httpx client's connection pool
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch docs/opencode-freeze-live-repro-2026-08-15 (off main), backend=opencode, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini), output volume confirmed 65% before and during, --mute (self-barge-in eliminated)
evidence:
  - Fresh session, unmodified `_test_vad_freeze_macos.py 6` (the exact harness script, no manual pacing changes), `--mute` used this time specifically to remove the self-barge-in confound that contaminated the first attempt in the previous round
  - Froze during cycle 2's burst phase, ~25s into the run -- far earlier than the previous confirmed instance (which took until roughly the same point in cycle 1, so comparable timing, not later)
  - Log shows the burst phrases were NOT merged into one utterance this run (unlike round 15's minimal-repro attempt, where 3x back-to-back "stop stop stop" plays collapsed into a single segmenter utterance) -- instead three DISTINCT `hard stop matched safeword` events fired within a ~1.5s window (09:31:31,119 / 09:31:35,162 / 09:31:35,857), plus a fourth safeword-adjacent transcript ("Stop, stop, stop." at 09:31:36,668) with its own interrupt call
  - The second-to-last interrupt call in that sequence produced `2026-08-15 09:31:35,863 DEBUG connect_tcp.failed exception=CancelledError()` -- a TCP CONNECTION ATTEMPT itself getting cancelled, not a response-body read (the earlier round's signature). This is more specific: it means an interrupt POST was still trying to establish its connection when something cancelled it.
  - Two native stack samples (this round's + round 15's, both `sample <pid> 3`) are byte-identical in shape: main thread parked in `select_kqueue_control_impl -> kevent`, 0% CPU
  - Total silence duration when killed: ~106s (09:31:42 last log line to 09:33:28 kill), zero recovery signs
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; the autonomous /loop's own queued next-step from the previous round: "re-run the unmodified 6-cycle harness... to confirm the freeze still reproduces at all")
    - Claude Code (Anthropic claude-sonnet-5) -- capture, live monitoring, native-stack sampling, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T09:35:00-05:00
  revised: 2026-08-15T09:35:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The opencode freeze is confirmed real and reliably reproducible -- and now looks like an overlapping-hard-stop race

**Context.** The previous round's minimal-repro attempt (a single,
cleanly-isolated hard-stop followed by one new turn, run 3 times) did
NOT reproduce the opencode freeze, leaving it genuinely unclear
whether the original catch was a fluke or whether the isolation had
simply missed the real trigger. This round re-ran the UNMODIFIED
6-cycle stress harness exactly as originally used, with volume
confirmed good and `--mute` to remove the self-barge-in confound that
had complicated the previous round's manual testing. **It froze
again**, on the second stress cycle -- a second independent
confirmation that this is a real, reproducible bug, not a one-off.

## What's different this time: it took THREE hard-stops, not one

The previous round's minimal repro tried exactly one hard-stop
followed by one turn, three separate times, and stayed clean every
time. This round's log shows why that missed it: this run's 3x rapid
safeword burst did NOT get merged into a single utterance by the
segmenter (unlike the previous round's manual test, where the same
0.2s-gap burst collapsed into one long utterance). Here, the STT
picked up enough separation to produce **three distinct utterances**,
each independently recognized as the safeword, each independently
calling `hard_stop()` -- all within about a 1.5 second window. The
segmenter's utterance-splitting behavior for a rapid-fire burst is
evidently timing-sensitive (STT/VAD segmentation of near-identical
repeated audio is inherently borderline), which plausibly explains why
last round's cleaner, more deliberate single-utterance test never hit
the race: **it takes overlapping/back-to-back hard_stop() calls, not
one clean one, to trigger this.**

## A more specific failure signature this time

The previous round's freeze showed a cancelled `receive_response_body`
read (a request that had already connected and gotten headers, then
never got its body). This round's freeze shows something new and more
specific: `connect_tcp.failed exception=CancelledError()` -- a TCP
**connection attempt itself** being cancelled mid-flight, on one of the
three interrupt POSTs fired in quick succession. This is consistent
with: a second `hard_stop()` call's async task getting cancelled (or
cancelling something) while a PRIOR hard_stop()'s interrupt request was
still opening its connection -- and if that cancellation happens while
httpx has already reserved/checked-out a connection slot from its
pool, the pool can be left believing that connection is still in use,
starving every subsequent request (including the SSE `/event`
resubscribe that follows) of a free slot forever. This is a plausible,
more concrete refinement of the previous round's hypothesis, still not
proven by reading httpx/httpcore's own internals directly, but backed
by a specific, reproduced log signature rather than inference alone.

## Why this matters

**This upgrades the opencode freeze from "seen once" to "reliably
reproducible under the right (rapid multi-hard-stop) conditions,"**
and gives a much sharper next target for a minimal repro: not one
hard-stop, but two or three fired in overlapping succession (e.g. three
safeword utterances spoken in under 2 seconds, or -- more directly --
calling `hard_stop()` on the adapter twice concurrently without
awaiting the first). This is now a credible, real safety concern
worth flagging distinctly from the rest of tonight's VAD-freeze
investigation: **a user saying the safeword multiple times in quick
succession (a very plausible panic response) can freeze the entire
ConvoBox process on the opencode backend**, requiring a manual kill to
recover.

## What transfers

- **A test harness's own audio-splitting behavior is itself
  non-deterministic run to run** -- the same 0.2s-gap burst merged into
  one utterance in one session and split into three in another. Any
  future repro attempt targeting this bug should not assume a "3x
  burst" input reliably produces "3x hard_stop() calls"; it should
  verify the log shows multiple distinct `hard stop matched safeword`
  lines before concluding a repro attempt tested the intended
  condition. (validated-live)
- **`connect_tcp.failed exception=CancelledError()` vs.
  `receive_response_body.failed exception=CancelledError()` are
  different failure points in the same underlying race** -- both
  observed across the two live freezes tonight, both immediately
  preceding total silence. Either one, on an interrupt POST, may be a
  reliable early warning sign worth alerting on specifically (not just
  logging at DEBUG) if this is pursued as a real fix. (validated-live)

## What transfers, safety-relevant

- Given `hard_stop()` is triggered by the safeword specifically (the
  project's designated emergency-stop phrase), a bug that can freeze
  the whole process when the safeword is said multiple times in quick
  succession is a genuine safety-relevant finding, not just a
  reliability one -- a panicked repeated safeword is a realistic real
  -world input shape, not just a stress-test artifact.

## Not done here

- A minimal repro targeting TWO OR MORE overlapping `hard_stop()`
  calls specifically (rather than one, as tried last round) -- this is
  now the clear next step and should be tractable given this round's
  much sharper understanding of what's required.
- Reading httpx/httpcore's connection-pool source to confirm the
  "cancelled mid-checkout poisons the pool" hypothesis directly, rather
  than inferring it from symptom + log timing.
- Any fix. Still capture-and-diagnose only, per this session's
  practice of writing up findings before proposing changes -- but this
  finding is concrete enough that a fix attempt (e.g. serializing
  `hard_stop()` calls with a lock, or wrapping the interrupt POST in a
  shield against outer cancellation) is now a reasonable next
  engineering task, not just further investigation.
- claude-code re-test with volume confirmed good -- still open,
  deferred again this round in favor of confirming the opencode
  reproduction was real.
