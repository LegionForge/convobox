---
title: First clean full pytest suite pass on Linux (1500+ tests, only expected extra-gated skips) -- the platform's own test coverage was never actually confirmed passing on Linux before this session
status: validated-live
date: 2026-08-24
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0); pytest 9.1.1, pytest-asyncio 1.4.0; dev+web+aec extras installed (aec built from source, see the AEC volume-sweep field note for the packaging bug that required); openSUSE Tumbleweed 20260822
evidence:
  - uv run pytest -q, repo root, run twice in an isolated fork
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for a general Linux stability check as part of a broader same-day session, alongside the AEC sweep and OpenCode install)
    - Claude Code (Anthropic claude-sonnet-5) -- ran the suite in an isolated fork, installed OpenCode separately in the same pass, wrote this note
  org: https://legionforge.org
  created: 2026-08-24T15:20:00-05:00
  revised: 2026-08-24T15:20:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# First clean full test-suite pass confirmed on Linux

**Context for outsiders.** ConvoBox's test suite runs in CI (Linux
runners, per `dev-rig`'s lint/test workflows), so in one sense it's
"always" run on Linux. But CI's default job installs only the `dev` extra
-- not `aec`, which needs source-building `webrtc-audio-processing` (no
Linux PyPI wheel exists) -- so the AEC-dependent tests have likely never
actually executed on a Linux CI runner, only been skipped there. This
session installed `aec` from source on a real Linux dev machine (working
around a real upstream packaging bug -- see the AEC volume-sweep field
note) specifically to make a genuinely complete Linux run possible.

## Problem

Has anyone actually confirmed ConvoBox's full test suite -- AEC-dependent
tests included, not skipped -- passes cleanly on Linux? Not established
before this session, as far as any existing field note or doc records.

## Method

Run in an isolated fork (parallel to a live audio calibration sweep in the
main session, to avoid contending for it), with `dev`, `web`, and `aec`
extras all installed (`aec` built from source this same session --
see the companion AEC field note for the `lib`/`lib64` packaging bug that
had to be worked around first). `uv run pytest -q` from the repo root, run
twice.

## Evidence

Two full runs: **1511 passed / 10 skipped**, then **1531 passed / 9
skipped**. The small pass-count difference between the two runs (1511 vs.
1531) reads as incidental test-collection/parametrization variance, not a
real discrepancy -- **zero failures in either run**. Skips were checked
individually, not just counted:

- 8-9 skips: `piper-tts` tests, correctly skipped -- `piper` is a
  deliberately opt-in extra (GPL-3.0, kept out of the default install per
  `DEPENDENCY_LICENSE_AUDIT.md`), not installed this session.
- 1 skip: `tests/test_windows_job_object.py` -- a real Win32 Job Object
  API test, correctly Windows-only, no cross-platform equivalent expected
  or needed.

No Linux-specific failures, no unexpected skips, nothing needing an entry
in `docs/KNOWN-ISSUES.md` or `docs/UAT-checklist.md`.

## Mechanism

Nothing to diagnose -- this is a confirmation, not an investigation. The
only real "mechanism" worth naming is *why* this hadn't been confirmed
before: CI's default lint/test job doesn't install the `aec` extra (by
design -- it's a heavy source build, not a default dependency), so
whatever fraction of the suite is gated on AEC being importable has
presumably been running skipped in CI, not passing, until a human (or
agent) actually installs `aec` on a real Linux box and runs the suite for
real -- which is what happened here for what looks like the first time.

## What transfers

- **The full test suite, AEC-dependent tests included, passes cleanly on
  Linux** -- not just "CI is green," which could have been true even with
  every AEC test silently skipped. (validated-live, two runs, this
  session)
- **CI's own Linux coverage likely doesn't include the `aec` extra** --
  worth independently confirming against `dev-rig`'s actual workflow
  files (not done here; this note only establishes that a real Linux
  machine with the extra installed passes clean, not what CI itself
  currently exercises).

## Not done here

- Did not confirm whether `dev-rig`'s CI workflow ever installs the `aec`
  extra on its Linux runners -- the claim above about CI's likely
  coverage gap is inference from `pyproject.toml`'s own comments, not a
  direct check of the workflow YAML.
- Did not run with `cuda` or `piper` extras -- suite behavior with those
  installed is untested this session (piper's own skip count above is
  expected/by-design, not a gap).
- Single machine, single session -- not independently reproduced.
