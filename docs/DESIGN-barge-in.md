# Design: configurable barge-in & interrupt handling

> **Version target: 0.3.0.** This is *new user-facing functionality* — a
> configurable interrupt system with new config surface — so by our
> numbering rule it's a MINOR bump (0.3.0), not a 0.2.x patch. (0.2.x is
> reserved for fixes to 0.2.0, e.g. the WASAPI octave bug in
> [KNOWN-ISSUES.md](KNOWN-ISSUES.md).)
>
> This design is implemented as part of
> [DESIGN-0.3.0-interaction-and-safety.md](DESIGN-0.3.0-interaction-and-safety.md)'s
> Phase 1, alongside the 0.3.0 TUI — see that doc for the full bundle
> (response tiering, approvals, and how they all share one underlying
> primitive) and priority order.

## Goal

Make interrupting ConvoBox feel like interrupting the voice assistants users
already know — and let them configure it to their own preference. We do
**not** invent a new interaction model; we expose the ones people already
have muscle memory for (Alexa, Google, ChatGPT voice) and let them pick.

Grounded in published turn-taking / backchannel research —
see [CONVERSATION-DESIGN-REFERENCES.md](CONVERSATION-DESIGN-REFERENCES.md).

## The core idea: expose axes, name the common cells

Every "interrupt pattern" a user might want is a combination of two
independent choices. We expose the two axes for power users and name the
useful cells as **presets** for everyone else.

- **Axis 1 — what happens to the assistant's *current turn*?**
  `let-finish` (audio + backend continue) · `mute` (stop audio, backend keeps
  working) · `abort` (stop audio *and* cancel backend work)
- **Axis 2 — what happens to the user's *new words*?**
  `drop` · `queue` (deliver when the turn ends) · `now` (deliver immediately —
  *steer* if still working, *fresh command* if aborted)

|  current turn ↓ / new words → | **drop** | **queue** | **now** |
|---|---|---|---|
| **let-finish** | do-not-disturb | patient | soft-steer |
| **mute audio** | *(odd)* | mute-then-queue | ★ **conversational** |
| **abort all** | halt | *(rare)* | take-over |

"More patterns than four" isn't an enumeration problem — the grid generates
them. Users sculpt behavior by setting the two axes; presets are just named
cells.

## Presets (the control surface)

| Preset | Cell (turn × words) | Feels like | Best for |
|--------|--------------------|-----------|----------|
| **`conversational`** ★ | mute × now (steer) | "stop talking over me, take my input, keep working" | default — natural without destroying work in flight |
| **`patient`** | let-finish × queue | it finishes, then does your thing | users who dislike interrupting |
| **`do-not-disturb`** | let-finish × drop | ignores you until done (safeword still stops) | focus / long tasks |
| **`halt`** | abort × drop | stop everything, await a fresh command | "just stop" |
| **`take-over`** | abort × now | stop everything and do the new thing now | the consumer-assistant reflex |

## Trigger axis (orthogonal): how is an interrupt *initiated*?

Separate from *what* the interrupt does is *what counts as* an interrupt:

- **`speech` (open / VAD-gated)** — any sustained user speech interrupts.
  The ChatGPT-voice / Gemini-Live model. Natural; needs AEC or headphones.
- **`push-word` (wake-word-gated)** — only a specific word interrupts. The
  Alexa / Google-Home model. Robust in noise / on speakers.
- **Safeword = always-on hard-abort.** In *every* preset and trigger, the
  safeword ("stop stop stop") is a guaranteed hard stop, honored
  mid-playback regardless of AEC. This is the non-negotiable safety floor.

## Resume word = the push-word trigger

A resume word *is* the `push-word` trigger, and for ConvoBox it's nearly free:
because we already run Whisper on every utterance, detecting a user-chosen
resume word is just a transcript match — the exact mechanism `SafewordDetector`
already uses. So it's a third instance of one proven pattern:

- `SafewordDetector` — hard-stop (shipped)
- `ConfirmwordDetector` — approval gate (shipped, 0.2.0)
- **`ResumeWordDetector`** — attention / interrupt (new): same normalized
  transcript match, same construction-time validation of the user's choice.

