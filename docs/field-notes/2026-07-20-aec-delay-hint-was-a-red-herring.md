---
title: A plausible AEC theory, verified two ways, was still wrong — because real calibration data already existed and nobody checked it first
status: diagnosed
date: 2026-07-20
project: ConvoBox (github.com/LegionForge/convobox)
versions: aec-audio-processing 1.0.1 (WebRTC AEC3); ConvoBox main circa PR #105
evidence:
  - uat-acoustic-calibration/20260716-152911/report.json (real on-hardware delay sweep, 222/247/272/297ms)
  - uat-acoustic-calibration/20260716-153747/report.json (real on-hardware delay sweep, 285/297/309ms)
  - Live UAT log, 2026-07-19/20 (convobox-tui.log, 115 barge-in fires, 47 UNDER-CANCELLING verdicts)
  - docs/DESIGN-echo-and-barge-in.md's 2026-07-20 "Correction" and "Capturing a live incident" sections
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT, caught the wrong config edit before it stuck)
    - Claude Code (Anthropic claude-fable-5) — log analysis, synthetic experiments, WebRTC research, writing
  org: https://legionforge.org
  created: 2026-07-20T17:15:00-05:00
  revised: 2026-07-20T17:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A plausible AEC theory, verified two ways, was still wrong

**Context for outsiders.** ConvoBox is a voice frontend for coding-agent
CLIs. Its speakers and microphone are open at the same time, so the
assistant's own TTS output can leak back into the mic and falsely
trigger "barge-in" (the assistant thinks the user is interrupting when
it's really just hearing itself). Acoustic echo cancellation (AEC, via
WebRTC's AEC3) is the fix. This note is about diagnosing why AEC wasn't
fully working — and getting it wrong, twice, in ways that looked
well-verified at the time.

## Problem

A live UAT session logged jarring, repeated self-barge-in: the assistant
kept interrupting itself mid-response. The log showed 115 barge-in
events, 93 forwarded as real user input, and only 1 caught by the
spoken-echo backstop filter. AEC verdicts: 32 `FLOOR-LIMITED` (working),
187 `NO ECHO DETECTED`, and 47 `UNDER-CANCELLING` — the code's own
diagnostic literally saying "try tuning aec_delay_ms."

## Evidence and mechanism (in the order it actually happened)

**First theory (wrong): stale hardcoded delay.** `convobox.yaml` had
`aec_delay_ms: 309` explicit; the log's own auto-tune estimate said the
real measured delay was ~222ms. An 87ms mismatch, with the code
explicitly logging "keeping configured 309ms" every response. This
looked like a clean, well-evidenced root cause — a stale value blocking
auto-tune, matching a similar incident already in this project's own
history (`STATUS.md`). **Fix applied: removed the explicit value from
`convobox.yaml`.**

**Verification attempt 1: synthetic experiment.** Swept the delay hint
fed to `EchoCanceller` from 0ms to 522ms against a fixed real delay of
222ms, using the exact production code (`src/convobox/audio/aec.py`),
offline, no live hardware needed. Result: suppression stayed ~40dB
across the *entire* range — the 87ms error the theory blamed moved
suppression by nothing measurable. A follow-up sweep added nonlinear
distortion (a cheap-speaker proxy) on top; still no reproduction of the
real log's pattern (low attenuation despite high measurable echo
headroom — confirmed genuine by comparing attenuation-vs-ceiling on the
actual `UNDER-CANCELLING` log lines, not a measurement artifact).

**Verification attempt 2: real WebRTC source.** A research pass read
AEC3's actual module source (`echo_canceller3.cc`) and found **zero
references to `stream_delay`** — AEC3 estimates delay itself via
`EchoPathDelayEstimator`, cross-correlating render/capture buffers
directly. WebRTC's docs for the related older interface state outright
that manually setting a delay can *disable* the more robust internal
estimator. Strong, citable, converging evidence: the delay hint likely
doesn't matter much for AEC3.

**Both verifications passed. The theory was still wrong**, because
neither verification checked the one thing that would have refuted it
immediately: **real calibration data already sitting in the repo.**
`scripts/acoustic_calibration.py` — an existing, working, unattended
on-hardware calibration tool, never mentioned or consulted during the
diagnosis — had already been run four days earlier
(`uat-acoustic-calibration/20260716-*/report.json`), sweeping delay
values on this *exact* device pair with ranked, repeated trials. Its
verdict: **309ms was the empirically best value of everything tested,
and 222ms — the auto-tune estimate the "fix" was restoring — was the
*worst*** (40% self-barge rejection vs. 75-100% for 247-309ms, with
noticeably higher variance). `309ms` was not stale cruft; it was a
deliberate, evidence-based choice. The config edit was reverted.

## What transfers

- **A theory that survives two independent verification methods can
  still be wrong, if neither method checks for existing empirical data
  that already answers the question.** Synthetic experiments and
  reading real source code are both legitimate verification — but
  "does real calibration evidence already exist for this exact
  question" should be the *first* check, not skipped in favor of
  building fresh evidence from scratch. (diagnosed)
- **A clean, well-cited theoretical result (delay hint barely matters
  for AEC3, confirmed synthetically and from source) does not
  necessarily explain real-world measured outcomes.** The real
  calibration data shows self-barge rejection varying from 40% to 100%
  across delay values in an actual room — something is correlating with
  this parameter in practice, even though the isolated synthetic model
  and the WebRTC source both suggest it shouldn't. This tension is
  reported honestly as **unresolved**, not resolved in either direction.
  (hypothesis)
- **Before touching a config value that looks wrong, check whether it
  was chosen deliberately.** A `grep` for related calibration/report
  artifacts, or simply asking "was this value ever measured," would
  have caught the mistake before it was made, not after.
  (validated-live — this incident)
- **The actual root cause of the live session's jarring self-barge-in
  is still unknown.** It happened while running the empirically-best
  known delay value, ruling out "wrong delay" as the explanation.
  Re-running the existing calibration script (conditions may have
  shifted in four days) is the next concrete step, not more theorizing.
  (diagnosed)
- Separately, discovered by direct reproduction while chasing this:
  **`uv`'s local build cache can cross-contaminate editable installs
  between two same-named, same-version local clones of one repo** — a
  `uv sync` in one clone silently repointed another clone's `convobox`
  import at the wrong source tree. See `TESTING.md` for the confirmed
  fix (`uv cache clean` + `--no-cache` reinstall) and the practical
  rule. (validated-live)
