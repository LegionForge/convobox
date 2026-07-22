# Design: echo handling, speech normalization, and the path to barge-in

Distilled from the 2026-07-11 live-mic UAT on Windows (first same-room
speakers+mic sessions), the session's log analysis, and review of
agent-generated suggestion notes produced during that UAT. This is the
durable record; the raw working notes it supersedes were deliberately
not kept in the repo.

## Echo handling: three layers, two implemented

ConvoBox hears its own TTS through an open mic. Defenses, in the order
they run (`scripts/run_convobox.py` main loop):

1. **Playback-overlap window** (implemented). Reconstructs when an
   utterance's audio began (transcript arrival minus STT latency, VAD
   trailing silence, and utterance duration) and drops it if that
   overlapped playback plus a 300ms reverb grace (`ECHO_GRACE_S`).
   Streaming-aware: each synthesized chunk extends the playback-end
   estimate; `stop()` clamps it to now. **Live result (2026-07-11 log):
   ~30 echo utterances caught, zero false drops of real speech, zero
   echo reached the backend** — including Whisper hallucination loops
   and wrong-language garble on far-field audio.
2. **Spoken-text filter** (implemented, backstop). ConvoBox knows what
   it just said; transcripts whose words are >=70% contained in a
   response spoken in the last 30s are dropped. Never applied under 3
   tokens (a real "yes"/"run it" must never be eaten). In the observed
   session the overlap window caught everything first — this layer
   exists for long reverb, delayed audio devices, and estimate drift.
3. **Acoustic echo cancellation** (planned — see below). The only layer
   that can enable true barge-in.

The safeword bypasses every drop path, always: a hard stop is checked on
the raw transcript before any gate and is honored mid-playback.

## Why there is no barge-in yet (and what NOT to try)

Ordinary speech during playback is dropped, not acted on (half-duplex).
Two seemingly-obvious shortcuts were evaluated and rejected during UAT
review:

- **"Stop playback when VAD detects speech during playback."** Without
  AEC the VAD cannot tell the user's voice from the assistant's own
  voice arriving through the mic — the TTS itself trips VAD and the
  assistant self-interrupts moments into every response. The text-level
  echo filter cannot guard this: it runs after transcription, seconds
  too late to gate an immediate `player.stop()`.
- **`sd.InputStream(echoCancellation=True, noiseSuppression=True, ...)`.**
  These parameters do not exist. They are WebRTC getUserMedia (browser)
  constraint names, not PortAudio stream parameters; the call raises
  TypeError. PortAudio exposes no AEC toggle on any host API. (Recorded
  because an agent-generated suggestion presented this as a working
  diff — it is not.)

Conclusion: **true barge-in is blocked on real acoustic echo
cancellation, not on orchestration changes.**

## The AEC plan

`aec-audio-processing` (PyPI) wraps WebRTC's audio processing module
(AEC3 — the canceller VoIP products use). Assessment (2026-07-11):

