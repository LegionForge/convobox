# ConvoBox quickstart

From zero to talking to a coding agent. Assumes Python 3.12+ and a backend
you can reach (the examples use [OpenCode](https://github.com/anthropics/…),
which runs a local server; `claude-code` and `codex` work too and are spawned
as subprocesses).

## 1. Install

Just want to run it? It's a real PyPI package — no clone needed:

```bash
pip install legionforge-convobox    # or: pipx install legionforge-convobox
```

This gives you the `convobox`, `convobox-settings`, and
`convobox-audio-devices` commands used throughout this guide. Contributing,
or want the `dev` extra's test/lint tooling? Clone instead:

```bash
git clone https://github.com/LegionForge/convobox
cd convobox
uv sync                    # or: pip install -e .
```

From a source checkout, run each tool as `python scripts/<name>.py` instead
of `convobox-<name>` (e.g. `python scripts/audio_devices.py --setup`).

Optional extras, installed only if you want them — same names either way:
`pip install "legionforge-convobox[aec]"` or, from a source checkout,
`uv sync --extra aec`:

| Extra | What it adds |
|---|---|
| `aec` | acoustic echo cancellation (WebRTC AEC3, Windows wheels) |
| `cuda` | GPU inference for STT (`stt.device: cuda`/`auto`), ~1GB, CUDA-only |
| `piper` | Piper TTS voices, 160+ (opt-in; Kokoro is the default engine) |
| `dev` | test/lint tooling (source checkout only) |

ConvoBox never bundles a TTS/STT engine you didn't ask for. The default TTS
engine (Kokoro) and the Whisper STT model download on first use.

## 2. Pick a voice

Kokoro (the default engine) ships a small built-in set of voices, cycled
with Space/Left/Right on the `tts.voice` field in the Settings TUI
(`convobox-settings`). For 160+ additional voices, switch `tts.engine` to
`piper` (requires the `piper` extra) and press `[V]` on the voice field to
browse, audition, download, and save straight into the config from Piper's
full catalog.

## 3. Find your audio device (if the default isn't right)

Especially on Windows, the same jack shows up under several host APIs with
different latency and behavior. The guided setup tests your default speaker
and mic and saves whichever one works:

```bash
convobox-audio-devices --setup
```

Or list devices and play a test tone by hand:

```bash
convobox-audio-devices          # list output + input devices
convobox-audio-devices --test 5 # play a tone to device 5
```

Whichever one you *hear* is the device to pin.

Already picked devices in `convobox.yaml` and just want to confirm they're
right? The Settings TUI (`convobox-settings`) can test both in place: select
**Audio → Input device** or **Output device** and press `[t]`. Testing the
input device records ~1 second from the mic and plays the recording
straight back through the configured output device — if you hear yourself,
both ends of the pipeline are correctly wired.

## 4. Configure

ConvoBox runs with no config file at all (all defaults) — write one only to
override what you need. Easiest path: `convobox-settings` (the Settings
TUI) edits and validates `convobox.yaml` for you, creating it on first save.

From a source checkout, you can instead start from the fully-commented
example file:

```bash
cp convobox.example.yaml convobox.yaml
```

Either way, the essentials:

```yaml
backend:
  name: opencode
  url: http://localhost:4096
tts:
  voice: en_US-lessac-medium     # whatever step 2 saved
```

If step 3 gave you a specific device, pin it with its **full name including
the host API**:

```yaml
audio:
  output_device: "Headphones (Realtek(R) Audio), MME"
```

## 5. Run

Start your backend first (for OpenCode: `opencode serve`). Then, without a
microphone, confirm the whole loop works:

```bash
convobox --text "Reply with one short sentence: it works."
```

You should hear the reply. Now go live:

```bash
convobox
```

Speak a command; it transcribes, sends it to the agent, and speaks the reply.
(Source checkout: `python scripts/run_convobox.py` in place of `convobox`.)

## Updating

```bash
pip install --upgrade legionforge-convobox    # or: pipx upgrade legionforge-convobox
```

Source checkout: `git pull` then `uv sync` (or `pip install -e .` again).
See [CHANGELOG.md](../CHANGELOG.md) for what changed.

## Talking to it

- **Interrupt / abort:** say a safeword — `stop stop stop` by default (add
  your own in the config). It hard-stops the agent's current work and keeps
  listening. Honored even mid-sentence.
- **Barge-in** (talking over a response to redirect it) is off by default
  (`interaction.interrupt_preset: do-not-disturb`); enable it with
  `interaction.interrupt_preset: conversational` once you have echo
  cancellation on or are wearing headphones (otherwise the assistant hears
  its own voice and interrupts itself). Other presets (`patient`, `halt`,
  `take-over`) trade off differently -- see `docs/DESIGN-barge-in.md`.
- **Same-room speakers + mic?** Turn on `audio.echo_cancellation: true`
  (needs the `[aec]` extra) so it doesn't transcribe its own speech.

## Listening states & indicators

Hands-free use means there's no screen focus to rely on for feedback, so
state changes need both a visual and (where noted) an auditory indicator,
Alexa-style. Modeled as an explicit state machine rather than ad hoc flags:

| State | Description | Indicator |
| --- | --- | --- |
| Off | Not running | none |
| Idle (resume-word only) | Passively spotting the resume word; not transcribing general speech | dim visual, no sound |
| Active listening | Woken; capturing and transcribing speech | visual change + activation earcon |
| Command captured | Utterance finalized, STT complete | brief distinct acknowledgment cue |
| Backend working | Target CLI is executing; visually distinct from "listening" since you can still interject | visual only |
| Responding (TTS playback) | Speaking a response; interruptible at any point (barge-in returns to Active listening) | visual only |
| **Hard stop (safeword heard)** | Safeword detected; execution is being halted | **its own unmistakable audio/visual class — never a louder variant of another state** |
| Stopped / muted | Explicitly told to stop; no resume-word spotting either | fully dim, no sound |

Inbound/outbound profanity filtering (what you say vs. what TTS speaks
back) is planned as a configurable option, off by default.

## Troubleshooting

- **No audio, but it's transcribing you fine.** Almost always the output
  device. Run `convobox-audio-devices --test <n>` down your devices until
  you hear a tone, then pin that one.
- **`Invalid sample rate` crash.** A WASAPI device rejecting the voice's rate
  — pin the MME or DirectSound variant of the same device instead (they
  resample), or update to a build with playback resampling.
- **"Is it broken or thinking?"** A long silent pause during a backend task
  logs `backend still working (Ns)…`. That's it working, not hung — say a
  safeword to abort if you want.
- **Two instances / mic contention.** Mic mode takes a single-instance lock;
  a duplicate launch exits with an explanatory error. On Windows, note that
  ONE launch shows as TWO python processes (a launcher + its worker).

See [DESIGN-echo-and-barge-in.md](DESIGN-echo-and-barge-in.md) for the audio
design and [UAT-checklist.md](UAT-checklist.md) for the full behavior matrix.
