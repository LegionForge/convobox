# Design: backend interactive questions over voice

Status: DESIGN RECORDED 2026-07-18 (UAT finding [L9]); slice 1 in progress.
Origin: live session #5 -- the opencode build agent called its interactive
`question` tool and the voice session deadlocked (see
`docs/UAT-checklist.md` [L9] for the full evidence chain).

## The human-expectation analysis (JP + Claude, 2026-07-18)

When a person misses a question, the natural repair moves are "sorry, can
you repeat that?", "are you okay?", "what are you waiting for?" -- and in
human conversation the ASKER re-asks after silence. Nobody expects to need
an emergency phrase to answer a routine multiple-choice question. If the
safeword is the only exit from a pending question, the turn-taking
contract is broken. This matches Google Conversation Design's No-Input
guidance (already the basis for `RecognitionErrorLadder`): on silence, the
system re-prompts with escalating clarity; it never waits silently forever.

**The architectural catch [L9] proved live:** conversational repair cannot
go THROUGH the backend. "Can you repeat the question?" is just another
steer prompt, and steers queue invisibly behind the blocked tool (verified:
~15 admitted-but-never-materialized prompts). Repair must be handled by
ConvoBox itself -- which is the better design anyway, because the pending
question's full text and options are readable locally, so ConvoBox can
speak them deterministically: no LLM round-trip, no queue.

## Server API (verified against a live opencode 1.18.3 server's /doc)

- `GET /api/session/{sessionID}/question` -- pending questions with full
  text + options (verified returning the real blocked question during [L9])
- `POST /api/session/{sessionID}/question/{requestID}/reply`
- `POST /api/session/{sessionID}/question/{requestID}/reject`
- `POST /session/{sessionID}/abort` -- what the safeword path already does

## The ladder (escalation order)

1. **Announce on arrival.** When a pending question is detected, ConvoBox
   itself speaks the question + enumerated options ("The agent is asking:
   ... Option one: ...; option two: ..."). Barge-in-proof because ConvoBox
   can re-speak it (step 3).
2. **Re-prompt on silence.** Question pending + N seconds of no answer ->
   repeat, `RecognitionErrorLadder`-style tiers: verbatim -> shortened with
   numbered options -> "say 'never mind' to cancel". The asker re-asks, as
   a human expects.
3. **Local repair phrases.** "repeat the question", "what are you waiting
   for?", "are you okay?" -> handled by the orchestrator directly
   (deterministic phrase match, same family as safeword/wake word),
   re-speaking the pending question. Never forwarded to the backend while
   a question is pending.
4. **Voice answer.** Match the user's reply against option labels/numbers
   ("option two", "the live test") and POST to the reply endpoint;
   "never mind" -> reject. Ambiguous reply -> re-offer options (tier 2).
5. **Safeword unchanged** -- still aborts everything; becomes the escape
   hatch it was designed to be, not the only exit.

**Honest heartbeat (smallest slice, biggest immediate value):** during
[L9] the working indicator said "thinking or running a tool" for 126s
while the backend was actually waiting on the USER. When a question is
pending, the heartbeat line must say so ("backend is waiting for YOUR
answer: <question summary>"). This alone would have surfaced the deadlock
in real time.

## Slicing

- Slice 1 (safe, read-only backend interaction): detect the pending
  question, log it, speak it (steps 1 + the honest status). No reply
  dispatch.
- Slice 2: silence re-prompt tiers + local repair phrases (steps 2-3).
- Slice 3: the voice answer (step 4) -- reply dispatch. NOTE: replying on
  the user's behalf is user-in-the-loop (the user speaks the answer;
  ConvoBox transports it), distinct from the Phase-3 auto-approval arc
  that remains gated on JP's direct involvement. Treat option-matching
  strictness with ConfirmwordDetector-grade care anyway.

## Related idea, recorded not scheduled (JP, 2026-07-18)

Interject a system prompt asking the backend to keep VOICE responses brief
(a token-budget hint), with an explicit "there's more if you want" follow-up
convention when a full explanation exists. Overlaps deliberately with the
existing response-tiering machinery (`ContinueDetector`,
`tier_responses`/`ContinuePromptGate`) and the ROADMAP's VERBALIZE/DISPLAY
contract -- tiering trims at the orchestrator after generation; this idea
shapes generation itself. They compose: a brevity prompt reduces what
tiering has to cut. Not to be implemented now; revisit after the question
ladder lands.