- License: BSD-3 (MIT-compatible; friendlier than piper-tts's GPL).
- Packaging: ~1MB Windows x86-64 wheels, Python 3.11–3.13.
- Runtime: low single-digit %CPU, ~10ms added latency (10ms frames).
- Fit: its far-end "reverse stream" API matches ConvoBox exactly — we
  GENERATE the reference signal in `play_stream`, sample by sample,
  which is the half most integrations struggle to capture.
- Integration sketch: tee playback chunks (resampled 22.05kHz → the
  mic's 16kHz) into the reverse stream; run mic chunks through
  `process_stream` before VAD. Ship behind `audio.echo_cancellation`
  config flag, keep layers 1–2 as backstops.
- Honest cost: room-specific tuning UAT (delay hint, adaptation
  convergence, mic placement changes), not code volume.

### Research: what Zoom/Webex/WebRTC do beyond basic linear AEC (2026-07-20)

Prompted by self-barge-in "still needs a bit of tweaking" once AEC lands.
Researched what commercial VoIP/conferencing products do beyond a plain
adaptive linear filter, since that's the gap between "AEC exists" and
"AEC works well enough on cheap speakers in a real room with no
headphones." Two claims below are cited against a fetched source each;
where a claim is general/uncited engineering knowledge rather than
something verified from a specific published source, it's labeled as
such rather than presented as a confirmed fact.

**The techniques that matter most for ConvoBox's specific problem
(open mic, no headphones):**

- **Nonlinear residual echo suppression**, a frequency-dependent
  post-filter that cleans up what the linear adaptive filter can't model
  by design -- echo from speaker distortion, amp non-linearity, and ADC
  clipping. Matters here specifically because cheap speakers at real-room
  volume are exactly the non-linear-distortion case this stage targets.
- **Double-talk detection**: monitors coherence between the far-end
  reference (what was played) and the mic signal to distinguish "user
  talking over playback" from "just echo," and throttles filter
  adaptation during genuine double-talk so real speech doesn't get
  mislearned as echo. This is the mechanism that keeps a real barge-in
  from being silently swallowed by an over-eager canceller -- directly
  relevant to `BargeInMonitor`.
- **Continuous delay estimation**, cross-correlating the reference and
  capture signals on an ongoing basis rather than once at startup, since
  render→capture delay drifts across devices/host-APIs. This is exactly
  the class of problem behind this project's own `aec_delay_ms` auto-tune
  incident (a stale baked-in value silently disabling auto-tuning) --
  cited [source](https://switchboard.audio/hub/how-webrtc-aec3-works/)
  describes AEC3 doing this continuously, not as a one-shot calibration.

**Does `aec-audio-processing` (WebRTC AEC3) already include these?**
Yes -- per the same source, all three are native AEC3 stages, not
something ConvoBox would need to layer on top. That reframes the open
"self-barge-in still needs tweaking" problem: it's more likely a
**wiring/tuning problem** (is the far-end reference correctly tee'd and
time-aligned before `process_stream`; is delay estimation actually
converging on this project's specific device paths) than "AEC3 lacks a
needed algorithm." Worth checking against real UAT numbers before
reaching for anything beyond AEC3.

**What Zoom/Webex publish about their own approach:** not much at the
algorithmic level. [Webex's own audio-quality blog](https://blog.webex.com/collaboration/video-conferencing/audio-quality/)
confirms AEC has to work for full-duplex to function at all, but
publishes no lower-level detail (proprietary). [Zoom's blog on its
"High-Fidelity Music Mode"](https://www.zoom.com/en/blog/high-fidelity-music-mode-professional-audio-on-zoom/)
confirms Zoom offers an echo-cancellation-mode choice ("Echo
cancellation" vs. "Aggressive") but likewise discloses no internals.
Both are general/uncited on internals beyond what's quoted here -- their
value is confirming *that* full-duplex AEC is table-stakes industry
practice, not revealing *how* they implement it.

If AEC3's built-in stages prove insufficient after real tuning, the
next-step academic reference is [double-talk-detection-aided residual
echo suppression via spectrogram masking](https://www.mdpi.com/2624-599X/4/3/39) --
state-of-the-art beyond AEC3, not needed unless AEC3-plus-tuning is
verified insufficient first.

### Correction (2026-07-20, same day): the delay-hint theory was wrong — real calibration data already existed

Earlier the same day, a live UAT log showing 47 `UNDER-CANCELLING` verdicts
was diagnosed as caused by a stale `aec_delay_ms: 309` fighting the
~222ms auto-tune estimate, and `convobox.yaml` was changed to remove the
explicit value. **That diagnosis and that config change were both
wrong**, and the mistake is worth recording plainly rather than quietly
fixing:

- `uat-acoustic-calibration/20260716-153747/report.json` (a real,
  on-hardware delay sweep from `scripts/acoustic_calibration.py`, same
  device pair: `Microphone (1080P Pro Stream)` / `Headphones (Realtek(R)
  Audio)`, both MME) ranks **309ms as the best of {285, 297, 309}**: 80%
  self-barge rejection, 10.6dB mean suppression, and the *lowest*
  variance across repeats (0.079dB stdev vs. 0.747/1.021 for the
  others) — explicitly `"best_trial": "309-r1"`,
  `"recommendation": {"aec_delay_ms": 309}`.
- A second sweep the same day, `uat-acoustic-calibration/20260716-152911/report.json`,
  tested {222, 247, 272, 297} and found **222ms — the auto-tune
  estimate — the *worst* of that set**: only 40% self-barge rejection
  and a noisy 3.336dB suppression stdev, versus 75-100% rejection for
  247-297ms.
- `309ms` was not stale cruft. It was a deliberately, empirically chosen
  value from real calibration data that already existed in the repo.
  The convobox.yaml edit was reverted; the explicit value is restored
  with a comment pointing at these two reports.

This does not fully contradict the delay-hint research above — the
synthetic sweep and the WebRTC AEC3 source both suggest the hint barely
affects AEC3's own internal convergence mathematically. What it means is
that claim, even if true in isolation, does not explain the *actual
measured outcomes* in this specific room: something in the real
acoustic/measurement pipeline clearly correlates with this parameter
(self-barge rejection rate varies from 40% to 100% across delay
values), and neither the synthetic model nor the WebRTC-source read
identified what. **Honest status: unresolved.** The mechanism connecting
delay-hint value to real-world outcome is not understood; only the
empirical ranking is trusted, because it's real hardware data, not a
synthetic proxy.

Practical consequence: **the live UAT session's jarring self-barge-in is
still unexplained.** It happened while running the empirically-best
known delay value, so "wrong delay" is ruled out as the cause. The room,
hardware, or backend-conversation conditions during that live session
may simply differ from the 2026-07-16 calibration run's controlled
trial (four days is enough time for anything to have shifted — device
selection, volume, distance, background noise) — re-running
`scripts/acoustic_calibration.py` is the fastest way to check whether
309ms is still the right value before looking anywhere else.

### Capturing a live incident for offline analysis (2026-07-20)

`scripts/acoustic_calibration.py` is a **controlled experiment**: known
text, scripted trials, ambient/delay sweeps, real hardware, but not a
real conversation with a coding-agent backend. It answers "does AEC work
here, systematically." It does not capture what happens during an
actual voice-driven dogfooding session, which is what produced the
jarring self-barge-in this document is chasing.

`scripts/run_convobox.py --aec-dump [DIR]` is the complementary tool:
capture the *real* reference (what was actually played) and *real* mic
signal (before and after cancellation) during an actual live session, to
WAV, for repeatable offline replay afterward — the same "aecdump"
methodology WebRTC's own `audioproc_f`/`unpack_aecdump` tooling uses.
Three files land in a timestamped subdirectory of `.aec-dumps/` (or a
custom `DIR`): `reference.wav`, `mic-raw.wav`, `mic-processed.wav`.
Verbose log lines mark the dump's start, per-response progress (frame
counts, elapsed seconds), and a final after-action summary at shutdown
— even on Ctrl+C, since the WAV headers must be finalized to be
playable. The `--tui` conversation view shows a `REC <n>s` tag on the
diagnostics line whenever a dump is active.

Implementation: `AecDumpWriter` (`src/convobox/audio/aec.py`) is an
optional observer wired into `EchoCanceller` (`dump=` constructor
param), writing already-computed int16 frames at zero extra conversion
cost from both `feed_reverse` (playback thread) and `process` (capture
thread) — each of the three files is written by exactly one thread for
its whole lifetime, so no locking was needed, same reasoning as the
class's existing frame counters.

Once a real incident is captured this way, replay the WAV pair through
`EchoCanceller` directly (construct with any candidate `delay_ms`, feed
`reference.wav`'s frames via `feed_reverse`, `mic-raw.wav`'s via
`process`, compare `attenuation_db()`/`measurable_ceiling_db()`) to test
hypotheses against the *exact* real audio that misbehaved, repeatably,
without needing another live session per hypothesis.

Once AEC is in, the target barge-in design is a config mode
(`interrupt_mode: none | stop_audio | abort_turn`) — adopted from the
UAT suggestion notes — where `stop_audio` cuts TTS but lets the backend
finish, and `abort_turn` behaves like a safeword. These modes are only
meaningful with echo cancellation active (or headphones).

## Speech normalization decisions (implemented 2026-07-11)

UAT finding: Piper spoke markdown decoration aloud ("asterisk asterisk"
through every bold phrase). `strip_code_for_speech` (orchestrator.py)
now strips decoration while keeping the decorated words:

- code (fenced + inline): dropped entirely — nobody wants a for-loop
  recited;
- emphasis asterisks, underscore emphasis (lookaround-guarded so
  snake_case survives), `## ` headings, `> ` blockquotes, `-`/`+`
  bullets: markers removed, words kept;
- links: text spoken, URL never;
- slashes untouched (paths read fine — explicit UAT decision);
- numbered lists keep their numbers (spoken enumeration is natural);
- `3 * 4` becomes `3 4` — accepted collateral: backends emit emphasis
  constantly and multiplication rarely, and a spoken "asterisk" is
  wrong in both cases.

## UAT coverage matrix

The per-subsystem UAT checklist derived from these designs lives in
[UAT-checklist.md](UAT-checklist.md).

## Barge-in target design (decided 2026-07-11)

JP's call, and it matches industry practice: **open barge-in** -- any
sustained user speech during playback stops the rendering and is treated
as the next input, no safeword needed. ("The assistant's speech is
disposable; the human's time is not.") The formal pattern, borrowed from
the cleanest public formalization (OpenAI's Realtime API interruption
flow) and telephony's decades-old bargein attribute:

1. **AEC** so the mic hears the user over the TTS (the load-bearing
   prerequisite; see the AEC plan above).
2. **Sustained-speech threshold** (~200-300ms of confirmed speech)
   before cutting playback, so coughs and chair creaks don't kill a
   response. The VAD already exposes the needed signal.
3. **Stop rendering, don't abort the work**: playback and TTS stop; the
   backend turn keeps running; the utterance routes through normal
   busy/interject logic. The safeword remains the escalation that also
   aborts the work. Config: `interrupt_mode: none | stop_audio |
   abort_turn`, with `stop_audio` becoming the default once AEC is
   verified; today's behavior is `none`.
4. **The truncation problem**: when speech is cut at sentence 2 of 6,
   the backend believes it delivered all 6. We cannot edit backend
   session history (unlike Realtime's conversation.item.truncate), but
   we know exactly which synthesized chunks played -- so the interject
   can carry a marker such as "(interrupted you mid-response)" or
   "(heard up to: ...)". Exact wording to be decided during barge-in
   implementation UAT.

Spectrum note for the record: wake-word-gated barge-in (Alexa-style,
which our safeword-only behavior resembles) is the deliberate
false-trigger trade-off for far-field speakers; open barge-in is where
conversational agents live. ConvoBox is a conversational agent.

### Status update (2026-07-11, later): implemented

`interaction.interrupt_mode` + `barge_in_min_speech_ms` shipped (still
defaulting to "none"). The BargeInMonitor state machine fires once per
sustained-speech episode crossing the threshold during playback;
barge-in utterances bypass the overlap gate (they overlap by
definition) but NOT the spoken-text echo filter -- if the "interruption"
matches our own words, it was self-echo tripping the monitor and is
dropped with a warning instead of forwarded. Forwarded text carries the
truncation marker. Enabling a non-none mode without AEC logs a loud
self-interruption warning (headphones users may proceed deliberately).
Default flips to stop_audio only after room UAT signs off.

### Status update (2026-07-14): migrated to the two-axis preset system

`interrupt_mode: none | stop_audio | abort_turn` was replaced by
`interrupt_preset`, selecting one of five named presets on the two-axis
grid in [DESIGN-barge-in.md](DESIGN-barge-in.md) (`conversational`,
`patient`, `do-not-disturb`, `halt`, `take-over`). Strict superset, not a
behavior change for existing configs: the default (`do-not-disturb` =
`let-finish` + `drop`) is behaviorally identical to the old `none`
default. `BargeInMonitor` now keys off the resolved `on_current_turn`
axis value (`let-finish`/`mute`/`abort`) instead of the three old mode
strings. The one genuinely new capability is the `patient` preset's
`on_new_words: queue` behavior (`QueuedInterjection` in
`run_convobox.py`): a barge-in utterance is held, not dropped or
delivered immediately, and flushed automatically once the backend is
fully idle (no longer busy AND nothing playing) via the existing
working-watchdog's poll loop. Settings TUI's Interaction tab updated to
offer the five presets. "Default flips to stop_audio [conversational]
only after room UAT signs off" (above) still applies -- this migration
deliberately did not change the shipped default, see config.py's
InteractionConfig docstring.

### Status update (2026-07-14): OpenAI Realtime API citation verified against real docs

The "cleanest public formalization" and `conversation.item.truncate`
references above (2026-07-11) were written from general knowledge, never
checked against OpenAI's actual published documentation -- same class of
gap as the Alexa/Google citations elsewhere in this repo's research docs,
now closed the same way (read the real page, don't cite from memory).
Fetched `developers.openai.com/api/docs/guides/realtime-conversations`
directly. Confirmed accurate and, in one respect, more nuanced than this
doc implied:

- `conversation.item.truncate` is real and behaves exactly as described:
  a client-sent event, sent after `input_audio_buffer.speech_started` and
  stopping playback, carrying `item_id`/`content_index`/`audio_end_ms` (where
  to cut). Server response removes both the unplayed audio AND its
  associated text transcript -- "the realtime model doesn't have enough
  information to precisely align transcript and audio," so truncation
  discards the transcript rather than trying to precisely trim it,
  exactly the imprecision problem ConvoBox's own marker-based approach
  (item 4 above) sidesteps differently (annotate, don't try to precisely
  edit).
- **New nuance, not previously captured**: OpenAI's truncation behavior
  is connection-type-dependent. WebRTC/SIP gets AUTOMATIC server-side
  truncation on speech-start detection -- no client action needed at all.
  WebSocket connections are client-driven, same shape as `conversation.item.truncate`
  above. **ConvoBox is architecturally the WebSocket case**: it has no
  server-side buffer it controls the way OpenAI's WebRTC/SIP path does,
  so client-driven truncation-equivalent (the `BARGE_IN_MARKER` text
  annotation, since ConvoBox can't edit arbitrary backend session
  history) is the correct analog for ConvoBox's architecture, not a
  simplification of it. Worth knowing if a future full-duplex/VAP
  upgrade ever considers a server-buffer model closer to WebRTC/SIP's
  automatic path.

## Research grounding

The turn-taking, barge-in, backchannel, and interrupt design here is
grounded in published conversation-analysis and spoken-dialogue research —
see [CONVERSATION-DESIGN-REFERENCES.md](CONVERSATION-DESIGN-REFERENCES.md)
for the sources and the concrete finding adopted from each (backchannels as
continuers, the ~200 ms turn-transition target, TRP-aware yielding, and the
Voice Activity Projection upgrade path beyond silence-timer VAD).
