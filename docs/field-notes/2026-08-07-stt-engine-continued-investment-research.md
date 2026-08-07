---
title: faster-whisper/ctranslate2's core reliability problems are still unfixed a month later -- onnx-asr/Parakeet TDT remains the strongest concrete alternative, Moonshine (English) is a new credible second option
status: diagnosed
date: 2026-08-07
project: ConvoBox (github.com/LegionForge/convobox)
versions: faster-whisper (ctranslate2 4.8.1); onnx-asr 0.12.0; openWakeWord 0.6.0 (repo HEAD past that tag); moonshine-ai/moonshine v0.1.0
evidence:
  - SYSTRAN/faster-whisper issues #660, #390, #992 (GitHub, fetched live via `gh issue view`)
  - OpenNMT/CTranslate2 release list (GitHub, fetched live via `gh release list`)
  - istupakov/onnx-asr repo, README, releases, issues (GitHub, fetched live)
  - dscripka/openWakeWord repo, releases, commits, issues (GitHub, fetched live)
  - usefulsensors/moonshine (moonshine-ai/moonshine) repo, releases, LICENSE (GitHub, fetched live)
  - docs/KNOWN-ISSUES.md's existing ctranslate2 allocator-leak entry (this repo)
  - docs/ROADMAP.md's "Alternative local STT engines" section, 2026-08-03 pass (this repo)
  - Open ASR Leaderboard summary via web search (secondary source, not fetched directly from Hugging Face Spaces)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (project owner; did not operate this pass -- unattended autonomous research)
    - Claude Code (Anthropic claude-sonnet-5) -- investigation and writing, no code changes
  org: https://legionforge.org
  created: 2026-08-07T02:15:00-05:00
  revised: 2026-08-07T02:15:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# faster-whisper/ctranslate2's reliability problems are still unfixed a month later

**Context for outsiders.** ConvoBox is a voice interface for driving coding
agents (Claude Code, Codex, OpenCode) by speech. It uses `faster-whisper`
(a `ctranslate2`-backed reimplementation of OpenAI Whisper) for local
speech-to-text. `docs/KNOWN-ISSUES.md` already documents two real,
live-hit problems with this engine: a native memory-allocator leak that
can eventually kill an STT session outright, and a hallucination failure
mode on short phrases (the app's own wake/resume word has repeatedly been
mis-transcribed as fluent, unrelated sentences). `docs/ROADMAP.md` ran a
first alternative-engine survey on 2026-08-03. This note is a one-month
follow-up check, done with no live hardware -- it only asks "has anything
changed upstream, and does the existing recommendation still hold," not
"we tested a replacement."

**Status ceiling, stated up front:** everything below is **diagnosed**
(read from primary sources -- GitHub issues/releases/READMEs, fetched live
today) or explicitly marked **hypothesis** where the underlying evidence
itself is someone else's unverified claim. Nothing here is
**validated-live** -- that requires this project's own hardware and a real
mic/GPU session, which this pass did not have access to.

## Problem

Is `faster-whisper`/`ctranslate2` still worth continued investment, or has
enough changed (upstream fixes, a matured alternative) that ConvoBox
should start a real replacement prototype instead of continuing to tune
around known failure modes?

## Evidence

**1. The ctranslate2 native-allocator leak: still open, still no fix, and
the "closed" issue's own last comment undercuts the closure.**

- `OpenNMT/CTranslate2` release history (fetched live via `gh release
  list`): latest tag is still **v4.8.1, 2026-07-03** -- unchanged from the
  2026-08-03 ROADMAP note, over a month with no new tagged release. The
  repo itself is not dead (`pushedAt: 2026-08-05`, i.e. commits landing
  two days ago) but nothing has shipped that fixes this.
- Issue **#660** ("Faster whisper holding memory not releasing it,
  killing the flask server"): still **OPEN**, last updated 2025-01-13.
  No maintainer resolution ever posted.
- Issue **#992** ("Memory on GPU not cleared after transcription"): still
  **OPEN**. Community consensus in the thread (`benniekiss`,
  2024-09-06): "That's likely the memory held by the cuda runtime, which
  iirc, can't really be freed unless the entire process is killed" --
  i.e. even the community's own working theory is that this specific
  class of leak is a CUDA-runtime-level floor, not something fixable in
  ctranslate2's Python-facing code at all.
- Issue **#390** ("Memory Leak investigation"): the ROADMAP's 2026-08-03
  note already flagged this as "closed via PR #448, but that fix could
  not be confirmed to specifically cover the MKL allocator leak." Checked
  again today: it's now showing **CLOSED / stateReason: COMPLETED**,
  closed 2026-01-10. But the comment immediately preceding closure (same
  day, user `wojnicki`) is a **new, unresolved leak report** -- a
  reproduction script showing CPU-mode RSS climbing from ~856MB to
  ~1363MB across repeated `transcribe()` calls, explicitly on the *CPU*
  path (`device="cpu"`), not the GPU path PR #448 targeted. Nobody
  responded to it before the issue closed. **This is worse than "closed,
  unclear if it covers the leak" -- it's "closed over a live, unaddressed
  counter-example."** Treat the closure as administrative, not as
  evidence the leak is gone.

