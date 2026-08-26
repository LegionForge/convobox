---
title: Performance audit on 2014-era Linux hardware finds almost all observed latency is backend/LLM thinking time, not ConvoBox's own code -- one real, safety-neutral TTS-streaming fix found; two speed levers measured and explicitly rejected for reliability reasons
status: diagnosed (real measurements on real hardware; the one recommended fix not yet implemented or live-verified)
date: 2026-08-25
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0); tts.engine kokoro, voice af_sarah; stt faster-whisper base, compute_type default (resolves to float32 on this CPU, not int8 -- see Mechanism); openSUSE Tumbleweed 20260822 (kernel 7.1.8-1-default)
hardware: Clevo P17SM-A barebone (Sager-branded, BIOS dated 2014-03-27), Intel Core i7-4810MQ (Haswell, 4C/8T) -- CPU-only for every measurement here, GPU (Intel HD 4600 + NVIDIA Quadro K3000M) unused by ConvoBox. Full spec in the companion AEC volume-sweep field note.
evidence:
  - Real audit against this repo's actual code and this session's own live logs (/tmp/convobox-eject-test.log and the same-day human-speech session), run by a fresh Opus-model subagent with explicit "reliability over performance" instructions
  - Real synthesis/transcription timing measurements taken live on this machine during the audit (kokoro chunk counts and time-to-first-audio at several text lengths; faster-whisper timing with auto-detect vs. pinned language, both on synthetic clips and 6 real mic recordings from this session)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the audit specifically, explicit that reliability matters more than speed and that nothing safety-critical should be touched for a performance win)
    - Claude Code (Anthropic claude-sonnet-5), coordinating -- spawned and briefed the audit agent, wrote this note
    - Claude Opus 5 (Anthropic), audit agent -- ran the actual investigation, took the real measurements, wrote the findings this note is built from
  org: https://legionforge.org
  created: 2026-08-25T12:43:00-05:00
  revised: 2026-08-25T12:43:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Performance audit: mostly backend latency, one real TTS fix, two explicit non-fixes

**Context for outsiders.** Earlier the same day, a real live human-speech
session on this 2014-era laptop (see the companion field notes) showed
noticeable end-to-end delay -- STT processing time, and stretches where
the log showed the backend "still working" for anywhere from 6 to over
100 seconds during a real turn. JP asked directly: is there a genuine
performance problem in ConvoBox's own code worth fixing, or is this just
expected for old, CPU-only hardware -- and, explicitly, **reliability
matters more than speed here**; nothing safety-critical (safeword/hard-
stop/kill_phrase, barge-in, AEC, the overlap-gate/echo-filter) should be
touched for a speed win.

## Problem

