# Regression test matrix

A routing document, not a second source of truth: this page says *where*
each backend × platform combination's coverage actually lives and *how
much of it runs automatically* — it doesn't duplicate `docs/UAT-checklist.md`'s
findings or `TESTING.md`'s own tier descriptions, it points at them.

Built 2026-08-25 after a live Linux testing session found a real gap:
claude-code had been tested thoroughly (TUI/web/text, real voice,
safeword, kill_phrase); OpenCode had only been tested at the isolated
adapter level; Codex hadn't been tested at all that session despite being
authenticated. This page exists so "have we actually tested X" has one
place to check, going forward, instead of needing to reconstruct it from
field notes each time.

## The three tiers (see `TESTING.md` for full detail)

1. **Unit/mocked** (`uv run pytest tests/`) — fakes/loopback servers, no
   real hardware, no real backend CLI. Runs in CI on every PR (Linux
   only, via the `test` job → `LegionForge/dev-rig`'s reusable workflow).
2. **Cross-platform, hardware-independent** (new, 2026-08-25) — real OS
   processes/protocols, but no real audio hardware and no live backend
   credentials. Runs in CI on `push:[main]`/`workflow_dispatch`, across
   Linux/macOS/Windows (the `process-kill-matrix` job — see
   `.github/workflows/ci.yml`).
3. **Human-required, live hardware** — a real mic/speaker/room and a real
   authenticated backend CLI. **Mandatory before any release tag**
   (`TESTING.md`'s "Release gate: what CI cannot test" — CI green is
   explicitly NOT release-ready on its own). No CI runner has a real
   speaker/mic/room; this tier is not a gap to automate away.

## Backend adapter contract conformance (Tier 1, extended 2026-08-25)

`tests/test_backend_adapter_conformance.py` — all three real adapters
against their fakes/loopback server, asserting the shared `BackendAdapter`
contract (the exact `force_kill()`-then-`aclose()` sequence
`Orchestrator`'s kill_phrase path runs; the 7 optional no-op methods) and
each backend's genuinely divergent `force_kill()` behavior explicitly.

| Backend | Status |
|---|---|
| claude-code | ✅ CI, every push (Tier 1) |
| codex | ✅ CI, every push (Tier 1) |
| opencode | ✅ CI, every push (Tier 1) |

## Process-kill reality (Tier 1 + Tier 2, extended 2026-08-25)

The actual, current, per-platform state — not aspiration. See
`docs/KNOWN-ISSUES.md` for the full incident history behind each row.

| Backend | Linux | macOS | Windows |
|---|---|---|---|
| claude-code | 🟡 Tier 2 CI (own-process only); N=1 live (2026-08-25 field note) for descendant behavior | ✅ 10/10 live-validated | ✅ 10/10 live-validated |
| codex | ✅ Tier 2 CI, real process-tree kill (`tests/test_real_process_tree_kill.py`) — closes the previously-explicit "expected, not confirmed" gap, plus two real bugs found+fixed in the same pass (Linux `ps` whitespace rendering; `ps` column-width truncation) | ✅ Tier 2 CI + 20/20 live-validated (2026-08-15/18/23 field notes) | ⚠️ **Known, disclosed, unfixed**: a detached descendant survives `force_kill()` — deliberately scoped as "make it observable" (`docs/BACKGROUND-JOB-OBSERVABILITY-SCOPE.md`), not "guarantee it's killed" |
| opencode | N/A by architecture (Tier 1 CI asserts local-disconnect-only) | N/A by architecture | N/A by architecture |

**claude-code's own descendant-cascade behavior is deliberately NOT
covered by an automated test** (see `tests/test_real_process_tree_kill.py`'s
own module docstring) — it depends on the real `claude` binary's internal
process management, which a bare test fake cannot meaningfully stand in
for. The 10/10 Windows/macOS numbers above are Tier 3 (live UAT), not
Tier 1/2.

## Live voice/UAT coverage (Tier 3 — mandatory before release, human-required)

| Backend | Linux | macOS | Windows |
|---|---|---|---|
| claude-code | ✅ Real human-voice session, 2026-08-25 (safeword, kill_phrase, self-barge-in — see the same-day field notes) | ✅ 2026-08-11 live demo field note | ✅ Referenced across multiple field notes (kill-phrase, VAD freeze) |
| codex | 🟡 First live-mic session run 2026-08-30 (`docs/UAT-codex-smoke.md`): loop basics, busy tracking, hard stop, kill-phrase, and error-recovery all confirmed; soft interject (`turn/steer`) and approval-mid-flight not yet exercised | ✅ Multiple field notes (force-kill fix rounds, kill-phrase live tests) | ✅ Multiple field notes (kill-phrase, VAD freeze, job-object) |
| opencode | 🟡 Adapter-level only (2026-08-25 field note) — no live-mic session yet | Not confirmed in a dedicated field note | Not confirmed in a dedicated field note |

Run via `docs/UAT-claude-code-smoke.md`, `docs/UAT-codex-smoke.md`
(written 2026-08-25, first run live 2026-08-30 — see its own Findings
log), `docs/UAT-opencode-smoke.md` (written 2026-08-25, not yet run
live — see its own Findings log). `docs/UAT-checklist.md`'s
`[G4]` ("verify against each backend") and `[L2]` (OpenCode's silent
default-model behavior) are the existing cross-backend items this table
routes to, not duplicates of them.

## Running the matrix before a release

**Tiers 1-2 (automated):** `uv run pytest` locally, or trigger
`process-kill-matrix` via `workflow_dispatch` in GitHub Actions for the
real cross-platform run. No coordination needed — one machine, one
command.

**Tier 3 (human-required, needs real hardware per platform):** this
project doesn't have a CI farm with real speakers/mics on three OSes, so
this tier runs as coordinated live sessions across whatever real machines
are actually available — in practice, separate Claude Code sessions per
platform, coordinated by a human moving between them (exactly how the
2026-08-24/25 Linux sessions this doc grew out of were run, alongside
concurrent macOS/Windows sessions the operator was also driving).

A repeatable version of that, not a new mechanism:

1. **Per platform, open (or resume) a Claude Code session on that real
   machine.** Point it at this repo, on the release branch/tag being
   validated.
2. **Hand each session the relevant UAT smoke doc(s)** for whichever
   backend(s) that machine can authenticate (not every machine needs
   every backend — this table's own gaps show which combinations still
   need a first real run).
3. **Run Step 0 (text/mute wiring check) unattended**, then Step 1 (the
   live-mic session) with a human actually present and speaking — Tier 3
   cannot be delegated to an agent alone. `TESTING.md`'s own "Release
   gate" section says this plainly: "CI green is NOT release-ready.
   ConvoBox's highest-value verification is live-mic UAT on real
   hardware, which no runner can perform."
4. **Log real findings into the smoke doc's own Findings log section**,
   dated — and into `docs/UAT-checklist.md` under the next free number in
   the right prefix (`[E*]`/`[L*]`/`[U*]`, check existing numbers first)
   for anything that should persist beyond one smoke doc.
5. **Update this table's Tier 3 section** once a cell moves from ❌/🟡 to
   ✅, so the next release doesn't have to re-derive coverage from field
   notes again.

This isn't new tooling — it's naming the thing that already happened
this session (parallel human-driven sessions per platform) as a
repeatable step, so "did we actually check codex on Linux before this
release" has a checklist to run through instead of a memory to rely on.
