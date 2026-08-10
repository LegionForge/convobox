---
title: First live-hardware pass on macOS -- real AEC calibration, real claude-code/codex backend round-trips, and a real crash found+fixed in the process
status: validated-live (AEC + backend connectivity); NOT full voice-loop validated (no live human speech through the mic yet)
date: 2026-08-10
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ e42cf27 (AEC docs) / ce413f9 (NameError fix); macOS 26.x, Apple Silicon; AIRHUG 28 (USB mic), Mac mini Speakers
evidence:
  - Real hardware, dedicated `git worktree` at convobox-UAT (branch uat-macos), separate from the dev tree
  - scripts/acoustic_calibration.py live output + JSON reports (uat-acoustic-calibration/, gitignored, not committed)
  - scripts/run_convobox.py --text live runs against real claude-code and codex CLIs
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; handed off from a Helios/Windows session, granted mic access mid-session, directed the backend/browser testing scope)
    - Claude Code (Anthropic claude-sonnet-5) -- ran the tests, root-caused and fixed the NameError, wrote this note
  org: https://legionforge.org
  created: 2026-08-10T01:35:00-05:00
  revised: 2026-08-10T01:35:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# First live-hardware pass on macOS

**Context for outsiders.** ConvoBox's own README support matrix has long
listed macOS as "implemented, not yet voice-validated" -- the code paths
exist and pass unit tests, but nobody had run real audio hardware against
them on this platform. This session got real mic/speaker access on a Mac
mini for the first time and used it to close part of that gap: signal-level
AEC calibration, and backend connectivity for two of the three supported
CLI agents. It does **not** close the whole gap -- no live human speech was
transcribed through the mic into a backend this session (see "What this
does NOT confirm" below).

## What was tested

### 1. AEC signal-level calibration (real speaker + real mic)

Ran `scripts/acoustic_calibration.py` (previously exercised on Windows
laptop-internal hardware per the 2026-08-09 field note, never before on
this Mac) twice independently against AIRHUG 28 (USB mic) and Mac mini
Speakers, with a real Piper voice (`en_US-lessac-medium`) actually
synthesized and played.

- Trial 1: `attenuation=2.49dB, ceiling=1.92dB` (auto-estimated delay
  238ms, `input_latency=169ms`, `output_latency=59ms`).
- Trial 2 (independent run): `attenuation=5.08dB, ceiling=0.69dB`.
- **Both readings sit at or below the tool's own measurable ceiling** --
  per `EchoCanceller.measurable_ceiling_db()`'s docstring, that means
  speaker echo at this mic barely rises above room ambient noise in the
  first place, not that AEC is failing to cancel it.
  `raw_playback_rms` (0.0047-0.0049) vs `ambient_rms` (0.0037-0.0040)
  confirms this directly.
- **Zero false barge-ins in either trial, with AEC on OR off**
  (`false_barge_ins: 0` for both `raw_vad` and `processed_vad`, both
  runs). Raw VAD did register 1-2 short spurious utterances from
  un-cancelled echo (once peaking at `peak_vad_probability=0.997`), but
  never sustained long enough to cross `BargeInMonitor`'s real threshold.

**Read as a genuinely different acoustic situation than the Windows
laptop-internal finding** (spectral-suppression vs. VAD-rejection
diverging, `aec_delay_ms=322` winning on spectral metrics but not on
VAD-rejection) documented in the 2026-08-09 field note -- not a
contradiction of it. Only 2 trials, one room, one external USB mic --
not enough to generalize to "macOS is fine," just enough to say this
specific mic/speaker/room combination's echo problem (if this repo ever
needs to chase one on it) looks small relative to room noise here.

Mic input level also checked via the Settings TUI's `probe_audio()`:
-27.1dBFS RMS / -2.2dBFS peak, reported "good" -- unlike a prior
Windows finding of a too-quiet default mic needing gain.

### 2. Backend connectivity (claude-code, codex; opencode untestable)

Ran `scripts/run_convobox.py --text "..." --permission-mode plan` against
two of the three backends, in an isolated `working_dir`, real TTS output
through the Mac mini speakers (not muted):

