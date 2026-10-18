# Hand-off: Android Flutter spike against a ConvoBox web session

Audience: a developer, or a Claude session on a Windows machine, building a
minimal Android Flutter app in its **own repo**. This file is self-contained;
you do not need the ConvoBox source, only a running server (section 2).

Evidence labels used throughout:

- **[code-read]**: read in ConvoBox source (file:line given). Line numbers are
  for `src/convobox/web/app.py` on the branch this doc was written on; they
  drift, so grep for the function name if a line is off.
- **[live-run]**: observed by actually running `scripts/run_mobile_test_server.py`
  and curl on macOS, 2026-10-04. Nothing else in this doc was run on a phone.
- **[unverified]**: from general knowledge or a design doc; test it before relying on it.

Background decisions live in `docs/DECISIONS-mobile-companion-2026-10-04.md`
and `docs/DESIGN-mobile-remote.md` (they may sit on a different branch than
this file). Relevant summary: native Flutter app, Tailscale transport, visible
push to wake the phone, biometric-gated approval later (v2). This spike only
answers the three feasibility questions in section 6. It is not the product.

## 1. What to build (scope cap)

One screen with: server URL + token fields (stored securely), a connect
button, a live event list (SSE), a text box that POSTs to `/api/text`, and
Approve/Deny buttons. Plus the three experiments. No signing, no pairing
flow, no audio transport, no iOS. Throw the code away afterwards if you like;
the deliverable is the results table in section 8.

## 2. Server side: get something to talk to

On the ConvoBox host (not the phone):

```
python scripts/run_mobile_test_server.py --port 5173 --approval-every 60
```

It prints `web UI: open http://127.0.0.1:5173/?token=<token>&atoken=<token>`
and `token: <token>`. **[live-run]** It uses the real web API with a fake
backend (`tests/fake_claude_cli.py`) in a temp dir, no mic, no TTS, no real
agent; nothing is executed. Typed triggers via `POST /api/text` **[live-run]**:

| Text sent | Effect |
|---|---|
| `approval test` | raises a fake pending approval (SSE `approval_request`); nothing runs whichever way you answer |
| anything else | goes to the fake CLI, which replies `echo: <text>` (other trigger words are listed in `tests/fake_claude_cli.py`'s docstring) |
| (flag) `--approval-every N` | also raises a fake approval every N s while none is pending |

The token is a random per-run string (16 random bytes, hex). Treat it as a
password: do not paste it into issues, chat logs or commits.

## 3. Auth and request rules **[code-read]**

1. Every route under `/api/` needs the token (`app.py:224-235`). `/health` and
   the static page at `/` do not (`app.py:224`, `:291`).
2. Send `Authorization: Bearer <token>` on every request, including the SSE
   GET. That works on every ConvoBox version.
3. `?token=<token>` in the URL is also accepted. On the base commit of this
   branch it works for any method (`app.py:233`); a newer commit
   (`ce771c8`, on `feat/audio-source-sink`) restricts it to GET/HEAD and makes
   the compare constant-time. **Do not rely on it**: use the header (Dart's
   `http`/`dio` can set headers on a streamed GET, so the browser-EventSource
   limitation that motivated `?token=` does not apply to you).
4. **Every mutating request (POST/PUT/PATCH/DELETE) must also carry a header
   named `X-ConvoBox-Client`** with any value (`app.py:61-62`, check at
   `:270-275`; missing gives `403 missing required header`). This is a browser
   CSRF guard but it applies to native clients too. **[live-run]** POST without
   it returned 403; with it, 200. Notification-action handlers (T1) must send
   it as well.
5. Check order: the CSRF middleware is registered after the token middleware, so in
   Starlette it wraps it and runs first **[code-read: `app.py:163`, `:237`; inference
   from Starlette's ordering, not live-tested for the no-token-no-header case]**.
   A POST missing the header gets 403 even if the token is wrong; a GET with a wrong
   token gets 401 [live-run].
6. No TLS in ConvoBox itself and no Host-header check **[code-read]**. TLS comes from
   Tailscale Serve (section 4) or WireGuard's own encryption alone (plain
   `http://` over the tailnet is encrypted on the wire by Tailscale, but
   Android may block cleartext HTTP, see section 7).
7. CORS is pinned to loopback origins (`app.py:152-161`); irrelevant for a
   native app, which is not a browser origin.

## 4. Reaching the server over Tailscale **[unverified on the target phone]**

Preferred (real HTTPS, server stays on loopback):

```
# on the ConvoBox host
tailscale serve --bg --https=443 http://127.0.0.1:5173
# URL for the phone: https://<host>.<tailnet>.ts.net/
```

Alternative: run the test server with `--bind-address 0.0.0.0` and use
`http://<tailscale-ip>:5173` (accepted bind values are only loopback or
`0.0.0.0`, validated by `WebConfig`; **[code-read]** `config.py` WebConfig
`bind_address`). That is plain HTTP; see the cleartext note in section 7. On a
shared LAN, `0.0.0.0` also exposes the port off-tailnet, so prefer the Serve
route. Install Tailscale on the phone, sign in to the same tailnet, and confirm
`https://<host>.<tailnet>.ts.net/health` returns `{"status":"ok"}` in the phone
browser before debugging the app. Whether SSE survives the Serve proxy without
buffering is untested **[unverified]**; the server sends a `: heartbeat`
comment every 15 s **[code-read]** `app.py:87,112`.

## 5. HTTP API contract **[code-read]** (fields confirmed by [live-run] where noted)

Base: `https://<host>.<tailnet>.ts.net` (or `http://<ip>:5173`). "Auth" = the
`Authorization` header; "CSRF" = `X-ConvoBox-Client`. JSON requests also need
`Content-Type: application/json`.

| Route | Method | Auth | CSRF | Request body | Success response | Errors |
|---|---|---|---|---|---|---|
| `/health` | GET | no | no | none | `{"status":"ok"}` (`app.py:291-293`) [live-run] | none |
| `/api/config` | GET | yes | no | none | `{"user_color":..,"assistant_color":..,"user_name":..,"assistant_name":..}` (strings or null) (`:370-405`) [live-run]; a cheap "is my token valid" probe | 401 |
| `/api/text` | POST | yes | yes | `{"text": "<string>"}` (`:46-47`) | `{"accepted":true}` (`:353-368`) [live-run] | 400 empty text; 503 no live session |
| `/api/sessions/{id}/approval` | POST | yes | yes | `{"action":"approve"\|"deny"\|"explain"}` (`:38-39`) | approve/deny: `{"status":"approved"\|"denied","explanation":null}`; explain: `{"status":"pending","explanation":"<text or null>"}` (`:434-457`) [live-run for approve and explain] | 409 `{"detail":"no approval is currently pending"}` [live-run, also what a second tap gets]; 409 if the decision could not be delivered (answered by voice or timed out) |
| `/api/stop` | POST | yes | yes | none | `{"stopped":true}` (`:336-351`) [live-run] | 503 no live session |
| `/api/listening` | GET / POST | yes | POST yes | POST `{"action":"pause"\|"resume"}` | `{"is_paused":bool}` (`:314-334`) | POST 503 [live-run: always 503 on the test server, no mic loop] |
| `/api/sessions` | GET | yes | no | none | `{"sessions":[{"id":"..","last_activity":"<ISO>"}]}` (`:407-415`) [live-run] | |
| `/api/sessions/{id}/events` | GET | yes | no | query `limit` (1-1000, default 100), `offset` (>=0) | `{"events":[<history row>...]}` oldest first (`:417-427`; rows are `SELECT *` from the events table, `web/history.py:129-140`, field names [unverified] until you print one) | 422 bad query |
| `/api/sessions/{id}/export` | GET | yes | no | none | streamed JSON `{"session_id":..,"events":[..]}` (`:459-485`) | |
| `/api/events/stream` | GET | yes | no | none | SSE, see below (`:487-507`) | 401 |
| `/api/quit` | POST | yes | yes | none | `{"status":"quitting"}` (`:295-312`) | 503 if no quit handler; **do not call in the spike** |

Session id: there is one live session per server process. The
`{session_id}` in the approval path is **ignored** (`app.py:442`, `del session_id`);
send any non-empty string, ideally the id from `/api/sessions`.

Other routes exist (`/api/settings/*`, `/api/artifacts*`, `/api/upload`, `/mcp`)
and are out of scope for this spike. Do not call `/api/settings/save` or
`/api/quit` against a server you care about.

### SSE stream `GET /api/events/stream`

Response `Content-Type: text/event-stream`. Format **[code-read]**
(`app.py:90-116`; [live-run] captured below): each message is one line
`data: <json>` followed by a blank line; there are **no** `event:` or `id:`
fields, so no `Last-Event-ID` resume. An idle connection gets a comment line
`: heartbeat` every 15 s that a parser must ignore. A new subscriber gets **no
backlog**; catch up with `/api/sessions/{id}/events` after a reconnect.

JSON payload shapes, from a real run of the test server **[live-run]**:

```
data: {"type": "transcript", "content": "hello phone"}
data: {"type": "status", "status": "working", "detail": null}
data: {"type": "response", "content": "echo: hello phone", "tool": null, "tool_input": null, "tool_output": null, "artifact_path": null}
data: {"type": "done", "content": null, "tool": null, "tool_input": null, "tool_output": null, "artifact_path": null}
data: {"type": "status", "status": "listening", "detail": null}
data: {"type": "approval_request", "content": "Run shell command: ... (FAKE, never executed)", "tool": "Bash", "tool_input": "rm -rf /tmp/convobox-mobile-test", "tool_output": null, "artifact_path": null}
data: {"type": "approval_resolved", "approved": true}
```

Other `type` values the code can emit **[code-read, not seen live]**:
`tool_call`, `tool_result`, `error`, `artifact` (`web/bridge.py:35-43`, same
field set as `response`); `dropped` with `{"count": n}` when this subscriber's
200-slot queue overflowed and older events were discarded
(`web/stream.py:74-89`); `session_ended` (no other fields) right before the
server closes the stream on shutdown (`web/stream.py:113-135`). Unknown types
must be ignored, not crashed on. On `approval_request`, the pending request
is answered via the approval route above; an `approval_resolved` from
anywhere (voice, another client) means any notification you showed for it is
stale and should be dismissed. Two clients can race; the loser gets 409.

## 6. The three experiments (real Android phone, not an emulator)

Use a physical device. Record the exact model and Android version. Disable
battery optimization **off** for the baseline run, then repeat once with the
phone's default (restricted) battery policy, because OEM skins (Samsung,
Xiaomi, etc.) kill background work differently. Give the app notification
permission (Android 13+ asks at runtime).