**User-selectable, validated at setup.** Ship a strong built-in default
*and* an advanced "choose your own." Validate the pick the way
`ConfirmwordDetector` validates approval words: it should be **distinctive &
multisyllabic** (rarely false-fires in normal speech) and **Whisper-safe**
(test-transcribe a few times at setup — remember "break break break" →
"Frank Frank"; warn if it keeps getting mangled). Naming the assistant
("Hey Athena") is welcome personalization.

A *dedicated* wake-word engine (openWakeWord etc.) is only needed for a
future low-power "asleep until called" idle mode — **deferred**; transcript
matching covers interrupt-while-active.

## Pause/resume listening (JP, 2026-07-14)

A fourth interaction primitive, related to but distinct from barge-in's
push-word trigger: a spoken **"stop listening" / "pause listening"** puts
ConvoBox into a standing paused state — every utterance is ignored except
the resume word — until the resume word is heard again, which resumes normal
listening. In JP's framing: *"like interrupt, but interrupt and stop
processing all speech, then listen for resume word."*

**Not the deferred wake-word roadmap item.** `docs/ROADMAP.md`'s "Wake word
(post-0.5)" item is specifically about a **dedicated low-power spotter
engine** (openWakeWord etc.) plus **speaker enrollment** (biometric
voice-trained, so other speakers can't trigger it) — that's what's deferred,
for closing the open-mic *trust* boundary. Pause/resume needs neither: it
rides on the Whisper transcription that's already running continuously
(same "transcript matching covers interrupt-while-active" principle used
for the push-word barge-in trigger), and it's *user-initiated* per session,
not an always-asleep-from-boot state. Cheap, buildable now.

**Priority ordering** (highest first — this governs where the check sits in
`run_convobox.py`'s main loop, ahead of the existing overlap/echo/confidence
gates):

1. **Safeword** — always checked first, unconditionally, regardless of
   pause state (a paused session must still be hard-stoppable; idempotent
   no-op if nothing is running, same as today). Does **not** by itself
   change pause state — pause and hard-stop are orthogonal axes.
2. **If currently paused** (and not a safeword match) — check *only* the
   resume word:
   - matched → resume (exit paused state); the utterance is never routed
     to the backend as a command.
   - not matched → drop silently (log at debug, don't reach the
     overlap/echo/confidence gates or the orchestrator at all).
3. **If not paused** (and not a safeword match) — check the pause phrase:
   - matched → hard-stop the backend (interrupts in-flight work, same as
     the safeword's abort) **and** enter the paused state; the utterance
     is never routed to the backend as a command.
   - not matched → today's normal flow, unchanged.

**One resume word, two jobs.** The same `ResumeWordDetector` instance serves
both the push-word barge-in trigger *and* resuming from pause — "get
ConvoBox's attention," just triggered from different starting states
(mid-response vs. fully paused). Pause/resume does **not** require barge-in's
`push-word` trigger to be configured; it's independent and on by default
(using `DEFAULT_RESUME_WORD` if the user never sets one).

**New detector: `PauseListeningDetector`.** Same shape as `SafewordDetector`
(a list of phrases, matched deterministically; only construction guard is
"doesn't normalize to nothing") — **not** `ConfirmwordDetector`'s stricter
guard, since accidentally saying "stop listening" is benign (you just say
the resume word again), not destructive. Default phrases: `["stop listening",
"pause listening"]`.

**Config schema change:** `resume_word` moves out of `interrupt:` (see below)
to `interaction:` top-level, since it's now shared by two independent
features, not owned by barge-in alone.

## Backchannel filtering (research-grounded, load-bearing)

"mm-hmm / uh-huh / yeah / right / oh" are **continuers** — they signal "keep
going," the opposite of a bid for the floor (Schegloff 1982; Ward &
Tsukahara 2000). A `speech`-triggered barge-in **must not** fire on them, or
it will feel broken. Rule: a **short, affirmation-class token** does not
count as an interrupt. (Bonus later: the assistant *producing* backchannels
is a large naturalness win.)

## The coding-agent nuance (why the default isn't `take-over`)

The consumer reflex is `take-over` — stop everything and respond to me. But a
person interrupting Siri costs nothing, whereas aborting an agent that's
three tool-calls into a refactor can corrupt state. So ConvoBox's **default
is `conversational` (mute + steer)** — it *feels* like take-over (the
assistant shuts up and takes your input) without hard-aborting work in
flight. True `abort` is opt-in (`halt`/`take-over` presets), and the
safeword is always the explicit hard stop. This is the README's
"voice-aware risk policy" made concrete.

## Default-by-safety

Out of the box, pick the trigger by whether the setup can support open
barge-in safely:

- **Headphones or AEC on** → `speech` trigger (natural barge-in).
- **Bare speakers without AEC** → auto-fall back to `push-word`, so a bad
  acoustic setup never surprises the user with dropped audio.

Controlled by `only_when_safe: true`.

## Latency target (acceptance criteria)

Human turn-transitions cluster around **~200 ms** (Stivers et al. 2009). So:

- **Interrupt-stop latency** (user speech onset → audio actually stops) and
  **response-start latency** target **~200 ms**, sub-second as the ceiling.
- Instrument both as tracked metrics (same template as the AEC telemetry),
  so "feels fast" becomes a number we can regress against.

## Config schema

```yaml
interaction:
  resume_word: "Athena"            # shared: push-word barge-in trigger AND pause/resume
                                  # (default was "ConvoBox" -- confidently
                                  # mis-heard as "Control Box" by Whisper
                                  # every time; verify any custom choice
                                  # round-trips through STT before relying on it)
  pause_listening_phrases:       # enters the paused (resume-word-only) state; hard-stops in flight
    - "stop listening"
    - "pause listening"
  interrupt:
    mode: conversational      # conversational | patient | do-not-disturb | halt | take-over
    # advanced — a preset just fills these in; override to sculpt any cell:
    on_current_turn: mute     # let-finish | mute | abort
    on_new_words:    now      # drop | queue | now
    trigger:         speech   # speech | push-word (uses interaction.resume_word)
    sensitivity_ms:  250      # sustained speech before it counts (backchannels excluded)
    only_when_safe:  true     # auto-fall back to push-word on speakers w/o AEC
  # safeword hard-stop is always on, in every mode, paused or not (see safeword config)
```

## Maps onto primitives we already have

Mostly orchestration wiring, not new backend work:

- `queue`, `steer` (interject), `hard_stop` are the three backend verbs
  already implemented per adapter → Axis 2 (`queue`/`now`/abort) picks which.
- `mute` = stop the `AudioPlayer`; Axis 1 picks whether audio mutes and
  whether the backend turn is aborted.
- `BargeInMonitor` already is the sustained-speech state machine → extend it
  with backchannel exclusion and the trigger/axis wiring.
- `BARGE_IN_MARKER` already handles the truncation problem (annotate history
  since we can't edit the backend's).

**The two axes fire at DIFFERENT pipeline stages -- this is the part that
isn't just "orchestration wiring," found while actually implementing it.**
Axis 1 for `mute`/`abort` must be decided at the RAW AUDIO level, chunk by
chunk, before STT -- that's what `BargeInMonitor` already does, fast,
because it can't wait for a transcript. Axis 1 = `let-finish` means that
fast trigger never runs at all. Axis 2, by contrast, is decided at the
TRANSCRIBED TEXT level, in the existing overlap-gate code that runs AFTER
STT for any utterance that overlapped playback -- completely independent of
whether axis 1's fast trigger fired:
- `drop` -- today's existing overlap-gate behavior, unchanged.
- `now` -- forward immediately with `BARGE_IN_MARKER` (today's existing
  "barged_in" forwarding path, now gated on axis 2 explicitly rather than
  implicitly following whenever axis 1 fired).
- `queue` -- the one genuinely NEW mechanism: hold the utterance's text in a
  small pending list, flush it (in order) once playback has ended AND the
  backend is idle. Doesn't touch `BargeInMonitor`'s state machine at all --
  it's an addition to the overlap-gate branch.

This means `patient`/`do-not-disturb` (`let-finish` + queue/drop) never
need to consult `BargeInMonitor`'s fast trigger in the first place -- they
operate purely on the overlap gate's existing "did this utterance overlap
playback" signal. `halt` (`abort` + `drop`) DOES need the fast trigger (to
abort quickly) but its drop means the utterance that caused it is simply
absorbed as "the stop," never forwarded -- same as `do-not-disturb`'s drop,
the only difference being whether the trigger also fired the abort.

