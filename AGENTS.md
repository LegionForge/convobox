# Working agreements for agents in this repo

These directives bind every coding agent working in ConvoBox — Codex,
opencode, Claude Code, voice-driven or not. They exist because each rule
below was violated at real cost during live sessions; the incident behind
each one is cited so the rule can be re-evaluated instead of cargo-culted.

## 1. One work-set at a time — commit before starting the next

Finish the current feature slice, get its tests green, and COMMIT it
before touching a different feature. Never leave two unrelated features
interleaved and uncommitted in the same files.

If you cannot commit (no permission, mid-review), STOP at one completed
work-set and report; do not start the next one on top.

*Incident (2026-07-18/19): four uncommitted work-sets (~600 lines, 15
files) accumulated in one evening, entangled inside the same functions.
Separating them for review required hunk-level file surgery, and until
then NONE of the work had a savepoint.*

## 2. Size checkpoint: >150 changed lines or >5 files = stop and commit

When the uncommitted diff crosses roughly 150 lines or 5 files, stop
adding scope. Commit what is coherent, park what is not, then continue.

## 3. Safety-critical code changes ride alone

The safeword path, approval handling, barge-in gates, and the VAD/STT
front-end are safety-critical. A change touching any of them gets its own
commit — never bundled with unrelated work — and states in its message
what re-verification was done.

## 4. Live config tuning: one knob at a time, then re-test the safeword

During UAT, change ONE tuning value at a time and note it in the session
log before the next test. After ANY change to `vad.*` or
`interaction.*`, re-test that "stop stop stop" is still heard and acted
on before continuing the session.

*Incident (2026-07-18 ~22:00): `vad.threshold` and `min_speech_ms` were
both raised mid-session; the session went deaf to the operator's
safeword — zero transcripts for the final two minutes while they tried
to stop it.*

## 5. Verify a "bug" end-to-end before proposing a fix

Before claiming code is broken, trace the full runtime path (including
indirection — a dispatch may live in a callee) and, when possible,
reproduce against the running system. Never offer a fix for a bug you
have not confirmed exists.

*Incident (2026-07-18 ~22:33): an agent declared the safeword "logged
but never dispatched" and offered a fix. The dispatch lives in
`Orchestrator.handle_transcript`, and the same session's own log showed
it firing correctly — the "fix" would have modified working
safety-critical code.*

## 6. Test artifacts never land in the repo root

Scratch files, message dumps, and smoke-test evidence go under a
gitignored path (leading-underscore names `_*` are gitignored) or the
system temp dir — never as bare files like `SomethingSmokeTest.txt`.

## 7. Push after each committed milestone

A local commit is not a savepoint until it is pushed. After committing a
work-set (and passing the pre-push scrub for private data), push.

## 8. Attribution

AI-assisted changes follow `docs/AI-ATTRIBUTION.md` — PR-body block or
`AI-Attribution:` commit trailer.

## 9. When in doubt, ask smaller

If a request could be satisfied by a small change or a sweeping one,
deliver the small one and name the sweeping option in the report. The
operator can always ask for more; un-mixing too much is expensive.

## 10. Collective work: claim scope before editing

When multiple agents or people work this repo concurrently, take an
explicit scope (branch, worktree, or ticket) before editing. Never
modify files carrying someone else's uncommitted work — ask, or wait
for their commit. Integrate through small PRs, never a shared working
tree.

*Positive precedent (2026-07-14/19): the autonomous agent worked a
separate `ConvoBox-auto` worktree while the operator's live tree stayed
untouched across dozens of merged PRs — zero clobbering. This rule
generalizes that separation for the multi-agent collaboration ahead.*
