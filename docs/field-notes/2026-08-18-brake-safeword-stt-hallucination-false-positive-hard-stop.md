---
title: A ~1s ambiguous barge-in was hallucinated by faster-whisper into "brake" repeated 69 times, false-triggering the safeword hard-stop
status: validated-live
date: 2026-08-18
project: ConvoBox (github.com/LegionForge/convobox)
versions: main @ 2dd83b3 (post-0.3.1-rc1); claude-code backend; faster-whisper 1.2.1, stt.device=cpu, stt.model default, stt.temperature=0.0, stt.hotwords="stop brake eject mayday listening resume alpha bravo delta halt abort"; Windows 11 (helios)
evidence:
  - convobox-UAT/convobox-tui.log, 2026-08-18 12:26:16-12:26:20 (timestamps quoted verbatim below)
  - convobox-UAT/convobox-tui.log, 2026-08-18 16:14:59 (follow-up contrast case, timestamp quoted verbatim below)
  - docs/KNOWN-ISSUES.md's existing STT-hallucination entries (2026-08-06, 2026-08-12) -- same failure class, this is a new concrete instance
  - convobox.yaml (UAT checkout): safeword.hard_stop_phrases includes "brake brake brake"; stt.hotwords includes "brake"; stt.min_language_probability=0.4
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live UAT session on helios, flagged the runaway-looking transcript live and asked for investigation; also flagged the follow-up contrast case live)
    - Claude Code (Anthropic claude-sonnet-5) -- log correlation, mechanism analysis, writing
  org: https://legionforge.org
  created: 2026-08-18T12:40:31-05:00
  revised: 2026-08-18T17:04:26-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A ~1s ambiguous barge-in was hallucinated into 69x "brake", false-triggering the safeword hard-stop

**Context for outsiders.** ConvoBox transcribes speech with faster-whisper
and matches the resulting text against a small set of hard-coded safety
phrases (`safeword.hard_stop_phrases`) to trigger an immediate hard-stop.
`stt.hotwords` biases the decoder toward a fixed vocabulary (mostly the
same safety phrases) to fight STT misses on short, unusual words. This
session caught a case where that same biasing plausibly amplified an STT
hallucination into an unwanted real safety-control trigger, not just a
wrong transcript.

## Problem

Mid-session, while a turn was in flight (`busy=True`) and its response
was playing, a very short (0.992s) barge-in was detected and sent to STT.
The resulting transcript was `"brake"` repeated **69 times**, which
matched the configured safeword `"brake brake brake"` and fired a real
hard-stop, interrupting the in-flight turn. JP did not intentionally say
"brake" 69 times -- his own live reaction: "no idea why it showed up,"
"it looks like a hallucination."

## Evidence

```
2026-08-18 12:26:18,116 INFO AEC stats for last response: attenuation=8.5dB of ~1.2dB measurable  delay=222ms  frames(reverse=3232, capture=7862)  [NO ECHO DETECTED: barely any speaker sound is reaching the mic -- check the output device is audible; this is NOT a cancellation result]
2026-08-18 12:26:18,276 INFO barge-in: sustained speech during playback -- stopping audio
2026-08-18 12:26:18,657 INFO Processing audio with duration 00:00.992
2026-08-18 12:26:19,112 INFO Detected language 'en' with probability 0.68
2026-08-18 12:26:20,315 INFO transcript='brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake brake' lang=en (0.68) dec=0.82 busy=True  [HARD STOP]
2026-08-18 12:26:20,315 INFO hard stop matched safeword 'brake brake brake'
2026-08-18 12:26:20,315 INFO hard-stop interrupted a turn that was still busy -- if it included a tool call, the underlying process is not guaranteed to have stopped; any result it eventually produces will be discarded, not spoken
```

Sixty-nine repetitions of the single word "brake" from a 0.992-second
audio clip -- at ordinary speech rates that duration could hold at most
2-4 real words, not 69. `lang=en` probability (0.68) and decode
confidence (`dec=0.82`) are both unremarkable-looking, not obviously
flagging the output as garbage the way a very low score might.

## Mechanism

**Confirmed:** this is a real transcript produced by one STT decode call
on one short audio clip -- not a caching bug, not a rendering/display
bug (ruled out live: this is the raw `transcript=` log line
`_transcribe_with_timeout()`/`LocalTranscriber` writes, upstream of any
UI rendering, and the safeword match/hard-stop that followed is real
downstream behavior driven by that exact string, not a display artifact).

**Leading hypothesis, not independently confirmed this session:** a
Whisper-family repetition-loop failure mode, well documented in the wild
for whisper/faster-whisper on short or acoustically ambiguous input --
the decoder gets stuck re-emitting the same token instead of terminating
or moving on. Two things about this specific instance make the
hotwords-amplification theory plausible rather than a generic version of
that same bug:

