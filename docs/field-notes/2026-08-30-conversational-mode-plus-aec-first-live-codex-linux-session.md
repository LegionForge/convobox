---
title: "conversational" interrupt preset + AEC, live with Codex on Linux for the first time -- real mid-turn steering confirmed, volume sensitivity reconfirmed on a second machine, a new phoneme-limit trigger found
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 + local uncommitted settings-TUI fixes (post-v0.4.0); backend codex, permission_mode plan; codex-cli 0.149.1; interaction.interrupt_preset conversational; audio.echo_cancellation true; tts.engine kokoro, voice af_sarah; stt faster-whisper-base; safeword.hard_stop_phrases ["stop stop stop","eject eject eject"], kill_phrase "eject eject eject"; openSUSE Tumbleweed; Sager-class laptop, 4th-gen Intel i7; --tui --web
evidence:
  - One real live --tui --web session, one real human speaker, convobox-tui.log (repo root, not committed -- excerpts quoted here)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran the live session himself, narrated volume changes and observations into the mic as part of the test, asked for this note)
    - Claude Code (Anthropic claude-sonnet-5) -- prepared the config, read the live log, diagnosed each finding, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T07:00:00+00:00
  revised: 2026-08-30T07:00:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# `conversational` + AEC, first live pass with Codex on Linux

Follow-up to earlier the same day's two Codex UAT sessions
(`docs/field-notes/2026-08-30-linux-second-codex-live-mic-uat-mic-layer-
freeze-and-approve-mode-crash.md`), which found that neither session had
actually exercised `interaction.interrupt_preset: conversational` --
both used the `do-not-disturb` default. This session fixes that gap:
config switched to `interrupt_preset: conversational` +
`audio.echo_cancellation: true` (the `aec` extra reinstalled first,
having been dropped by an earlier plain `uv sync --extra dev`).

## Headline finding: real mid-turn steering confirmed, first time with Codex

While the assistant was speaking, the operator said "Okay, that looks
like that was a self-bargain, but that's okay, man." -- not a safeword,
an ordinary sentence. Under `conversational`'s axes (`mute` current
turn, steer `now`), this should mute the in-flight response and
immediately hand the new words to the backend as a real redirect, not a
queued follow-up. The log confirms it worked exactly that way:

```
transcript="Okay, that looks like that was a self-bargain, but that's okay, man." ... [BARGE-IN]
response: Fair enough, man 😄 I'll take the self-bargain. What's on your mind?
```

