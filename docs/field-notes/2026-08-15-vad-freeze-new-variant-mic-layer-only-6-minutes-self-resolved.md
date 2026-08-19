---
title: A new freeze variant caught on macOS -- total mic-pipeline silence for 6+ minutes with NO codex subprocess ever spawned (a pure VAD/capture-layer freeze, not the readline()-stall variant this session's other notes cover) -- and the first freeze this session to fully self-resolve with no manual kill
status: validated-live
date: 2026-08-15
project: ConvoBox (github.com/LegionForge/convobox)
versions: branch feat/force-kill-and-kill-phrase-safety @ 3f718e8, backend=codex, permission_mode=permissive, macOS Darwin 25.6.0 (Apple Silicon, jps-Mac-mini)
evidence:
  - Autonomous /loop round 6. Same idle-trigger test shape as this session's other severe-freeze catches, this time attempting to capture a live `sample`/`lldb` stack trace of the codex process mid-freeze (macOS's built-in native profiler, no py-spy/external tool needed) -- the freeze that occurred was a different variant than the ones targeted, so no codex process existed to sample
  - Direct responsiveness probes (three, spaced minutes apart, different phrasing each time) rather than relying on a log line, same discipline as the opencode false-alarm note earlier tonight -- this one confirmed a REAL freeze, not a false alarm
  - Full raw session log (/tmp/convobox_sample_test.log, not committed)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; set up the autonomous /loop that ran this round)
    - Claude Code (Anthropic claude-sonnet-5) -- harness operation, live monitoring, writing, running autonomously via /loop
  org: https://legionforge.org
  created: 2026-08-15T04:40:00-05:00
  revised: 2026-08-15T04:40:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A new freeze variant: pure mic-layer silence, no backend involved, 6+ minutes, self-resolved

**Context.** This round set out to capture a live native stack trace
(via macOS's built-in `sample`/`lldb`, since `py-spy` isn't installed)
of the hung codex app-server process during a freeze, to finally see
WHAT it's blocked on rather than just that it's blocked. The freeze
that actually occurred made that plan impossible in a very informative
way: there was no codex process to sample.

## What happened

Ran the by-now-standard idle-trigger setup: one stress cycle (pause,
3x safeword burst, resume, followup), then idle. The cycle's own
safeword burst was heard and hard-stopped correctly (`transcript='Stop,
stop, stop.'`, `busy=False`, matched immediately) -- but that hard-stop
never needed a real backend turn (nothing was in flight), so **no codex
subprocess was ever spawned this run**. After that single utterance,
`Processing audio` stopped appearing in the log entirely -- no more
segmentation, no more STT calls, no more `dropped (...)` tier-1
messages, nothing -- for the rest of the observation window.

**This is structurally different from every other severe freeze this
session documented.** All of this session's prior severe catches
involved a stuck `codex app-server _read_loop: readline()` -- a
backend-subprocess-I/O-layer symptom. This one has no backend process
in the picture at all; whatever's stuck is upstream of that, in the mic
capture / VAD segmentation layer itself, matching the ORIGINAL
`docs/KNOWN-ISSUES.md` entry's own framing ("VAD segmenter's per-window
model call is synchronous with no offload/timeout") more directly than
any of tonight's codex-readline() catches have.

## Duration and resolution: 6+ minutes, then full self-recovery

Silence began at `04:31:34` (last `Processing audio` line). Three
responsiveness probes were sent, spaced minutes apart with different
phrasing each time (same false-alarm-avoidance discipline as the
opencode note earlier tonight): the first two produced zero response,
the third -- at `04:37:50`, **6 minutes 16 seconds** after the silence
began -- was picked up (`"no speech recognizable"`, a content miss but
proof the pipeline was alive again), and a fourth probe 40 seconds
later (`"stop stop stop"`) triggered a full, completely normal turn: a
codex subprocess spawned for the first time, responded ("Stopped."),
the whole path working end-to-end again.

**This is the first freeze this session where ConvoBox recovered
entirely on its own, with no manual kill or intervention of any kind.**
Every prior severe catch (three independent codex readline() freezes)
required a manual `kill -TERM` to end; this one simply stopped being
frozen. The main `run_convobox.py` process's own CPU usage stayed
nonzero and normal-looking throughout (`~4% CPU`, small but real deltas
between samples) -- consistent with the same "alive but not doing its
job" signature this session has seen before, not a hard OS-level block.

## What transfers

- **There are at least two structurally distinct freeze mechanisms on
  macOS, not one.** A backend-subprocess-I/O stall (this session's
  other notes: `codex app-server _read_loop`'s `readline()`, always
  involving a real spawned codex process) and a pure mic/VAD-layer
  stall (this note: no backend process involved at all). Root-causing
  "the freeze" as a single problem risks missing that these may need
  entirely separate fixes. (validated-live)
- **Duration and recovery behavior vary far more than this session's
  earlier framing suggested.** Prior notes described the severe variant
  as needing manual intervention, based on three catches that all did.
  This one ran roughly 4x longer than any of those (6+ min vs. ~90-114s)
  and recovered on its own -- "requires a kill" is not a universal
  property of every severe freeze, "duration is unpredictable and can
  be very long" is the more accurate framing. This also more closely
  matches the ORIGINAL 2026-08-14 Windows incident that started this
  whole investigation (a 41-minute freeze, also mic-pipeline-silence-
  shaped, also not confirmed to have needed a kill to end -- see that
  session's own field note for the exact detail). (validated-live)
- **The "always verify with a second, differently-phrased probe before
  concluding a freeze" discipline (established this session after the
  opencode false alarm) worked correctly here too, in the other
  direction** -- it correctly distinguished a real 6-minute freeze from
  a false alarm, not just the reverse. Worth keeping as standard
  practice for any future manual freeze investigation. (validated-live)

## Not done here

- Capturing a native stack sample of WHATEVER is actually blocked
  during this variant -- the original goal of this round. Since no
  codex process existed to sample, and the main `run_convobox.py`
  process itself wasn't confirmed hard-blocked (CPU wasn't zero), a
  `sample`/`lldb` capture of the Python process's own native frames
  during a future recurrence is the natural next attempt, ideally
  combined with `faulthandler.dump_traceback()`-style Python-level
  introspection (which WOULD show real Python stack frames, unlike a
  native sampler) if this variant can be reproduced again.
- Determining whether this variant and the readline()-stall variant
  share a root cause or are genuinely independent bugs.
- A second independent catch of this specific variant to confirm the
  6-minute duration and self-recovery aren't a one-off.
