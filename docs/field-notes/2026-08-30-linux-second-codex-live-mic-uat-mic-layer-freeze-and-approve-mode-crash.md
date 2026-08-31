---
title: Second live Codex UAT pass on Linux -- a mic-layer freeze reproduces on a new platform, real safeword mechanics confirmed sound, and `permission_mode: approve` found broken against current codex-cli
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 (post-v0.4.0); backend codex, permission_mode plan; codex-cli 0.149.1; tts.engine kokoro, voice af_sarah; stt faster-whisper-base; interaction.interrupt_preset do-not-disturb (default -- not exercised this session); audio.echo_cancellation false (default); safeword.hard_stop_phrases ["stop stop stop","eject eject eject"], kill_phrase "eject eject eject"; openSUSE Tumbleweed; --tui --web
evidence:
  - Two real live --tui --web sessions, one real human speaker, convobox-tui.log (repo root, not committed -- excerpts quoted here)
  - Direct manual reproduction of the codex-cli crash (`codex -c approval_policy=untrusted -c sandbox_mode=workspace-write app-server`)
  - Direct isolated call to `render()` in scripts/settings_tui.py confirming an unrelated but adjacent finding (see the companion settings-TUI field note, same date)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran both live sessions himself following the UAT smoke doc, reported live observations, asked for this note)
    - Claude Code (Anthropic claude-sonnet-5) -- prepared the test config, read the live logs, diagnosed each finding, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T06:14:00+00:00
  revised: 2026-08-30T06:14:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Second live Codex UAT pass on Linux

Follow-up to `docs/UAT-codex-smoke.md`'s first live-mic pass (same day,
earlier session). That pass's Findings log already covers loop basics,
busy tracking, the Kokoro ~510-phoneme limit reconfirmed on a new
backend, and the overlap-gate behavior that first looked like "hard stop
isn't working" but wasn't. This note covers three further, more
significant findings from a second pass aimed specifically at
re-testing hard-stop responsiveness and the still-open soft-interject /
approval-mid-flight checklist items.

## Finding 1: a real ~3.5-minute mic-capture freeze, not a phrasing issue

The operator reported "still not interrupting well" before any log was
checked. `convobox-tui.log` shows the real cause: **213.4 seconds of
total silence in the mic/STT pipeline** (00:53:40.683 -> 00:57:13.997,
zero `Processing audio` lines), while the codex adapter's own
`_read_loop` kept logging its routine 5-second idle-poll warning on
schedule the entire time, every single one reporting `busy=False`:

```
2026-08-30 00:53:40,683 INFO dropped (overlap gate, no echo cancellation): "1 Lantern 2, Metal 3, Bortz ..." [echo-match: 0.75 of tokens in last response]
2026-08-30 00:53:43,057 WARNING codex app-server _read_loop: readline() still pending after 65.5s (proc.returncode=None, busy=False) ...
[... identical warnings every 5s for the entire window ...]
2026-08-30 00:57:13,101 WARNING codex app-server _read_loop: readline() still pending after 275.6s (proc.returncode=None, busy=False) ...
2026-08-30 00:57:13,997 INFO Processing audio with duration 00:05.024
```

That the backend-adapter's own polling task kept firing on schedule the
whole time proves the event loop itself was not blocked -- only the
mic-capture/VAD side went dark. This is the exact signature already
diagnosed once before, on macOS, in `docs/KNOWN-ISSUES.md`'s VAD
segmenter entry (`docs/field-notes/2026-08-15-vad-freeze-new-variant-
mic-layer-only-6-minutes-self-resolved.md`): "mic-layer-only, no codex
subprocess involved at all... self-resolved." Both that occurrence and
this one used `backend=codex`. **This is the second occurrence overall
and the first confirmed on Linux** -- raising confidence this is
platform-independent, though still not proven backend-independent (both
catches so far used codex). Full detail, including the operational
impact (every hard-stop/kill-phrase attempt spoken during the freeze
went completely unheard, not just dropped) is in
`docs/KNOWN-ISSUES.md`'s entry, updated the same day as this note.
Root cause remains unestablished; it self-resolved with no restart, same
as the first catch.

**What this rules out:** the moment the freeze lifted on its own, the
very next "stop stop stop" and "eject eject eject" attempts matched
instantly:

