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

## 11. Long-lived branches: merge yourself into main, prefer that over
merging main into yourself

A branch that's going to live longer than a day or two should land on
`main` in small increments, not accumulate repeated
`Merge origin/main into <branch>` commits while staying parked. A PR that
reviews and merges cleanly is usually a sign the underlying branch is
ready for `main` too — if a merged PR's base isn't `main`, ask why before
building anything else on top of that assumption.

*Incident (issue #126): `wip/approval-echotailguard` accumulated
merge-forwards for weeks instead of landing; three real, reviewed,
CI-passed PRs (#109, #112, #116) merged against that branch, not `main`,
making a shipped-feeling feature invisible to anything working from
`main` — including a fresh session that built an equivalent feature for a
different backend, unaware the first one existed, until a sync surfaced a
6-file conflict.*

## 12. Close out staged-but-uncommitted structural changes same-session

A structural change sitting staged-but-uncommitted (a file removed from
tracking, a `.gitignore` edit, a rename) is easy to forget and hard to
tell apart later from "in-progress work I still need." Commit it or
revert it before moving on to something else — don't let it carry
forward as ambient working-tree state.

*Incident (issue #126): a `CLAUDE.md` swap plus a `.gitignore` edit
excluding it sat staged-but-uncommitted in the UAT checkout for a while,
until it finally blocked a sync.*

## 13. Working across multiple checkouts sharing a venv: verify which one
you're actually testing

Before treating "a feature that should work doesn't" as a regression, run
`python scripts/check_venv_identity.py` from the checkout you meant to
test. A venv shared across checkouts (e.g. this repo's dev/UAT split, a
directory junction to skip installing the audio/ML stack twice) silently
repoints its editable `convobox` install to whichever checkout last ran
`uv sync`/`uv run` — the other checkout's own scripts keep running, but
every `import convobox` inside them resolves against the wrong tree, with
no error at import time.

*Incident (issue #126, docs/field-notes/2026-07-22-shared-venv-editable-
install-cross-contamination.md): this bit the project 3+ times in one
evening — a config field that "should" exist but didn't, and a live "I
can't scroll anymore" report that turned out to be the UAT checkout's
older script running against dev's newer library.*

## 14. After any `uv sync`, verify the venv actually works — don't trust
a clean exit

Run `python scripts/check_venv_extras.py` after combining or changing
extras. `uv sync` only installs the extras named on that exact command
and silently removes others from a previous run; rarer, an interrupted
sync can leave one package's dist-info hollow while later syncs keep
reporting success because they see the right version already
"installed" and skip it.

*Incident (docs/KNOWN-ISSUES.md, "An interrupted `uv sync` can leave one
package's metadata hollow..."): `aec-audio-processing`'s dist-info lost
its `RECORD`/`METADATA` mid-sync on Helios/Windows 2026-09-06 — the
package still imported without error (an empty namespace package), and
`uv sync --extra aec` kept reporting success afterward. Nothing
surfaced until `--web` actually tried to construct an `EchoCanceller`,
hours later.*

## 15. Import each module one way per file

Import every module once, at the top of the file, in one style:

- Default: `from x import name`.
- If code needs the module object itself — to monkeypatch an attribute
  (`monkeypatch.setattr(mod, "_CONST", ...)`), or to inspect it — import
  the module once at the top (`import x as x_mod`) and reach the names
  through it (`x_mod.Name`). Do not *also* `from x import Name` in the
  same file.
- No function-local `import x` for a module the file already imports.
  Function-local imports (Ruff `PLC0415`, about 200 existing sites) are for
  a stated reason (a heavy dependency loaded lazily, a circular import)
  and new ones get a one-line comment saying so.
- Patching: `monkeypatch.setattr(mod, "X", fake)` replaces the name in
  `mod`'s own namespace, so code *inside* `mod` sees the fake however the
  test imported anything. It does **not** reach another module that did
  `from mod import X` (that module holds its own binding), so patch the
  namespace where the name is looked up. Mixing import styles in a test
  file does not by itself make a patch miss; this rule is about
  consistency and the bot flag, not correctness.

To check a file you touched, run the AST scan sketched here (modules
imported both ways in one file):

```python
import ast, sys
t = ast.parse(open(sys.argv[1], encoding="utf-8").read())
plain = {a.name for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names}
frm = {n.module for n in ast.walk(t) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
print(sorted(plain & frm))
```

*Incident (PR #447, 2026-11-22): `tests/test_approval_plausibility.py`
imported `convobox.safeword.detector` at the top (`from ... import
SafewordDetector`) and again inside one test (`import ... as sw`) to
inspect the module. The code-quality bot flagged it, and because `main`
requires conversation resolution the thread blocked the merge until a
fix commit landed. The same flag had been raised and fixed earlier on
the same PR in `tests/test_approval_prompt_gate.py` (commit 785bca8, which
used `inspect.getmodule(Class)` to get the module without a second
import, a third acceptable fix). Existing code that predates this rule
(seven test files with mixed imports and about 200 function-local imports
as of 2026-11-22) is not rewritten wholesale; convert a file when you are
already editing it.*
