# Backend voice-loop smoke test (Claude Code)

A short recon pass to confirm a backend runs through the full voice loop
(mic → STT → backend → TTS → speakers), and to observe the interrupt
behavior that feeds the barge-in design ([DESIGN-barge-in.md](DESIGN-barge-in.md)).
Written for Claude Code; the same structure applies to Codex (swap the
config and the backend-specific notes).

Not full validation — just enough to (a) prove the loop works on a second
backend and (b) learn how that backend treats interject/hard-stop.

## Config

```yaml
backend:
  name: claude-code
  command: ["claude", "--model", "claude-haiku-4-5"]   # haiku = snappy/cheap for smoke
```

Prereqs: `claude` on PATH and authenticated (`claude --version`).

## ⚠️ Safety

The spawned `claude` runs in **the current working directory** with tool
access. For a smoke test, keep to **read-only / benign prompts** ("what does
this repo do", "how many Python files are here", "explain the orchestrator").
Do **not** say "fix / refactor / delete …" unless you intend it to act on the
real repo — or run from a throwaway directory.

## Step 0 — wiring check (no mic)

```
python scripts/run_convobox.py --text "In one sentence, what does this repo do?" --mute
```
Expect: `backend=claude-code`, a `muted stream: …` line (TTS synthesized),
exit 0, and — since the shutdown fix — **no tracebacks on exit**. This
confirms spawn → response → TTS without involving the mic.

## Step 1 — interactive (mic)

```
python scripts/run_convobox.py
```

**A · Loop basics**
- [ ] Simple question → spoken answer (STT → Claude → TTS end-to-end).
- [ ] Follow-up that needs the first ("…and who wrote it?") → context carries
      (multi-turn on one process).

**B · Busy tracking / indicator**
- [ ] Ask something that uses tools ("how many Python files are here?") →
      periodic `backend still working (Ns…)` heartbeat, then a spoken answer.

**C · Interrupt semantics ⭐ (feeds the barge-in design)**
- [ ] **Soft interject:** while it's speaking/working, ask a *new* question.
      **Expected on Claude Code:** this **queues** — the current turn finishes,
      *then* your new question is answered as a separate turn. Claude Code's
      stream-json input has no steer/queue distinction, so a mid-run message
      is a next-turn queue, not a mid-turn redirect (adapter-documented;
      confirm live). → the `conversational`/`now` preset behaves as
      queue-next on this backend, unlike opencode's true steer.
- [ ] **Hard stop:** while it's working, say **"stop stop stop."** The turn
      cancels (control_request interrupt); ask a fresh question right after →
      confirm the process recovered and answers again.

**D · Robustness**
- [ ] On a backend error (rate limit, etc.) → ConvoBox logs and keeps
      listening (crash-resilience), doesn't die.

**E · Feel**
- [ ] Note time-to-first-audio vs opencode (Haiku should feel quick).

## Findings log (2026-07-12, first Claude Code smoke)

- ✅ Loop works end-to-end (`--text --mute`: response + TTS, exit 0).
- 🐛 **Fixed:** exit sprayed asyncio pipe-cleanup tracebacks on the
  subprocess adapters — the child transports were GC'd after the loop
  closed. Fixed by `BackendAdapter.aclose()` + shutdown wiring; re-verified
  0 tracebacks. (Not a memory leak — the `claude` children do exit.)
- ℹ️ `mkl_malloc: failed to allocate memory` once at STT load = transient
  host memory pressure (28.9 GB free at the time of re-check; the same model
  loads in ~1.6s), **not** a ConvoBox bug. Retry; close heavy apps if it
  recurs.
