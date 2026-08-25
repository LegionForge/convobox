---
title: First real human-speech session on Linux -- safeword hard-stop confirmed live, and the operator's own real voice independently confirms the same-day AEC volume-sweep's self-barge-in findings at 50% ("pretty much 100%") and 30% ("about half and half")
status: validated-live
date: 2026-08-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0); backend claude-code, permission_mode plan; tts.engine kokoro, voice af_sarah; stt faster-whisper-base; interaction.interrupt_preset conversational; audio.echo_cancellation true; safeword.hard_stop_phrases default (["stop stop stop","abort abort abort"]); openSUSE Tumbleweed 20260822; Sager laptop (2014), onboard ALC892
evidence:
  - Real live --web session, one real human speaker (JP Cruz), /tmp/convobox-voice-test.log (session-local scratch, not committed)
  - Same session driven/observed through a real Chrome browser at http://127.0.0.1:5173 -- screenshot confirms the real transcript, including the safeword trigger and a clean session-ended state
  - Same-day companion notes this one directly cross-confirms: 2026-08-24-linux-volume-sweep-reproduces-high-volume-aec-regression.md (synthetic N=10 calibration harness) and 2026-08-25-linux-tui-web-ui-first-live-verification.md (typed-only Web UI check, no voice)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; logged into the Linux machine directly, ran the entire live voice session himself following a spoken test script, changed system volume live between 50% and 30% to compare, narrated his own real-time observations into the mic as part of the test, asked for this note)
    - Claude Code (Anthropic claude-sonnet-5) -- launched the session, wrote the test script, watched the live log and browser in real time, wrote this note
  org: https://legionforge.org
  created: 2026-08-25T07:32:00-05:00
  revised: 2026-08-25T07:32:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# First real human-speech session on Linux

**Context for outsiders.** Every Linux test earlier this session (the
AEC volume sweep, the OpenCode adapter check, the TUI/Web UI pass) used
either synthetic TTS-vs-mic calibration or typed text -- no real human
voice had gone through ConvoBox's actual live orchestrator loop on Linux
yet. This note closes that gap, following the exact structure of the
2026-08-11 macOS equivalent (`2026-08-11-macos-live-human-demo-safeword-
bargein-and-self-echo-loop.md`): a real person, speaking normally, into
the real pipeline, for the first time on this platform.

## Setup

Real `scripts/run_convobox.py --web` (no `--text`), claude-code backend,
`permission_mode: plan` (read-only), isolated `working_dir`,
`interrupt_preset: conversational`, `echo_cancellation: true`. JP was
given a short spoken test script (baseline turn, stay-silent self-echo
check, real barge-in, safeword, listening-pause) and ran it himself,
speaking naturally, while also directly comparing self-barge-in
frequency at two system volumes (50% and 30%) by narrating his own
observations into the mic mid-session -- an unplanned but genuinely
valuable addition to the original script.

## Real human safeword: confirmed working

```
transcript='Stop, stop, stop!' lang=en (0.79) dec=0.45 busy=False  [HARD STOP]
hard stop matched safeword 'stop stop stop'
```

Fired correctly, immediately, mid-response. Confirmed visually in the
browser transcript too (screenshot taken this session) -- the
`TRANSCRIPT` bubble reads "Stop, stop, stop!" and the session correctly
treated it as a hard stop, not a normal turn.

## Self-barge-in at real human volumes: JP's live read matches the sweep's numbers

JP explicitly compared two system volumes mid-session by ear, narrating
each observation into the mic as part of the transcript itself:

- **At 50%**: *"Okay, on this device, pretty much 100% barge in at 50%
  noise level or volume level."* ... *"Okay, yeah, again at 50%, it looks
  like you're barging in pretty much all of the time."* (two independent
  statements, ~2 minutes apart)
- **At 30%**: *"So as of right now at 30% you're still getting a
  significant amount of self-barging."* -- and, reported directly in chat
  after the session (not in the transcript itself): **"about half and
  half probability"** at 30%.

