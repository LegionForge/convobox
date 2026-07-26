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

## Follow-up (same day): tested the feed_reverse() hypothesis directly -- it didn't hold up

JP confirmed he believes these incidents genuinely were TTS echo. That
raised the natural next question: should `EchoCanceller.feed_reverse()`
(the REAL cancellation path) get the same gap-aware silence padding, in
case skipped reverse frames during a synthesis stall desync AEC3's
delay alignment for everything afterward?

Installed the real `aec-audio-processing` extra (build tools were
already present: `meson`/`ninja`/`swig` via Homebrew) and ran the exact
scenario against the actual WebRTC AEC3 binding, not a guess: a
continuous synthetic far-end + matching mic echo, with a genuine
0.5s/2.0s silent gap (the speaker really did stop, matching what a
synthesis stall means), comparing "skip feed_reverse during the gap"
(current behavior) against "feed real silence during the gap" (the
hypothesized fix), measuring suppression well after the gap so
convergence time isn't a confound. Across 5 random seeds x 2 gap
lengths:

```
seed=1 gap=0.5s  skip=42.7dB  pad=43.0dB  diff=+0.4dB
seed=1 gap=2.0s  skip=43.7dB  pad=42.8dB  diff=-1.0dB
seed=2 gap=0.5s  skip=42.7dB  pad=43.1dB  diff=+0.5dB
seed=2 gap=2.0s  skip=43.9dB  pad=43.9dB  diff=+0.1dB
seed=3 gap=0.5s  skip=42.6dB  pad=42.9dB  diff=+0.3dB
seed=3 gap=2.0s  skip=43.7dB  pad=43.7dB  diff=+0.0dB
seed=7 gap=0.5s  skip=42.4dB  pad=43.1dB  diff=+0.6dB
seed=7 gap=2.0s  skip=43.7dB  pad=44.0dB  diff=+0.2dB
seed=42 gap=0.5s skip=42.7dB  pad=43.0dB  diff=+0.3dB
seed=42 gap=2.0s skip=43.8dB  pad=43.8dB  diff=+0.0dB
```

**The difference is noise-level (within ±1dB, no consistent
direction) regardless of gap length.** WebRTC AEC3 does not appear to
rely on a fixed reverse/capture frame-count relationship the way the
hypothesis assumed -- it's evidently more robust to a bounded gap of
missing reverse frames than that. **Conclusion: `feed_reverse()` does
NOT need the same fix.** Not implementing it -- there's now direct
evidence it wouldn't help, and changing safety-adjacent AEC code
without a measured benefit is exactly the wrong move. The diagnostic-
only fix (above) stands on its own regardless, since a capture tool's
timeline accuracy is a separate concern from real-time cancellation
behavior.

**What actually explains persistent echo, then?** The 2026-07-25
incident's own logged AEC verdict was `UNDER-CANCELLING: ~12.0dB of
echo headroom remains` -- AEC's own real-time telemetry already
reported incomplete cancellation for that response, independent of any
cross-correlation analysis. That's consistent with this project's
already-documented, unresolved finding
(`docs/KNOWN-ISSUES.md`'s "WebRTC APM's noise suppression / auto gain
control are unused" entry): erratic 0.5-12dB attenuation, sometimes
leaving real audible headroom, on this exact hardware. The likely
answer isn't a new bug in the reference-feeding pipeline -- it's the
already-known, already-proposed-but-JP-gated AEC quality gap. Worth
revisiting that go-ahead now that there's a concrete incident
consistent with it.

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
- **A plausible mechanism is still a hypothesis until it's tested against
  the real thing, even when the reasoning reads solidly.** The
  feed_reverse frame-skip theory above was structurally coherent and
  directly analogous to a bug this same session just fixed in the
  diagnostic path -- and it still didn't survive being run against the
  real WebRTC AEC3 binding (5 seeds x 2 gap lengths, noise-level
  difference throughout). Installing the real dependency to check
  (`aec-audio-processing`, build tools already on hand) took a few
  minutes and prevented shipping an unnecessary change to
  safety-adjacent code on the strength of a good-sounding argument
  alone. (validated-live, empirically)