### T1: notification action approves/denies while the phone is locked

Question: can a notification button POST `approve`/`deny` to the server with
the screen locked and the app not in the foreground, and can the app read its
stored credential at that moment?

Steps:
1. Store the server URL and token in secure storage (`flutter_secure_storage`
   or Android Keystore directly [unverified: package behavior while locked is
   exactly what this tests]). Run once with the default keystore settings
   and once with a key that requires user authentication, if you can set that
   up; record both.
2. Connect the app (foreground), trigger `approval test` (or wait for
   `--approval-every`), and have the app show a local notification with
   Approve and Deny actions when it sees `approval_request`.
3. Lock the phone and background/swipe away the app. Trigger the approval
   again from the host (`curl` with the Bearer and `X-ConvoBox-Client`
   headers, text `approval test`). Note: with the app backgrounded, SSE may
   already be dead; if so, use T3's push to raise the notification, or for
   this test only, post the local notification while the screen is still on,
   then lock the phone before tapping the action.
4. From the lock screen tap Approve. Check the server console and
   `GET /api/events/stream` (from your laptop) for `approval_resolved`.
   Repeat with Deny and with the app process killed.
5. Whether the action runs without unlocking is controlled by the Android
   notification action flag (`authenticationRequired` in
   `flutter_local_notifications`, [unverified]); test with it both true and false.

Measure: did the POST arrive (server console / `approval_resolved`), latency
from tap to server, did the credential read succeed while locked (yes/no and the
exception text if not), did the action work with the app process dead.

### T2: foreground-started audio/voice session keeps the microphone on lock

Question: if the app starts a microphone capture (Android foreground service of
type `microphone`, started while the app is visible), does it keep receiving
audio after the screen locks, and for how long?

Steps:
1. Start a foreground service (`FOREGROUND_SERVICE_MICROPHONE`, type
   `microphone`, persistent notification) and record from the mic, writing a
   level meter/RMS line to a file or logcat once a second (no need to send
   audio anywhere; the server has no audio endpoint).
2. Lock the screen and talk for 5 minutes, then check whether the RMS stays
   non-zero. Repeat with the app swiped from recents, and after 30 minutes.
3. Repeat with `RECORD_AUDIO` granted "only while using the app" vs "all the
   time" if the OS version offers it. Note the Android 14+ rule that the
   foreground service must start while the app is in the foreground
   [unverified; this is why the test starts it from a visible app].

Measure: audio still captured while locked (yes/no, how many minutes), does
the privacy mic indicator show, any silent zero-fill (a classic Android
failure: capture continues but returns zeros), and whether another app (a
call, voice assistant) steals the mic.

### T3: a visible push alert reaches the app while backgrounded

Question: with the app backgrounded and screen locked, does a visible alert
arrive, and can it be delivered without Google services?

ConvoBox has **no push sender today** (`sw.js` only caches the app shell,
`docs/DESIGN-mobile-remote.md`). For the spike, send the alert by hand from the
host. Test two paths:

A. **FCM** (needs Google Play services on the phone and a Firebase project of
   your own): send a notification-type message from the Firebase console
   to the app's token. Measure delivery time in each of: screen on, locked,
   Doze (leave the phone idle and unplugged 30+ minutes), app force-stopped.