**The migration is a strict superset, not a breaking redesign** (noticed
while scoping the config/`BargeInMonitor` migration, `src/convobox/interrupt_presets.py`):
today's three `interrupt_mode` values map cleanly onto three of the five new
presets --

| Old `interrupt_mode` | New preset | Why |
|---|---|---|
| `none` | `do-not-disturb` | half-duplex: assistant keeps talking (let-finish), your words during playback are simply dropped (drop) -- exactly that preset's axes. |
| `stop_audio` | `conversational` | mute + forward now -- today's open-barge-in behavior IS the shipped default's axes. |
| `abort_turn` | `take-over` | abort + forward now. |

`patient` and `halt` are genuinely new capability, not expressible in the
old three-mode scheme. Worth stating plainly in whatever PR does the actual
config migration: existing `convobox.yaml` files using `interrupt_mode`
aren't losing anything, they're being handed a name for what they already
had plus two new options -- not a downgrade some users need to relearn.

**Migrated 2026-07-14**: `config.py`'s `InteractionConfig.interrupt_mode`
is now `interrupt_preset` (validated against `PRESETS` via
`resolve_preset`), `BargeInMonitor` keys off the resolved
`on_current_turn` axis, and `patient`'s `queue` behavior is implemented
(`QueuedInterjection`, flushed by the existing working-watchdog once the
backend is fully idle) -- see `docs/DESIGN-echo-and-barge-in.md`'s
2026-07-14 status update for the concrete mechanism.

