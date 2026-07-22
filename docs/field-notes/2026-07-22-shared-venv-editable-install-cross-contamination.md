---
title: A shared venv silently repoints between two clones' editable installs, breaking one of them without touching its code
status: validated-live
date: 2026-07-22
project: ConvoBox (github.com/LegionForge/convobox)
versions: uv 0.9.x, editable installs (PEP 660), Windows directory junction sharing a single .venv between two clones
evidence:
  - Live session, 2026-07-21/22 — user report "I don't think I'm able to scroll anymore" against the UAT clone
  - `python -c "import convobox; print(convobox.__file__)"` resolving to D:\LegionForge\ConvoBox (dev) instead of D:\LegionForge\convobox-UAT while running UAT's own checked-out scripts/run_convobox.py
  - Fix verified: `uv cache clean convobox && uv sync --reinstall-package convobox --extra dev --extra aec --no-cache` from the UAT directory, re-check `convobox.__file__`, then `pytest tests/test_conversation_tui.py tests/test_conversation_tui_keys.py` (44 passed) confirming the feature works once resolution is consistent again
  - Same root cause recurred at least twice earlier in the same session, under different symptoms (an `AttributeError` on a config field that "should" exist, and general confusion about which repo's code was actually running)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; reported the symptom live)
    - Claude Code (Anthropic claude-sonnet-5) — diagnosis, fix, writing
  org: https://legionforge.org
  created: 2026-07-22T00:20:00-05:00
  revised: 2026-07-22T00:20:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A shared venv silently repoints between two clones' editable installs

**Context for outsiders.** ConvoBox is developed in two clones on the same
machine: a dev checkout and a separate UAT checkout used for live voice
testing, sharing one Python virtual environment (the UAT clone's `.venv`
is a directory junction to the dev clone's `.venv` — same interpreter,
same site-packages, intentionally, to avoid installing the whole audio/ML
stack twice). `convobox` itself is installed **editable** (`pip install
-e .` / `uv sync`) in both. This note is about a failure mode that shape
of setup creates, not anything specific to voice software.

## Problem

Running `uv sync` (or `uv run <anything>`, which syncs first) in **either**
clone silently repoints the *shared* venv's editable `convobox` install to
resolve against **that clone's** source tree — not the one whose commands
you were actually running. The other clone's own scripts keep executing
(they're plain files, not part of the installed package), but every
`import convobox...` inside them now resolves against the *other* clone's
library code. Nothing errors at import time if the two trees are
reasonably close in shape; the symptom is subtler and often looks like a
real code regression instead of an environment problem.

Concretely this session: dev's `main` had just merged five PRs in
sequence (adapters, config, orchestrator, TUI changes among them) while
the UAT clone's own checked-out branch was still several commits behind
on those same files. Every `uv sync`/`pytest` run from the dev directory
during that work silently repointed the shared venv at dev's tree. The
next live UAT session then ran **UAT's older `scripts/run_convobox.py`**
against **dev's newer `convobox` library** — an unintended, never-tested
combination of two different points in the same file's history. The
reported symptom was "I can't scroll the transcript anymore" (a feature
that works correctly and has passing tests on both trees *individually*).

## Evidence

```
$ .venv/Scripts/python.exe -c "import convobox; print(convobox.__file__)"
D:\LegionForge\ConvoBox\src\convobox\__init__.py
```
— run from inside `D:\LegionForge\convobox-UAT`. Fix:
```
$ uv cache clean convobox
$ uv sync --reinstall-package convobox --extra dev --extra aec --no-cache
 - convobox==0.2.0 (from file:///D:/LegionForge/ConvoBox)
 + convobox==0.2.0 (from file:///D:/LegionForge/convobox-UAT)
$ .venv/Scripts/python.exe -c "import convobox; print(convobox.__file__)"
D:\LegionForge\convobox-UAT\src\convobox\__init__.py
$ .venv/Scripts/python.exe -m pytest tests/test_conversation_tui.py tests/test_conversation_tui_keys.py -q
44 passed
```

## Mechanism

Ruled out first (before landing on the real cause): a real regression in
the scroll-handling code introduced by the PR #123 merge-conflict
resolution done the same night. Checked directly — that conflict was
docstring-only in `tui/state.py`, and the scroll fields/`_handle_tui_key`
function were untouched by it. This ruled out "I broke it while resolving
a conflict" before looking anywhere else, rather than assuming the most
recent risky-looking edit was the cause just because it was recent.

The actual mechanism is `uv`'s editable-install metadata being a property
of the **venv**, not of either source checkout: an editable install is
just a path pointer recorded in site-packages, and `uv sync`/`uv run`
resolves and rewrites that pointer relative to whatever directory the
command was invoked from. Two clones sharing one venv (by directory
junction, deliberately, to skip a second multi-gigabyte install) means
that pointer is a single mutable resource contended by both clones' own
tooling, with no isolation and no warning when one clone's routine `uv
sync` silently invalidates the other's.

## What transfers

- **Any workflow sharing one venv across multiple checkouts of the same
  editable package is exposed to this** — not ConvoBox- or uv-specific.
  pip's own editable installs (`.egg-link` / `__editable__.*.pth`) have
  the same single-pointer-per-venv property.
- **The diagnostic is cheap and unambiguous**: `python -c "import
  <pkg>; print(<pkg>.__file__)"` from inside the checkout you think you're
  testing. If it doesn't resolve under that checkout's own path, that's
  the whole bug, before looking at anything else.
- **The fix is a full reinstall targeting the checkout you actually want**,
  not a partial sync: `uv cache clean <pkg> && uv sync
  --reinstall-package <pkg> --no-cache` from inside that checkout.
- **Rule out recent code changes in the affected area first, but don't
  stop there** — a real regression and an environment-resolution problem
  can look identical from the symptom alone ("a feature stopped working"),
  and only checking `__file__` distinguishes them cheaply.