- **opencode: could not be tested.** `opencode serve` was not running,
  and `opencode auth list` showed **zero configured provider
  credentials** on this machine. Not something to configure unattended
  (API keys/OAuth are the operator's call) -- left alone, noted as an
  open item.
- **claude-code: tested, hit a real crash, fixed, re-tested clean.** See
  "Real bug found" below.
- **codex: tested clean** (post-fix), same `--text` smoke test, no
  crash, response spoken correctly.

## Real bug found: `--text` mode crashed on its first backend event

`run_convobox.py --text "reply with exactly the word banana"` against a
real claude-code backend raised, on the very first dispatched event:

```
NameError: cannot access free variable 'indicator' where it is not
associated with a value in enclosing scope
```

**Root cause.** `_dispatch_event()` (inside `main()`) closes over a
local variable `indicator`, which is only ever assigned
(`indicator = WorkingIndicator()`) in the mic-loop setup path, well
after `_dispatch_event`'s own definition. That assignment's own comment
reasoned this was fine because Python resolves closures at *call* time,
not *definition* time -- true for the mic-loop path, but it missed that
`--text` mode calls `_dispatch_event` (via `handle_transcript()`) and
`return`s **before** that later assignment is ever reached. The closure
saw a genuinely unbound name, not just a stale one.

**Fix** (commit `ce413f9`, dev tree): bind
`indicator: WorkingIndicator | None = None` immediately before
`_dispatch_event`'s definition. `_on_backend_event()`'s own `indicator`
parameter already defaults to `None` and is designed to tolerate it
(nothing to animate outside the mic-loop/TUI path) -- this just makes
that the real value `--text` mode's dispatches see, instead of an
unbound-variable crash. The mic-loop path's later reassignment to a
real `WorkingIndicator` is unchanged and still works the same way.

**Verified live, not just unit-tested**: reproduced the crash against a
real claude-code backend, applied the fix, re-ran the *identical*
command -- clean dispatch, no traceback, real TTS audio played. Then
ran the same smoke test against codex, also clean. Full suite (1356
tests) green, ruff/mypy clean on touched files. No new unit test added
for this specific path -- `main()`'s `--text` mode has no existing
fake-adapter integration harness in this repo, and building one was
judged disproportionate to a same-session fix; noted as a real coverage
gap (see "What transfers" below), not silently skipped.

## Chrome/web-UI testing: still not available this session

Checked twice for `mcp__claude-in-chrome`-style browser-control tooling
(once proactively, once after being asked directly) -- genuinely absent
from this session's tool list, not just disconnected. A 2026-08-07
field note (folded into `!history.md`'s vault summary, not this repo)
records that the same tool DID work in a past session once the
operator's browser (BrowserOS, a de-googled Chromium build, not literal
Chrome) was actually running -- so this looks like a session/environment
gap, not a structural one. Web UI / browser-driven UAT on macOS remains
untested pending either a future session with that tool connected, or
the operator testing it directly.

## What transfers

- **AEC signal-level cancellation works on real macOS hardware** (AIRHUG
  28 + Mac mini Speakers): confirmed live, not just unit-tested. Echo
  here is small relative to room noise, so this pairing doesn't stress
  AEC hard -- a different mic/speaker pair (especially a laptop's
  internal array, per the Windows finding) could behave differently on
  this same OS. (validated-live, n=2 trials, one room)
- **claude-code and codex backends both work end-to-end via `--text`
  mode on macOS**, including real TTS audio through real speakers.
  (validated-live)
- **opencode backend is untested on this machine, for a credentials
  reason, not a code reason** -- needs the operator to configure
  provider auth before it can be tried here.
- **A real, previously-undetected crash existed in `main()`'s `--text`
  mode** (the repo's own primary "scriptable validation, full path
  minus mic" tool, referenced constantly across this project's own UAT
  history) -- it shipped through a green CI/test suite because no test
  harness exercises `main()`'s `--text` control flow end-to-end. Worth a
  lightweight fake-adapter integration test for this path specifically,
  next time there's room for it.
- **This still does NOT confirm the full voice loop on macOS** -- no
  live human speech was captured by the mic and transcribed into a
  backend this session. The README's "implemented, not yet
  voice-validated: macOS" line is only partially closed by this pass;
  see docs/KNOWN-ISSUES.md's AEC entry for the precise scope of what's
  now confirmed vs. still open.

## Open question for a future session

Full voice-loop UAT on macOS (a human actually speaking to it, live)
still hasn't happened -- this pass only exercised the AEC/backend
halves independently, the same split this repo's own AEC entry already
draws for the code-construction-vs-live-measurement gap it closed on
2026-07-16.
