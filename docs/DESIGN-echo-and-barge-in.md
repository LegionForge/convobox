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

## Research grounding

The turn-taking, barge-in, backchannel, and interrupt design here is
grounded in published conversation-analysis and spoken-dialogue research —
see [CONVERSATION-DESIGN-REFERENCES.md](CONVERSATION-DESIGN-REFERENCES.md)
for the sources and the concrete finding adopted from each (backchannels as
continuers, the ~200 ms turn-transition target, TRP-aware yielding, and the
Voice Activity Projection upgrade path beyond silence-timer VAD).
