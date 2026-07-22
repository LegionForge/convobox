# Credits & attributions

ConvoBox stands on a lot of other people's work — code, models, research,
and the accumulated design wisdom of every voice assistant that came before
it. This file names those sources. It's the human-readable acknowledgments
layer; for the rigorous, per-package **license** analysis (and the piper
GPL question), see [`DEPENDENCY_LICENSE_AUDIT.md`](DEPENDENCY_LICENSE_AUDIT.md).

ConvoBox itself is **MIT-licensed** — © 2026 JP Cruz / LegionForge — see
[`LICENSE`](LICENSE).

---

## Software ConvoBox is built on

Roles below; licenses as commonly published, with
[`DEPENDENCY_LICENSE_AUDIT.md`](DEPENDENCY_LICENSE_AUDIT.md) as the
authoritative, metadata-verified source.

| Project | Role in ConvoBox | License |
|---------|------------------|---------|
| [sounddevice](https://python-sounddevice.readthedocs.io/) + [PortAudio](http://www.portaudio.com/) | Cross-platform audio capture & playback | MIT |
| [NumPy](https://numpy.org/) | Audio buffer math, resampling | BSD-3 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Voice-activity detection / utterance segmentation | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + [CTranslate2](https://github.com/OpenNMT/CTranslate2) | Speech-to-text inference | MIT |
| [Piper](https://github.com/rhasspy/piper) (`piper-tts`) | Local text-to-speech | GPL-3.0-or-later — see audit |
| [aec-audio-processing](https://pypi.org/project/aec-audio-processing/) (WebRTC APM / AEC3) | Acoustic echo cancellation (optional `[aec]` extra) | BSD-3 |
| [httpx](https://www.python-httpx.org/) · [httpx-sse](https://github.com/florimondmanca/httpx-sse) | Backend HTTP + SSE transport | BSD-3 / MIT |
| [Pydantic](https://docs.pydantic.dev/) | Config schema & validation | MIT |
| [PyYAML](https://pyyaml.org/) | Config file parsing | MIT |

Dev-only tooling (not distributed): pytest, pytest-asyncio, mypy, ruff,
bandit.

## Models & voices

- **Whisper** speech-recognition models — originally by OpenAI (MIT),
  run via SYSTRAN's CTranslate2 conversions (e.g. `Systran/faster-whisper-base`).
- **Silero VAD** model — by the [Silero Team](https://github.com/snakers4/silero-vad) (MIT).
- **Piper voices** (e.g. `en_GB-alba-medium`) — from the Piper voices
  collection; **each voice carries its own license and attribution** (varies
  per voice — check the individual voice's model card before redistributing).

## Research & design foundations

ConvoBox's turn-taking, barge-in, backchannel, and interrupt design is
grounded in published conversation-analysis, pragmatics, and spoken-dialogue
research rather than invented from scratch. The specific works — Sacks/
Schegloff/Jefferson on turn-taking, Schegloff and Ward & Tsukahara on
backchannels, Stivers et al. on turn timing, Skantze and the Voice Activity
Projection work on machine turn-taking, and Grice on the cooperative
principle — are catalogued with the concrete finding we adopt from each in
[`docs/CONVERSATION-DESIGN-REFERENCES.md`](docs/CONVERSATION-DESIGN-REFERENCES.md).

## Product-design influences (prior art we deliberately match)

ConvoBox intentionally mirrors interaction patterns users already know, so
they don't have to learn a new mental model:

- **Amazon Alexa / Google Home** — wake-word-gated interruption ("say the
  word to barge in"); the model behind our `push-word` trigger.
- **Siri** — on-device, close-talk voice interaction.
- **ChatGPT Advanced Voice, Gemini Live, OpenAI Realtime API** — open,
  VAD-gated barge-in and the response-truncation pattern; the model behind
  our `natural` trigger and the `BARGE_IN_MARKER`.

These are acknowledged as influences on *interaction design*; no code or
assets from them are used.

## Development

Built by JP Cruz (LegionForge). Portions of the implementation, research
synthesis, and design iteration were developed with AI assistance (Claude);
see the `Co-Authored-By` trailers in the git history.

For the repo-wide attribution convention used when Codex, Claude Code, or
opencode edits are committed, see [`docs/AI-ATTRIBUTION.md`](docs/AI-ATTRIBUTION.md).
