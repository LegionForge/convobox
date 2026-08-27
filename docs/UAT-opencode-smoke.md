# Backend voice-loop smoke test (OpenCode)

A short recon pass to confirm a backend runs through the full voice loop
(mic → STT → backend → TTS → speakers), and to observe the interrupt
behavior that feeds the barge-in design ([DESIGN-barge-in.md](DESIGN-barge-in.md)).
Cloned from [UAT-claude-code-smoke.md](UAT-claude-code-smoke.md)'s template,
adapted for OpenCode's real, documented differences (see
`src/convobox/adapters/opencode.py` and `OPENCODE_API_NOTES.md`).

Not full validation — just enough to (a) prove the loop works on this
backend and (b) learn how it treats interject/hard-stop/kill.

## Config

```yaml
backend:
  name: opencode
  url: http://localhost:4096
```

Prereqs: `opencode` on PATH, authenticated, and **already running**
(`opencode serve`) before starting ConvoBox — unlike claude-code/codex,
this backend is not a subprocess ConvoBox spawns itself.

## ⚠️ Safety

The real `opencode serve` process runs with real tool access in whatever
directory it was started in. For a smoke test, keep to **read-only /
benign prompts** ("what does this repo do", "how many Python files are
here") — or point it at a throwaway directory.

**This backend has no tool-call approval concept at all** (confirmed —
`OpenCodeAdapter` has no `resolve_pending_approval`/`set_interactive_approvals`
override; `BackendAdapter`'s own default no-ops apply). Unlike
claude-code (`permission_mode: plan`, read-only by default) or codex
(auto-declines every approval RPC today), **nothing in ConvoBox gates
what OpenCode does here** — whatever it decides to run, it runs. Treat
the benign-prompts rule above as load-bearing, not a formality, for this
backend specifically.

## Step 0 — wiring check (no mic)

```
python scripts/run_convobox.py --text "In one sentence, what does this repo do?" --mute
```
Expect: `backend=opencode`, a `muted stream: …` line (TTS synthesized),
exit 0, no tracebacks on exit. Confirms spawn → response → TTS without
involving the mic.

## Step 1 — interactive (mic)

```
python scripts/run_convobox.py
```

**A · Loop basics**
- [ ] Simple question → spoken answer (STT → OpenCode → TTS end-to-end).
- [ ] Follow-up that needs the first ("…and who wrote it?") → context
      carries (multi-turn on one session).

**B · Busy tracking / indicator**
- [ ] Ask something that uses tools ("how many Python files are here?") →
      periodic `backend still working (Ns…)` heartbeat, then a spoken answer.

**C · Interrupt semantics ⭐ (feeds the barge-in design)**
- [ ] **Soft interject:** while it's working, ask a *new* question.
      **Expected on OpenCode, unlike Claude Code:** this **steers** the
      current turn (`delivery: "steer"` on the prompt POST) — a genuine
      mid-turn redirect, not a queued next turn. Confirm live which one
      actually happens.
- [ ] **Hard stop:** while it's working, say **"stop stop stop."**
      `POST /api/session/:id/interrupt` fires. Ask a fresh question right
      after → confirm it recovered and answers again.
- [ ] **Known no-op, not a bug:** triggering the interrupt endpoint while
      OpenCode is genuinely idle (nothing in flight) returns successfully
      but does nothing — confirmed live. Don't mistake an idle-time hard
      stop for a failure.

**D · Robustness**
- [ ] On a backend error → ConvoBox logs and keeps listening
      (crash-resilience), doesn't die.

**E · Feel**
- [ ] Note time-to-first-audio vs claude-code/codex — OpenCode Zen's
      default hosted model may be noticeably slower or faster depending
      on which model is actually selected (see `docs/UAT-checklist.md`'s
      `[L2]` — OpenCode can silently default to a hosted free-tier model
      with no error either way; confirm which one actually answered).

**F · Kill-phrase / force-kill (say "eject eject eject", if configured
as `safeword.kill_phrase`)**
- [ ] Expect the ConvoBox session to end (same mechanism as the Web UI's
      Quit button) — `force_kill()` closes the local SSE stream/HTTP
      client.
- [ ] **Do NOT expect this to reliably kill the real `opencode serve`
      process.** `OpenCodeAdapter.force_kill()` has no process to
      escalate against by architecture — it's a connection severance
      only. `docs/KNOWN-ISSUES.md` records a real 30-run harness where
      the remote process died anyway in 7/30 runs, unpredictably, root
      cause not established — treat any outcome here (dies or doesn't)
      as expected, not a pass/fail signal either way.

## Findings log

*(Not yet run live — this doc was written 2026-08-25 alongside the
cross-backend regression test matrix, cloned from the claude-code
template before an actual live session against this backend. Log real
findings here the same way the claude-code doc does, dated.)*
