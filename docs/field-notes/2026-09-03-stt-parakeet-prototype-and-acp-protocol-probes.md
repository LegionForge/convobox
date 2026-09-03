---
title: Two roadmap R&D passes -- a real Parakeet TDT prototype (mixed result, not a clear win) and live ACP protocol probes answering both open scoping questions
status: validated-live
date: 2026-09-03
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 8a5233d; onnx-asr 0.12.0 (nemo-parakeet-tdt-0.6b-v3, CoreML EP); opencode 1.18.20 (`opencode acp`); faster-whisper base/cpu/default (ConvoBox's current shipped config); macOS, Apple Silicon
evidence:
  - Real onnx_asr.recognize() calls against 8 real Kokoro-synthesized WAV files (known ground-truth text), compared against the same files run through ConvoBox's own create_stt_engine(faster-whisper)
  - Real raw JSON-RPC probes against a spawned `opencode acp` subprocess (no ConvoBox code involved), covering initialize/session.new/session.prompt/session.set_mode/session.request_permission
  - ACP spec fetched directly (agentclientprotocol.com) for the full JSON-RPC method list
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (asked for R&D-only work on roadmap items, no implementation)
    - Claude Code (Anthropic claude-sonnet-5) -- ran both prototypes, wrote this note
  org: https://legionforge.org
  created: 2026-09-03T11:40:00Z
  revised: 2026-09-03T11:40:00Z
license: CC BY 4.0 (intent; repo code MIT)
---

# Two roadmap R&D passes: Parakeet TDT prototype, ACP protocol probes

JP asked for R&D on roadmap items, explicitly not implementation. Picked
the two candidates with the clearest "prove it's real" mandate already
written into `docs/ROADMAP.md`: the `onnx-asr`/Parakeet TDT STT
candidate (flagged "when, not if... still not started" as of the last
2026-08-07 check-in), and the two open ACP scoping questions (steering
support, permission-mode mapping). Nothing here touched shipped code --
a scratch venv, throwaway probe scripts, and this note.

## Part 1: Parakeet TDT 0.6B v3 prototype

### Method

No real human-voice fixture with known ground truth exists in this repo
(the closest thing, `uat-acoustic-calibration/`, is self-barge-in
trial audio, not transcription-accuracy fixtures). Followed this
project's own established pattern instead (`scripts/
roundtrip_smoketest.py`'s "synthesize via TTS, verify STT recovers it"
methodology): generated 8 WAV files via ConvoBox's own Kokoro engine
(`af_sarah`), covering both ordinary sentences and the specific
short/low-signal phrase category that originally motivated this
candidate (`resume_word`, safeword, kill phrase, approval phrase) --
same known-text-in, transcribed-text-out comparison, just on
synthesized rather than human audio. Real limitation, stated plainly:
this cannot test noise robustness or real speaker variation, only
raw recognition accuracy + latency on clean audio with a known answer.

Ran the identical 8 files through both engines:
- ConvoBox's actual shipped config path (`create_stt_engine`,
  faster-whisper base/cpu/default) -- zero mocking, the real class.
- `onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")` (auto-downloads
  from Hugging Face, CoreML execution provider available on this Mac).

### Results

| file | text | whisper (s) | whisper output | parakeet (s) | parakeet output |
|---|---|---|---|---|---|
| long_ordinary | "The quick brown fox jumps over the lazy dog while the sun sets slowly behind the mountains." | 0.724 | correct (added a comma) | 2.437* | correct (added a comma) |
| medium_technical | "Please refactor the authentication module to use dependency injection instead of global state." | 0.720 | correct | 0.598 | correct |
| medium_command | "create a file named hello dot txt containing the word hi" | 0.619 | **"hi" -> "high"** | 0.605 | correct |
| short_approval | "juliette papa charlie" | 0.529 | correct | 0.321 | correct |
| short_kill_phrase | "eject eject eject" | 0.579 | correct | 0.256 | correct |
| short_safeword | "stop stop stop" | 0.515 | correct | 0.226 | correct |
| short_yes_no | "no" | 0.508 | correct | 0.211 | correct |
| **short_resume_word** | **"Athena"** | 0.474 | **"Assino!" (wrong)** | 0.231 | **"Asina." (also wrong)** |