Separate ConvoBox's own pipeline latency (STT inference, TTS synthesis,
VAD, AEC -- things this project's code could actually change) from
backend/LLM latency (the `claude`/`opencode`/`codex` subprocess's own
reasoning time -- structurally outside ConvoBox's control). Then: is
there a real, safe optimization opportunity in the ConvoBox-side portion,
or is the honest answer "nothing meaningfully wrong, this is just old
hardware doing expensive CPU-only inference"?

## Method

A fresh Opus-model subagent, briefed explicitly with the reliability-
first constraint and told this was a research/audit task -- no code
changes. It read the real pipeline code (`src/convobox/stt/`,
`src/convobox/tts/`, `src/convobox/audio/aec.py`/`capture.py`/
`playback.py`, `src/convobox/vad/segmenter.py`), cross-checked
`docs/KNOWN-ISSUES.md`/`docs/ROADMAP.md`/`docs/STATUS.md` for
already-tracked performance topics, and took real timing measurements on
this actual machine -- both synthetic clips and real recordings pulled
from this session's own live logs.

## Evidence

**1. The `[THINKING]` heartbeat is confirmed backend-only.**
`WorkingIndicator.observe()` (`scripts/run_convobox.py:799-857`) only
counts `adapter.is_busy() and not player.is_playing()`. This session's
own log proves what that's measuring -- a 102s stretch is bracketed by:

```
claude_code _read_loop: readline() still pending after 30.5s (returncode=None, busy=True)
...
claude_code _read_loop: readline() finally returned after 125.1s total
```

The `claude` subprocess emitted nothing on stdout for 125 seconds.
ConvoBox itself stayed fully responsive throughout (segmented,
transcribed, and correctly barge-in-stopped audio mid-stall). Nothing to
fix -- the heartbeat is reporting real backend latency honestly. Also
confirmed: `is_playing()` goes true at playback-thread start, so TTS
synthesis time is *not* folded into this heartbeat either -- it's
tracked separately (finding 2).

**2. kokoro TTS does not stream by sentence -- real, measured, safe to fix.**
`KokoroTTSEngine.synthesize_stream` (`src/convobox/tts/kokoro.py:65`)
delegates to kokoro-onnx's `create_stream()`, which batches by its
~510-phoneme limit, not by sentence -- anything shorter comes back as one
chunk, after full synthesis completes:

| response length | chunks yielded | time to first audio |
|---|---|---|
| 20 chars | 1 | 1.4s |
| 250 chars | 1 | 10.5s |
| 400 chars | 1 | 18.2s |

Pre-splitting the same 400-char text by sentence: **first audio at 3.4s
instead of 18.2s (5.4x)**. Live corroboration from this session's own
log: a 127-character response at `07:57:08.309`, playback didn't end
until `07:57:20.496` -- consistent with several seconds of silent
synthesis before speech.

This directly falsifies an existing, open expectation in
`docs/UAT-checklist.md:485` **[T2]** ("confirm it's ~one sentence, not
the whole response") -- true for Piper (which streams, per the ~11x fix
recorded in `docs/STATUS.md:645`) but **not for kokoro**, the engine that
replaced Piper as default on 2026-07-24. The Piper streaming fix was
never carried across to the engine that superseded it.

**Why this is the one worth doing**: output path only. Touches nothing in
safeword/hard-stop/kill_phrase, barge-in, AEC, or the overlap gate. It's
also a *reliability* improvement, not just speed -- smaller synthesis
chunks structurally reduce exposure to the already-documented kokoro
~510-phoneme silent-hang (`docs/KNOWN-ISSUES.md:1349`).

**Real caveat, measured, not assumed**: per-sentence synthesis RTF is
~1.04 vs. ~0.76 for whole-text batching on this CPU -- synthesizing
strictly sentence-by-sentence can't stay ahead of playback and would risk
gaps between sentences. **The safe shape is first-sentence-alone, then
the remainder as one batch**: first audio at ~1.5-3.5s, with the tail
synthesizing at full batch efficiency while the first sentence plays.

**Zero-code option to try first**: `interaction.tier_responses: true`
(schema default `false`; unset in this session's `convobox.yaml`) already
exists and speaks only the first paragraph -- already built, already
tested elsewhere in this project. Worth sizing the real win with this
before writing any new code.

**3. STT has a ~1.6s floor; ~40% of it is avoidable but shouldn't be avoided.**
Across 49 real utterances from this session's two live logs: median STT
time 1.6-1.8s, almost independent of utterance length (0.93s of audio ->
1.62s to transcribe; 14.6s of audio -> 2.14s) -- Whisper's encoder always
runs a 30s-padded window regardless of real content length, so this floor
is architectural.

With `language: null` (the default, and this session's config),
faster-whisper runs a full extra `detect_language()` encoder pass
(`.venv/.../faster_whisper/transcribe.py:1819`) before the real decode
encodes again:

| | auto-detect | pinned `en` |
|---|---|---|
| clean synthetic clips, 1-5s | 1.70-2.00s | 0.93-1.21s |
| 6 real mic recordings (this session) | median 4.08s | median 1.09s (-73%) |

**Explicitly not recommended.** This would contradict an existing,
live-validated decision: `LanguageTracker`'s own docstring,
`TESTING.md:440`, and `docs/STATUS.md:594` all record that pinning
language previously made real Russian speech decode as confident-sounding
English nonsense; `convobox.example.yaml:122` calls auto-detect
"recommended" for exactly this reason. That decision stands unchanged.
What's new here is simply that nobody had measured its price before --
roughly 0.6-0.8s added to every single utterance on this hardware. A fair
thing for an English-only user to weigh for their own config
(`stt.language: en`, a per-user opt-in), not a reason to change the
project default.

**4. VAD and AEC confirmed genuinely cheap -- no action, matches an existing decision.**
Measured: ~0.24ms per 32ms VAD window, ~0.46ms per 32ms AEC chunk --
combined, under 3% of one core. This confirms with real numbers the call
already made in `docs/STATUS.md:690-694` ("not worth speculatively
optimizing now"). No reason to revisit, and given these files sit right
next to the barge-in/overlap-gate safety logic, no reason to touch them
at all.

**5. STT temperature: measured, and explicitly a safety regression, not a speed win.**
Pinning `stt.temperature: 0.0` does cut worst-case STT time on a
pathological noisy clip (17.8s -> 6.9s) -- but the mechanism why is the
problem: **the temperature-fallback ladder is what rejects runaway
hallucinated repetitions.** With temperature pinned, garbage transcripts
that the ladder would otherwise catch got through instead -- observed
live during the audit: `'4-5-5-5-5-5-5...'` and `'Thank you so much for
watching this video'` (a classic Whisper hallucination on noise/silence)
both passed through where auto-fallback correctly returned empty.
**This is a safety regression wearing a performance costume** -- given
this session's own theme (safeword/kill_phrase reliability), pinning
temperature is the opposite of what this project needs.

**6. Small doc bug found, unrelated to performance.** `src/convobox/config.py:107`
and `convobox.example.yaml:112` both claim `compute_type: default` means
"int8 on cpu." Verified live: it doesn't -- ctranslate2 says so on every
startup ("The compute type inferred from the saved model is float16, but
the target device...do not support efficient float16...converted to use
the float32 compute type instead"), confirmed `resolved=float32`. Small
practical impact (int8 vs. float32 is only marginally faster on this
Haswell CPU), but the comment tells operators the wrong thing.

## Mechanism

No single root cause -- five independent, unrelated findings, which is
itself informative: there is no one big bottleneck hiding in ConvoBox's
own code. The pipeline's CPU-bound stages (VAD, AEC) are already cheap;
the one real inefficiency (kokoro's batching granularity) is a narrow,
specific gap left over from a TTS engine swap, not a general design flaw;
and the two "obvious" speed levers (pinning STT language, pinning STT
temperature) both turn out to be reliability mechanisms in disguise once
actually measured against what they protect against.

## What transfers

- **Act on this one**: try `interaction.tier_responses: true` first
  (config-only, zero risk) to size kokoro's real streaming win; if
  meaningful, implement first-sentence-then-remainder chunking in
  `kokoro.py` (measured-safe shape, not naive per-sentence synthesis).
  Fix the `compute_type` doc comment while in that area. (diagnosed,
  measured; not yet implemented or live-verified)
- **Do not act on these, for reliability reasons, now with real numbers
  attached**: pinning `stt.language` (saves ~0.6-0.8s/utterance;
  contradicts an existing live-validated non-English-safety decision) and
  pinning `stt.temperature` (saves time on pathological clips specifically
  *by* letting hallucinated garbage through the fallback ladder that
  exists to catch it). (validated-live, this session's measurements)
- **No action needed on VAD/AEC/capture** -- confirms, with real numbers,
  a call this project already made in `docs/STATUS.md`. (validated-live)
- **The backend/LLM thinking time this session's live logs showed (up to
  100+ seconds) is not a ConvoBox performance problem** -- it's the
  coding-agent subprocess's own reasoning time, entirely outside this
  project's control, and ConvoBox stayed correctly responsive (VAD,
  transcription, barge-in) throughout every stall observed.
  (validated-live)

## Not done here

- `tier_responses: true` was not actually tried live this session --
  the audit found it as an existing, relevant knob, not as something it
  ran.
- First-sentence-then-remainder kokoro chunking was not implemented --
  diagnosed and measured as the safe shape, not built.
- The `compute_type` documentation bug was not fixed.
- No upstream report or `docs/KNOWN-ISSUES.md` entry added for the
  kokoro non-streaming gap.
- STT auto-detect's ~0.6-0.8s/utterance cost was measured but not added
  to any user-facing config documentation as the "missing cost side" of
  the existing auto-detect-vs-pin decision.
