# Architecture

The full pipeline diagrams, component breakdown, and codebase walkthroughs
that don't fit in the README's condensed overview.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#0d1117', 'mainBkg': '#161b22', 'primaryColor': '#1c2938', 'primaryBorderColor': '#30363d', 'primaryTextColor': '#e6edf3', 'lineColor': '#6e7681', 'clusterBkg': '#161b22', 'clusterBorder': '#30363d', 'edgeLabelBackground': '#161b22', 'titleColor': '#e6edf3'}}}%%
flowchart TB
    classDef hw       fill:#0d2137,stroke:#4a90d9,stroke-width:2px,color:#a8d4ff
    classDef pipeline fill:#0d1f15,stroke:#3fb950,stroke-width:2px,color:#7ee787
    classDef routing  fill:#0a1e1e,stroke:#39c5cf,stroke-width:2px,color:#79e8ef
    classDef safety   fill:#1f1808,stroke:#e3b341,stroke-width:2px,color:#f0c842
    classDef backend  fill:#16112b,stroke:#a371f7,stroke-width:2px,color:#d2a8ff
    classDef tool     fill:#1f160d,stroke:#f0883e,stroke-width:2px,color:#ffa657
    classDef future   fill:#0d1117,stroke:#484f58,stroke-width:1px,stroke-dasharray:4 4,color:#6e7681

    subgraph HW["Audio Hardware · native per platform"]
        direction LR
        MIC(["Microphone"]):::hw
        SPK(["Speakers"]):::hw
    end

    subgraph LOCAL["Local Pipeline · no audio leaves the machine"]
        direction TB
        CAP["MicrophoneStream · sounddevice / PortAudio · continuous float32 @ 16kHz"]:::pipeline
        VAD["UtteranceSegmenter · Silero VAD · hysteresis band · max_utterance_s cap"]:::pipeline
        STT["LocalTranscriber · faster-whisper · auto-detect by default · decoder + language confidence"]:::pipeline
        SW["SafewordDetector · deterministic substring match · no LLM in this path"]:::safety
        ORCH["Orchestrator · hard-stop precedence · empty-transcript guard · busy/idle routing"]:::routing
        TTS["TTSEngine · Kokoro (default) or Piper · sanitize_text · streaming synthesis"]:::pipeline
        PLAY["AudioPlayer · barge-in stop()"]:::pipeline
    end

    subgraph ADAPTERS["Backend Adapters · one per CLI"]
        OC["OpenCodeAdapter · typed HTTP + SSE client"]:::backend
        CC["ClaudeCodeAdapter · bidirectional stream-json CLI"]:::backend
        CX["CodexAdapter · app-server JSON-RPC over stdio"]:::backend
    end

    subgraph TOOLS["Tools · same pipeline, no backend"]
        direction LR
        SPIKE["scripts/spike.py · logged transcripts"]:::tool
        TUI["scripts/voice_tui.py · live clarity dashboard"]:::tool
        PICKER["scripts/voice_picker_tui.py · browse/audition/pick a voice"]:::tool
        AUDIO["scripts/audio_devices.py · find/test your audio device"]:::tool
        ROUNDTRIP["scripts/roundtrip_smoketest.py · TTS to STT, any voice"]:::tool
    end

    MIC --> CAP --> VAD -->|"one utterance"| STT --> SW
    SW -->|"transcript · safeword checked first"| ORCH
    ORCH -->|"send_text / send_interject / send_hard_stop"| OC
    OC -->|"SSE events · TEXT / TOOL / DONE / ERROR"| ORCH
    ORCH -->|"prose only · strip_code_for_speech"| TTS --> PLAY --> SPK
    SW -.-> SPIKE
    SW -.-> TUI
    PICKER -.->|"convobox.yaml snippet"| TTS
    ROUNDTRIP -.-> TTS
    ROUNDTRIP -.-> STT

    style HW fill:#0d1525,stroke:#4a90d9,stroke-width:2px,color:#e6edf3
    style LOCAL fill:#0a130d,stroke:#3fb950,stroke-width:2px,color:#e6edf3
    style ADAPTERS fill:#150d22,stroke:#a371f7,stroke-width:2px,color:#e6edf3
    style TOOLS fill:#1a1208,stroke:#f0883e,stroke-width:2px,color:#e6edf3
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
  intent into whatever that tool actually understands, preferring each
  tool's native structured/headless interface over PTY scraping. Three are
  implemented: **OpenCode** (typed client over its HTTP+SSE server),
  **Claude Code** (bidirectional stream-json subprocess), and **Codex**
  (app-server JSON-RPC over stdio), each verified against a live instance.
  OpenCode's real API shape (the endpoint paths were wrong in an early
  assumed version, then corrected against a real `opencode serve`) is
  documented in [../OPENCODE_API_NOTES.md](../OPENCODE_API_NOTES.md).
- **Local TTS** — streams spoken responses back, filtering out raw
  code/diff output in favor of prose summaries.
