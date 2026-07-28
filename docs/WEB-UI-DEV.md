# Web UI — contributor notes

For end-user instructions see [WEB-UI-USAGE.md](WEB-UI-USAGE.md). For the
original design and a running account of what shipped vs. deviated from
that design, see [WEB-UI-ARCHITECTURE.md](WEB-UI-ARCHITECTURE.md) — this
page is the shorter "where's the code, how do I test it, how do I extend
it" version for someone about to touch `src/convobox/web/`.

## Layout

```
src/convobox/web/
├── history.py       HistoryDB -- SQLite storage, no fastapi import at all
├── stream.py        EventBroadcaster -- in-memory SSE fan-out, stdlib only
├── bridge.py        WebEventForwarder + WebApprovalBridge -- Orchestrator
│                    on_event hook and the approval-gate glue (see below)
├── settings_api.py  add_settings_routes() -- GET/POST /api/settings/* ,
│                    reusing scripts/settings_tui.py's own validate/save/
│                    test contract directly, not a second copy of it
├── app.py           create_app() -- the FastAPI app, routes, SSE endpoint
└── static/
    └── index.html   the whole frontend: one file, inline CSS/JS, no build step
```

`history.py`/`stream.py`/`bridge.py` deliberately have **no dependency on
fastapi/uvicorn** — only `app.py` does. That split is why `web.enabled`
without the `web` extra installed still fails cleanly with one clear
`ImportError` at the single point that needs it
(`scripts/run_convobox.py`'s lazy `import uvicorn` /
`from convobox.web.app import create_app`, inside the `if
config.web.enabled:` branch), rather than failing to import the whole
`convobox.web` package for everyone.

## How a real event gets from the mic to the browser

Backend events (responses, tool calls) and the user's OWN transcripts are
two genuinely separate paths in — `Orchestrator.on_event` only ever sees
the former (a transcript is what PROMPTS a backend event, not one itself),
so there are two entry points into `WebEventForwarder`:

```
Orchestrator._on_event(event)                        run_convobox.py's 3 call sites for
  -> on_event hook (run_convobox.py's _dispatch_event)  orchestrator.handle_transcript(text):
       -> _on_backend_event(...)  # TUI/logging          mic loop, --text mode, queued interjection
       -> web_forwarder(event)  # WebEventForwarder.__call__
                                                        -> web_forwarder.forward_transcript(text)
            (both, if web.enabled)                          (if web.enabled)
                 |                                                |
                 v                                                v
       HistoryDB.append_event(...)  # only if history_tracking_enabled, both paths
       EventBroadcaster.broadcast(payload)  # always, if web.enabled, both paths
                 -> every subscribed browser's asyncio.Queue
                      -> GET /api/events/stream's sse_lines() generator
                           -> `data: {...}\n\n` over the open SSE connection
```

`EventBroadcaster` carries plain JSON-able dicts, not `BackendEvent`
objects — a transcript has no `BackendEvent` representation (no backend
adapter ever emits one), so whoever is broadcasting (`WebEventForwarder`)
shapes the payload before handing it to the broadcaster, which stays
opaque to what it's carrying.

Found live while building a demo of this feature (2026-07-26): the
original wiring only ever called `WebEventForwarder.__call__`
(BackendEvents), never anything for the user's own recognized speech —
so a captured session showed backend responses appearing with no visible
prompt. `forward_transcript()` closes that gap.

`WebEventForwarder` is the entire integration surface — `Orchestrator`
itself was never modified to support this; it already had a generic
`on_event: Callable[[BackendEvent], None] | None` hook (built earlier for
the TUI), and `WebEventForwarder` just plugs into that same hook alongside
the existing one. If you're adding a NEW thing that needs to observe every
backend event, that hook is almost certainly what you want too — don't add
a second one.

## Running it locally without a real backend

`tests/fake_claude_cli.py` speaks the real Claude Code stream-json protocol
over real pipes and is the fastest way to smoke-test end to end without a
live coding-agent CLI:

```yaml
# scratch-convobox.yaml
backend:
  name: claude-code
  command: ["python", "tests/fake_claude_cli.py"]
web:
  enabled: true
  history_tracking_enabled: true
```

```bash
CONVOBOX_CONFIG=scratch-convobox.yaml python scripts/run_convobox.py --text "hello" --mute
```

Open the logged URL, or just `curl http://127.0.0.1:5173/health` while it's
running. `--mute` skips audio playback (TTS still synthesizes) so this
works headless/over SSH. This is the actual pattern used to live-verify
every slice of this feature while it was built — see the git history for
`feat(web):` commits, each of which describes exactly what was smoke-tested
this way.

## Testing

- `tests/test_web_history.py` — `HistoryDB` in isolation (SQLite only, no
  FastAPI).
- `tests/test_web_bridge.py` — `WebEventForwarder` in isolation, plus one
  integration test that builds a real `Orchestrator` with `WebEventForwarder`
  as its `on_event` hook and drives a real event through
  `_consume_events()` — proving the two actually compose, not just that
  each works alone.
- `tests/test_web_app.py` — the FastAPI app: REST endpoints via
  `fastapi.testclient.TestClient`, CORS behavior, the static-mount-doesn't-
  shadow-the-API regression test, and the SSE stream.

**The SSE stream is NOT tested through `TestClient`/`httpx.ASGITransport`.**
`ASGITransport.handle_async_request` awaits the whole ASGI call to
completion before returning anything — confirmed live while building this
(even the response *headers* never arrived in a test using it) — so it
cannot drive a response body that only ends on client disconnect. Two
separate techniques cover this instead:
1. `sse_lines()` (the wire-format generator itself) is unit-tested directly
   as a plain async generator over a queue — bounded, no HTTP involved.
2. The actual route is tested by spinning up a **real** `uvicorn.Server`
   bound to `127.0.0.1:0` and connecting a real `httpx.AsyncClient` over a
   real socket (see `running_server` fixture in `test_web_app.py`).

If you add a new streaming endpoint, follow pattern 2, not `TestClient`.

## Extending the frontend

`static/index.html` is deliberately one file with no build step. If you're
adding a new event type to render, or a new pane, the pattern to follow:
- New content goes through `renderEvent()`, and any user/backend-controlled
  string goes through `textContent`, never `innerHTML`. This is not
  paranoia for its own sake — see WEB-UI-USAGE.md's security section: the
  whole point is that server-controlled event content can never become
  executable markup, even though this server has no auth of its own.
- Don't reach for React/Vite/npm unless a specific feature genuinely can't
  be done in plain JS reasonably (e.g. something needing real component
  state management at a complexity this file can no longer hold clearly).
  That's a real threshold, not a soft preference — ask before crossing it,
  since it changes the project's dependency/build story for every
  contributor, not just this feature.

## Known gaps (as of this writing)

- No remote-access story (auth, TLS) — see WEB-UI-USAGE.md's security
  section. Out of scope until there's an actual threat model calling for
  it.
- Settings edits (`/api/settings/save`) always target whatever
  `config_path` the running server was started with — there is no
  hot-reload, so a save only takes effect on the next `run_convobox.py`
  restart. The API surfaces this the same way the TUI does (see
  `settings_api.py`'s module docstring); a frontend built against it needs
  its own "restart needed" messaging rather than implying the change is
  live.
