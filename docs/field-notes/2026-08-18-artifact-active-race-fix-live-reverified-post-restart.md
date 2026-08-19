---
title: PR #302's get_shown_artifact race fix (server-side Python, the one item its own test plan left unchecked pending a UAT process restart) is now live-reverified -- 5/5 clean across three separate runs against a genuinely fresh process on current main, real concurrent HTTP requests, no TestClient shortcuts
status: validated-live
date: 2026-08-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ c8db010 (post-#306), macOS Darwin, real uvicorn server (not FastAPI TestClient), httpx.AsyncClient real network round-trips
evidence:
  - Recreated a real, standalone ConvoBox web server (`convobox.web.app.create_app()` with an in-memory HistoryDB and a temp working_dir) and ran it under real uvicorn in a background thread -- not FastAPI's synchronous TestClient, which the existing unit tests already use and which doesn't exercise real asyncio scheduling gaps the way an actual HTTP round-trip does.
  - Five scenarios hit via httpx.AsyncClient over real HTTP: (1) three genuinely concurrent POST /api/artifacts/active calls (asyncio.gather, real network round-trips) -- highest sequence number must win regardless of response-arrival order; (2) a lower (stale) sequence number's POST arriving strictly AFTER a higher one -- must be ignored, not overwrite; (3) the exact same sequence number posted twice (idempotent retry) -- must not error or flip state; (4) path=None (pane closed) with a fresh sequence -- must clear the active path; (5) 20 genuinely concurrent POSTs with increasing sequence numbers (300-319) -- the highest (319) must win under real stress.
  - First run failed 4/5 for a confounding reason unrelated to the fix itself: the test script wasn't sending the `X-ConvoBox-Client` header the app's own CSRF guard (`src/convobox/web/app.py`, added 2026-08-08 for GitHub issue #235) requires on every mutating request -- every POST silently 403'd, and the route's own defense-in-depth (treating any unresolvable state as "nothing confidently known" rather than surfacing an error) meant the failure looked identical to a real race-condition failure until the actual HTTP status code was inspected directly.
  - After adding the header: 5/5 clean, reproduced identically across 3 separate full runs of the same fresh-process-plus-live-HTTP harness.
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked "any items you can test or need to test before marking rc3?" then "please test what you can automatedly on this mac")
    - Claude Code (Anthropic claude-sonnet-5) -- test design, live re-verification, debugging, writing
  org: https://legionforge.org
  created: 2026-08-18T00:00:00-05:00
  revised: 2026-08-18T00:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# PR #302's race fix is live-reverified, closing its own unchecked test-plan box

**Context.** PR #302 fixed a real live UAT finding -- `get_shown_artifact`
intermittently reporting the previous artifact tab right after a fast
switch, "works on the 2nd try, then the 3rd try." The fix (a monotonic
sequence-number guard on `POST /api/artifacts/active`) was unit-tested
(49 passed) and merged, but the PR's own test plan explicitly left one
box unchecked: "Full live re-verification pending next UAT process
restart -- this is server-side Python, so the already-running session
has the old module loaded in memory." That gap was still open when this
Mac session picked up the 2026-08-18 handoff.

## What was actually tested, and why it's a genuinely different check than the existing unit tests

The existing `tests/test_web_artifacts.py` suite uses FastAPI's
`TestClient`, which runs requests synchronously against the app's ASGI
interface directly -- no real network stack, no real asyncio event-loop
interleaving between concurrent requests. The exact bug this PR fixed
(`Starlette awaits body-parsing before the route's own code runs, [...]
enough of a scheduling gap for two concurrent requests to complete out
of order`) is precisely the kind of race that a synchronous test
harness can't reproduce even in principle -- it depends on genuine
concurrent I/O scheduling. This round built a standalone server (real
`uvicorn`, backgrounded in a thread) and hit it over real HTTP with
`httpx.AsyncClient`, using `asyncio.gather()` to fire genuinely
concurrent requests -- a meaningfully different, closer-to-real check
than what CI already runs.

## A confound caught before it was mistaken for a real finding

The first run failed 4 of 5 scenarios. Before concluding the fix
didn't hold, the actual HTTP response was inspected directly (status
code + body), which immediately showed `403 missing required header`
-- an unrelated CSRF guard (`X-ConvoBox-Client`, added for a different
GitHub issue entirely) that the test script simply wasn't sending. The
route's own error handling treats an unresolvable/failed report as
"nothing confidently known" rather than surfacing the failure loudly
(a deliberate design choice, documented inline in `artifacts.py`, so a
stale or racy report never breaks the UI) -- which meant this confound
produced output that looked EXACTLY like the race condition being
untested, not obviously a header problem. Only checking the raw
response, not just the final state, distinguished the two.

## Result

5/5 clean, reproduced identically across three separate full runs. The
sequence-number guard works exactly as designed under genuine
concurrent load: highest sequence wins regardless of arrival order,
stale (lower) sequences are ignored regardless of arrival order,
duplicate sequences are idempotent, and the "closed" (path=None) case
still applies correctly when its own sequence is fresher than whatever
came before.

## Why this matters

This closes the one specifically-flagged, unchecked verification gap
remaining from the 2026-08-17/18 UAT round before this codebase would
be ready for an rc3 tag on this specific item -- not by re-running the
existing (already-passing) unit tests, but by exercising the actual
mechanism class (real concurrent HTTP scheduling) those tests can't
reach.

## What transfers

- **A test harness silently producing the "expected failure shape"
  when it's actually broken for an unrelated reason is a real risk
  worth actively guarding against.** The CSRF-403 confound looked
  identical to "the race condition still reproduces" until the raw
  HTTP status was checked -- always inspect the actual response/
  exception when a live test's result matches what a real bug would
  produce, don't just trust the final-state assertion. (validated-live)
- **A route's own deliberate "swallow failures gracefully" design
  (correct for production, where a stale report shouldn't break the
  UI) can mask a TEST'S own setup bug identically to a real regression
  when testing that same route.** Worth remembering when live-testing
  any endpoint with this kind of defense-in-depth error handling.
  (validated-live)
- **A real uvicorn server + real HTTP client is a meaningfully
  different (and sometimes necessary) test tier from FastAPI's
  TestClient** for any fix whose bug depended on genuine async I/O
  scheduling -- TestClient's synchronous request handling cannot
  reproduce this class of race even in principle. (validated-live)

## Not done here

- No code changes -- this is a pure verification pass confirming
  already-shipped code behaves correctly under a stronger test than
  CI runs.
- Did not test this specific race through the actual browser frontend
  (real tab clicks triggering real `fetch()` calls) -- this tested the
  server-side guard directly over HTTP, which is the part CI/unit
  tests couldn't reach; the frontend's own JS behavior on rapid clicks
  was implicitly covered by the original live UAT finding that
  prompted #302 in the first place.
