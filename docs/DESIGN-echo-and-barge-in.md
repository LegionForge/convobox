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
