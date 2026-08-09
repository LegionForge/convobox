# Design: mic mute/unmute as a hard, physical-only listening cutoff (distinct from pause/resume)

Status: DESIGN RECORDED 2026-08-07, not implemented. Origin: a live UAT
finding the same day (`docs/KNOWN-ISSUES.md`'s "VAD segmenter's
per-window model call is synchronous..." entry, PR #231's live-UAT
follow-up) plus JP's own direct request in the same session.

## Why this needs a design note

Live UAT this session found that today's "pause listening" still runs
every incoming utterance through VAD + STT -- it just gates what happens
to the resulting *transcript* (`ListeningGate.observe(transcript)`,
`scripts/run_convobox.py`, called AFTER `transcriber.transcribe()` has
already run). That's why a real freeze reproduced specifically under
rapid-fire hotword-biased speech *while paused*: pause was never a
cutoff for STT/VAD load, only for what ConvoBox does with the result.

JP's own read, live: "stop and resume listening seem to be significantly
more reliable... assuming 1) I don't pound the paused client with lots
of hotwords, and 2) I don't spam the client while paused with lots of
conversation." Then, proposing this: "maybe I(we) have to re-envision or
re-work the purpose of the 'hands-free' pause listening/resume listening
process... maybe even a 'mute listening' or something should be in
there as well, with a physical 'unmute' as the only pathway back."

So this isn't a UI polish request -- it's a proposed second, genuinely
different control tier, motivated by a real gap the first one can't
close by design.

## Current state, precisely (grounds everything below)

- **`ListeningGate`** (`scripts/run_convobox.py`): pure state machine,
  `observe(transcript: str)`. Operates on text. Nothing upstream of STT
  changes behavior when paused -- `MicrophoneStream` keeps capturing,
  `UtteranceSegmenter.segment()`/`feed_async()` keeps running every
  window through Silero, `transcriber.transcribe()` keeps running on
  every completed utterance. Pause only decides what happens to the
  transcript that comes out the other end (drop it, unless it's the
  resume word).
- **Dual access, deliberately**: a spoken pause/resume phrase and the
  web UI's Stop/Resume Listening button both act on the exact same
  `ListeningGate` (`WebListeningBridge`, `src/convobox/web/bridge.py`) --
  "whichever happens first is simply what's true next." This is the
  "hands-free" property JP wants to keep for pause/resume specifically.
- **Where chunks actually flow**: `_mic_chunks(mic)` (an async generator,
  `scripts/run_convobox.py`) yields processed audio chunks from
  `MicrophoneStream.stream()`; the main loop does
  `async for utterance in segmenter.segment(_mic_chunks(mic))`. There is
  currently no gate anywhere in that path -- every captured chunk reaches
  the segmenter unconditionally.

## Proposed shape: mute is a cutoff *before* the segmenter, not a second gate on the transcript

**Core design decision:** mute must stop chunks from reaching
`segmenter.feed_async()` at all, not add another `if muted` branch
downstream of STT -- otherwise it inherits the exact same load-under-
stress problem pause has today, and solves nothing.

Concretely, one of two shapes (open question, not decided here):

1. **Gate inside `_mic_chunks`**: skip yielding (or yield nothing,
   `continue`) while muted. `segmenter.segment()`'s own `async for chunk
   in chunks` loop simply never sees muted-period audio -- no VAD calls,
   no STT calls, no thread submissions, full stop.
2. **Gate at the consumption point**: wrap the `_mic_chunks(mic)` call
   itself with a filter before it reaches `segmenter.segment()`.

(1) is likely simpler -- one check, one place, no new wrapping
generator -- but needs care around the segmenter's own in-progress-
utterance state (`_speech`, `_triggered`, etc. in
`UtteranceSegmenter`): if mute engages mid-utterance, that partial state
needs an explicit `flush()` or reset, the same way pause already
hard-stops in-flight work rather than leaving it dangling.

**Physical-only unmute, by design, matching JP's stated intent:** no
`ResumeWordDetector`-style voice path for unmute at all. While muted, no
audio reaches STT, so there is no transcript to check a resume word
against even in principle -- this isn't a restriction to work around,
it's the direct consequence of cutting off before the segmenter, and it
matches what JP explicitly asked for ("a physical 'unmute' as the only
pathway back").

**API shape, mirroring the existing pattern
(`/api/listening`/`WebListeningBridge`):** a new `/api/mute` route and
`WebMuteBridge` (or similar), same trust boundary (loopback-only, no
auth, matching every other mutating route in `src/convobox/web/app.py`).
Kept as a **separate** gate/bridge from `ListeningGate`/
`WebListeningBridge`, not folded into it -- pause and mute are
orthogonal axes (a muted session's pause state doesn't need to change,
and vice versa), same reasoning `ListeningGate`'s own docstring already
uses for why pause and hard-stop are separate.

## Open questions for JP (not decided here)

1. **Does engaging mute also hard-stop in-flight backend work**, the
   same way pause does today (`WebListeningBridge.pause()`'s docstring:
   "hard-stops in-flight playback and backend work, not just a future-
   transcript gate")? Arguable either way -- mute is fundamentally about
   audio *input*, not backend state, so a case exists for leaving
   in-flight work alone. Real UX call, not a technical one.
2. **Can mute and pause both be active at once, and what does the UI
   show if so?** E.g. does muting while paused make sense at all (audio
   already isn't being acted on), or should the UI simplify to "mute
   implies/supersedes pause" while muted?
3. **UI placement and iconography.** Two more buttons in an already
   fairly full ribbon (`Clear history`, `Stop/Resume listening`, `Stop`,
   `Settings`, `Quit`). Worth exploring whether mute/unmute reads as a
   clearly *different* control from pause/resume at a glance (different
   icon shape/color, not just adjacent text) -- same WCAG 1.1.1/1.4.1
   discipline already used for the pause/resume icon (icon+text, never
   icon-alone; never color-alone) applies here, arguably more so since
   the whole point is that these two controls must not be confused for
   each other.
4. **Does the TUI need an equivalent**, or is mute a web-UI-only
   control for now (pause/resume already has both voice and TUI/web
   button access; mute's "physical-only" premise doesn't obviously need
   a TUI keybinding, but worth deciding explicitly rather than by
   default).

## Slicing (proposed, not committed to)

- **Slice 0:** decide the open questions above with JP; confirm the
  chunk-gating shape (`_mic_chunks` vs. a wrapping filter) against the
  segmenter's in-progress-utterance state, including whether an
  in-progress utterance should `flush()` or be discarded when mute
  engages mid-speech.
- **Slice 1 (safe, read-only):** add the mute gate in the mic loop and a
  `MuteGate` state machine (mirroring `ListeningGate`'s own
  pure-state-machine, independently-unit-testable shape), log every
  chunk that gets dropped while muted at DEBUG level, no UI yet. Proves
  the mechanism doesn't regress the existing pause/resume/safeword paths
  before any control surface exists to trigger it.
- **Slice 2:** `/api/mute` route + `WebMuteBridge`, unit-tested the same
  way `WebListeningBridge`/`WebSafewordBridge` already are.
- **Slice 3:** web UI mute/unmute buttons, live-verified in a real
  browser (same pattern used for the pause/resume icon this session) --
  and, ideally, one real live-mic UAT pass repeating the exact rapid-fire
  hotword-stress scenario that surfaced this gap, confirming muted state
  genuinely produces zero `Processing audio` load during the stress
  window.

## What this note deliberately does not do

No code was written. The exact chunk-gating point ((1) vs. (2) above)
and the mid-utterance-mute state question are real implementation
decisions that need Slice 0's answers first, not guessed here.
