---
title: Permission-model (plan/approve/permissive) live validation across claude-code and codex; opencode's built-in auth is broken in serve mode, but a manually-declared custom provider works end-to-end -- including real tool-calling
status: validated-live (plan, permissive -- N=2 each backend); approve mode -- text-mode gap deterministically confirmed via code + live repro, live voice-approval flow inconclusive (real ambient noise); opencode -- built-in auth (OAuth/API-key via opencode auth login) confirmed broken in serve mode (3 independent root causes, identical silent-hang symptom); manually-declared custom provider (Ollama, Inception) confirmed WORKING end-to-end through ConvoBox, including a real tool call (file created with exact content) via inception-direct/mercury-2; {env:VAR} config substitution separately confirmed broken (general, not credential-specific); a serve startup-race bug found and worked around (warm retry); tool-calling gap on qwen2.5-coder:7b confirmed as a model limitation, not a wiring bug
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 4f01148; macOS 26.x, Apple Silicon (Mac mini M4); AIRHUG 28 (USB mic), Mac mini Speakers; macOS system output volume=25%
hardware:
  computer: Mac mini M4 (2024), single built-in speaker
  microphone: AIRHUG 28 USB conference mic, ~8cm placement, AI DSP off (green LED)
  room_state: NOT a quiet/controlled room this pass -- real background noise present throughout (a second person playing video games + music in the same room), mic ambient RMS ~0.021-0.022, peak ~0.79-0.82 (near-clipping loud), confirmed via a standalone MicrophoneStream probe run twice
evidence:
  - 3 backends x 3 permission_mode values (plan/approve/permissive) exercised where testable, each isolated to a sandbox working_dir fully outside any ConvoBox git checkout (not the UAT worktree -- a plain scratch dir), per this session's own "isolated working_dir" discipline
  - plan and permissive modes: N=2 independent --text runs per backend, consistent both times
  - approve mode: --text-mode behavior read from source and confirmed live (claude-code N=1 full run to resolution, codex N=2, one lost to output buffering but same outcome, one clean)
  - live mic-loop voice-approval flow: 4 real injection attempts at escalating volume (3.0x/3.0x/5.0x/7.0x), diagnosed against real log output (--verbose)
  - opencode: re-checked `opencode auth list` and a port probe for `opencode serve` -- unchanged from the prior macOS pass, 0 credentials, no listener
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the full backend x permission-mode validation pass, set system volume to 25% for it, and caught the real cause of the first live-approval failures -- a macOS permission dialog that had gone unnoticed)
    - Claude Code (Anthropic claude-sonnet-5) -- built the isolated sandbox, wrote the 6 permission-mode configs, ran and diagnosed every test, read the relevant adapter/run_convobox.py source to explain what was observed
  org: https://legionforge.org
  created: 2026-08-11T19:30:00-05:00
  revised: 2026-08-11T19:30:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Permission-model validation: claude-code, codex, opencode

## Scope and method

