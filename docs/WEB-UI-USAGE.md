# Web UI usage

An optional, local-only browser view of a live ConvoBox session: the
transcript/tool-call/response stream as it happens, plus (if you opt in)
persisted history you can browse or export after the fact. Off by default —
nothing here changes how ConvoBox behaves unless you turn it on.

See [WEB-UI-ARCHITECTURE.md](WEB-UI-ARCHITECTURE.md) for the full design and
build history; this page is the short "how do I actually use it" version.

## Enable it

Two ways, same effect:

```bash
# One-off, this run only:
python scripts/run_convobox.py --web

# Or persistently, in convobox.yaml:
web:
  enabled: true
```

Then open the address it logs (default `http://127.0.0.1:5173`) in a
browser. That's the whole install step — no separate frontend build, no
`npm install`. The page is a single static file
(`src/convobox/web/static/index.html`) served by the same process, and it
ships inside the `convobox` package itself (verified in the built wheel,
not just source).

`--web`/`web.enabled` requires the optional **web** extra:

```bash
uv sync --extra web        # or: pip install -e ".[web]"
```

Everyone who doesn't use the web UI never pays for this dependency —
`fastapi`/`uvicorn` are only imported at all once `web.enabled` is true,
with a clear error telling you to install the extra if you forgot.

## What you see

- **Live events** as they happen: your transcripts, the backend's
  responses, tool calls/results, pending approval requests. Streamed over
  Server-Sent Events, so the page updates itself — no refresh needed, and
  it reconnects automatically if the connection drops.
- **Recent history**, loaded once on page open, for whichever session is
  most active.
- A session picker if more than one session's history exists.

- **Approving or denying a pending tool call** from the browser: an
  `APPROVAL_REQUEST` bubble gets real Approve/Deny/Explain buttons, wired to
  the same `ApprovalPromptGate`/`Orchestrator.resolve_pending_approval` path
  a spoken approval phrase answers. Voice and the browser can both answer a
  pending request; whichever gets there first wins, and the other is told
  it's already resolved.
- **A Quit button** ends the whole session (mic loop, backend, and the web
  server itself), not just one pending decision — arms on first click,
  fires on a second click within a few seconds, auto-disarms otherwise.
- **Editing `convobox.yaml` settings** from the browser (`GET /api/settings`,
  `POST /api/settings/schema`/`/validate`/`/save`/`/test`): the same
  edit/validate/save/test contract as `scripts/settings_tui.py`, reusing
  that file's validation and save logic directly rather than a second copy.
  Like the TUI, there is no hot-reload — a save writes `convobox.yaml`
  (with a timestamped backup) but only takes effect on the next restart.

Multiple simultaneous browser tabs do all correctly receive the same live
stream, though (each gets its own server-side subscription).

## Persisting history to disk

By default, turning the web UI on gets you the **live view only** — nothing
is written to disk, and closing the browser (or ConvoBox itself) loses that
session's event history for good. To keep it across restarts:

```yaml
web:
  enabled: true
  history_tracking_enabled: true   # opt-in, separate from `enabled`
  history_dir: .convobox-history   # default; gitignored by convention
```

This is a deliberately separate opt-in from `web.enabled` — viewing a live
session and persisting transcripts/tool-call history to a SQLite file on
disk are different privacy decisions, and turning on the first must never
silently do the second. With `history_tracking_enabled` off, `/api/sessions`
and `/api/sessions/{id}/events` (the history-browsing endpoints) just
honestly report nothing — the live stream still works either way.

The database (`events.db` under `history_dir`) is created `chmod 600`
(owner-readable only) on first write.

## Security posture — read this before changing `bind_address`

This server has **no authentication**. The entire model is: it's bound to
your own machine's loopback address, so only processes on that machine can
reach it. That is the whole security boundary.

This used to be a read-only guarantee (anyone who could reach the port got
the same *view* you do, but couldn't act). That's no longer true: approving
a pending tool call, quitting the whole session, and editing
`convobox.yaml` settings are all real mutations reachable over this same
no-auth loopback trust model. Each was a deliberate, discussed decision to
extend that trust model from view-only to a real control surface — not a
silent scope creep — but the practical upshot is the same: anything that
can reach `127.0.0.1:<port>` on this machine can now approve/deny tool
calls, end your session, or rewrite your config, with no login screen.
Don't widen `bind_address` beyond loopback without accounting for that.

- `web.bind_address` defaults to `127.0.0.1` and is validated: a specific
  non-loopback address (a real LAN IP, say) is rejected outright, since
  that would expose an unauthenticated view of live transcripts and tool
  calls to your network with no login screen. `0.0.0.0` is still allowed,
  but only as an explicit, deliberate choice (e.g. reaching it from another
  device on the same LAN) — if you set it, you're accepting that anyone who
  can reach that address gets the same view you do.
- Don't put this behind a public-facing reverse proxy without adding real
  authentication in front of it first. Nothing here was built with that
  threat model in mind.
- All event content (transcripts, tool output, backend responses) is
  rendered client-side via `textContent`, never `innerHTML` — untrusted
  content is never interpreted as HTML/script, even though the operator is
  also the only person who can reach the page under the intended threat
  model.

## Exporting a session

```
GET /api/sessions/{session_id}/export
```

Downloads that session's full event history as JSON (only meaningful with
`history_tracking_enabled`, since otherwise nothing is stored). Useful for
sharing a session log without giving someone live access to the server
itself — review the contents first, the same way you would before sharing
any transcript.

## Troubleshooting

- **"ImportError: the 'web' extra isn't installed."** Run `uv sync --extra
  web` (or `pip install -e ".[web]"`), then retry.
- **Nothing shows up in the browser.** Confirm `web.enabled` is actually
  true for the session you're looking at (the startup log line says `web UI
  listening on http://...` when it's really on) and that you're pointed at
  the right port — `web.port` defaults to `5173` but is yours to change.
- **History is empty even though I've been talking to it.** Check
  `web.history_tracking_enabled` — the live view works either way, but
  `/api/sessions` only ever shows something once that's on.