```
2026-08-30 00:59:30,034 INFO transcript='... Stop, stop, stop.' ... [HARD STOP]
2026-08-30 00:59:30,034 INFO hard stop matched safeword 'stop stop stop'
...
2026-08-30 01:00:16,808 INFO transcript='Eject. Eject. Eject.' ... [HARD STOP]
2026-08-30 01:00:16,808 WARNING kill phrase matched 'eject eject eject' -- force-killing backend
```

The safeword-matching logic itself is sound. The bug is entirely
upstream of it, in mic capture.

## Finding 2: `permission_mode: approve` crashes against current codex-cli

Re-checking settings before re-testing the still-open "approval
mid-flight" checklist item, switching `backend.permission_mode` from
`plan` to `approve` for the codex backend produced an immediate crash on
the very first turn:

```
ConnectionError: codex app-server exited
```

Traced to `_PERMISSION_CODEX_OVERRIDES` in
`src/convobox/adapters/codex.py`, which hardcodes `("untrusted",
"workspace-write")` for `approve` mode (verified live as of 2026-07-20
per that dict's own comment). Reproduced directly by hand:

```
$ codex -c approval_policy=untrusted -c sandbox_mode=workspace-write app-server
Error: approval_policy = "untrusted" is no longer supported; remove this setting
```

`codex-cli` 0.149.1's own error for a genuinely unknown value lists the
currently valid `approval_policy` variants: `untrusted`, `on-failure`,
`on-request`, `granular`, `never` -- `untrusted` is still a *recognized*
enum member, just explicitly deprecated with a dedicated error, not a
typo upstream.

**A naive swap is a false fix, tested and reverted the same session.**
Trying `on-request` in place of `untrusted` (same `sandbox_mode:
workspace-write`) stops the crash, but a follow-up file-write prompt
completed with **no approval RPC at all** -- the file was created
directly. Current codex-cli treats in-workspace writes as already
permitted under `workspace-write` regardless of `approval_policy`, so
`approve` mode's entire "writes escalate to a voice-gated decision"
premise no longer holds at that sandbox setting. That combination was
reverted immediately rather than left in place; a real fix needs a
different mapping (candidate: `sandbox_mode: read-only` paired with
`on-request`/`on-failure`, forcing every write to escalate since the
sandbox itself disallows it) verified live against the actual approval
RPCs before trusting it. Full writeup in `docs/KNOWN-ISSUES.md`'s new
entry. Net effect: **approval-mid-flight could not be tested this
session either** -- blocked on this being fixed properly first, not
worked around under time pressure.

## Finding 3: "conversation mode" was never exercised in either session -- clarified, not a bug

The operator asked whether hard-stop was supposed to "stop and steer"
the conversation. It doesn't, by the config both sessions actually used:
`interaction.interrupt_preset` defaults to `do-not-disturb` (let the
current turn finish, drop any new words spoken over it -- the safeword
is the only thing that bypasses this). The preset that does what the
operator was expecting -- mute the current turn immediately and steer
the backend with new words -- is `conversational` (`InterruptAxes("mute",
"now")` in `src/convobox/interrupt_presets.py`), which neither UAT
session enabled.

`conversational` also requires `audio.echo_cancellation: true` to be
usable at all (confirmed already live on Linux, 5 days earlier, in
`docs/field-notes/2026-08-25-linux-first-real-human-speech-demo-
safeword-and-self-barge-in-confirmed.md`) -- and that same session found
real limits even with AEC on: near-100% false self-barge-in at 50%
system volume, "about half and half" at 30%. That prior session used
`backend=claude-code`; `conversational` has not yet been tested with
codex on Linux at all. The `aec` extra was also not installed in this
machine's venv at the time this was discovered (removed by an earlier
plain `uv sync --extra dev`); reinstalled (`uv sync --extra dev --extra
web --extra aec`) so a future session can actually test this combination
live.

## Not done here

- Soft interject (`turn/steer`) still not exercised in either session --
  needs a deliberate attempt during a `busy=True` "still working"
  heartbeat window specifically, before any TTS starts, which neither
  session's prompts happened to land in.
- `conversational` + `echo_cancellation` + codex on Linux: prepped
  (venv updated) but not yet run.
- Root cause of the mic-layer freeze (Finding 1): still unestablished,
  same as the original 2026-08-15 catch.
