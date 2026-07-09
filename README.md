# ConvoBox

A local, backend-agnostic voice frontend for CLI coding agents.

## Purpose

ConvoBox sits between you and whichever coding agent CLI you're driving —
Claude Code, Codex, OpenCode, and eventually others — and lets you work by
voice instead of (or alongside) the keyboard. It is not tied to any single
backend: the goal is a portable voice setup you can point at whatever tool
you're using that day, rather than a feature bolted onto one product.

## Direction

- **Natural, full-duplex conversation, not push-to-talk.** Continuous
  listening with voice-activity detection, not hold-a-key-to-talk. You
  should be able to interject the way you would with a person, not wait for
  a turn.
- **Local-first.** Speech-to-text and text-to-speech run on-device by
  default. No audio has to leave the machine for the core loop to work.
  This isn't just a privacy preference: it avoids metered cloud STT/TTS
  billing, keeps the raw voice-processing step out of the token budget of
  whatever coding agent you're actually talking to, and gives you a local
  pipeline you can tune to your own voice. "Local" doesn't mean "hardcoded
  to the device in front of you," though — the capture/indicator layer and
  the actual STT/TTS compute should stay decoupled, so the heavy
  processing can later run on a beefier machine on your own private
  network (e.g. via Tailscale) with a thin client on a laptop or phone,
  without leaving infrastructure you control.
- **Backend-agnostic by design.** A thin adapter interface
  (`send_text`, `send_interject`, `send_hard_stop`, `is_busy`) is
  implemented per backend, preferring each tool's native structured/headless
  interface (e.g. streamed JSON events, an HTTP+SSE server) over scraping
  terminal output, with a PTY/keystroke fallback where nothing better
  exists.
- **Two distinct interrupt semantics.** A *soft interject* ("oh, also—")
  shouldn't derail a long-running task; a *hard stop* (a deliberate,
  deterministic safeword) should abort it immediately. These are modeled
  separately rather than collapsed into one "interrupt" action.
- **Voice-aware, not voice-restricted, risk policy.** Destructive actions
  can warrant stricter confirmation when triggered by voice, given STT
  misrecognition and ambient-pickup failure modes that keyboard input
  doesn't have. That default should be configurable per user, not
  hardcoded — the same agency a keyboard session already has should be
  available on the voice side too.

## Architecture

```
   mic  →  VAD  →  local STT  →  safeword check  →  orchestrator  →  backend adapter
                                       ↓                                    ↓
                                  (deterministic,                    Claude Code /
                                   no LLM in this                    Codex / OpenCode /
                                   path)                              ... (per adapter)
                                                                            ↓
                                                                    local streaming TTS
                                                                    (prose only, skips
                                                                     raw diffs/code)
```

- **Audio capture** — continuous mic input, segmented into utterances by a
  neural voice-activity detector (tolerant of pauses/disfluencies).
- **Local STT** — transcribes each segment on-device.
- **Safeword detection** — deterministic keyword-spotting over each
  transcript, intentionally kept out of any LLM's hands so a hard stop
  can't be second-guessed by a model.
- **Orchestrator** — tracks each backend's busy/idle state and routes an
  utterance as a fresh command, a soft interject, or a hard stop.
- **Backend adapters** — one per target CLI, translating the orchestrator's
  intent into whatever that tool actually understands.
- **Local TTS** — streams spoken responses back, filtering out raw
  code/diff output in favor of prose summaries.
- **Optional local LLM cleanup pass** between STT and the adapter, to fix
  mangled technical vocabulary — under evaluation, not assumed necessary.
  See Status.

## Prior art

ConvoBox is not the first attempt at voice-driven coding agents. Related
projects, and where this one differs:

