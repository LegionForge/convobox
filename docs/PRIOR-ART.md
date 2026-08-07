# Prior art

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
- **Claude Code's native `/voice`** (shipped 2026-03-03) — push-to-talk
  dictation tuned for code vocabulary, one directional (speech in, no
  speech out), free and excluded from rate limits, Claude Code only.
- **OpenAI Codex CLI's native dictation** (shipped 2026-02-25, six days
  *before* Claude Code's — a direct competitive reaction) — same shape:
  push-to-talk, speech in only, no spoken output, Codex CLI only.
- **Aider's built-in `/voice`** — Whisper-based push-to-talk dictation,
  aider only.
- **[AgentsRoom](https://agentsroom.dev/)** — the closest existing
  competitor on backend-agnostic and full-duplex, and worth being honest
  about: a priced (free tier + €6.99/mo Pro), commercial desktop app supporting
  **8 CLIs** (Claude Code, Codex, Antigravity, OpenCode, Aider, Grok
  Build, Mistral Vibe, Kimi Code), with real two-way voice conversation
  *and* PreToolUse-hook-based guardrails ("block any `rm -rf`, any
  `push --force`") that overlap with ConvoBox's own safety framing.
  Backend-agnostic and full-duplex, yes — but voice STT/TTS routes
  through AgentsRoom's own backend rather than confirmed on-device, so
  it isn't local-first the way ConvoBox is, and its safety mechanism is
  generic hook-based guardrails rather than a voice-native safeword
  hard-stop or spoken approval gate for destructive actions.

None of the above combine backend-agnostic *and* local-first (both
directions, not just STT) *and* full-duplex *and* voice-native safety
gating (a spoken safeword hard-stop, voice approval-gating for
destructive tool calls) in one project. That combination — not just
"voice for a coding agent," which both Anthropic and OpenAI now ship for
free — is the gap ConvoBox is trying to fill.

**A note on durability, not just current-state comparison:** opencode's
maintainers have explicitly closed the most-requested native-voice
feature request as not planned. Unlike Claude Code/Codex CLI (where the
platform vendors are actively encroaching on at least the dictation
slice of this space), that's a standing gap third-party tools like
ConvoBox can keep filling rather than compete against a first-party
feature that might ship any month.

**[RealtimeVoiceChat](https://github.com/KoljaB/RealtimeVoiceChat)**
deserves a separate callout: it already implements almost the entire
Phase 2 pipeline (see [ROADMAP.md](ROADMAP.md)) — a browser client with no
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
