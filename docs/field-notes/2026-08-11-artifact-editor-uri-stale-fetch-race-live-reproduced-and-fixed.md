---
title: "Open in editor" pointed at the wrong file due to an unguarded stale fetch on a shared DOM node -- live-reproduced and fixed
status: validated-live
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 235f30d (PR #249 merged); web UI (src/convobox/web/static/index.html); backend=codex, permission_mode=permissive
evidence:
  - Real UAT session, D:/LegionForge/convobox-UAT (git worktree), --web, real codex backend, working_dir D:/LegionForge/_artifact-test-scratch
  - Browser-side instrumentation (window.fetch monkey-patch + href-write logger) run against both the pre-fix and post-fix code, same harness
  - src/convobox/web/static/index.html renderArtifact() (before/after)
  - PR #249 (2026-08-10, root-cause correction for a related but distinct symptom)
  - docs/KNOWN-ISSUES.md, "Open in editor" section
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for #249 to be UAT'd before merge, watched VS Code directly to confirm/deny each reproduction, decided to fix now with a follow-up PR)
    - Claude Code (Anthropic claude-sonnet-5) -- code reading, live reproduction harness design and execution, fix implementation, verification, writing
  org: https://legionforge.org
  created: 2026-08-11T23:35:00-05:00
  revised: 2026-08-11T23:35:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# "Open in editor" pointed at the wrong file due to an unguarded stale fetch on a shared DOM node

**Context for outsiders.** ConvoBox's web UI shows files a coding-agent
backend creates or edits as an "artifact" pane, with an "Open in editor"
link that hands the file off to VS Code via a `vscode://file/` URI. This
note documents a real race condition in that link -- live-reproduced on
the running app, not just diagnosed by reading code -- and the fix.

## Problem

Clicking "Open in editor" could open a *different* file than the one
currently shown in the artifact pane. First live-hit 2026-08-09 during a
real UAT session; the initial diagnosis (PR #249) turned out to explain a
different, disproven symptom (a Windows backslash in the URI -- kept as a
portability fix, but JP directly proved VS Code tolerates it fine). The
real mechanism sat elsewhere and remained an unconfirmed hypothesis until
this session.

## Evidence

`renderArtifact(path)` in `index.html` does two independent async things
per artifact event: renders the body content, and separately fetches
`/api/artifacts/{path}/editor-uri` to populate the "Open in editor" link.
The link's target, `artifactEditorLink`, is a single DOM element reused
across every render -- there was no check anywhere that a given
editor-uri response still belonged to the *current* render before it was
applied.

Live reproduction harness: with the browser tab open against a real
running session, `window.fetch` was monkey-patched to delay the *first*
of two real `editor-uri` calls by 12 seconds while leaving the second
untouched, then two real, back-to-back file edits were driven through
the web UI's own text composer (a genuine codex backend, not a mock).

Pre-fix run:

```
t=901343  render #1 (test.md) issues editor-uri fetch, delayed 12000ms
t=907149  render #2 (test.js) issues editor-uri fetch, delay 0
t=907163  render #2 resolves -> href correctly set to test.js
t=913351  render #1's delayed fetch finally resolves -> href WRONGLY
          overwritten to test.md
```

Final observed state: artifact pane title/content = `test.js` (correct,
most recent edit), `artifactEditorLink.href` =
`vscode://file/.../test.md` (wrong, stale). Exactly the originally
reported symptom, reproduced against the real endpoint.

One earlier run in the same session reproduced the identical wrong final
state through a *second real, undelayed* `test.md` edit arriving after
the `test.js` edits, rather than primarily through the injected delay --
confirming the race is reachable under ordinary backend/tool-call timing,
not only a contrived artificial ordering.

Post-fix run, identical harness and delay:

```
t=901343-ish  render #1 (test.md) issues editor-uri fetch, delayed 12000ms
render #2 (test.js) issues + resolves -> href set to test.js (ONE write)
render #1's delayed fetch resolves 6s later -> discarded, no href write
```

`hrefLog` (an instrumented property-descriptor override on `.href`)
recorded exactly one write in the post-fix run, versus two (including the
wrong one) pre-fix -- direct, mechanical proof the stale response was
discarded rather than merely "probably fine now."

## Mechanism

**Ruled out** (PR #249, 2026-08-10): backslash-vs-forward-slash URI
formatting. Directly disproven by JP testing the exact unpatched,
still-running server against a real artifact -- VS Code opened the
correct file despite the backslash URI. The `as_posix()` change was kept
as a portability improvement regardless.

**Corrected claim from the same PR's writeup**: it stated the main
content render "already tracks `artifactLoadCounter` specifically to
prevent a stale response from clobbering a newer one." Rechecking the
code: `artifactLoadCounter` was only ever used as a cache-busting query
parameter on the body-content URL, never compared against anywhere --
there was no staleness check for *either* the body content or the editor
link. The body content happens to be race-safe for a different, unrelated
reason: each render creates fresh DOM nodes (`artifactBodyEl.innerHTML`
is cleared, then a new `<img>`/`<iframe>`/`<pre>` is appended), so a slow
stale response from an old render targets a node that's either already
detached or gets replaced outright. `artifactEditorLink` has no such
protection because it's a single node reused across every render --
exactly why it was the one that broke.

## What transfers

- **A shared/singleton DOM target for an async result needs an explicit
  staleness check; per-render-fresh DOM nodes get it "for free" as a side
  effect of how they're constructed, not because anyone designed it in.**
  When auditing a page for this class of race, the fresh-node case being
  safe can create false confidence that a nearby singleton case is also
  covered by the same mechanism -- verify each target independently.
  (validated-live)
- **Injected `setTimeout` delays in application JS are a workable way to
  force a specific resolve-order for a live reproduction, but only if the
  delay is verified longer than the real-world gap between the two events
  being raced.** An earlier attempt in this repo (2026-08-09/10) used a
  ~50ms artificial gap and was swamped by Chrome's own background-tab
  throttling. This session's first attempt at 5s was *also* too short --
  the real gap between two sequential codex tool-call edits turned out to
  be ~5.7s on its own, so the "delayed" response resolved before the
  second event even fired. 12s was the delay that finally guaranteed
  overlap. (validated-live)
- **Verify the browser tab actually re-executed the code under test
  before trusting a "still broken" result.** The first post-fix
  verification attempt appeared to still reproduce the bug; the served
  HTTP response for `/` did contain the fix, but the tab's already-loaded
  DOM/script state did not, because navigating to the URL the tab was
  already on did not force a re-execution. Checking `document.querySelectorAll('script')`
  content directly (not a fresh `fetch('/')`) caught this before it
  produced a false "fix doesn't work" conclusion. A hard reload
  (Ctrl+Shift+R) resolved it. (validated-live)

## Fix

`renderArtifact()` now captures `artifactLoadCounter` into a local
`loadId` at the top of the function; the editor-uri fetch's `.then()`
callback checks `loadId !== artifactLoadCounter` and discards the
response if a newer render has started since the fetch was issued.
