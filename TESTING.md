# Testing ConvoBox

## Setup

```bash
pip install uv   # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --extra dev
```

This installs the full pipeline (torch, faster-whisper/ctranslate2, silero-vad,
piper-tts, sounddevice, httpx, pytest) into `.venv/`.

## Automated tests (no audio hardware needed)

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

52 tests, all pure logic / mocked hardware / real-server-over-loopback —
no mic, no models to download. Covers: safeword matching (including that a
phrase normalizing to empty raises at construction instead of silently
vanishing), audio capture/playback against a mocked `sounddevice`, VAD
segmentation logic against a scripted fake model (including a regression
test for the hysteresis-band hang), TTS streaming against a mocked Piper
voice (proves chunks are yielded incrementally, not buffered), the
orchestrator's hard-stop/interject/fresh routing plus that it starts its
own event-drain loop, and the OpenCode adapter against a real HTTP+SSE
server on a real loopback socket (including that `is_busy()` clears when
the stream ends without a DONE event, and that a schemeless backend URL
still triggers the insecure-transport warning). Run 2-3x if iterating on
timing-sensitive code — `test_stop_halts_in_progress_playback_promptly` in
particular is a real concurrency test, not inherently flake-proof just
because it passed once.

## Type checking

```bash
PYTHONPATH=src .venv/bin/python -m mypy src/ scripts/ tests/ --ignore-missing-imports
```

Clean across the whole tree. Caught two real gaps worth knowing about if
you're extending this: `BackendAdapter.events()` (and `TTSEngine`-adjacent
code) must return an `AsyncGenerator`, not just any `AsyncIterator` — the
orchestrator and hard-stop/shutdown paths call `.aclose()` on what it
returns, which only `AsyncGenerator` guarantees. And a test double for
`AudioPlayer` needs to actually subclass it (not just duck-type the same
methods) since `Orchestrator.player` is typed as the concrete class — this
codebase deliberately doesn't introduce an ABC/Protocol for
single-implementation roles like audio capture/playback (see the
Architecture section of the README on avoiding premature pluggability), so
a test double for one of those has to be a real subclass, not a duck type.

## Real end-to-end round trip (no mic needed, downloads real models)

```bash
uv run python -m piper.download_voices en_US-lessac-medium --download-dir .models/piper
PYTHONPATH=src .venv/bin/python scripts/roundtrip_smoketest.py
```

Synthesizes speech with the real Piper voice, transcribes it back with real
faster-whisper (`tiny.en`), and prints transcript + latency. This is the
strongest signal available without a microphone: it proves TTS and STT
both actually work and are fast (observed on this machine: TTS 40–400ms,
STT 140–200ms per utterance, i.e. well under real-time). It also
round-trips the safeword phrase ("Stop stop stop." synthesized and
transcribed back as "Stop, stop, stop." — the safeword detector's
punctuation normalization is specifically there to still match this).

This exercises `synthesize()` (the concatenating convenience wrapper), not
the streaming path directly. To see the streaming behavior itself — first
chunk arriving well before the full response finishes synthesizing —
iterate `tts.synthesize_stream(text)` instead of awaiting `synthesize()`;
on a ~20-sentence passage the first chunk lands around 140ms in while
total synthesis takes ~1.6s.

## Live mic → VAD → STT spike

```bash
PYTHONPATH=src .venv/bin/python scripts/spike.py
```

**Known blocker on this development machine: no microphone input device.**
`scripts/spike.py --list-devices` (or `sounddevice.query_devices()`
directly) here shows only two output-only devices (`JetKVM v1`, `Mac mini
Speakers`) — Mac Minis have no built-in microphone. This script has NOT
been run against live audio. To actually test it:

1. Attach a USB mic (or any input device) to the machine you run this on.
2. Confirm it shows up: `PYTHONPATH=src .venv/bin/python scripts/spike.py --list-devices`
   — look for an entry with `> 0` in parens before "in" (input channels).
3. If it's not the system default, pass it with `--device NAME` (or
   `--device 2` for a numeric index — both work) — or set
   `audio.input_device` in a `convobox.yaml` config file instead.
4. Run `scripts/spike.py [--device ...]`, speak, watch for logged
   `transcript=... latency_ms=... rtf=...` lines. Say "stop stop stop" to
   exit cleanly (exercises the safeword path for real).

The VAD segmentation logic itself has been validated against real
synthesized speech (not live mic, not mocked) — see the "real Silero VAD"
check referenced in project notes: piped Piper-synthesized audio plus
silence padding through the actual `UtteranceSegmenter`, correctly
returning exactly one utterance. What's unverified is the live capture
path specifically (real ambient noise, real mic gain/latency,
`sounddevice.InputStream` actually opening on real hardware).

## What's not tested at all yet

- TTS playback through real speakers (`AudioPlayer` is unit-tested against
  a mocked `OutputStream` only).
- The orchestrator wired to a live OpenCode server (only tested against
  the in-repo fake server).
- Barge-in (interrupting TTS playback mid-utterance) end to end.
- Anything on Windows or Linux — everything above has only run on this
  macOS machine.
