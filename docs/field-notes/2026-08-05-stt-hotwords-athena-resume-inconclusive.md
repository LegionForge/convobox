---
title: The same stt.hotwords-active branch produced both the best and worst "Athena" resume runs of the day — and mic hardware turned out to matter more
status: hypothesis
date: 2026-08-05
project: ConvoBox (github.com/LegionForge/convobox)
versions: feat/stt-hotwords-bias (PR #204, commits c7a84f3/c1f78a1, authored 2026-08-03; merged with main @ 55df0f1 for the later runs); faster-whisper base, device=cpu, compute_type=int8, stt.hotwords="Athena stop break brake eject mayday Whiskey Tango Foxtrot[ listening]", stt.temperature unset for runs 1-3, pinned to 0.0 for runs 9-14, vad.max_utterance_s=30.0 for runs 9-14; backend=codex
evidence:
  - convobox-tui.log 2026-08-05 14:41:35-14:43:10 (session 1: original incident)
  - convobox-tui.log 2026-08-05 15:36:22-15:43:21 (session 2: fix/web-listening-bridge-tui-desync, no hotwords support present)
  - convobox-tui.log 2026-08-05 16:19:22-16:23:17 (session 3: feat/stt-hotwords-bias merged with main @ c59e117)
  - convobox-tui.log 2026-08-05 19:54:42-20:03:57 (UAT part 9: hotwords ON, temperature=0.0, max_utterance_s=30, 1080 Pro Stream mic)
  - convobox-tui.log 2026-08-05 20:03:57-20:08:03+ (UAT part 10: hotwords OFF, otherwise identical to part 9)
  - convobox-tui.log 2026-08-05 21:45:56-21:51:41 (UAT part 12: hotwords ON, Lavalier mic)
  - convobox-tui.log 2026-08-05 21:55:27-22:01:44 (UAT part 13: hotwords ON, 1080 Pro Stream mic again)
  - convobox-tui.log 2026-08-05 22:06:04-22:26:42+ (UAT part 14: hotwords ON, Airhug 28 mic, AI DSP toggled on/off/on/off within the session)
  - uat-echo.log (per-run labels; note it is overwritten, not appended, by `Tee-Object` without `-Append` -- only the LAST label survives on disk, session-start banners in convobox-tui.log are the durable boundary markers)
  - PR #204 (open, unmerged) — its own live-UAT checklist item this note answers
  - src/convobox/config.py:76-104 (STTConfig: temperature/condition_on_previous_text default to a faster-whisper no-op when unset)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran all sessions, correctly pushed back on treating any single run as proof, deliberately varied mic/DSP to stress-test the comparison)
    - Claude Code (Anthropic claude-sonnet-5) — live log investigation, writing
  org: https://legionforge.org
  created: 2026-08-05T16:35:00-05:00
  revised: 2026-08-05T22:35:15-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The same hotwords-active branch produced both the best and worst "Athena" resume runs of the day