B. **Without Google services** (the self-hosted preference in the decision
   doc), try one or both, each [unverified]:
   - **ntfy** (self-hosted or the public instance with a random topic): the
     ntfy Android app subscribes to a topic and shows alerts; you publish with
     `curl -d "msg" https://<ntfy-host>/<random-topic>`. Check it works over the
     tailnet to a self-hosted ntfy and whether it runs a foreground
     connection (it will show a persistent notification).
   - **UnifiedPush** with a distributor app (for example ntfy as the
     distributor) and the Flutter `unifiedpush` package.
   Record whether each path needs Play services, what permanent notification
   it shows, and delivery time in the same four states.

Measure: delivered or not, seconds from send to display, does tapping open
your app, does it survive Doze and a reboot without re-opening the app.

## 7. Flutter and Android Studio setup on Windows **[unverified; standard steps]**

1. Install Git, Android Studio (includes the SDK and an emulator), and the
   Flutter SDK (unzip from flutter.dev to a path without spaces, add its
   `bin` to PATH).
2. In Android Studio: install the Flutter and Dart plugins; SDK Manager,
   install an Android SDK Platform (API 34 or newer), Command-line Tools
   and Platform-Tools.
3. `flutter doctor --android-licenses`, then `flutter doctor` until clean.
4. Phone: enable Developer options and USB debugging, plug in, accept the
   RSA prompt; `adb devices` must list it, then `flutter devices`.
   (Wireless debugging also works on Android 11+.)
5. `flutter create convobox_spike --platforms=android`, then `flutter run` on
   the phone to confirm a hello-world installs.
6. Manifest permissions you will need: `INTERNET`, `POST_NOTIFICATIONS`,
   `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`, `RECORD_AUDIO`.
   If you use plain `http://` to a tailnet IP, Android blocks cleartext by
   default: either use the Tailscale Serve `https://` URL (preferred) or add a
   network security config for that host only. Do not enable cleartext app-wide.
7. Candidate packages (verify current versions and Android support):
   `http` or `dio` (SSE by reading a streamed response line by line, setting
   the Bearer header), `flutter_secure_storage`, `flutter_local_notifications`,
   `flutter_foreground_task` or `flutter_background_service`, `record` (mic),
   `firebase_messaging` (T3-A), `unifiedpush` (T3-B).
8. Keep the server's token out of your repo: enter it at runtime, store it in
   secure storage, never in source or a committed `.env`.

## 8. What to report back, and the format

Send one Markdown file, `SPIKE-RESULTS.md`, with the following. Plain
statements of what happened beat interpretation. Include raw evidence
(server console lines, `adb logcat` excerpts with tokens redacted).

Header: phone model, Android version and OEM skin, Flutter and package
versions, how the phone reached the server (Serve https or raw IP http),
ConvoBox commit if known.

Results table (one row per run; add rows for variants):

| ID | Experiment / variant | Pass criterion | Result (PASS / FAIL / PARTIAL) | Evidence | Notes (battery policy, timing, errors) |
|---|---|---|---|---|---|
| T1a | Lock-screen Approve, app backgrounded | server logs `approval_resolved` approved=true with no unlock | | | |
| T1b | Lock-screen Deny | approved=false, no unlock | | | |
| T1c | Same with app process killed | POST still arrives | | | |
| T1d | Credential read while locked | read succeeds, or exact failure recorded | | | |
| T1e | `authenticationRequired` true vs false | behavior difference recorded | | | |
| T2a | Mic via foreground service, screen locked 5 min | non-zero audio for the full 5 min | | | |
| T2b | Same after 30 min / swiped from recents | non-zero audio | | | |
| T2c | Zero-fill / mic stolen check | no silent zeros | | | |
| T3a | FCM alert, locked | alert shown, seconds recorded | | | |
| T3b | FCM alert in Doze / force-stopped | delivered or not | | | |
| T3c | Non-Google path (ntfy / UnifiedPush) | alert shown without Play services | | | |
| T3d | Tap opens the app | app foregrounds | | | |

Then three short paragraphs: (1) the one-sentence answer to each of T1, T2,
T3 (can the product plan in the decision doc work on Android as written?);
(2) any server API surprise (a field, status code or header that differed from
section 5; quote the actual response); (3) anything that would change the
plan (for example "the credential cannot be read while locked, so inline
actions need a different design").

Do not report impressions without the table row they belong to, and mark
anything you did not run as NOT RUN rather than leaving it blank.

## 9. Known limits of this hand-off

- Only the server side was run (macOS host, curl); no Flutter code, phone,
  Tailscale, SSE-through-proxy, push or audio test has been done by the author.
- The test server's approvals are simulated bookkeeping. The real approval
  path (Claude Code hook, voice gate) is not exercised; the HTTP contract is the same.
- `/api/listening` always returns 503 on the test server (no mic loop). The
  real server answers it.
- There is no audio endpoint on the server, so T2 only tests whether the
  phone can keep the microphone, not a working voice round trip.