## Phasing

**In 0.3.0:** the two-axis model + presets; the `speech`/`push-word` triggers
(resume word via transcript match + `ResumeWordDetector`); pause/resume
listening (`PauseListeningDetector` + the resume word, shared); backchannel
filtering; default-by-safety; interrupt-stop / response-start latency
instrumentation.

**Deferred (post-0.3.0):** Voice Activity Projection for predictive
endpointing (beyond silence-timer VAD); a low-power idle wake-word *engine*;
TRP-aware graceful yielding; speaker verification / voice enrollment (so the
TV can't trip the resume word); `duck` mode; full-duplex generative direction.

## Open questions (validate in UAT)

- Confirm `conversational` as the shipped default (vs. `take-over` for the
  consumer reflex).
- The backchannel token set — start with English affirmations; per-language
  later.
- Resume-word validation UX — how many test-transcriptions, what threshold to
  warn.
- Per-backend behavior of `BARGE_IN_MARKER` (opencode, then Claude Code /
  Codex) — this is where the barge-in and backend-validation cycles overlap.
- Should resuming from pause produce an audible/logged acknowledgment (a
  short tone or "listening again"), or resume silently? Leaning toward a
  short acknowledgment (matches Alexa/Google's wake confirmation), but
  needs a live-UAT check against feeling naggy.
- **False-interruption recovery (identified 2026-07-14, via LiveKit Agents
  research — see `docs/CONVERSATION-DESIGN-REFERENCES.md` §4).**
  `BargeInMonitor` fires purely from VAD-level sustained-speech timing,
  before STT can confirm content — so a false positive (backchannel, cough,
  ambient noise misclassified as speech) stops playback (and, on `abort`,
  hard-stops the backend turn) for good, with no resume path once the
  transcript comes back empty/backchannel-shaped. Would a resume mechanism
  need to reconstruct "how much was already spoken" and interact with
  `interaction.tier_responses`'s own reveal-state? Not scoped or built —
  needs real design work, and the audio behavior can't be verified without
  a live mic session.
