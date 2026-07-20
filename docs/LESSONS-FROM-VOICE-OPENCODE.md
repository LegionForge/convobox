# Lessons from an earlier attempt

An earlier, unreleased project of mine (`voice-opencode`, on hold, TS/Bun,
scoped to OpenCode only) targeted the same space and is worth mining for
what to keep and what to avoid:

- **The OpenCode HTTP+SSE client is directly reusable as a template.**
  `POST /api/sessions` → open a session, `GET
  /api/sessions/:id/events` (SSE) → stream typed messages (`text |
  tool_call | tool_result | error | done`), `POST
  /api/sessions/:id/messages` → send text. That maps cleanly onto
  ConvoBox's `send_text`/`is_busy` adapter surface for the OpenCode
  backend specifically, and confirms the "prefer the tool's native
  structured interface over scraping terminal output" principle is
  achievable, not just aspirational.
- **Never string-interpolate spoken/response text into a shell command.**
  Its Windows TTS engine built a PowerShell `-Command` string by
  interpolating the text to speak directly into it — a straightforward
  command-injection hole, since that text can be arbitrary LLM output. The
  fix (write text to a temp file via base64 rather than inlining it,
  sanitize control characters, cap length) is a lesson to design in from
  the start for ConvoBox's TTS engine, not retrofit later: LLM-response
  text handed to any subprocess is untrusted input.
- **Shelling out per-OS for audio capture/playback was fragile and never
  finished.** Recording was implemented three separate times — a
  `mciSendString` PowerShell hack on Windows, `sox` on macOS, `arecord` on
  Linux — each spawning a subprocess and round-tripping through a temp
  `.wav`/`.mp3` file per utterance. This is exactly why ConvoBox picks
  [sounddevice](https://github.com/spatialaudio/python-sounddevice) (real
  PortAudio bindings) instead of shelling out: one cross-platform audio
  path, no per-OS subprocess maintenance burden, no file-write-then-play
  latency added to every turn.
- **"Local-first" was aspirational, not real, and that gap wasn't
  visible until you looked at what actually ran.** The project was
  designed with a pluggable local/cloud STT engine factory, but the local
  Whisper engine was a stub (`throw new Error('Local Whisper not
  implemented')`) — the only STT that ever worked was the paid OpenAI
  Whisper API. Pluggability got built before the default path worked
  offline. Lesson for ConvoBox: get faster-whisper actually transcribing
  locally first (see [STATUS.md](STATUS.md)); treat multi-engine abstraction as
  something to add once there's a working local baseline to abstract
  from, not a prerequisite for one.
