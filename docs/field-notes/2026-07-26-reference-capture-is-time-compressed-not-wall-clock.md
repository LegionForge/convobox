---
title: The "not echo" conclusion in two prior field notes rests on a cross-correlation methodology with a real blind spot -- reference.wav is time-compressed, not wall-clock continuous
status: hypothesis -- structurally confirmed by code reading, NOT re-verified against live audio (unavailable on this machine)
date: 2026-07-26
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 7e776bf; WebRTC AEC3 (aec-audio-processing); src/convobox/audio/aec.py, src/convobox/audio/incident_capture.py
evidence:
  - src/convobox/audio/playback.py (AudioPlayer.on_block_played's calling contract: fires only per real block WRITTEN to the device)
  - src/convobox/audio/aec.py (EchoCanceller.feed_reverse, module docstring's "called from the PLAYBACK thread with each block actually being written to the device")
  - src/convobox/audio/incident_capture.py (IncidentCapture.observe_reference / ._observe: pure concatenation, no gap/silence awareness)
  - scripts/run_convobox.py's _feed_reference (the single combined hook feeding both incident_capture.observe_reference AND canceller.feed_reverse from the same on_block_played call)
  - docs/field-notes/2026-07-20-self-barge-in-was-backchannel-not-echo.md (first "not echo" conclusion)
  - docs/field-notes/2026-07-25-timing-coincidence-is-not-echo-correlation.md (second "not echo" conclusion, same method)
  - JP Cruz, live conversation 2026-07-26: he was not the source of the quoted utterances in either incident, and considers TTS echo highly likely -- new information neither prior note had
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; flagged live that he was not the speaker in either incident, prompting this re-examination)
    - Claude Code (Anthropic claude-sonnet-5) — investigation, writing
  org: https://legionforge.org
  created: 2026-07-26T21:10:00-05:00
  revised: 2026-07-26T21:10:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The "not echo" conclusion rests on a methodology with a real blind spot

**Context for outsiders.** ConvoBox is a local voice frontend for CLI
coding agents with open mic and speakers. Two prior field notes each
concluded, via cross-correlation, that a captured barge-in was NOT
uncancelled TTS echo, but the operator's own real backchannel speech.
This note reopens that conclusion after new information the prior
analyses didn't have.

## What prompted this

Both `2026-07-20-self-barge-in-was-backchannel-not-echo.md` and
`2026-07-25-timing-coincidence-is-not-echo-correlation.md` concluded
their respective incidents were real human speech (backchannel
acknowledgments like "Thank you very much.", "Okay, get it."), not
TTS echo, based on a full-signal FFT cross-correlation finding no
measurable correlation between `reference.wav` and `mic-raw.wav` at any
lag (peak ~0.15-0.17 against a noise floor of similar magnitude, both
times).

JP reports directly (2026-07-26) that **he was not the one who made
those utterances**, and considers it highly likely they were TTS echo
after all -- new evidence neither prior investigation had, since both
assumed the transcribed speech was the operator's own without
confirming it.

## The mechanism this note found (structural, from code, not re-verified against audio)

`reference.wav` (the diagnostic capture) and the REAL far-end reference
fed to `EchoCanceller` for actual cancellation come from the exact same
call site: `AudioPlayer.on_block_played`, which by design (see
`aec.py`'s own module docstring) fires only when a real audio block is
*actually written to the device* -- deliberately not at queue time,
since streamed synthesis can run faster than real-time and queue-time
feeding would race the reference ahead of the audio.

The consequence, not previously examined: if synthesis stalls even
briefly mid-response (a slow chunk in `play_stream`'s streaming path),
the reverse/reference stream simply stops getting fed for that
duration -- `IncidentCapture._observe`/`AecDumpWriter` do plain
concatenation with **no gap or silence awareness at all**. Meanwhile
`mic-raw.wav` is a continuous, real wall-clock recording via the
capture path, which never stops for TTS synthesis timing.

This makes `reference.wav` **time-compressed relative to wall-clock
time** whenever a synthesis stall occurs, while `mic-raw.wav` stays on
the real clock. A cross-correlation searching for ONE fixed lag across
the *whole* recording -- exactly what both prior notes did -- would
smear or cancel out real echo the moment even one such gap exists in
the analyzed window, because everything after the gap is now
misaligned relative to everything before it at any single fixed lag.
**A near-zero whole-signal correlation is consistent with either "no
echo" or "real echo across a signal with an internal gap the
correlation didn't know to account for."** The prior notes' conclusion
assumed the former without ruling out the latter.

## What this does NOT establish

- This is not proof the incidents WERE echo -- only that the evidence
  previously used to rule echo OUT has a real blind spot, and JP's
  direct report (he wasn't the speaker) is new information the "real
  human backchannel" conclusion depended on being false.
- Whether a synthesis stall of meaningful length actually occurred
  during either specific incident is unverified -- this note reasons
  from the code's structural capability for this to happen, not from
  re-analyzing the original audio (unavailable on this machine; both
  incidents' raw files live in a separate `convobox-UAT` working
  directory this session never had access to).
- The SAME blind spot potentially affects the real, live
  `EchoCanceller.feed_reverse()` path, not just the diagnostic dump --
  WebRTC AEC3's delay model (`set_stream_delay`) assumes a roughly
  steady relationship between reverse and capture frame arrival; a
  feeding gap could desync that model until it reconverges (the
  existing code already notes APM needs "a few hundred ms" to converge
  after startup -- the same kind of transient may recur after any real
  feeding gap, not just at startup). Not measured here.

## Fix implemented this session (low-risk, diagnostic-only)

`IncidentCapture`'s reference channel now pads real silence when the
gap since the last `observe_reference` call exceeds one frame's worth
of audio, so `reference.wav` stays wall-clock aligned with
`mic-raw.wav`/`mic-processed.wav` going forward -- future incident
captures will support a trustworthy whole-signal cross-correlation
without this blind spot. See the commit implementing this for the
exact mechanism and its tests.

## Not done, needs a decision

Whether `EchoCanceller.feed_reverse()` itself (the REAL cancellation
path, not just the diagnostic dump) should also get gap-aware silence
padding fed into APM's actual reverse stream -- this would change live
cancellation timing behavior, not just what gets logged, and can't be
verified without a real mic/speaker session. Flagged for JP; not
touched in this pass.

## What transfers

- **A diagnostic capture that silently omits real-time gaps is not a
  reliable timeline to cross-correlate against a continuous one, even
  with a "search every possible lag" full-signal approach** -- lag
  search only helps if a SINGLE lag is valid for the entire signal,
  which a time-compressed reference violates the moment it has an
  internal gap. (hypothesis -- structural, not yet re-verified against
  real audio)
- **"The evidence doesn't show X" and "X is false" are not the same
  claim, especially when the evidence-gathering method has an
  unexamined blind spot.** Both prior notes were careful about the
  alignment/lag-search details they DID check (and caught real
  mistakes doing so -- see 2026-07-25's own self-correction of an
  earlier clock-alignment error in the same investigation) but didn't
  question whether the reference signal's own internal timeline was
  trustworthy in the first place.
- **Ask the person who was allegedly recorded whether they said it.**
  Two independent "the operator said a real backchannel phrase"
  conclusions both went unchallenged until the operator was asked
  directly and said no -- a one-line question that should probably
  happen before finalizing this class of diagnosis, not after.
