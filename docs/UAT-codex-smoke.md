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
- [ ] Simple question → spoken answer (STT → Codex → TTS end-to-end).
- [ ] Follow-up that needs the first ("…and who wrote it?") → context
      carries (multi-turn on one thread).

**B · Busy tracking / indicator**
- [ ] Ask something that uses tools ("how many Python files are here?") →
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
- [ ] **Hard stop — known, documented live bug, expected to reproduce:**
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
- [ ] On a backend error → ConvoBox logs and keeps listening
      (crash-resilience), doesn't die.

**E · Feel**
- [ ] Note time-to-first-audio vs claude-code/opencode.

**F · Kill-phrase / force-kill (say "eject eject eject", if configured
as `safeword.kill_phrase`) — real, documented platform split**
- [ ] **Linux/macOS: expect a clean kill.** `force_kill()` terminates the
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

## Findings log

*(Not yet run live — this doc was written 2026-08-25 alongside the
cross-backend regression test matrix, cloned from the claude-code
template before an actual live UAT session against this backend on this
platform. Log real findings here the same way the claude-code doc does,
dated.)*
