# Dependency update policy

How to handle a Dependabot PR, or any manual version bump, so a merge
means "verified," not "probably fine." The goal (JP, 2026-09-13): be
aggressive about testing dependency changes so end users never have to
find out the hard way — every ConvoBox install carries whatever wasn't
caught here. For evaluating whether a *new* dependency belongs in this
project at all (license, maintenance health), see
[DEPENDENCY_LICENSE_AUDIT.md](DEPENDENCY_LICENSE_AUDIT.md) instead —
this file is about updating one already in the tree.

## The core rule

**A version constraint's bounds — floor AND ceiling — must never extend
past a version that has actually been installed and run against this
repo's real test suite.** Not "it's just a patch bump," not "that floor
is old so it must be fine," not "the changelog looks harmless." Every
bound is either the edge of what's been tested, or it's wrong by
construction.

This isn't hypothetical caution — it's what happened twice on the same
dependency in one week (`mcp`, PRs #386/#407/#408, worked example
below). Both a floor and a ceiling that looked reasonable turned out to
be untested and wrong in opposite directions.

## The checklist

When a Dependabot PR lands (or you're considering a manual bump):

**1. Check security advisories first, across the FULL range in play** —
current pin through the proposed target, not just the target itself.
An advisory can be the one thing that justifies urgency ahead of full
verification.

```bash
# GitHub's own advisory DB, by package
gh api graphql -f query='
{
  securityVulnerabilities(ecosystem: PIP, package: "<name>", first: 20) {
    nodes {
      advisory { summary severity publishedAt }
      vulnerableVersionRange
      firstPatchedVersion { identifier }
    }
  }
}'

# OSV.dev, independent source, cross-check against the above
curl -s "https://api.osv.dev/v1/query" -X POST \
  -d '{"package":{"name":"<name>","ecosystem":"PyPI"}}' | python3 -m json.tool
```

If every known advisory is already fixed below your current floor,
there's no security pressure to move faster than full verification
below can happen on its own schedule.

**2. Read the actual release notes for every version between current and
target** — not just the newest one. An intermediate version can carry
its own behavior change the final version's own notes never re-mention.

**3. Install the target version into an isolated scratch venv** (never
the real dev venv — that's what makes this safe to do without touching
anything real) and run the tests that actually exercise the dependency,
then the full suite:

```bash
SCRATCH=/tmp/dep-check-$$
uv venv "$SCRATCH/.venv" -q --python 3.12
uv pip install --python "$SCRATCH/.venv/bin/python" -q -e ".[<relevant-extras>]"
uv pip install --python "$SCRATCH/.venv/bin/python" -q "<name>==<target>"
"$SCRATCH/.venv/bin/python" -m pytest tests/test_<relevant>.py -q
# then, in the real repo, after actually bumping pyproject.toml + uv lock:
uv run pytest -q && uv run ruff check . && uv run mypy src/convobox scripts
rm -rf "$SCRATCH"
```

**4. If something fails, don't just decline and stay pinned forever.**
Figure out which of two cases it is:

- **A real upstream break, nothing to fix on our side** — decline the
  PR with a comment citing the specific failure and version(s) tested,
  and leave the constraint as-is. Re-test the next version Dependabot
  proposes; don't assume it's fixed without checking.
- **Our own code was relying on undocumented/incorrect behavior the
  new version correctly stopped providing** — fix our code, then
  re-verify against the *entire* range (old versions too, to confirm no
  regression) before widening the constraint. This was the `mcp`
  case: the dependency's new stricter default was reasonable; our tool
  was raising the wrong exception type for it.

**5. If it passes clean, widen the constraint to exactly the tested
version** — never round up past it, never leave slack "just in case."
`<2.3` because `2.2.0` was the newest version that actually got
installed and tested, not because `2.3` seemed like a safe guess.

**6. Leave a paper trail at the constraint itself**, not just in the PR
description. A comment in `pyproject.toml` next to the version pin
should say what was tested, what (if anything) broke, and the date —
so the next Dependabot PR for the same package doesn't require
re-deriving all of this from git history.

## Anti-patterns

- Treating "patch/minor version" as a substitute for installing it.
- Widening a ceiling because a PR asks for it, without actually running
  the target version against real tests.
- Leaving a floor unexamined because it's old — old isn't the same as
  tested. (`mcp>=1.0` had never once been verified; `mcp==1.0.0` didn't
  even have the module the code imports.)
- Declining a PR with no comment — the next automated PR for the same
  package will ask the identical question with no memory of the answer.
- Bumping for a security advisory without checking that the advisory's
  *fixed* version is actually the one landing in the lockfile.

## Worked example: `mcp`, PRs #386 → #407 → #408

1. **#386** proposed `mcp>=1.0,<2.1` → `>=1.0,<2.2`. Declined: `2.1.0`/
   `2.1.1` both live-tested broken (`ToolError` message-wrapping change
   hid our tool's real error text from callers, failing 3 tests). While
   investigating, also caught that the *floor* (`>=1.0`) had never been
   tested at all and was itself broken (`mcp==1.0.0` predates the module
   this code imports) — fixed floor to `>=2.0` in a follow-up PR.
2. **#407** proposed widening further to `<2.3` (which would've also
   permitted the still-broken `2.1.x` plus the new `2.2.0`). Live-tested
   `2.2.0` — same 3 failures. Declined again, same reasoning.
3. **#408**: rather than stay pinned indefinitely, root-caused *why*
   `2.1.0`+ broke those 3 tests — mcp's own new (reasonable) design:
   any exception besides its own `ToolError` is now treated as an
   unanticipated crash and its message hidden from the caller. Our tool
   raised plain `ValueError` for deliberate validation failures instead
   of mcp's sanctioned `ToolError`. Fixed the tool, then re-verified
   `2.0.0`/`2.0.1`/`2.1.0`/`2.1.1`/`2.2.0` individually — all five now
   pass. Ceiling widened to `<2.3` for real this time, backed by five
   individually-verified versions instead of zero.

The lesson isn't "always fix your own code" — sometimes step 4's answer
really is "upstream broke, wait." It's that declining a PR is a
checkpoint to investigate from, not a place to stop.