**Context for outsiders.** ConvoBox's voice pipeline uses a spoken resume
word ("Athena" by default) to un-pause listening after a pause phrase.
Short proper nouns are known to mis-transcribe on faster-whisper (this
project's own recurring "Athena" hallucination pattern, the whole reason
PR #204 exists). #204 adds `stt.hotwords`, a free-text prompt bias passed
to `transcribe(hotwords=...)`, with one open live-UAT question on its own
checklist: does it actually reduce the miss rate? This note is the answer
so far — and it is not a clean yes.

## Problem

Three real sessions across one afternoon gave three different resume-word
success rates. The instinct is to credit the best one to `stt.hotwords`.
The timeline doesn't support that instinct cleanly.

## Evidence

| Session | Time | Branch | `stt.hotwords` actually active? | Result |
|---|---|---|---|---|
| 1 (original incident) | 14:41-14:43 | `feat/stt-hotwords-bias` | **Yes** | 0-for-3: "Pina" (mis-transcribed), then two no-input drops. Never resumed by voice; fixed via the web button (separate issue, see the 2026-08-05 web-resume field note). |
| 2 (follow-up) | 15:36-15:43 | `fix/web-listening-bridge-tui-desync` (off `main`) | **No** — confirmed by grep: no `hotwords` field on `STTConfig` on this branch; the config key is silently dropped (pydantic `extra="ignore"`) | 3 cycles, 1/2/2 attempts before each success |
| 3 (this session) | 16:19-16:23 | `feat/stt-hotwords-bias` merged to `main` @ `c59e117` | **Yes** | 3-for-3, first try every time: 16:20:30, 16:20:54, 16:22:27 |

Session 1's transcript: `26517:...dropped (paused, not the resume word):
'Pina'`, `26520`/`26523`: two consecutive `dropped (no input, STT heard
nothing recognizable)`. Session 3's transcript: `26891`, `26918`, `27055`,
all `resumed listening (resume word matched): 'Athena'`, each on the
immediately-following STT attempt after its pause.

Session 1 and session 3 ran the **identical hotwords-active code and
config** — `feat/stt-hotwords-bias` has carried `stt.hotwords` since
commits authored 2026-08-03, well before either session, and
`convobox.yaml`'s `hotwords:` value was unchanged between them. Same
branch, same setting, opposite outcomes (0-for-3 vs. 3-for-3).

## Mechanism

Ruled out as a clean explanation: **hotwords being present or absent**,
since it was present in both the best and worst runs. Whatever produced
session 3's clean run, it isn't simply "hotwords was on this time."

Candidate confounds, none isolated:

- **STT decoding isn't pinned.** `stt.temperature` and
  `stt.condition_on_previous_text` are both unset this whole day, so
  faster-whisper's own temperature-fallback ladder is live — the same
  audio can decode differently run to run for reasons that have nothing
  to do with hotwords. This alone is enough to explain session-to-session
  variance without invoking hotwords at all.
- **Operator state.** Session 1's resume attempts followed a
  false-positive pause (the "No I tried to say the stop listening
  phrase" trigger) and landed inside a genuinely confusing, frustrating
  incident (see the web-resume field note). Session 3's resume attempts
  were calm, deliberate UAT. Speech clarity/pace under frustration vs.
  calm testing is a real, unmeasured variable.
- **Time-of-day / room drift.** ~1.5 hours apart; operator reports no
  perceived change in room noise or mic position, but "no perceived
  change" isn't a controlled measurement of either.
- **Small n regardless.** 3 genuine "Athena" attempts per session, 9
  total across the whole day. Not enough to support a rate claim in
  either direction even before the confounds above.

## Follow-up: temperature pinned, mic hardware isolated as a dominant confound (same day, later)

The recommended follow-up ran: `stt.temperature: 0.0` pinned (removes
faster-whisper's own decode randomness) and `vad.max_utterance_s: 30`
set (separately motivated by
`docs/field-notes/2026-08-05-vad-segmenter-silent-unbounded-lockup.md`),
then a genuine same-sitting `stt.hotwords` on/off A/B (parts 9 and 10,
same mic, back-to-back), followed by three more runs (12/13/14) that
varied the microphone instead, which turned out to matter far more than
hotwords.

**Parts 9 vs. 10 — the actual controlled hotwords A/B, mic and
temperature held constant:**

| | Part 9 (hotwords ON) | Part 10 (hotwords OFF) |
|---|---|---|
| Pause cycles | 2 | 2 |
| Resolved by voice | **2/2** | **0/2** — both needed the web button, after 4 and 7 non-matching drops |
| "Athena" ever mis-transcribed when genuinely said | No | Never got a clean shot — the only literal "Athena" hearings that run came after both pauses were already cleared, and were dropped by the confidence gate, not tested against the resume word |

This is the cleanest single data point in favor of hotwords helping —
genuinely isolated (same mic, same temperature, same day, back-to-back).
Still n=2 cycles per condition.

**Parts 12/13/14 — mic hardware swapped, hotwords held ON throughout:**

| Part | Mic | Cycles resolved by voice | Notes |
|---|---|---|---|
| 12 | Lavalier | **0/2** | 11 drops before giving up on cycle 1 (web button), including three consecutive near-miss phonetic mangles of "Athena" itself — `'Inna.'`, `'Pina'`, `'Pena.'` — and the operator's own live comment mid-session: *"It looks like it's not picking up that well on this particular [mic]."* |
| 13 | 1080 Pro Stream (main mic) | **4/5** | One 10-drop cycle still needed the web button; the other four resolved by voice, mostly by embedding "Athena" in a full sentence (see below) |
| 14 | Airhug 28 (AI DSP toggled on/off/on/off through the session) | **14/14** | Best run of the day. DSP state showed no clean correlation with performance — both DSP-ON stretches were low-drop (≤2/cycle), but the two DSP-OFF stretches differed wildly from each other (1-2 drops/cycle early, 5/11/4 drops/cycle later) |

Mic choice alone swung the voice-resolution rate from 0/2 to 14/14, all
else held constant (hotwords ON, temperature 0.0, cap 30s). That is a
far larger effect than anything hotwords showed in parts 9/10. Whatever
share of the day's variance hotwords accounts for, it is not the
dominant lever for this failure mode — mic hardware is.

**A secondary finding inside part 14**: performance dipped hard in the
middle of a ~20-minute continuous session (one cycle needed 11 drops)
without lining up with any deliberate variable change (it falls in the
middle of a DSP-OFF stretch whose OTHER cycles were fine). Reads more
like session-length/operator-fatigue drift than a DSP effect — a new,
unisolated candidate confound, not yet investigated on its own.

**A real, actionable, twice-corroborated technique, independent of any
config**: saying "Athena" embedded in a full sentence ("trying to say
the word Athena," "just trying to say the wake word Athena") resolved
reliably in parts 10, 13, and 14, while "Athena" said alone was the
specific case that failed the confidence gate in part 10 (0.33 < 0.40)
and produced several of the phonetic near-misses elsewhere. Plausible
mechanism: a longer utterance gives both the language-ID confidence
score and the acoustic model more context than an isolated single word.
Not yet tested as a deliberate controlled variable, but consistent
enough across independent sessions to note.

## What transfers

- **Don't credit a single session's result to the one variable you
  changed if an earlier session with the same variable already
  contradicts it.** Session 1 alone would have been "hotwords doesn't
  help, still 0-for-3." Session 3 alone would have been "hotwords fixed
  it, 3-for-3." Both readings are wrong; only comparing across sessions
  surfaced that. (validated-live, as a methodology lesson — not a claim
  about hotwords itself)
- **A same-proper-noun resume-word test needs decoding pinned before
  attributing anything to a bias parameter.** Done in the follow-up:
  `temperature: 0.0` removed the STT's own randomness as a variable.
  (validated-live)
- **With decoding pinned and mic held constant, hotwords ON showed a
  real advantage over OFF (2/2 vs 0/2 voice-resolved, parts 9/10).**
  Still small-n, but this is the first genuinely isolated data point for
  #204 all day. (hypothesis, trending toward validated-live — one more
  same-mic on/off pair would firm this up)
- **Mic hardware is a bigger lever than hotwords for this failure mode.**
  0/2 to 14/14 voice-resolved swinging on mic choice alone, hotwords
  held constant. Any future STT-reliability work on the resume word
  should treat mic selection/positioning as at least as high-priority as
  STT-side config. (validated-live)
- **AI DSP on/off showed no clean effect** in the one session that
  toggled it mid-run (part 14) — both states performed well; the one bad
  stretch didn't align with a DSP transition. (hypothesis, single
  session)
- **A newly surfaced, unisolated confound**: apparent within-session
  performance drift over ~20 minutes, independent of any deliberate
  variable (part 14). Not yet investigated — worth a dedicated session
  if it recurs.
- **Actionable operator technique**: embedding the resume word in a full
  sentence resolves more reliably than saying it alone, corroborated
  across three independent sessions/mics. Worth surfacing as UX guidance
  (e.g. Settings TUI hint text) once corroborated further — not yet a
  product decision, just a live observation.
