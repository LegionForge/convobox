---
title: A 10-cycle codex stress batch surfaces a self-resolving 66.3s stall AND a second independent severe freeze (114.0s to unblock, tail-triggered again) -- 2 for 2 batches now, both near the tail after explicit stress stopped
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=codex, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 2. Same synthetic-speech stress harness (_test_vad_freeze_macos.py, not committed), scaled from 5 to 10 cycles, codex backend
  - Full raw session log (/tmp/convobox_session_codex10.log, not committed), all 20 readline() stall/recovery events quoted below
  - ps process-state/CPU-time forensics on the hung app-server subprocess, same discipline as this session's earlier notes
  - Cross-reference: docs/field-notes/2026-08-15-vad-mic-freeze-live-reproduced-on-macos.md (this session's first severe-freeze catch, 5-cycle batch)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T02:40:00-05:00
  revised: 2026-08-15T02:40:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A 10-cycle batch surfaces a self-resolving 66.3s stall AND a second severe freeze -- 2 for 2 batches now

**Context.** Continuing the "try more cycles" ask from this session's
handoff. The first VAD-freeze macOS repro (5 cycles) caught exactly one
severe freeze, on the final cycle. This round doubled to 10 cycles to
build real statistical footing on how often the severe variant recurs.

## Result: 20 total stall events, most short, two long-but-recovering, one severe

Every `readline()` "finally returned" duration from the full batch:

```
0.6s  1.3s  0.9s  0.5s  1.0s  1.8s
20.5s
1.3s  2.1s
66.3s
1.0s  0.6s  5.6s  1.2s
42.3s
0.9s  6.2s  0.8s  1.1s
114.0s  <- severe, required a manual kill (see below)
```

**Two new data points that didn't exist before this run:** a 66.3s stall
and a 42.3s stall, BOTH of which self-resolved on their own -- no
intervention, `readline()` simply returned once the data finally
arrived. This matters: the earlier session assumed a binary split (short
self-resolving stalls vs. the severe multi-minute unrecoverable freeze).
**There is a real middle ground** -- stalls well past a minute that still
recover unaided. A monitoring/alerting threshold set at "anything over
30s is the severe case" would have been wrong twice in this one batch.

## The severe freeze, again: same signature, same trigger shape

The 10th and final cycle's own readline() stall grew past the
5.5s/10.5s/... pattern and never self-resolved -- eventually manually
killed at **114.0s** (`kill -TERM` on the app-server PID -> immediate
`readline()` return, `proc.returncode=-15`, same unblock-on-kill
behavior as this session's first severe catch). CPU forensics: two `ps`
samples 3 seconds apart showed byte-identical `TIME 0:01.16` -- genuinely
zero CPU, not merely slow, the same signature every prior severe
instance has shown.

**Critically, this one also started AFTER the harness's own scripted
audio had already finished playing** -- the stall began at the tail of
cycle 10, well after the last `playing followup_utterance` line, meaning
ordinary residual mic activity (not a deliberate stress burst) triggered
it. This is now the **second** time this exact pattern has been observed
this session (the first severe catch, 5-cycle batch, also happened right
at the run's tail) -- and it directly matches the original 2026-08-14
Windows finding that motivated this whole macOS investigation: a
41-minute freeze "triggered by ordinary low-volume activity, not a
stress burst." **2 for 2 batches now hit exactly one severe freeze each,
both at the tail, both without an active deliberate stress trigger at
the moment they began.**

## What transfers

- **A severe freeze isn't rare or hard to hit on macOS** -- two
  independent stress batches this session (5-cycle, then 10-cycle) each
  produced exactly one. Small sample (n=2), but consistent enough to
  treat "roughly one severe freeze per extended session" as a working
  estimate until contradicted, not an outlier. (validated-live)
- **The severe variant appears to correlate with idle/tail-end activity
  more than active stress bursts** -- both this session's catches AND
  the original Windows 41-minute incident happened after or between
  deliberate stress, not during it. If true, a stress-only test harness
  may actually be UNDER-representing real-world risk (a live session
  left running quietly, not being actively hammered, might be MORE
  likely to hit this, not less). Worth testing directly in a future
  round: run the harness once, then leave the session idle and just
  listening for several minutes, see if the freeze still occurs.
  (plausible pattern, not yet confirmed as causal)
- **A stall duration threshold for "this is the severe case" needs to be
  much higher than previously assumed** -- 66.3s and 42.3s both
  self-resolved in this same batch. Any future alerting/dashboard logic
  should not treat "over a minute" as automatically unrecoverable.
  (validated-live)

## Not done here

- Testing the "idle activity triggers it more than active stress" theory
  directly (run the harness, then go quiet and just listen) -- flagged
  above as the natural next experiment, not attempted this round.
- A third batch to see if the "2 for 2" pattern holds or was coincidence
  -- worth doing if this investigation continues.
- Root-causing WHY these particular readline() calls take so long before
  either resolving or hanging forever -- still an open question from
  every prior note in this thread; this note only adds more data on
  frequency and duration distribution, not mechanism.
