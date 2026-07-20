# ConvoBox quickstart

From zero to talking to a coding agent. Assumes Python 3.12+ and a backend
you can reach (the examples use [OpenCode](https://github.com/anthropics/…),
which runs a local server; `claude-code` and `codex` work too and are spawned
as subprocesses).

## 1. Install

```bash
git clone https://github.com/LegionForge/convobox
cd convobox
uv sync                    # or: pip install -e .
```

Optional extras, installed only if you want them:

```bash
uv sync --extra aec        # acoustic echo cancellation (WebRTC AEC3, Windows wheels)
uv sync --extra dev        # test/lint tooling
```

ConvoBox never bundles a TTS/STT engine you didn't ask for. Piper (the default
voice engine) and the Whisper STT model download on first use.

## 2. Pick a voice

Piper has 160+ voices. Browse, audition through your speakers, and save your
choice straight into the config:

```bash
python scripts/voice_picker_tui.py     # full-screen: arrow keys, / to filter,
                                        # P to play, Enter to choose, Q to save
```

(There's also a scriptable REPL/flag version at `scripts/voice_picker.py`.)

## 3. Find your audio device (if the default isn't right)

Especially on Windows, the same jack shows up under several host APIs with
different latency and behavior. List them and play a test tone:

```bash
python scripts/audio_devices.py         # list output + input devices
python scripts/audio_devices.py --test 5   # play a tone to device 5
```

Whichever one you *hear* is the device to pin.

## 4. Configure

```bash
cp convobox.example.yaml convobox.yaml
```

Edit `convobox.yaml` — it's fully commented. The essentials:

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

ConvoBox runs with no config file at all (all defaults) — the file only
overrides what you set.

## 5. Run

Start your backend first (for OpenCode: `opencode serve`). Then, without a
microphone, confirm the whole loop works:

```bash
python scripts/run_convobox.py --text "Reply with one short sentence: it works."
```

You should hear the reply. Now go live:

```bash
python scripts/run_convobox.py
```

Speak a command; it transcribes, sends it to the agent, and speaks the reply.

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
| Idle (wake-word only) | Passively spotting the wake word; not transcribing general speech | dim visual, no sound |
| Active listening | Woken; capturing and transcribing speech | visual change + activation earcon |
| Command captured | Utterance finalized, STT complete | brief distinct acknowledgment cue |
| Backend working | Target CLI is executing; visually distinct from "listening" since you can still interject | visual only |
| Responding (TTS playback) | Speaking a response; interruptible at any point (barge-in returns to Active listening) | visual only |
| **Hard stop (safeword heard)** | Safeword detected; execution is being halted | **its own unmistakable audio/visual class — never a louder variant of another state** |
| Stopped / muted | Explicitly told to stop; no wake-word spotting either | fully dim, no sound |

Inbound/outbound profanity filtering (what you say vs. what TTS speaks
back) is planned as a configurable option, off by default.

## Troubleshooting

- **No audio, but it's transcribing you fine.** Almost always the output
  device. Run `python scripts/audio_devices.py --test <n>` down your devices
  until you hear a tone, then pin that one.
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