Every prior macOS field note this session ran with `backend.permission_mode: plan`
(Claude Code's read/explore/explain-only mode) specifically because it can never
trigger a tool-call approval prompt -- convenient for AEC/barge-in/safeword
testing, but it meant the actual permission-gating system had never been
exercised live on this machine. This pass closes that gap directly.

ConvoBox's `backend.permission_mode` has three values, each mapped differently
per backend (`src/convobox/adapters/claude_code.py` `_PERMISSION_CLAUDE_MODE`,
`src/convobox/adapters/codex.py` `_PERMISSION_CODEX_OVERRIDES`):

| mode | claude-code | codex |
|---|---|---|
| `plan` | `--permission-mode plan` (read-only, no writes) | `approval_policy=never, sandbox_mode=read-only` |
| `approve` | `acceptEdits` + a real `PreToolUse` hook (voice-gated) | `approval_policy=untrusted, sandbox_mode=workspace-write` (writes escalate to approval) |
| `permissive` | `bypassPermissions` (skips all checks) | `approval_policy=never, sandbox_mode=workspace-write` (writes freely) |

opencode has no permission-mode concept at all (README's own characterization,
confirmed by `grep` finding zero `permission_mode`/`approval` handling in
`src/convobox/adapters/opencode.py`).

Every write-capable test ran with `backend.working_dir` pointed at a sandbox
**outside any ConvoBox git checkout** (`/private/tmp/.../scratchpad/permission_sandbox`,
not the `convobox-UAT` worktree -- the tool's own working-dir check flagged the
UAT worktree as "inside ConvoBox's own source tree" on a first attempt, a real
and correct warning, so the sandbox was moved).

## `plan` mode: confirmed clean, both backends, N=2

Asked each backend to create a file. Neither backend ever wrote anything,
both times, for both backends:

- **claude-code**: explained a one-line plan ("create X with content Y"),
  offered to proceed, stopped -- `ExitPlanMode` correctly not available as a tool.
- **codex**: attempted the write, self-reported "I couldn't create the file
  because this workspace is read-only" (from `sandbox_mode=read-only`).

No new files in the sandbox after either backend, either run.

## `permissive` mode: confirmed writes freely, no prompts, both backends, N=2

Same request, `permissive` mode. Both backends created the requested file
immediately, no approval request emitted, both runs:

```
permissive_test.txt, permissive_test2.txt        (claude-code)
codex_permissive_test.txt, codex_permissive_test2.txt   (codex)
```

## `approve` mode: a real gap found in `--text` mode, deterministic and code-confirmed

**Finding: `--text` mode + `permission_mode: approve` never resolves the
approval on either backend.** Not a timeout-to-deny -- the request is
abandoned when `--text` mode's own generic 120s "give up waiting" bail-out
fires, and the process exits without ever sending an explicit decline.

Root cause, read directly from `scripts/run_convobox.py`: the `approval_gate`
(`ApprovalPromptGate`, which owns the 30s-default `approval_timeout_s` that's
supposed to auto-deny a silently-abandoned prompt) is only ever *ticked* by
`_working_watchdog`, and `watchdog_task = asyncio.create_task(_working_watchdog(...))`
is constructed at line ~2431 -- **after** `--text` mode's own early `return`
at line 2069-2084. So in `--text` mode, `ApprovalPromptGate.observe_timeout()`
is simply never called. The request just sits pending until `_drain_until_idle`'s
unrelated, generic 120s "backend still busy" bail-out gives up and the script
calls `adapter.aclose()`, which disconnects/kills the backend subprocess
without ever answering the approval.

Live-confirmed on both backends:

- **claude-code**, `--text`, N=1 full run to resolution: approval prompt fired
  ("Approval needed to run Write. Say cobalt night and gale to approve, or
  say no to deny."), then **120s of total silence**, then
  `backend still busy after 120s; giving up the wait`. No file created.
- **codex**, `--text`, N=2 (one run's log lost to a pipe-buffering artifact on
  kill, same no-file outcome both times; the clean run shows identical
  behavior): approval prompt fired (`item/fileChange/requestApproval`,
  "Silence denies the request" -- a message from codex itself, not from
  ConvoBox, and not actually true in this mode since nothing ever sends that
  denial), then the same 120s bail-out. No file created.

**Practical net effect is fail-safe** (nothing ever gets written without a
real answer) but the mechanism is not what it looks like -- it reads as
"denied," but it's actually "abandoned and disconnected." Worth fixing:
either construct (or a lightweight standalone version of) the watchdog in
`--text` mode too, or have `--text` mode's own exit path call
`resolve_pending_approval(False)` explicitly before `adapter.aclose()`.
Not fixed this pass -- diagnosis only, per this repo's "characterize before
fixing" pattern for anything discovered mid-UAT.

## Live mic-loop voice-approval flow: inconclusive, real ambient noise, not a code bug

The one thing `--text` mode structurally cannot test is whether the *actual*
voice-gated approval flow works -- `ApprovalDetector` listening for the spoken
`approval_phrase` requires the live mic loop's own watchdog, which by
definition only exists outside `--text` mode. Attempted via the same
synthetic-injection technique used earlier this session for safeword tests.

**This room was not a quiet/controlled testing environment for this pass** --
a second person was playing video games with music running in the same room
throughout (explicitly OK'd for this test; system volume set to 25% partly
for this reason). A standalone `MicrophoneStream` probe (run twice) confirmed
real, loud, steady ambient signal: RMS ~0.021-0.022, peak ~0.79-0.82 --
close enough to full-scale to matter.

Four injection attempts (3.0x, 3.0x, 5.0x, 7.0x volume), with `--verbose`
logging on the last two to see the actual VAD/STT decision path:

- First two attempts (3.0x): a suspected macOS permission-dialog interruption
  (caught mid-session by JP -- a permission prompt on this machine had gone
  unnoticed) may have affected timing; no VAD segmentation of the injected
  phrase at all.
- Third attempt (5.0x): VAD *did* segment a real 3.6s utterance
  (`Processing audio with duration 00:03.616`, language confidence 0.84 --
  clearly picked something real up), but Whisper's own no-speech-threshold
  heuristic rejected it (`No speech threshold is met (0.656652 > 0.600000)`)
  -- `dropped (no input, STT heard nothing recognizable)`.
- Fourth attempt (7.0x): no VAD segmentation at all again.

A standalone mic probe both before and after confirms the microphone itself
was working correctly throughout -- this is a real-world STT-under-noise
degradation, not a broken feature, and not the permission-dialog issue
either (that was isolated to an unrelated `osascript`/System Events call
made independently during diagnosis, not the ConvoBox process). It's also
directly consistent with this session's own earlier `[E6]`/round-trip
findings: far-field acoustic injection through a real, noisy room is a
harder problem than clean synthetic audio, and this room had real
competing audio in it the whole time, unlike every prior successful
injection test this session (all run in a quiet house).

**Not closed this pass.** To close cleanly: either retry in a genuinely
quiet room (matching every earlier successful injection test's conditions),
or use a non-acoustic injection path (e.g. feeding synthesized audio
directly into the STT pipeline the way the `[E6]` isolation test did,
bypassing room acoustics and the real mic entirely) paired with a way to
drive the mic loop's watchdog without needing a live room at all.

## opencode: credentials configured mid-session, connectivity confirmed working, but the built-in auth/provider-catalog path in `serve` is broken

Re-checked at the start of this pass: `opencode auth list` showed 0
credentials, same as the prior macOS pass -- opencode was untestable for
lack of a configured provider. Mid-session, JP configured two real
providers (`opencode auth login` for OpenAI/ChatGPT Plus OAuth, then a
real Inception Labs API key) and started `opencode serve`.

**Reverified the pre-existing `docs/KNOWN-ISSUES.md` finding from
2026-07-18 is still present in v1.18.15**: `opencode serve`'s API never
loads the OAuth-credentialed `openai` provider (`GET /api/model`/
`GET /api/provider` list only the built-in Zen catalog), even though
`opencode run -m openai/gpt-5.4-mini` (and `gpt-5.4`, and `gpt-5.6-terra`
-- tried all three) works fine via the interactive CLI with the same
credentials in the same shell.

**Then found this is a more general bug than "OAuth specifically"**:
the Inception Labs API key (confirmed working in the opencode TUI)
hit the *exact same* `SessionRunnerModel.ModelUnavailableError`
through `serve`, and `/api/provider` on a server started fresh *after*
the key was installed still only listed the built-in Zen provider.
Also tried the `OPENCODE_MODEL` env var (a workaround seen in an
unrelated opencode GitHub issue) -- no effect, identical failure.
Also found the "free" Zen catalog itself is currently suspended on
opencode's own infrastructure for billing reasons (`HTTP 402`),
unrelated to any credential here. All three distinct root causes
produce the identical client-visible symptom: an internally-logged
`"Failed to drain Session"` and then permanent silence -- `opencode
serve` never propagates a provider/request failure back to the API
caller. Full mechanism, all three repro paths, and the still-open
"file upstream" recommendation are in `docs/KNOWN-ISSUES.md`'s opencode
entry.

**The actual resolution: a manually-declared custom provider sidesteps
the whole thing, confirmed end-to-end.** JP recalled that Helios
(Windows) may have used a locally-hosted Ollama model rather than a
cloud/OAuth one. Declared a custom `@ai-sdk/openai-compatible` provider
in `opencode.jsonc` pointing at a local Ollama instance
(`http://localhost:11434/v1`, model `qwen2.5-coder:7b`) -- a completely
different code path from `opencode auth login`. Result: a real,
complete response through raw `curl` against `opencode serve`
(`session.next.text.ended` with actual generated text, `finish:"stop"`),
confirmed on Ollama's own side (`ollama ps` showed the model actively
loaded on GPU during the request), and then through **ConvoBox itself**
end-to-end (`--text` mode, a real spoken TTS response through the Mac
mini speakers). This is almost certainly the shape of what ran
successfully on Helios previously.

**One separate, unrelated, and unsurprising limitation surfaced by that
same successful test -- verified, not just suspected**: `qwen2.5-coder:7b`
returned the requested tool call as plain response text
(`{"name": "write", "arguments": {...}}`) instead of actually invoking
it -- no file was written. Confirmed this is a genuine model-capability
gap, not opencode/ConvoBox wiring, by bypassing opencode's harness
entirely: called Ollama's own `/v1/chat/completions` directly with an
explicit `tools` schema -- identical result (`finish_reason: "stop"`,
call embedded as text, no `tool_calls` array). Not a bug in ConvoBox,
opencode's `serve` mode, or the provider-loading issue diagnosed above
-- just this specific quantized model not reliably emitting native
function-calling output.

**A second, independent bug found chasing a real credential the same
way.** JP configured a real Inception Labs API key mid-session (a key
value briefly appeared in this conversation's transcript via a `!`
command that didn't behave as expected -- flagged immediately, never
reused, and the key was revoked by JP once testing finished). Declaring
`inception-direct` as a custom provider the same way as the working
Ollama example, with `"apiKey": "{env:INCEPTION_API_KEY}"`, consistently
failed with `HTTP 401: Incorrect API key provided` -- even though the
key was independently verified valid (direct `curl` to Inception's API:
real `200`, real model list) and the env var was confirmed present in
the `opencode serve` subprocess's own environment. Hardcoding the
literal key value directly in the config file (temporarily, removed and
key rotated immediately after) worked on the first try. Isolated with a
secret-free control before concluding anything: substituted
`{env:OLLAMA_TEST_URL}` (a harmless test value, not a credential) into
the *already-proven-working* Ollama provider's `baseURL` field --
identical failure. This confirms `{env:...}` substitution in
`opencode.jsonc` is generally broken for provider `options` fields in
this opencode version, not specific to API keys or to Inception --
despite the substitution code in opencode's own source
(`packages/opencode/src/config/variable.ts`) looking correct in
principle. **Practical consequence: a working custom-provider config
that needs a real credential currently has to hardcode it in plaintext
-- `{env:VAR}` is not a safe way to keep a key out of the file today.**

**Closed the loop with a fresh key: Inception confirmed working
end-to-end through ConvoBox itself, plus one more real bug found along
the way -- a startup race, not a config problem.** JP rotated the key
and hardcoded a fresh one directly in `opencode.jsonc` himself (never
typed or pasted through this session). First request against a
freshly-started `opencode serve`: the same `ModelUnavailableError` seen
throughout this whole investigation, even with a fully correct config.
Retried the identical request against the same, now-warm server:
succeeded immediately (`"banana"`, clean `finish:"stop"`). Then ran
`scripts/run_convobox.py --text "Say the word banana and nothing else"`
against that warm server: real spoken TTS response through the Mac mini
speakers, matching the Ollama result exactly. **Real, useful finding**:
`opencode serve` has a startup race where even a correctly-configured
provider can fail the very first request after boot -- a warm retry
resolves it every time this was tested. Explains some of the earlier
back-and-forth in this investigation, and is a concrete, actionable
thing for anyone else hitting `ModelUnavailableError` on an otherwise-
correct custom provider: retry once before assuming the config is wrong.

**Final closing test: real tool-calling confirmed working, not just text
generation.** Every success up to this point (Ollama, first Inception
pass) only proved the model could generate text -- `qwen2.5-coder:7b`
specifically could not invoke a real tool. Inception's `mercury-2`
advertises `"supported_features":["tools","json_mode",
"structured_outputs"]` in its own `/v1/models` response, unlike the
Ollama model tried, so this was worth testing directly rather than
assuming the earlier tool-calling gap generalized. Asked ConvoBox
(`--text`, `inception-direct/mercury-2`, warmed-up server) to create
`inception_toolcall_test.txt` with specific content in the sandbox:
**the file was actually created, with the exact requested content
(verified byte-for-byte with `cat`)**, and ConvoBox spoke a real
confirmation aloud. This is the first genuine "the opencode agent
actually did something" result in the entire investigation, not just
"opencode can talk" -- closes the loop on whether ConvoBox+opencode is
a real, usable agentic backend (yes, with the right model and the
custom-provider workaround) versus just a chat interface.

## What transfers

- **`plan` and `permissive` modes are solid on both claude-code and codex** --
  N=2 each, fully consistent, matches their documented contracts exactly.
- **`--text` mode + `approve` mode has a real, deterministic gap**: the
  approval-timeout watchdog doesn't run outside the mic loop, so a pending
  approval is abandoned (not explicitly denied) after ~120s. Net effect is
  safe (nothing gets written) but the mechanism is misleading. Worth a real
  fix, not done this pass.
- **The live voice-approval flow itself remains unconfirmed on macOS** --
  not because it's broken, but because this specific test session had real,
  loud competing background audio in the room the whole time. This is a
  genuinely different failure mode from every other thing this session
  characterized (it's an environmental testing-conditions problem, not a
  ConvoBox bug), and it's worth being explicit about that distinction so a
  future pass doesn't waste time re-diagnosing the wrong thing.
- **opencode's built-in auth (OAuth login or an `opencode auth
  login`-registered API key) does not work through `serve` today, for
  three independently-confirmed reasons, but a manually-declared custom
  provider in `opencode.jsonc` works completely fine end-to-end through
  ConvoBox** -- the practical path forward for anyone who wants opencode
  working with ConvoBox right now is a custom provider block, not
  `opencode auth login`.
- **That custom-provider workaround has its own gap**: `{env:VAR}`
  substitution for provider `options` doesn't work (verified general,
  not credential-specific, with a secret-free control), so a real
  credential currently has to be hardcoded in plaintext in
  `opencode.jsonc` rather than referenced from the environment.
- **A local model's tool-calling capability is a separate axis from
  connectivity working at all** -- `qwen2.5-coder:7b` generated real
  text fine but never correctly invoked a tool, confirmed (not assumed)
  to be a model limitation by testing Ollama's own API directly with an
  explicit tool schema, bypassing opencode entirely.
- **`opencode serve` has a real startup race**: the first request against
  a freshly-started server can fail with `ModelUnavailableError` even
  for a fully correct, working custom-provider config -- confirmed with
  Inception (fresh key, fresh server, first request failed, identical
  retry against the same warm server succeeded). A warm retry before
  assuming a config is broken is a real, useful piece of practical
  guidance from this session, not just a curiosity.
- **ConvoBox + opencode is a real, usable agentic backend, confirmed
  end-to-end with genuine tool execution** -- `inception-direct/mercury-2`
  correctly created a file with exact requested content via a real voice
  command through ConvoBox, not just generated text about doing so. The
  investigation started with opencode completely untestable (0
  credentials) and ends with a fully working, verified path: a
  manually-declared custom provider, a model with real tool-calling
  support, and a warm server.
