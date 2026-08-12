---
title: First real human-speech demo on macOS -- safeword and barge-in both confirmed live, plus a real self-triggered barge-in loop found and diagnosed live in conversational mode
status: validated-live
date: 2026-08-11
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 123d3dc; macOS 26.x, Apple Silicon; AIRHUG 28 (USB mic), Mac mini Speakers; backend=claude-code, permission_mode=plan
evidence:
  - Real live-mic session, two real human speakers (JP Cruz + his son), `/tmp/demo_session.log` (convobox-UAT worktree scratch, not committed)
  - TTS volume set to 4.0x (Piper linear gain) + macOS system output volume set to 75%, both at JP's explicit request mid-session
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; ran the actual live demo with his son, asked for volume changes and the barge-in-mode switch, diagnosed the self-echo loop together with Claude Code in real time)
    - Claude Code (Anthropic claude-sonnet-5) -- set up and restarted the sessions, watched the live log via a background Monitor, diagnosed the self-echo loop, wrote this note
  org: https://legionforge.org
  created: 2026-08-11T08:00:00-05:00
  revised: 2026-08-11T08:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# First real human-speech demo on macOS

**Context for outsiders.** Every macOS test through the rest of this
session (see the other 2026-08-10 field notes) used synthetic TTS
injection because no human speaker was available. This note is
different: JP asked to actually demo ConvoBox live to his son, so this
is real, unscripted human speech through the real pipeline for the
first time on this platform -- closing the one gap every earlier
macOS note in this session explicitly flagged as still open.

## Setup

Real `scripts/run_convobox.py` (no `--text`), claude-code backend,
`permission_mode: plan` (read-only), isolated `working_dir`. Volume
was increased twice at JP's request during the live session: TTS gain
1.0x -> 2.0x -> 4.0x (Piper's own linear multiplier, all values
previously validated non-clipping earlier in this session's AEC
volume-escalation testing), plus the macOS system output volume set
to 75% (`osascript -e "set volume output volume 75"`) -- a genuinely
different lever than the app's own gain, since the app-level knob has
no defined "max," while system volume has a real 0-100% scale. Each
volume change required a full session restart (TTS engine config is
read once at startup), so Claude Code's own conversational context
reset each time -- said plainly to JP each time this happened.

## Real human safeword: confirmed working, 3 times

- `'Stop, stop, stop.'` -> matched `'stop stop stop'`, hard-stopped
  correctly (first occurrence, while paused from an earlier mishap --
  see below).
- `'Stop, stop, stop.'` -> matched again, second real occurrence.
- `'Abort, abort, abort.'` -> matched `'abort abort abort'`, confirming
  a second configured phrase, not just the first one, works live.
- One near-miss, instructive rather than a bug: a long, garbled
  transcript from a Whisper hallucination (see below) happened to
  contain the substring "stop listening" buried in the middle of an
  otherwise-nonsensical sentence -- this matched the PAUSE phrase (not
  the safeword) and hard-stopped + paused the session. JP had to say
  "Athena" (the configured resume word) to get it listening normally
  again; a natural-sounding "Resume listening" did NOT work, since
  only the exact configured resume word matches. Both of these are
  already-documented, known characteristics (substring-match pause/
  safeword detection; exact resume-word matching) -- this is the first
  time either was hit by a REAL accidental mishearing rather than a
  deliberately constructed test case.

## Real human barge-in: confirmed working, then found a real loop

After switching `interaction.interrupt_preset` from `do-not-disturb`
to `conversational` (JP's explicit request, to be able to talk over
responses), barge-in fired correctly on the very first attempt --
JP talked over the startup announcement, `barge-in: sustained speech
during playback -- stopping audio` fired, playback stopped cleanly.
A second deliberate interrupt worked the same way, with the backend
correctly acknowledging the interruption in its next reply.