*Parakeet's first call includes CoreML JIT/graph-partition warmup
(visible in the raw log as `coreml_execution_provider.cc` GetCapability
messages) -- treat the first-file timing as inflated, the rest as
steady-state.

### What this actually shows

**Latency: a real, measurable win, but not the marketing number.**
Once warmed up, Parakeet is consistently ~2-3x faster than
faster-whisper on these short/medium clips (0.2-0.6s vs 0.5-0.7s) on
this single-CPU-utterance workload. The Open ASR Leaderboard's
"~3,300x realtime throughput" figure the roadmap cited is a batch/GPU
aggregate number, not a single-utterance CPU latency claim -- worth
correcting in the roadmap text, the two numbers aren't comparable and
citing them together overstates this workload's actual speedup.

**One clear, real accuracy win:** "hi" vs "high" -- faster-whisper
mis-transcribed a real word in the command-style phrase; Parakeet got
it right. Small sample, but a genuine difference on identical audio.

**The specific claim that motivated this candidate does NOT hold up
here.** The roadmap's case for Parakeet leaned on "trained on 36,000+
hours of noisy/non-speech audio... rarely hallucinates on silence/
low-signal input," directly aimed at the "Athena" resume-word
hallucination this project already hit and documented
(2026-08-06 field note). On this test, Parakeet did NOT recover the
correct word -- it produced a DIFFERENT wrong transcription ("Asina."
vs. Whisper's "Assino!"), not a fix. Neither engine handles this short,
low-signal, single-word case correctly out of the box. This is a real,
if narrow, negative finding against the roadmap's stated rationale, not
just "unverified" as the roadmap currently frames it.

**A real functional loss, not previously flagged:** `onnx-asr`'s public
`RecognizeOptions` (confirmed by reading `onnx_asr/adapters.py`
directly, not docs) has no hotwords/prompt-biasing parameter at all --
only `language`, `target_language`, `pnc` (the latter two Canary-only).
ConvoBox's *existing*, already-shipped mitigation for exactly this
failure mode is `stt.hotwords` (faster-whisper's prompt-biasing param).
Switching engines would mean losing a working mitigation for a problem
the new engine doesn't independently solve either -- this is a real
cost the roadmap's Parakeet section doesn't currently mention at all.

### Recommendation

Don't switch. The roadmap's own "when, not if" framing should soften to
"maybe, and not for the reason originally given" -- the accuracy case
specifically built around the hallucination problem doesn't hold up
under a real test, and there's a real regression (no hotwords
equivalent) the roadmap didn't previously account for. The latency win
is real but this project isn't latency-bound on STT today (0.5-0.7s is
not the bottleneck in a voice turn that also does LLM inference + TTS).
If STT engine work continues, the next real question is whether
Parakeet's accuracy holds up on genuinely noisy/reverberant REAL human
audio (its actual claimed strength, untested here) -- that would need
real mic captures, not synthesized audio, to settle either way.

## Part 2: ACP protocol probes -- both open scoping questions answered

`docs/ROADMAP.md`'s ACP section had two explicit "needs a live probe
before committing" unknowns. Answered both directly against a real
`opencode acp` subprocess (raw JSON-RPC over stdio, no ConvoBox
adapter code involved -- same "spawn it, hand-roll the protocol,
watch the wire" methodology already used for the 2026-09-02 Codex
approve-mode investigation).

### Question 1: does ACP support steering an in-flight turn, or only cancel-then-reprompt?

**Answered from the spec itself** (agentclientprotocol.com), not just
opencode's implementation of it: **no steering mechanism exists in ACP
at all.** The full method list has exactly one way to interrupt a
running prompt -- `session/cancel`, a notification that tells the agent
to "stop all language model requests as soon as possible" and "abort
all tool call invocations in progress." There is no equivalent to
Codex's real `turn/steer` (which redirects an in-flight turn with new
input, confirmed live in `codex.py`'s own module docstring).

