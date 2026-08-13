---
title: An exhaustive 10-cycle automated batch, run after removing the Windows Audio Enhancements confound, still shows a real ~30% total-stall rate and unreliable pause/resume
status: validated-live
date: 2026-08-12
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #271 branch (docs/vad-freeze-live-repro-2026-08-12) @ b15fe47; stt.device=cpu, stt.model=base; backend=codex, permission_mode=permissive; resume_word="resume listening"
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT, --tui --web -v, real codex backend, working_dir _artifact-test-scratch
  - A self-healing synthetic-speech stress supervisor (scratch script, not committed), run for 10 automated cycles with Windows mic "Audio Enhancements" confirmed OFF
  - docs/field-notes/2026-08-12-vad-freeze-harness-catches-short-stalls-and-a-12-minute-unrecoverable-one.md (same-day predecessor, since corrected for the Enhancements confound)
  - src/convobox/resumeword/detector.py, src/convobox/listening_pause/detector.py, scripts/run_convobox.py's ListeningGate (code read to rule out a resume-matcher bug)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; identified the Windows Audio Enhancements confound live via direct questioning, disabled it, confirmed pickup by ear and via the web UI)
    - Claude Code (Anthropic claude-sonnet-5) -- supervisor design/fix, batch execution and live analysis, code tracing, writing
  org: https://legionforge.org
  created: 2026-08-12T21:05:00-05:00
  revised: 2026-08-12T21:05:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# An exhaustive 10-cycle automated batch, run clean of the Windows Enhancements confound, still shows a real ~30% total-stall rate

**Context for outsiders.** Same freeze investigation as this session's
two earlier notes. This one is the first batch run with the actual
confound (Windows' own mic echo/noise suppression treating same-machine
synthetic test audio as self-echo) identified and removed -- so it's the
first data in this whole investigation that can be trusted as a clean
measurement of the app's own behavior, not a mix of the app and a
test-environment artifact.

## How the confound was found and fixed

Mid-session, with the operator physically present: raising system output
volume from 7% to 14% did not fix synthetic-audio pickup (ruling out
volume). The operator asked directly: "could Windows be doing AEC?"
Checked `aec.py` directly -- ConvoBox's own AEC only ever receives a
far-end reference from its *own* playback thread (`feed_reverse()`,
called per-block from `EchoAwarePlayer`); a synthetic harness playing
audio via an independent process's own `sd.play()` never feeds that
reference, so ConvoBox's own AEC structurally cannot be the mechanism.
That pointed at Windows' own OS-level mic enhancements instead -- checked
Settings -> Sound -> Input -> device properties -> "Enable audio
enhancements": **on**. Disabled it. Retested: perfect pickup, correct
transcripts, correct safeword matches, immediately.

## A second, independent bug found in the test harness itself