**Read on the allocator leak: nothing has changed. The 2026-08-03
ROADMAP conclusion -- "this is likely the durable state of things, not a
stopgap" -- holds, with slightly stronger evidence now (#390's own
closing sequence) than it had a month ago.**

**2. `onnx-asr` (the Parakeet TDT delivery vehicle): actively maintained,
explicit Windows+CUDA support, and the accuracy/speed numbers still hold
up.**

- Release cadence is real and recent: **v0.12.0, 2026-07-15** is latest;
  ten releases show roughly monthly-or-faster cadence back through
  2025-11.
- The README states platform support explicitly, not by inference:
  *"Works on Windows, Linux, and macOS on x86 and Arm CPUs, with support
  for CUDA, TensorRT, CoreML, DirectML, ROCm, and WebGPU."* DirectML is
  worth noting specifically for this project's Windows/RTX 4060 dev
  machine -- it's a Windows-native GPU inference path that doesn't
  depend on CUDA at all, a second GPU option ctranslate2 doesn't offer.
- A real open issue is directly relevant to ConvoBox's own usage shape:
  **#131**, "parakeet-tdt-0.6b-v3: hard length cliff (~300s OK, ~600s
  broadcast error)" (open, updated 2026-07-11) -- a genuine limitation
  on long single-shot audio. Less of a concern for ConvoBox specifically
  since its STT calls are short per-utterance clips (seconds, not
  minutes), and the README separately documents built-in long-form
  support via VAD-based chunking as the intended workaround for exactly
  this case -- not independently verified here, but a real documented
  path, not a gap.
