---
title: The Settings TUI could not open a convobox.yaml it could not validate -- the one tool meant to fix a bad config couldn't open with one
status: validated-live
date: 2026-08-06
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + PR #210 (fix/stt-compute-type-device-validation branch, merged with main through PR #206)
evidence:
  - convobox.yaml (this UAT checkout's own file, left with stt.device: cpu / stt.compute_type: float16 from a prior PR #210 live-test)
  - scripts/settings_tui.py:2527 (run_tui()'s unguarded load_config(path) call, pre-fix)
  - src/convobox/config.py (STTConfig._validate_compute_type_matches_device, added by PR #210)
  - PR #215 (the fix)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live-triggered the crash while UAT-testing PR #210, then scoped the follow-up)
    - Claude Code (Anthropic claude-sonnet-5) -- live incident investigation, code trace, fix implementation, tests, writing
  org: https://legionforge.org
  created: 2026-08-06T19:08:31-05:00
  revised: 2026-08-06T19:08:31-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The Settings TUI could not open a convobox.yaml it could not validate

**Context for outsiders.** ConvoBox has two config load paths for the
same `convobox.yaml`: the voice loop (`run_convobox.py`) and a curses-
style Settings TUI (`settings_tui.py`) for editing it. This note documents
a live incident where a config value already on disk -- rejected by a
validator that had just shipped in the same live-UAT session -- crashed
*both* entry points, including the one whose entire purpose is letting an
operator fix exactly this kind of mistake.

## Problem

While live-UAT-testing PR #210 (`fix(stt): reject an incompatible
compute_type/device pairing at config load`), the operator deliberately
set `stt.device: cpu` with `stt.compute_type: float16` in `convobox.yaml`
to confirm the new validator rejected it cleanly. It did -- for
`run_convobox.py`. Restoring the file to a valid pairing and launching a
real session worked too. But when `settings_tui.py` was then launched
against a config that (by that point, incidentally) still had the
rejected pairing, it crashed with the same unhandled
`pydantic.ValidationError`, instead of opening so the operator could fix
the value from inside the tool built for exactly that.

## Evidence

```
$ .venv\Scripts\python.exe scripts\settings_tui.py
Traceback (most recent call last):
  File "D:\LegionForge\convobox-UAT\scripts\settings_tui.py", line 2557, in <module>
    main()
  File "D:\LegionForge\convobox-UAT\scripts\settings_tui.py", line 2553, in main
    run_tui(Path(args.config) if args.config else None)
  File "D:\LegionForge\convobox-UAT\scripts\settings_tui.py", line 2527, in run_tui
    config = load_config(path)
  File "D:\LegionForge\convobox-UAT\src\convobox\config.py", line 627, in load_config
    return AppConfig.model_validate(raw)
pydantic_core._pydantic_core.ValidationError: 1 validation error for AppConfig
stt
  Value error, compute_type 'float16' is not supported on device 'cpu' --
  use one of ('float32', 'int16', 'int8', 'int8_float32') or 'default'
```

## Mechanism

`run_tui()` called the same strict `load_config()` / `AppConfig.
model_validate(raw)` that `run_convobox.py` uses, with no exception
handling at all. Pydantic validates `AppConfig` as a single atomic unit --
if any field (including a cross-field `model_validator` inside a nested
section like `STTConfig`) fails, `model_validate()` raises and returns
*nothing*, not a partially-valid object. There was no path from "the file
has one bad section" to "open the editor anyway."

This was never PR #210's bug to begin with: that PR closed the gap
between "clean rejection" and "raw ctranslate2 traceback" for values
entered *through* the TUI (caught at `validate_config()`, already called
before every save) and for `run_convobox.py`'s startup load (where a
hard failure is the *correct* behavior -- refusing to start a voice
session on an unvalidated config is not a bug). The gap this note
documents is different and narrower: a value already on disk, loaded
before any editing has happened, in the one tool whose job is recovery.

Two things made this an easy trap to fall into, not just a hypothetical:
- The Settings TUI already had `backup_config()`/`save_with_backup()`
  writing a timestamped `convobox.yaml.backup-<stamp>` before every save
  -- ~80 of them existed in this checkout by the time of the incident --
  but nothing offered them back when a *load* failed.
- `load_config()` already parses to a raw dict before validating; the
  raw dict (and, per pydantic's own `ValidationError.errors()`, the exact
  `loc` of what failed) was available and simply discarded on the
  exception path.

## What transfers

- **A model validated as one atomic unit gives you all-or-nothing on
  failure -- if a recovery UI needs partial success, it has to ask for
  it explicitly** (per-field/per-section `TypeAdapter(...).validate_python()`
  attempts with a defaults fallback, not a single top-level
  `model_validate()`), not assume pydantic will hand back whatever parsed
  cleanly. (validated-live)
- **The tool that's supposed to fix a broken state must not depend on
  that state already being valid to open.** Any editor/recovery UI over
  a validated config format is worth an explicit check: does opening the
  editor itself require the thing it's meant to fix to already be fixed?
  (validated-live, this instance; the general claim is a design
  heuristic, not independently measured elsewhere)
- **Backup-on-write infrastructure is easy to build and easy to forget
  to wire into the read/recovery path** -- `settings_tui.py` had a full,
  working, already-exercised backup mechanism (~80 real backups on disk)
  for over a week before anything used it for recovery rather than just
  provenance. Worth an explicit check on any "write a backup" feature:
  is there also a "here's how you'd use it" path, or does it just
  accumulate. (validated-live, this instance)

## Fix

`convobox.config.load_config_lenient()`: on a `ValidationError`, falls
each individually-invalid top-level section back to its own schema
default (via `TypeAdapter(field.annotation).validate_python(section_raw)`
per `AppConfig` field, catching per-section rather than per-file) instead
of failing the whole file, returning the raw dict and a list of
human-readable problems alongside an always-valid `AppConfig`.
`settings_tui.py`'s `run_tui()` uses it: on a load problem, the working
copy is marked dirty, the TUI jumps straight to the first affected
section, that section's tab is marked (`! STT`), and the status banner
names what happened and how to respond -- fix and save, or press the new
`[B]` to restore the most recent backup. `run_convobox.py` is
unchanged -- it keeps failing hard on an unvalidated config, which is
correct there.