**This independently confirms the same-day synthetic calibration
sweep's own N=10 numbers**, from a completely different measurement
method (a human ear judging real conversational flow, vs. an automated
VAD-based trial counter):

| Volume | Calibration sweep (N=10, synthetic TTS) | JP's live real-voice read |
|---|---|---|
| 50% | 50 raw / **13 AEC-processed** false-barges across 10 trials (every trial had at least one) | "pretty much 100%... barging in pretty much all of the time" |
| 30% | 8 raw / **4 AEC-processed** false-barges across 10 trials | "still getting a significant amount"; "about half and half probability" |

Two independently-built measurement paths -- one fully automated and
synthetic, one a real human's subjective real-time judgment during an
actual conversation -- landed on the same qualitative shape at both
volumes tested. This is real, meaningful corroboration: the calibration
harness's numbers are not an artifact of its own synthetic setup.

The live log's own `AEC stats` verdicts track this too, drifting from
mostly `FLOOR-LIMITED: echo cancelled down to room noise -- success`
early in the session (lower volume) toward mostly `NO ECHO DETECTED`
and, later, `UNDER-CANCELLING: ~2-5dB of echo headroom remains` as the
session went on -- consistent with JP raising volume back toward 50% for
the direct comparison.

## Layered anti-self-echo defenses: multiple different mechanisms observed firing correctly

Beyond AEC itself (imperfect at higher volume, as above), two other
independent layers fired correctly and repeatedly during this real
session:

- **Backchannel detection** -- short acknowledgment words correctly
  *not* treated as barge-in attempts: `dropped (backchannel, not a real
  interrupt attempt): 'Sounds good.'` / `'Understood'` / `'got it.'` /
  `'Yeah.'` (four separate real occurrences).
- **Overlap-gate + spoken-echo filter** -- utterances that were the
  assistant's own echo, not new user speech, correctly dropped:
  `dropped (overlap gate, echo-cancellation active): 'eject, eject,
  eject.' [echo-match: 1.00 of tokens in last response]` and, once,
  the sibling mechanism: `dropped (spoken-echo filter, barge-in was our
  own echo): 'I got it, nope.' [echo-match: 0.75 of tokens in last
  response]`. Roughly a dozen real occurrences of the overlap-gate
  version across the session, at every volume tested.

**This matters alongside the volume-sweep finding above, not instead of
it**: AEC alone degrading at high volume is real, but it isn't the whole
self-echo story -- these two independent, non-AEC layers were still
catching real echo throughout the session, including at 50% where AEC
itself was least effective.

## A real STT reliability artifact, caught and flagged live by the backend itself

Three consecutive turns produced the transcript `'God it.'` (clearly
meant as "Got it" -- an acknowledgment, not new content) with dropping
confidence each time (`dec=0.42`, `0.40`, `0.37`). The backend's own
reply on the third occurrence noticed the pattern on its own:

```
response: Noted -- that same fragment ("God it") has repeated three
times in a row now with interruptions each time. If you're testing for
transcription reliability, this looks like it could be worth flagging
as a repeat/loop artifact rather than expected behavior.
```

Separately, "eject eject eject" (JP's own improvised phrase, tried
several times as an attempted way to end the session) was transcribed
once as `'Ejekt, Ejekt, Ejekt.'` with **language detected as Turkish
(`lang='tr'`, probability 0.37)** rather than English -- a real STT
language-misdetection glitch on a repeated/staccato three-word phrase,
similar in shape (though not identical in mechanism) to this project's
other documented safeword/repeat-phrase STT reliability findings
(`docs/field-notes/2026-08-15-safety-phrase-reliability-*`,
`2026-08-06-resume-word-hallucination-and-runaway-repetition.md`).

