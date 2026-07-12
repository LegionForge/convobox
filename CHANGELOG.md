# Changelog

All notable changes to ConvoBox are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0, so
minor versions carry feature and behavior changes.

## [0.2.0] — 2026-07-12

The first release where the **whole product loop works end-to-end**: speak,
and a real coding agent responds by voice. 0.1.0 was the front-half spike
(mic → transcript); 0.2.0 closes the loop through a backend and back out to
speech, hardened across a full day of live voice UAT on Windows 11.

**Tested configuration:** Windows 11 · opencode backend · faster-whisper STT
· Piper TTS. Other backends/platforms are implemented but not yet
voice-validated — see the README support matrix and `docs/KNOWN-ISSUES.md`.

### Added
- **Full voice loop** (`scripts/run_convobox.py`): mic → Silero VAD
  utterance segmentation → faster-whisper STT → orchestrator → backend
  adapter → Piper TTS → speakers, run against a live opencode server.
- **Acoustic echo cancellation** (opt-in, `audio.echo_cancellation`): WebRTC
  AEC fed the playback as a far-end reference, with auto-estimated
  render-to-capture delay and a floor-aware three-way verdict.
- **Streaming TTS**: audio starts on the first synthesized sentence, so
  time-to-first-audio is proportional to one sentence, not the whole reply.
- **Deterministic safeword hard-stop**: `stop/break/brake/eject/mayday`
  (×3), matched on the raw transcript and honored mid-playback.
- **Soft interject vs. hard stop** as distinct backend semantics
  (opencode: steer vs. interrupt).
- **Guided audio setup** (`scripts/audio_devices.py --setup`): default-first
  device testing, continuous test tone + live mic-level meter, mic
  record/playback with replay & re-record, a chooser deduped to one entry
  per physical device (hiding Windows' host-API duplicates and unopenable
  WDM-KS/meta devices), and a warning if no mic/speaker was selected.
- **Pluggable STT/TTS engines**: `STTEngine`/`TTSEngine` ABCs with
  `create_stt_engine`/`create_tts_engine` factories (installed at setup
  time, never bundled).
- **`ConfirmwordDetector`** — safety-tier primitive (the inverse of the
  safeword) that refuses to be armed with a common affirmation. Library
  primitive; orchestrator wiring deferred.
- **Single-instance mic lock** and a **working-indicator heartbeat** for a
  silently-busy backend.

### Fixed
- **Playback across arbitrary devices/host APIs**: open the output stream at
  the device's *native* rate and resample to match, instead of forcing
  Piper's 22050 Hz (which WASAPI rejected outright and DirectSound
  mis-resampled to silence).
- **WASAPI garbled static**: a phase-continuous streaming resampler
  (`_StreamResampler`) eliminates the per-chunk boundary clicks that were
  inaudible at integer device ratios (MME) but garbled at non-integer ones
  (48 kHz WASAPI).
- **Backend timeout no longer crashes the session**: the prompt POST gets a
  generous read timeout, and the run loop guards each utterance so a backend
  error is logged and listening continues instead of taking down the app.

### Known issues
- **WASAPI output plays speech an octave too high** (mono-on-stereo channel
  handling in PortAudio's WASAPI path). Use an **MME** output device.
  Documented in `docs/KNOWN-ISSUES.md`.

## [0.1.0]

Initial spike: microphone capture, VAD, and local transcription — the
front-half of the loop (mic → transcript), before any backend or TTS.