The reply directly acknowledges the interrupting content in real time --
this is `turn/steer` (or the equivalent mid-turn redirect) actually
firing, not a coincidence of conversation flow. This resolves the
"soft interject never exercised" gap flagged in every earlier UAT pass
this project has run against Codex. Confirmed multiple further times
across the session (e.g. "I was actually interrupting just to see if
barging in intentionally was working correctly" -> "Ah, got it -- you
were intentionally testing barge-in. Sounds like it's working
correctly.").

## Volume sensitivity reconfirmed, second machine

Same qualitative shape as `docs/field-notes/2026-08-25-linux-first-real-
human-speech-demo-safeword-and-self-barge-in-confirmed.md`'s macOS-
adjacent Linux session, now on different hardware (a 4th-gen Intel i7
laptop vs. that session's machine):

- **60%** (implied starting point): frequent self-barge-in, several
  distinct "self-bargain" incidents caught mid-response.
- **40%**: operator's own live read -- *"looks like we're doing pretty
  well at 40%. You're not barging in on yourself as much. Maybe only
  barged in once."*
- **~30-35%**: still some self-barge-in caught (*"you were barging in on
  yourself. It's okay."*) but described as *"doing really well"*
  overall; by this point in the session most barge-ins were the
  operator's own deliberate tests, not false triggers.

AEC stats logged alongside mostly read `NO ECHO DETECTED: barely any
speaker sound is reaching the mic` at lower volumes (consistent with
less echo to cancel in the first place), with a couple of
`UNDER-CANCELLING` and, later in the session, `FLOOR-LIMITED: echo
cancelled down to room noise -- success` results. Not a controlled
trial-by-trial protocol (same caveat as the 2026-08-25 note) -- real,
informed corroboration that the volume-dependence is a genuine hardware/
acoustic effect, not an artifact of one machine or one AEC tuning.

## New nuance on the existing Kokoro ~510-phoneme limit: short Arabic text triggers it too

The known issue (`docs/KNOWN-ISSUES.md`, diagnosed 2026-07-24, already
reconfirmed once this same day on a long English response) fired three
more times this session -- **every time on a short Arabic response**,
one or two sentences, nowhere near the length that triggers it in
English:

```
response: ممتاز—يبدو أن 35% كان مستوى جيدًا جدًا، مع بعض التشويش البسيط. هل تقصد «تقطّعًا» أو «تداخلًا»؟
WARNING Phonemes are too long, truncating to 510 phonemes
ERROR Task exception was never retrieved
... IndexError: index 510 is out of bounds for axis 0 with size 510
```

Two things worth separating:

1. **Arabic phonemization is apparently far denser per character than
   English's**, hitting the same hard 510-token model limit at a
   fraction of the visible text length. Not diagnosed further here
   (would need inspecting misaki/espeak-ng's actual token output for
   Arabic input), but a real, actionable data point for anyone relying
   on Kokoro for non-English responses.
2. **A `"Phonemes are too long, truncating to 510 phonemes"` warning now
   appears from kokoro-onnx itself before the crash** -- suggesting the
   library's own truncation attempt has an off-by-one (truncating to
   exactly 510, then indexing `voice[len(tokens)]` with `len(tokens) ==
   510` against a 510-length array) rather than genuinely preventing the
   overflow. Consistent with, not contradicting, the existing entry's
   root-cause diagnosis.
3. **Better failure mode than the earlier English case this same day**:
   playback started ~25ms after the error this time (some chunks had
   already synthesized successfully before the failing batch), so the
   operator heard a truncated response rather than total silence for the
   full 30-second stall timeout. Not a fix -- still the same underlying
   bug -- just a less bad outcome depending on exactly where in the
   stream the failure lands.

All three occurrences self-recovered the same way the English case did
earlier: logged, conversation continued normally on the next turn. No
new KNOWN-ISSUES status change; this is added as a live-confirmation
addendum to the existing entry.

## Hard stop / kill phrase: reconfirmed once more

Both fired correctly at session end, same as every other pass this
project has run:

```
transcript='Stop, stop, stop.' ... [HARD STOP]
hard stop matched safeword 'stop stop stop'
...
transcript='eject, eject, eject.' lang=en (0.31) ... [HARD STOP]
kill phrase matched 'eject eject eject' -- force-killing backend
```

Notable: the kill-phrase transcript's own confidence was only 0.31 (STT
was clearly struggling with the repeated staccato phrase, consistent
with this project's other documented safeword-STT-reliability findings)
but still matched and force-killed cleanly. Session ended with a clean
shutdown.

## Model quirk, not a ConvoBox bug

The backend spontaneously replied in Arabic several times mid-session
despite the operator speaking English throughout, apparently primed by
one early, accidental Arabic mis-transcription (`'في المنظر'`, STT
language detection `lang=ar`). Interesting conversational behavior, not
investigated further -- backend/model behavior, not something in
ConvoBox's own pipeline.

## Not done here

- Still not a controlled trial-by-trial volume sweep with Codex
  specifically (the 2026-08-24 synthetic sweep and 2026-08-25 live
  session that established the N=10 numbers both used claude-code).
- Root cause of Arabic's disproportionate phoneme density: not
  diagnosed, flagged only.
- Approval-mid-flight and a controlled repeat of soft-interject (this
  session's confirmation was incidental to natural conversation, not a
  deliberate isolated test) remain open from earlier the same day.