- **Optional local LLM cleanup pass** between STT and the adapter, to fix
  mangled technical vocabulary — under evaluation, not assumed necessary.
  See [STATUS.md](STATUS.md).

## One utterance, end to end

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#0d1117', 'mainBkg': '#161b22', 'primaryColor': '#1c2938', 'primaryBorderColor': '#30363d', 'primaryTextColor': '#e6edf3', 'lineColor': '#6e7681', 'clusterBkg': '#161b22', 'clusterBorder': '#30363d', 'edgeLabelBackground': '#161b22', 'titleColor': '#e6edf3'}}}%%
flowchart TB
    classDef step     fill:#0d1f15,stroke:#3fb950,stroke-width:2px,color:#7ee787
    classDef decision fill:#1f1808,stroke:#e3b341,stroke-width:2px,color:#f0c842
    classDef stop     fill:#1f160d,stroke:#f0883e,stroke-width:2px,color:#ffa657
    classDef dropped  fill:#0d0d18,stroke:#484f58,stroke-width:2px,stroke-dasharray:6 4,color:#8b949e
    classDef backend  fill:#16112b,stroke:#a371f7,stroke-width:2px,color:#d2a8ff

    WIN["mic chunk · consumed in 512-sample / 32ms windows"]:::step
    ACC["VAD accumulates a speech run · brief dips and ambiguous windows stay inside it"]:::step
    ENDQ{"silence >= min_silence_ms · or max_utterance_s cap reached?"}:::decision
    UTT["utterance emitted · trailing silence included so STT does not clip the last phoneme"]:::step
    STT2["faster-whisper transcribe · language + confidence + latency measured"]:::step
    SWQ{"safeword match? · deterministic, checked before everything else"}:::decision
    HALT["hard stop · stop TTS · stop playback · adapter.send_hard_stop()"]:::stop
    EMPTYQ{"transcript empty?"}:::decision
    NOISE["dropped · background noise never reaches the backend"]:::dropped
    BUSYQ{"backend busy?"}:::decision
    INTJ["send_interject · a soft 'oh, also...' that must not derail the task"]:::backend
    SEND["send_text · fresh command"]:::backend
    EVENTS["drain SSE events · TEXT / TOOL_CALL / TOOL_RESULT / ERROR / DONE"]:::backend
    SPEAKQ{"TEXT event with prose? · strip_code_for_speech removes code and diffs"}:::decision
    SPEAK["TTS synthesis (Kokoro default, Piper opt-in) · fire-and-forget task · AudioPlayer to speakers"]:::step
    SILENT["nothing spoken · code and diffs stay on screen"]:::dropped

    WIN --> ACC --> ENDQ
    ENDQ -->|"not yet"| ACC
    ENDQ -->|"yes"| UTT --> STT2 --> SWQ
    SWQ -->|"yes"| HALT
    SWQ -->|"no"| EMPTYQ
    EMPTYQ -->|"yes"| NOISE
    EMPTYQ -->|"no"| BUSYQ
    BUSYQ -->|"yes"| INTJ --> EVENTS
    BUSYQ -->|"no"| SEND --> EVENTS
    EVENTS --> SPEAKQ
    SPEAKQ -->|"prose"| SPEAK
    SPEAKQ -->|"code only"| SILENT
```

## Component software

Current candidate stack for the local pipeline:

- Python, managed with [uv](https://github.com/astral-sh/uv)
- [sounddevice](https://github.com/spatialaudio/python-sounddevice) — audio
  capture
- [Silero VAD](https://github.com/snakers4/silero-vad) — speech
  segmentation
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — local
  speech-to-text
- A local TTS engine — Kokoro (Apache 2.0, default since 2026-07-24) or
  Piper (GPL-3.0, opt-in only via `uv sync --extra piper`, kept out of
  the default install for exactly the licensing reason this section used
  to flag as unresolved — see
  [../DEPENDENCY_LICENSE_AUDIT.md](../DEPENDENCY_LICENSE_AUDIT.md)).
  Whatever the choice, response text must never be interpolated directly
  into a shell command to invoke it — see
  [LESSONS-FROM-VOICE-OPENCODE.md](LESSONS-FROM-VOICE-OPENCODE.md).
- [Ollama](https://ollama.com) — for the optional local LLM cleanup pass,
  if testing shows it's warranted

## Reviewing this codebase?

`.tours/` has three [CodeTour](https://marketplace.visualstudio.com/items?itemName=vsls-contrib.codetour)
walkthroughs (VS Code will prompt to install the extension via
`.vscode/extensions.json`): *1. Architecture & Data Flow* follows one
utterance through every pipeline stage with the data handoff called out at
each boundary; *2. Review Findings: Security & Performance* visits the
concrete bugs a review pass found and fixed, in place; *3. Extension
Points: Modularity & Pluggability* shows collaborators exactly where to
plug in a new backend adapter or TTS engine, and — just as important —
which modules are deliberately single-implementation, not extension
points. Each step is anchored by both a line number and a text pattern so
the tour stays accurate as the code around it changes — see the comment
at the top of any `.tour` file if you're adding a new step.
