---
title: claude-code backend survived a full 6-cycle VAD stress run on macOS with no severe freeze, unlike codex's severe freeze on cycle 5 of 5 earlier the same session; also isolates a benign false-alarm shape in the stall diagnostic itself
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=claude-code, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - A second real ConvoBox session, same synthetic-speech stress harness as this session's earlier codex repro (`_test_vad_freeze_macos.py`), same methodology, claude-code backend instead of codex
  - Full raw session log (`/tmp/convobox_session_cc.log`, not committed)
  - This session's own earlier note, `2026-08-15-vad-mic-freeze-live-reproduced-on-macos.md` (the codex severe-freeze baseline this note compares against)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked to keep testing new combinations after the first two macOS field notes)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing
  org: https://legionforge.org
  created: 2026-08-15T01:50:00-05:00
  revised: 2026-08-15T01:50:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# claude-code survives a 6-cycle VAD stress run where codex froze on cycle 5 of 5

**Context.** This session's first VAD-freeze note reproduced a severe
freeze on macOS using the codex backend -- 94.4s stuck `readline()`
followed by 2+ minutes of total mic-pipeline silence, on the fifth of
five stress cycles. That note didn't test whether the freeze is codex-
specific. This one does: same harness, same machine, claude-code backend
instead, six cycles (one more than the codex run).

## Result: no severe freeze, all 6 cycles completed normally

Every cycle's pause/burst/resume/followup sequence produced a normal
response (`"Understood, standing by."`, `"Stopped. I'm here when you're
ready."`, etc.) and the mic pipeline kept processing new audio
throughout -- no multi-minute silence gap like the codex run showed.
`claude_code._read_loop`'s own stall diagnostic did fire repeatedly
(max observed: 25.5s, several 10-20s stalls across the run) but every
one of them recovered on its own, the same shape as codex's OWN cycles
1-4 (the ones that self-resolved) -- just no cycle 5-style permanent
freeze this run.

This doesn't prove claude-code is immune -- six cycles is a small sample,
and the codex freeze itself only appeared on its fifth cycle, not
earlier ones. But it's directly consistent with tonight's other two
findings about this backend: claude-code scored 10/10 on the
`force_kill()` reliability test (vs. codex's 0/10) and has now also gone
a full stress run without the severe mic-pipeline-silence failure mode
codex hit on a comparable run. Three independent tests this session
now point the same direction: **claude-code is measurably more robust
than codex on this macOS setup**, not proven immune, but a real,
repeated pattern worth taking seriously when choosing a default backend
for macOS deployments specifically.

## A methodology note worth keeping: `_drain_stderr` stalls are structurally different from `_read_loop` stalls

While watching this run, `claude_code._drain_stderr`'s own stall
diagnostic climbed continuously and never once "finally returned" --
0.5s, 5.5s, 10.5s... past 100s by the time the session was torn down.
**This is not a freeze indicator.** `_drain_stderr` reads the CLI's
stderr pipe, which a healthy, quiet process simply never writes to for
the whole session -- an idle stderr pipe with nothing arriving is the
EXPECTED steady state, not evidence of anything stuck. `_read_loop`
(stdout, where every JSON-RPC/NDJSON message actually arrives) is the
one whose stalls are a real signal; `_drain_stderr`'s only matters if it
ever stops matching a live, still-running process (i.e., check
`proc.returncode`, which the diagnostic already logs, rather than the
raw duration). Worth noting explicitly since both call sites share the
exact same `readline_with_stall_diagnostic()` helper and log at the same
WARNING level -- a future session (or an automated alert) reading these
logs without this context could easily over-count `_drain_stderr`
duration as equally alarming, when in practice it is close to always
benign.

## What transfers

- **A backend that passes one reliability test (force_kill) is more
  likely, not guaranteed, to pass a structurally different one (freeze
  resistance)** -- worth treating as a real correlation to watch, not
  proof of a shared root cause between the two failure modes. (validated-
  live, small sample)
- **Distinguish stdout-reader stalls from stderr-drain stalls when
  triaging this diagnostic** -- same warning shape, very different
  meaning. A monitoring/alerting rule built on this log line should
  filter or weight `_drain_stderr` differently from `_read_loop`.
  (validated-live)

## Not done here

- A matched, larger sample (e.g. 10+ cycles on both backends, same
  session, alternating) to build real statistical confidence rather than
  comparing one 5-cycle codex run against one 6-cycle claude-code run.
- Root-causing WHY claude-code appears more resilient -- this note only
  establishes the correlation, not a mechanism.
- Testing opencode against this same VAD-freeze harness -- this session
  only tested opencode's `force_kill()` behavior (separate field note),
  not its mic-pipeline freeze resistance.