- **[VoiceMode](https://github.com/mbailey/voicemode)** — local-first,
  open-source, Whisper STT + Kokoro TTS. Runs as an MCP server, so it's
  scoped to MCP-aware hosts rather than arbitrary CLIs.
- **[duck_talk](https://github.com/dhuynh95/duck_talk)** — real-time voice
  interface for Claude Code specifically, built on cloud Gemini Live
  sessions rather than local STT/TTS.
- **[RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) /
  [RealtimeTTS](https://github.com/KoljaB/RealtimeTTS) /
  [RealtimeVoiceChat](https://github.com/KoljaB/RealtimeVoiceChat)** — not
  coding-agent tools, but the low-latency local STT/TTS/VAD/barge-in
  building blocks this project leans on.
- **Claude Code's native `/voice`** — push-to-talk dictation, one
  directional (speech in, no speech out), Claude Code only.
- **Aider's built-in `/voice`** — Whisper-based push-to-talk dictation,
  aider only.

None of the above are both backend-agnostic *and* local-first *and*
full-duplex. That combination is the gap ConvoBox is trying to fill.

**[RealtimeVoiceChat](https://github.com/KoljaB/RealtimeVoiceChat)**
deserves a separate callout: it already implements almost the entire
Phase 2 pipeline (see [Roadmap](#roadmap)) — a browser client with no
install, talking over WebSocket to a Dockerized server that runs
VAD → STT (faster-whisper) → LLM → TTS (Coqui/Kokoro/Orpheus),
streamed both directions with barge-in support. It's pointed at a chat
LLM rather than a CLI coding agent, but the audio pipeline and Docker
packaging are directly reusable — the plan is to evaluate forking it and
replacing its "send transcript to the LLM" step with ConvoBox's
orchestrator/backend-adapter layer, rather than rebuilding that pipeline
from scratch.

Other relevant Docker-native building blocks, if a more piecemeal
approach ends up being preferable to forking RealtimeVoiceChat:

- **[docker-whisper](https://github.com/hwdsl2/docker-whisper)** —
  self-hosted, OpenAI-API-compatible Whisper (faster-whisper) server,
  GPU-accelerated, offline, multi-arch.
- **[LocalAI](https://localai.io)** — Docker-native, OpenAI-compatible
  local inference server covering STT, TTS, and an implementation of
  [OpenAI's Realtime API](https://localai.io/features/openai-realtime/)
  spec (full-duplex streaming).
- **[OpenVoiceOS](https://github.com/OpenVoiceOS/ovos-docker-stt)** —
  plugin-based STT/TTS container images, OCI-compatible (Docker, Podman,
  Kubernetes).

The Wyoming protocol / Rhasspy satellite ecosystem (Home Assistant's
local voice stack) is the closest *conceptual* prior art for a
thin-client/server split with local STT/TTS, but it's no longer
maintained — superseded by a newer ESPHome-based approach — so it's a
reference for the pattern, not something to build on directly.

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

## Component software

Current candidate stack for the local pipeline:

- Python, managed with [uv](https://github.com/astral-sh/uv)
- [sounddevice](https://github.com/spatialaudio/python-sounddevice) — audio
  capture
- [Silero VAD](https://github.com/snakers4/silero-vad) — speech
  segmentation
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — local
  speech-to-text
- A local TTS engine (Kokoro or Piper — not yet finalized)
- [Ollama](https://ollama.com) — for the optional local LLM cleanup pass,
  if testing shows it's warranted

## Roadmap

Rough phased direction, not commitments — captured to keep design
decisions from painting the architecture into a corner, not as a
schedule.

1. **Native desktop client** (macOS, Windows, Linux). Audio capture,
   listening-state indicators, and TTS playback as a lightweight native
   process per platform, talking to a local server process over
   localhost.
2. **Browser client + networked server.** The server component —
   VAD/STT/TTS/orchestrator/backend adapters — runs the same regardless
   of who's talking to it. A browser tab becomes just another thin client
   (mic in, indicators + audio out) pointed at that server over your own
   private network (e.g. Tailscale) instead of localhost. Exposing
   agent-execution access this way needs real auth, not just "reachable
   on the network" — scoping to a private tailnet, the way other services
   here already are, is the likely default rather than open LAN access.
3. **Mobile — deprioritized, not designed away.** Not being built now,
   but the client/server split above means a native mobile client is
   "just another client" against the same server API later, not a
   re-architecture, as long as that protocol stays platform-agnostic.
   Some phones already do on-device STT/TTS well; the likely mobile shape
   is a hybrid — local STT/TTS for responsiveness/privacy, still calling
   the server (over Tailscale, SSH, or similar) for the actual agent
   execution, since the CLI backends themselves can't run on a phone.

**Cross-platform packaging: Docker for the server, not the client.** The
server-side component (orchestrator, STT/TTS, backend adapters) is a good
fit for a single Docker image that runs identically on Mac/Windows/Linux
hosts — the same container serves the Phase 1 localhost client and the
Phase 2 browser client. The audio-capture/indicator client can't move
into the container the same way: microphone and speaker access don't
pass through Docker cleanly on any of the three platforms (especially
macOS/Windows, where Docker Desktop runs in a VM with no direct hardware
audio access), so that piece stays a thin native process per platform
regardless of how the server is packaged.

## Status

Early design stage. The first concrete artifact is a standalone
measurement spike — mic → VAD → local STT → logged transcript and latency,
no backend wiring yet — to get real accuracy and latency numbers before
committing to any guardrail or adapter design. Nothing here is stable.

## Open questions

- **Licensing model.** Currently MIT. A split model — free for personal
  use, AGPL (or similar copyleft) for commercial use — is under
  consideration but not decided. Revisit before this leaves early design
  stage.

## License

MIT — see [LICENSE](LICENSE).