**But the mistranscription isn't actually why "eject" did nothing.**
Checked directly against this session's config and `scripts/
run_convobox.py`: **no voice phrase ends a ConvoBox session at all, by
design.** `safeword.hard_stop_phrases` (`"stop stop stop"`, `"abort abort
abort"`, neither of which is `"eject"`) only interrupts the backend's
*current turn* -- it does not exit. The only ways to actually end a
session are `Ctrl+C` in the terminal or the Web UI's Quit button (`POST
/api/quit`). **JP confirmed this live: he had to fall back to clicking
Quit in the browser** because no spoken phrase -- correctly transcribed
or not -- would have ended the session. This is expected behavior, not a
bug, but it's a real, freshly-confirmed UX data point: there is currently
no voice-driven way to end a ConvoBox session, only a hard-stop-current-
work one. Whether that's a gap worth closing (a configurable voice exit
phrase, analogous to `kill_phrase` but for a clean quit rather than a
force-kill) is a real open product question this session surfaced, not
one this note answers.

## Clean shutdown, confirmed not a crash

Session end produced a `CancelledError` traceback from uvicorn's own
internal lifespan task, immediately followed by the app's own explicit
reassurance, printed by design:

```
INFO exiting
ConvoBox exited cleanly. (A short 'CancelledError' traceback from
uvicorn's own internal lifespan task just above, if you saw one, is
known-harmless shutdown noise -- not a crash. See docs/KNOWN-ISSUES.md.)
```

The browser confirmed the same thing visually: the header changed to
"session ended -- restart ConvoBox and reload this page to reconnect",
not an error state.

## What transfers

- **Linux's safeword hard-stop works with a real human voice**, not just
  synthetic/text input -- the one thing every earlier Linux note this
  session explicitly flagged as untested. (validated-live, one real
  occurrence)
- **The AEC volume-sweep's synthetic findings are now independently
  confirmed by a real human ear in a real conversation**, at both 50%
  (severe) and 30% (moderate/roughly-half) -- two completely different
  measurement methods agreeing is strong evidence neither is a harness
  artifact.
- **Self-echo defense on this project is genuinely layered, not just
  "AEC or nothing"** -- backchannel detection and the overlap-gate/
  spoken-echo filter both independently caught real echo throughout this
  session, including exactly when AEC itself was weakest (high volume).
  This is a real, live-confirmed reason the practical self-barge-in
  experience is likely better than AEC's own numbers alone would suggest
  -- though JP's own "pretty much 100% at 50%" read shows those other
  layers don't fully cover the gap either.
- **STT mistranscribes repeated/staccato three-word phrases in ways that
  can include wrong language detection** ("eject eject eject" -> Turkish,
  0.37 confidence) -- consistent with, and adding a new example to, this
  project's existing body of safeword/repeat-phrase STT reliability
  findings.
- **ConvoBox has no voice-driven way to end a session, by design** --
  only Ctrl+C or the Web UI's Quit button. Live-confirmed when JP's
  attempts to end the session by voice ("eject eject eject", not a
  configured phrase) went nowhere and he fell back to the Quit button.
  Not a bug -- `hard_stop_phrases` are scoped to interrupting the current
  turn, not ending the session -- but a real UX gap worth a product
  decision: should a configurable voice exit phrase exist, analogous to
  `kill_phrase` but for a clean quit?

## Not done here

- Did not test `"abort abort abort"` (the second configured safeword) --
  only `"stop stop stop"` was actually said.
- Did not test the listening-pause phrases (`"stop listening"` /
  `"resume listening"`) -- the script offered this as optional and it
  wasn't run.
- Did not test with a second human speaker (the macOS note's demo had
  two) -- single-speaker session only.
- The 50%-vs-30% comparison, while a real and valuable addition, wasn't
  a controlled protocol (JP narrated his own subjective impression, not
  a counted trial-by-trial tally the way the synthetic sweep was) --
  treat "pretty much 100%" and "about half and half" as informed
  real-world corroboration of the sweep's numbers, not as a second
  independent statistical measurement.
- Did not investigate the "God it"/repeat-fragment or the Turkish-
  language-detection artifacts beyond noting them -- root cause (STT
  model behavior on short repeated/degraded-confidence phrases) not
  diagnosed.