**Then a real, sustained self-triggered barge-in loop appeared.**
Over roughly the next 90 seconds, `barge-in` fired 20 times, most of
them cutting a response after only a word or two, with several
firing when nobody was known to be speaking (JP had stepped away to
drive his son to school partway through). Diagnosed live, with JP
actively participating and confirming the theory ("Okay, it looks
like these interruptions are registering as a self-bargain" — his own
words, garbled by STT but the intent came through clearly enough for
both of us to confirm the read):

- Of the 19 `barge-in`-triggering events that had a following AEC
  stats line, **18 of 19 (95%) were `UNDER-CANCELLING`** -- real,
  unremoved echo headroom, not `FLOOR-LIMITED` success.
- Mean attenuation during this stretch: **6.54dB** (stdev 3.09) --
  close to this session's earlier steady-state baseline (6.75dB from
  the 10-run calibration batch). **Mean ceiling during this stretch:
  14.22dB** -- dramatically higher than the earlier steady-state
  ceiling mean of ~0.53dB.
- **The mechanism, in plain terms**: attenuation itself didn't
  collapse -- AEC kept removing roughly the same amount of echo it
  always does. What changed is the ceiling: with rapid back-to-back
  short responses (each cut short by the previous false-trigger before
  it could finish), the room's measured echo-to-ambient headroom
  spiked, meaning there was suddenly much MORE echo reaching the mic
  relative to ambient than during steady, uninterrupted conversation.
  A fixed amount of real cancellation against a much bigger echo
  signal leaves proportionally more residual -- easily enough to keep
  crossing `BargeInMonitor`'s sustained-speech threshold on its own.
- Several of the transcripts during this stretch were themselves
  Whisper hallucinations (single words, "200.", "10.1.", "Contrister.",
  even one in Korean script at low confidence) -- consistent with
  `[E6]`'s already-documented far-field pattern, now showing up as the
  INPUT to a barge-in decision, not just a dropped/garbled response.

**This reproduces, with real live data, a hypothesis this project's
own memory had already flagged as untested**: that sustained high-rate
utterances/turns could pile up faster than the audio pipeline drains,
producing exactly this "fine under normal use, degrades under
rapid-fire stress" pattern. The earlier hypothesis was about VAD/STT
thread submissions during a pause; this is the same shape of problem
in the AEC-convergence domain during rapid successive barge-ins
instead.

**Resolution**: stopped the session cleanly (SIGTERM; SIGINT did not
land, consistent with earlier findings this same overall session) once
it became clear the loop was firing with no one present to interrupt
it. No code change made or proposed this pass -- this is a live-data
characterization of a real risk, not a diagnosed root cause with a
fix ready.

## What transfers

- **The safeword and barge-in mechanisms both genuinely work with
  real human speech on macOS** -- the single biggest standing gap
  from every earlier macOS field note this session is now closed for
  the core interaction primitives (not for STT accuracy in general,
  which remains genuinely hard over an open-air speaker path, per
  `[E6]`).
- **`conversational` mode's self-barge-in risk is real and
  reproducible under rapid-fire conditions**, not just a theoretical
  concern -- 18/19 barge-in events in one live stretch showed
  under-cancelling, and the mechanism (ceiling spiking under rapid
  short-turn conditions, not attenuation collapsing) is now understood
  well enough to describe precisely, even though it isn't fixed.
  `do-not-disturb` mode (the config's original default) does not have
  this risk, since ordinary speech can't trigger anything during
  playback in that mode.
- **A real substring-match pause-phrase near-miss happened
  organically** (not constructed) -- a Whisper hallucination
  containing "stop listening" as a buried substring paused a live
  session. Matches this project's own already-documented
  characterization of that risk category exactly; no new mitigation
  proposed here, just a real-world occurrence on record.
- **Real human speech and background conversation is measurably
  harder for far-field Whisper than the synthetic TTS injection used
  elsewhere in this session** -- expected, given real speech has more
  natural variation (mumbling, overlapping voices, a second speaker in
  the room) than a single clean synthesized voice, and matches this
  session's own real-room-noise field note's finding that background
  audio content (not volume) drives false-trigger risk.