Before running this batch, a second real bug was caught (via the
operator's own patient real-time correction) in the *test supervisor's*
own success/failure detection, unrelated to the app: it declared a
visibly successful cycle (correct transcripts, correct hard-stops, all
observed live) a "failure" and restarted a healthy session, because it
measured success as "no silence after the whole cycle finishes" --
which is indistinguishable from a session correctly going idle once
nothing more is being said. Fixed to measure activity *during* the
burst itself instead. Worth naming explicitly: **two consecutive,
unrelated confounds (Windows Enhancements, then the supervisor's own
detection logic) both had to be found and fixed before this
investigation produced trustworthy automated data** -- neither the app's
behavior nor a human's read of "seems broken" should be trusted without
ruling out the test setup first.

## The batch

10 cycles, same protocol as prior sessions (pause phrase, 18-phrase
rapid-fire burst across 6 safewords x3 repeats, resume phrase), fully
automated, Windows Enhancements confirmed off, corrected detection logic.

| Cycle | Burst hit rate | Pause registered | Resume registered | Outcome |
|---|---|---|---|---|
| 1 | 100% | yes | yes | clean |
| 2 | 33% | yes | no | success (weak) |
| 3 | 0% | no | no | **restart** |
| 4 | 28% | yes | no | **restart** |
| 5 | 94% | yes | no | success |
| 6 | 100% | yes | yes | clean |
| 7 | 44% | yes | no | success (weak) |
| 8 | 72% | no | yes | ambiguous |
| 9 | 6% | no | yes | **restart** |
| 10 | 100% | yes | no | success |

**Mean burst hit rate: 58%. 3/10 cycles (30%) required a full session
restart** (near-zero pickup, matching the original freeze's total-silence
signature). **Only 2/10 cycles (20%) had both pause and resume cleanly
register.**

**Important measurement caveat, stated plainly:** `pause_registered`/
`resume_registered` are a weak proxy -- "did the log grow at all" -- not
confirmation that the phrase was correctly *interpreted* as the pause/
resume command specifically. Code tracing (see below) shows the far more
likely explanation for most "resume failed" rows: the pause phrase
itself sometimes fails to register as a genuine pause (garbled/partial
transcription under this stress pattern -- 'stop listening' transcribed
as just 'listening' was observed earlier the same evening), so the
session was never actually in the paused state, and the resume phrase
correctly falls through as ordinary conversation rather than being
incorrectly rejected by broken matcher logic.

## Ruled out: a bug in resume-word matching itself

Traced the full path: `ResumeWordDetector.check()`
(`resumeword/detector.py`) does a plain normalized-substring match --
for `resume_word="resume listening"` against a transcript of exactly
`"resume listening"`, this trivially returns `True`. `ListeningGate
.observe()` (`run_convobox.py`) checks `is_paused` first; if the wake
detector matches, flips it and returns `"resume"`. Both are correct,
simple, and would work given the right inputs. **Directly confirmed via
the log**: two "resume listening" transcripts this session (20:36:43,
20:46:34) were transcribed with high confidence (0.97/0.96 language
probability) but appeared as plain `transcript=` lines, not
`resumed listening (resume word matched)` lines -- consistent with
`observe()` returning `"pass"` because `is_paused` was already `False`
at that moment, not with the matcher itself failing on a correct input.

## What transfers

- **Fixing one confound doesn't mean the next problem you see is real --
  check for a second one before trusting automated results.** This
  session needed two independent, unrelated fixes (Windows Enhancements,
  then the test harness's own detection logic) before its data was
  trustworthy. (validated-live)
- **A synthetic same-machine speech-injection harness needs its
  environment audited for OS-level echo/noise suppression before first
  use, not after results look wrong.** The mechanism (treating
  same-machine playback as self-echo) is generic to any OS with driver-
  level mic enhancements, not specific to this app or this session.
  (validated-live)
- **Even with both confounds removed, the underlying freeze is real and
  frequent under this stress pattern: ~30% full-restart rate across 10
  independent cycles**, not a rare edge case. (validated-live)
- **A "did X fail" signal from log-activity presence/absence alone is too
  coarse to distinguish "the matcher is broken" from "the precondition
  for the matcher never held."** Confirmed here by direct code tracing:
  the resume-word matcher itself is correct; a downstream reporting
  metric built only on log-growth cannot tell that apart from "pause
  never actually took effect." (validated-live)

## Next steps, not done this session

1. Instrument `ListeningGate.observe()`'s own return value (or at least
   whether `is_paused` was true at check time) into the log, so future
   sessions can directly distinguish "pause never took effect" from
   "resume matcher failed on a real pause" without this note's own
   after-the-fact inference.
2. Re-run a similarly-sized batch specifically measuring **pause-phrase
   recognition reliability** in isolation (not bundled with the full
   burst-stress protocol), since that looks like the more fundamental,
   underlying variable this whole investigation keeps running into.
3. The 30% full-restart rate is now the headline number for release
   discussions -- treat it as the current best estimate of how often this
   stress pattern produces a real, user-visible stall, not the earlier
   sessions' smaller, less controlled samples.