**This is a real capability regression, not a detail.** Any backend
migrated to ACP loses live mid-turn steering entirely -- ConvoBox would
have to fall back to cancel-and-restart for every "actually, do X
instead" mid-turn correction, on every ACP-speaking backend, not just
the ones that already lack it (OpenCode's own native adapter has never
had steering either, so no regression there specifically -- but Codex
would go backward if it were ever migrated to ACP, and this closes the
door on any future ACP backend ever getting steering without an ACP
spec extension).

### Question 2: how should `backend.permission_mode` map onto ACP's permission primitive?

**Two live findings, one reassuring, one that revises the roadmap's
assumption:**

1. **`session/request_permission` did NOT fire at all under default
   config.** Spawned `opencode acp`, ran `session/new` with no special
   config, sent a prompt that required a real file write -- the write
   happened immediately, zero `session/request_permission` calls, zero
   errors. The roadmap's framing ("a live-answerable approval channel
   exists... closing the 'does ACP's permission primitive cover our
   voice-gated approval channel' unknown") is accurate for Kilo's own
   documented internal wiring but was NOT verified live for OpenCode's
   own `opencode acp` until now -- and the default posture there is
   full trust, not "ask by default."

2. **`session/set_mode` is real and does gate writes -- with a caveat
   worth flagging.** `session/set_mode(sessionId, "plan")` is accepted
   (confirmed against the live error message for an invalid mode ID,
   which lists what's NOT valid rather than accepting anything). With
   mode set to `"plan"`, the SAME file-write prompt that succeeded
   under default config was correctly refused -- the agent's own
   response explained it's in plan mode and can't make file edits, no
   `session/request_permission` fired (nothing to gate; the model chose
   not to attempt the write), and the file was confirmed absent from
   disk afterward. This matches Codex's `plan` mode behavior in
   outcome.
   **Open question this doesn't settle:** this is one successful
   compliance test, not proof the restriction is *enforced* the way
   Codex's `sandbox_mode=read-only` is (an OS/tool-layer block that
   holds even if the model tries to defy the instruction). Whether
   OpenCode's "plan" mode is a hard sandbox or a prompt-level
   instruction the model is merely complying with is genuinely
   unanswered by this test -- would need an adversarial probe (a prompt
   specifically trying to get the model to write anyway) to settle,
   which is future work, not done here.

**Practical mapping this suggests, if an ACP adapter gets built:**
`permission_mode` doesn't need protocol-level per-mode config at all
for the `approve` case -- `session/request_permission` is a
CLIENT-implemented method (agent calls client), so ConvoBox's own ACP
client code would simply choose whether to answer it interactively
(voice-gated, matching today's architecture) or auto-answer, entirely
under ConvoBox's own control regardless of what the agent-side mode is
set to. `plan` maps cleanly to `session/set_mode("plan")` (verified
above). `permissive` needs no protocol action at all -- default
`opencode acp` behavior already lets writes through unprompted, so
`permissive` is just "the mode that never bothers changing session
mode or intercepting the (never-fired) permission callback." This is a
cleaner mapping than Codex's -- which this same week turned out to
have an upstream approval_policy regression that makes its own
`approve` mode currently unusable (see `docs/KNOWN-ISSUES.md`) --
since the decision authority lives entirely on ConvoBox's own side of
the wire, not in an agent-side config value ConvoBox has to trust
matches what it thinks it configured.

## Not done here (explicitly out of scope, per JP's "R&D not
implementing" instruction)

- No adapter code written. No `src/convobox/adapters/acp.py`.
- No live probe of Kilo's own `kilo acp` (not installed on this
  machine) -- only OpenCode's implementation was tested live; Kilo's
  permission-primitive claims from the roadmap remain source-read, not
  independently verified.
- The "does OpenCode's plan mode hard-enforce or just prompt-comply"
  question above -- flagged, not resolved.
- No real-human-audio test of Parakeet's noise-robustness claim -- the
  actual claimed strength remains untested against a real environment.
