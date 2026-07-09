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

## Status

Early design stage. The first concrete artifact is a standalone
measurement spike — mic → VAD → local STT → logged transcript and latency,
no backend wiring yet — to get real accuracy and latency numbers before
committing to any guardrail or adapter design. Nothing here is stable.

## License

MIT — see [LICENSE](LICENSE).
