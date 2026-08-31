# Backend voice-loop smoke test (Codex)

A short recon pass to confirm a backend runs through the full voice loop
(mic → STT → backend → TTS → speakers), and to observe the interrupt
behavior that feeds the barge-in design ([DESIGN-barge-in.md](DESIGN-barge-in.md)).
Cloned from [UAT-claude-code-smoke.md](UAT-claude-code-smoke.md)'s template
("the same structure applies to Codex"), adapted for Codex's real,
documented differences (see `src/convobox/adapters/codex.py` and
`docs/KNOWN-ISSUES.md`).

Not full validation — just enough to (a) prove the loop works on this
backend and (b) learn how it treats interject/hard-stop/kill.

## Config

```yaml
backend:
  name: codex
  command: ["codex"]   # ConvoBox appends "app-server" itself
```

Prereqs: `codex` on PATH and authenticated (`codex --version`).

## ⚠️ Safety

The spawned `codex app-server` runs in the current working directory
with real tool access, gated by `backend.permission_mode` (`plan` is the
safe read-only default — see `docs/PERMISSION-MODEL.md`). For a smoke
test, keep to **read-only / benign prompts**, or run from a throwaway
directory, same as the claude-code template.

**Approval requests are currently auto-declined, not voice-wired.**
Codex's real app-server protocol has genuine approval RPCs
(`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`,
and legacy equivalents) — unlike OpenCode, which has none at all — but
`CodexAdapter` currently answers every one of them itself (decline/deny),
with no operator-facing voice gate yet (matches the README's own "real
approval channel not yet voice-wired" note). Confirm this is actually
happening (a logged auto-decline, not silence) rather than assuming it.

## Step 0 — wiring check (no mic)

```
python scripts/run_convobox.py --text "In one sentence, what does this repo do?" --mute
```
Expect: `backend=codex`, a `muted stream: …` line (TTS synthesized),
exit 0, no tracebacks on exit. Confirms spawn → response → TTS without
involving the mic.

## Step 1 — interactive (mic)

```
python scripts/run_convobox.py
```

**A · Loop basics**
- [x] Simple question → spoken answer (STT → Codex → TTS end-to-end).
- [x] Follow-up that needs the first ("…and who wrote it?") → context
      carries (multi-turn on one thread).

**B · Busy tracking / indicator**
- [x] Ask something that uses tools ("how many Python files are here?") →
      periodic `backend still working (Ns…)` heartbeat, then a spoken answer.

**C · Interrupt semantics ⭐ (feeds the barge-in design)**
- [ ] **Soft interject:** while it's working, ask a *new* question.
      **Expected on Codex, unlike Claude Code:** `turn/steer` fires — a
      genuine mid-turn redirect, not a queued next turn (falls back to a
      fresh turn if there's no active turn to steer, or if steer misses
      its target turn). Confirm live which one actually happens.
- [ ] **Approval mid-flight:** if a prompt triggers an approval request,
      confirm it's auto-declined (see Safety above) and the turn
      completes/fails accordingly, rather than hanging silently.
- [x] **Hard stop — known, documented live bug, expected to reproduce:**
      while it's working, say **"stop stop stop."** `turn/interrupt`
      fires and (per the app-server's own RPC response) succeeds — but
      **this does NOT guarantee an in-flight tool call actually stops**.
      `docs/field-notes/2026-08-09-hard-stop-does-not-cancel-an-in-flight-tool-call.md`
      records 5 separate live incidents where the interrupt RPC
      succeeded while the underlying tool call kept running regardless
      (16-48+ seconds observed). If you see this, that's the documented
      behavior, not a new bug — log how long the tool call actually took
      to stop (if it ever did) rather than treating a fast-looking
      recovery as proof it's fixed.

**D · Robustness**
- [x] On a backend error → ConvoBox logs and keeps listening
      (crash-resilience), doesn't die.

**E · Feel**
- [x] Note time-to-first-audio vs claude-code/opencode.

**F · Kill-phrase / force-kill (say "eject eject eject", if configured
as `safeword.kill_phrase`) — real, documented platform split**
- [x] **Linux/macOS: expect a clean kill.** `force_kill()` terminates the
      app-server directly, then (if a turn was genuinely in flight when
      it fired) `_kill_by_command_text()` SIGKILLs the real spawned
      command and every live descendant, matched by `ps` substring —
      live-validated 20/20 on macOS after several real bug-fix rounds;
      Linux parity confirmed 2026-08-25 (see the same-day field notes and
      `docs/KNOWN-ISSUES.md`) after fixing two real Linux-specific gaps
      found in that pass (a whitespace-rendering difference in `ps`, and
      a `ps` column-width truncation issue) — both now covered by
      `tests/test_real_process_tree_kill.py`.
- [ ] **Windows: expect a REAL, KNOWN, currently-unfixed gap.** A
      detached descendant process (e.g. one spawned via PowerShell
      `Start-Process`) survives `force_kill()` entirely — disclosed, not
      silently broken; see `docs/KNOWN-ISSUES.md` and
      `docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md` for why this is
      deliberately scoped as "make it observable," not "guarantee it's
      killed" (a blanket kill-everything-ever-spawned approach was
      considered and rejected as the wrong default). If you're testing on
      Windows, confirm the ConvoBox *session* still ends cleanly even
      though that one descendant does not.

## Findings log (2026-08-30, first Codex live-mic smoke, Linux, --tui --web)

- ✅ **A · Loop basics** — simple question → spoken answer end-to-end;
  follow-up carried context on the same thread.
- ✅ **B · Busy tracking** — a tool-using question produced periodic
  `codex app-server _read_loop` heartbeats then a spoken answer.
- ℹ️ **Echo at 50% volume correctly ignored — not a bug.** The assistant's
  own TTS played back through the mic and was transcribed by STT, but
  ConvoBox's text-level echo filter recognized it (`echo-match: 0.87–1.00`
  of tokens vs. the last response) and dropped it every time. This is the
  documented behavior for a session with no `audio.echo_cancellation`
  configured (see `scripts/run_convobox.py`'s own module docstring) —
  working as designed, not something to fix.
- 🐛 **Reconfirms existing known issue, new trigger: Kokoro's ~510-phoneme
  limit** (`docs/KNOWN-ISSUES.md` → "Kokoro can't synthesize past ~510
  phonemes"). A long Codex web-search-summary response hit it live:
  `RuntimeError: Kokoro synthesis stalled (no audio chunk within 30.0s)`.
  ConvoBox's existing 30s stall-timeout worked exactly as designed — it
  turned kokoro-onnx's silent internal hang into a catchable error, logged
  it, and the conversation kept going normally on the next turn (real
  live confirmation of **D · Robustness**, not just a design claim). The
  cost: that entire response was never spoken to the operator, no partial
  audio, no "I can't finish that" fallback. Not a new root cause — the
  existing entry's "not yet built" pre-chunking layer (or its cheaper
  deferred alternative, auto-routing long text to Piper) is still the
  fix; this just adds a second live repro against a different backend.
- ✅ **C (partial) / F · Hard stop and kill phrase DID fire correctly** —
  initially looked broken but the log (`convobox-tui.log`) shows both
  worked: `hard stop matched safeword 'stop stop stop'` (twice), then
  `kill phrase matched 'eject eject eject' -- force-killing backend`,
  followed by a clean shutdown and exit. Root cause of the "unresponsive"
  feel: two earlier, genuine interrupt attempts phrased casually
  ("I'm trying to interrupt.", "and to wrap it again.") are **not** the
  configured safeword, so — correctly, by design — they were silently
  dropped by the overlap gate while the assistant's turn was still
  speaking/synthesizing. Only the exact tripled phrase ("stop stop stop",
  "eject eject eject") bypasses that gate on the raw transcript. Once
  spoken, the match landed within ~1s of the transcript. **Real UX gap
  worth flagging** (not yet in `docs/KNOWN-ISSUES.md`): the operator gets
  zero feedback — no tone, no log the user can hear — when a real
  barge-in attempt is heard but silently dropped for not being the exact
  safeword; on a slow machine with long synthesis gaps this reads as the
  app being unresponsive rather than as "wrong phrase, try again."
- ℹ️ **C · Soft interject (`turn/steer`) and approval-mid-flight were not
  exercised this session** — no prompt in this pass triggered either
  path. Still open for a future run.
- ℹ️ **E · Feel** — time-to-first-audio was inconsistent across the
  session: ~2.3s (response text → first audio block) early on, but ~20s
  for a short 40-word response later in the session, with STT/backend
  work overlapping it the whole time. Consistent with this machine being
  slow rather than a specific code path; not investigated further here.

## Findings log (2026-08-30, second Codex live-mic smoke, Linux, retest of hard-stop)

- 🐛 **Real root cause found for "still not interrupting well," reported
  live before the log was checked.** Not the overlap-gate behavior from
  the first session — `convobox-tui.log` shows a genuine **213.4s total
  silence in the mic/STT pipeline** (00:53:40 → 00:57:13, zero
  `Processing audio` lines), while the codex adapter's own `_read_loop`
  kept logging its routine idle-poll warnings on schedule the entire
  time (`busy=False` throughout) — proving the backend side was fine and
  only mic-capture/VAD went dark. This matches an already-documented,
  previously macOS-only, rare, self-resolving freeze variant in
  `docs/KNOWN-ISSUES.md`'s VAD segmenter entry — this is a second
  occurrence and the first seen on Linux; full detail added there rather
  than duplicated here. Every hard-stop/kill-phrase attempt spoken during
  the freeze went completely unheard (no `dropped (...)` line — the mic
  layer just wasn't producing STT output at all); the moment it
  self-resolved, "stop stop stop" and "eject eject eject" both matched
  instantly on the next attempt. The safeword-matching logic itself is
  not at fault.
- ✅ **F · Kill phrase reconfirmed** — `eject eject eject` force-killed
  the backend and exited cleanly a second time, same session.
- ℹ️ **C · Soft interject and approval-mid-flight still not exercised**
  this session either — `permission_mode: approve` is currently broken
  against the installed codex-cli (see the new
  `docs/KNOWN-ISSUES.md` entry on `approval_policy=untrusted` being
  removed upstream), so approval-mid-flight is blocked until that's
  properly fixed. Soft interject needs a deliberate attempt during a
  `busy=True` heartbeat window specifically — none of this session's
  interject attempts landed in that window either.