- A previously-open GPU-performance issue (**#13**, "GPU inference ...
  significantly slower than CPU") is now **CLOSED** -- some prior rough
  edge on the GPU path has been addressed since.
- External benchmark check (secondary source, not fetched directly from
  the Open ASR Leaderboard itself -- **hypothesis-strength**, consistent
  with but not a re-verification of the ROADMAP's own 2026-08-03
  numbers): Parakeet TDT 0.6B v3 reported around 6.32-6.34% WER vs.
  Whisper large-v3's ~7.4%, with a large realtime-throughput advantage
  (reported RTFx in the thousands vs. large-v3's reported ~69). Same
  caveat the ROADMAP already carries: no independent WER/hallucination
  test has been run on ConvoBox's own hardware/audio -- these are still
  someone else's numbers.

**Read: `onnx-asr` looks like a healthier, better-fitting dependency
today than it did on 2026-08-03, not just an unchanged option -- the
DirectML support and the closed GPU-slowness issue are both new positive
signals for this project's specific Windows+RTX-4060 environment.**

**3. `openWakeWord`: functionally still alive, but maintenance has
visibly slowed, and it has open, unresolved platform bugs on both
platforms this project might run on.**

- No tagged release since **v0.6.0, 2024-02-11** -- over two years with
  commits landing on `main` (most recently 2025-12-30) but nothing
  packaged/tagged since. Not archived, not dead, but not actively
  shipped either.
- **Issue #187**, "ONNXRuntimeError Windows" -- open since 2025-03-20,
  still unresolved, directly relevant to this project's primary dev
  platform.
- **Issue #336**, "ONNX inference backend produces near-zero scores on
  macOS ARM64 (Apple Silicon)" -- open, filed 2026-07-24 (two weeks
  before this note), i.e. a *recent*, apparently unaddressed accuracy
  regression on Apple Silicon, the platform this project's own roadmap
  names as the next validation target.
- The underlying architectural point from the 2026-08-03 ROADMAP note is
  unchanged and not something this pass re-litigates: `MicrophoneStream`
  is single-consumer, so wiring a parallel wake-word classifier still
  needs real broadcast/tee plumbing that doesn't exist today -- an
  integration cost independent of which wake-word library gets picked.

**Read: the maintenance-slowdown signal is new since 2026-08-03 (the
ROADMAP note didn't check release/commit cadence) and is a real reason
for caution beyond the previously-known integration cost -- an unfixed,
year-old Windows error on the exact platform this project develops on is
a bad sign for adopting it as a dependency right now.**

**4. New candidate not previously surveyed: Moonshine
(`moonshine-ai/moonshine`, formerly `usefulsensors/moonshine`).**

- Very actively developed: latest release **v0.1.0, 2026-07-27**,
  multiple releases per week through July 2026, a commit pushed the same
  day this note was written (2026-08-07). By far the most active project
  of the three alternatives checked here.
- Explicitly designed for low-latency, edge/resource-constrained
  real-time use -- architecturally a different design point than
  Whisper's encoder-decoder (per third-party writeups found via search;
  **hypothesis-strength**, not independently confirmed against
  Moonshine's own technical documentation in this pass).
- Third-party sources describe it as substantially better-controlled for
  hallucination-on-silence than Whisper -- **hypothesis-strength only**:
  this claim comes from blog-style secondary sources, not a benchmark
  this note independently checked, and it is exactly the kind of claim
  the 2026-08-03 ROADMAP note already taught this project to distrust by
  default (the FunASR marketing-only-benchmark precedent). Flagging it as
  worth a real look, not accepting it at face value.
- **License is more nuanced than a single badge would suggest, verified
  directly from the LICENSE file, not summarized secondhand:** English-
  language models ship under a **fully permissive MIT license**.
  Non-English models ship under a separate "Moonshine Community License"
  that requires registration for commercial use and adds a $1M-annual-
  revenue threshold above which a separate license must be requested.
  Since ConvoBox's documented usage is English-only today, the relevant
  license for this project specifically is the clean MIT one -- this is
  a real, currently-usable option license-wise, not a Piper-style
  opt-in-required case.

**Read: genuinely new information this pass surfaced, not in the
2026-08-03 survey at all. Worth a line in ROADMAP.md's watch list. Not
elevated above onnx-asr/Parakeet here because the hallucination-control
claim specifically is unverified marketing-adjacent language, the same
category of claim this project has already been burned by once (FunASR)
-- it needs the same "prove it on our own hardware" bar before it
influences anything.**

## Mechanism

No new mechanism was investigated this pass -- this is a status check on
sources the 2026-08-03 ROADMAP pass already cited (ctranslate2 leak) plus
health checks on the two alternatives it already named (onnx-asr,
openWakeWord), plus one new candidate search (Moonshine). The one
correction to the prior record: issue #390 being marked CLOSED could
easily be misread as "the leak got fixed" without reading its actual
final comment -- worth flagging explicitly since a future pass skimming
issue *state* alone (open/closed) rather than content would draw the
wrong conclusion.

## What transfers

- **"Closed" on a GitHub issue is not evidence of a fix -- read the
  actual last comment before updating a belief based on issue state
  alone.** (diagnosed, this instance: #390 closed same-day as an
  unaddressed new leak reproduction, no maintainer response in between)
- **A library's tagged-release cadence and recent-issue activity are a
  cheap, fast maintenance-health signal, cheaper than reading code.**
  Comparing `onnx-asr` (monthly releases, issues actively triaged and
  closed) against `openWakeWord` (no release in 2+ years, a year-old
  open Windows bug on this project's own dev platform) took a handful of
  `gh` calls and materially changed which of the two looks safer to
  depend on right now. (diagnosed, both instances)
- **A permissive-sounding project can have a per-file/per-model license
  split that only shows up by reading the actual LICENSE, not a repo
  badge or a blog summary.** Moonshine's English models are MIT; its
  other-language models are not -- a summary based on the top-line
  license badge alone would have missed this. (diagnosed, this instance)

## Recommendation for JP

**Keep faster-whisper as the shipped default; start a real, small,
hands-on `onnx-asr`/Parakeet TDT prototype next time there's a live
session with hardware available -- this is now a "when," not an "if,"
call, but still not urgent enough to interrupt other UAT work for.**

Reasoning, in order of what should actually move this decision:

1. **The core problem (allocator leak, short-phrase hallucination) is
   unambiguously still there and unambiguously still unfixed upstream.**
   Nothing this pass found changes that. Continuing to invest *more*
   tuning effort into faster-whisper's own knobs (temperature, hotwords,
   `condition_on_previous_text`) past what's already shipped is low
   expected value -- those already-shipped mitigations are the cheap
   wins; there isn't a cheaper one left to try.
2. **`onnx-asr` looks like a safe, well-maintained dependency to build a
   real side-by-side prototype against** -- active releases, real CI/
   typing/coverage hygiene, explicit Windows+CUDA+DirectML support
   matching this project's actual dev machine, and a WER/speed edge that
   (with the caveat it's still someone else's numbers) is large enough
   to be worth confirming rather than dismissing.
3. **Don't reach for `openWakeWord` right now.** The wake-word-classifier
   architecture is still conceptually sound (sidesteps hallucination by
   construction rather than tuning around it), but this specific
   library's maintenance has visibly slowed and it has an open, unfixed,
   year-old bug on Windows -- the exact platform this needs to work on.
   If the wake-word-classifier idea gets revisited, check for a
   healthier-maintained alternative library first rather than assuming
   openWakeWord is still the obvious pick.
4. **Note Moonshine for the next ROADMAP refresh, don't act on it yet.**
   It's the most actively developed of everything checked here and its
   English-model license is genuinely clean (MIT), but its
   hallucination-control claim is currently unverified marketing-
   adjacent language -- same category of claim this project already
   learned to distrust once (FunASR). Worth a real look if/when onnx-asr
   doesn't pan out, or as a second candidate in the same prototype pass.
5. **What would actually justify spending real engineering time now
   instead of "next time there's hardware available":** if the allocator
   leak recurs badly enough in an actual live session to lose STT
   mid-session again (the 2026-08-02 "stayed dead the rest of the
   session" incident in KNOWN-ISSUES.md), that's the trigger to escalate
   this from "worth prototyping eventually" to "do it now."