1. `stt.hotwords` explicitly includes `brake` (it's also a safeword and
   in the hotword list for exactly that reason -- to make STT *more*
   likely to recognize it, on the theory that missing a safety phrase is
   worse than a false positive). A biasing list that makes the decoder
   more confident in a specific token is also exactly the kind of thing
   that could make an already-present repetition-loop tendency harder to
   break out of, once it starts.
2. The clip is extremely short (0.992s) and immediately followed a
   barge-in during active TTS playback -- exactly the kind of acoustically
   marginal, low-information input this failure class tends to hit.

**Ruled out, or argues against:** self-echo of the assistant's own TTS
leaking through as false "speech." The AEC verdict logged 160ms before
this barge-in (`NO ECHO DETECTED: barely any speaker sound is reaching
the mic`) is for the *prior* response's playback, not this exact instant,
so it isn't conclusive on its own -- but it's the closest available signal,
and it points away from self-echo, not toward it. Not independently
confirmed either way this session.

**Not determined:** what the actual sound was (JP doesn't know either --
"no idea why it showed up"). Could be genuine ambient noise, a partial/
garbled real utterance, or something else entirely. No claim is made here
about the true source signal, only about what STT did with it.

## What transfers

- **validated-live:** faster-whisper can produce a many-times-repeated
  single-word transcript from a sub-1-second audio clip, and ConvoBox's
  safeword matcher treats that transcript exactly like a normal one --
  correctly, in the sense that it still only fires the hard-stop once
  (not 69 times) and doesn't crash or loop. The safety mechanism itself
  degrades gracefully under this input.
- **validated-live:** this specific hallucination produced a real,
  user-unintended hard-stop on an in-flight turn -- a false-positive
  safety trigger from noise/ambiguous audio, distinct from (but the same
  general class as) the STT-hallucination-bypasses-safety-detection
  findings already in `docs/KNOWN-ISSUES.md` (2026-08-06, 2026-08-12).
  Those were about hallucination *hiding* a safeword from the matcher;
  this is hallucination *manufacturing* one that was never really said.
- **hypothesis, not confirmed:** `stt.hotwords` biasing toward a word also
  makes a repetition-loop hallucination on that exact word more likely.
  Testing this would mean deliberately feeding the same kind of short,
  ambiguous clip through the STT layer with and without that word in
  `hotwords`, holding everything else constant -- not done here.
- **Practical note, not yet acted on:** every default safeword/hotword is
  a short, common English word (`stop`, `brake`, `halt`, `abort`, ...).
  If the hotwords-amplification hypothesis holds, the same mechanism
  could in principle repeat for any of them, not just `brake` -- this
  instance is one data point, not a "brake specifically is worse" claim.

## Follow-up (same session, 2026-08-18 16:14): a second repetition-loop
hallucination, this time correctly caught and dropped -- useful contrast
on when the existing confidence gate actually protects against this
failure class.

```
2026-08-18 16:14:59,884 INFO dropped low-confidence transcript='停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止 停止' lang=th (0.15 < 0.40) [ERROR-LADDER: tier 1]
```

`停止` (停止) is Chinese for "stop" -- repeated **39 times**,
same repetition-loop shape as the "brake" instance above. Two things
differ, and both matter:

1. **The language guess itself was wrong and low-confidence.**
   faster-whisper decoded this as Thai (`lang=th`), not even Chinese
   (the script it actually hallucinated), at `0.15` probability --
   well under `stt.min_language_probability: 0.4`. This is exactly the
   gate `min_language_probability` exists for, and it worked: the
   transcript was logged as `dropped low-confidence transcript` and
   never reached the safeword matcher at all. No hard-stop, no
   downstream effect of any kind.
2. **No hotword was involved.** `stt.hotwords` in this config has no
   Chinese/Thai entries and doesn't include "stop" in any script --
   this hallucination happened on its own, unprompted by any biasing
   list, in a way the "brake" instance's leading hypothesis can't
   explain by itself.

**What this changes about the finding above:** it sharpens, rather than
weakens, the original claim. The repetition-loop failure mode itself is
not hotwords-dependent -- it can and does happen without any hotword
bias at all (this instance). But `min_language_probability` is a real,
working defense against the WRONG-LANGUAGE case, correctly catching this
one. The "brake" instance slipped through specifically because it
decoded in the *correct* language (`en`) at a confidence (`0.68`) above
threshold, despite the content being complete garbage -- a same-language,
moderate-confidence repetition loop is a gap this gate was never designed
to catch, since it only checks "is this plausibly the configured
language," not "is this plausible human speech content." The
hotwords-amplification hypothesis from the original finding is still
unconfirmed, and now looks like at most a contributing factor for
same-language cases, not the whole story -- repetition-loop hallucination
appears to be a more general faster-whisper behavior under short/
ambiguous audio, independent of hotwords.
