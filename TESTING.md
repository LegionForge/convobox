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

63 tests, all pure logic / mocked hardware / real-server-over-loopback —
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
synthesized speech (not live mic, not mocked): piped Piper-synthesized
audio plus silence padding through the actual `UtteranceSegmenter`,
correctly returning exactly one utterance.

**`scripts/spike.py` itself — the actual script's async wiring — has now
been run for real, end to end, without physical hardware:**

```bash
PYTHONPATH=src .venv/bin/python scripts/spike_smoketest.py
```

This synthesizes two real utterances with Piper ("Run the test suite
please." and "Stop stop stop.", with real silence gaps between them),
feeds that audio through a fake `sounddevice.InputStream` that drives
`MicrophoneStream`'s real capture callback from a background thread — the
only thing substituted is the physical hardware — and then runs
`spike.run()` completely for real: real Silero VAD segmentation, real
faster-whisper transcription, real safeword detection, real clean exit.
Observed: both utterances correctly segmented and transcribed, safeword
correctly matched on the second one, `run()` exited cleanly with no hang.

What's still unverified is specifically the **live capture path** on real
hardware (real ambient noise, real mic gain/latency, `sounddevice.InputStream`
actually opening a real device) — `scripts/spike.py` itself, not just its
components, needs a real mic attached to close that last gap.

## Real TTS playback through real speakers (barge-in included)

```bash
.venv/bin/python scripts/playback_smoketest.py
```

`AudioPlayer` was previously only tested against a mocked `OutputStream`.
This plays real Piper-synthesized speech through whatever real output
device is available — this machine has no mic but does have real speaker
output — and separately tests barge-in against real hardware timing
instead of a scripted fake delay.

Observed on this machine: a short phrase played to completion with
`is_playing()` correctly `False`/`True`/`False` before/during/after; a
12s phrase was cut off by `stop()` after only 0.79s of real playback
(called at the 0.5s mark) — barge-in genuinely silences audio, not just
sets a flag. The ~0.29s gap between calling `stop()` and audio actually
stopping is real measured driver/buffering latency (CoreAudio here) that
the mocked-`OutputStream` unit tests can't surface, since fake writes
return instantly. Worth re-measuring if `AudioPlayer`'s block size (1024
samples) or the persistent-stream-reuse deferral (see README → Status)
ever changes — that latency number is exactly what'd move.

## Testing on Windows

Everything above has only ever run on macOS (Apple Silicon). Nothing here
has been verified on Windows or Linux — but every native dependency
(`torch`, `onnxruntime`, `ctranslate2`, `sounddevice`, `piper-tts`) ships a
prebuilt Windows wheel, and this codebase has zero platform-specific code
(no `sys.platform`/`platform.system` branches, no subprocess/shell-outs,
`pathlib` everywhere) — so it *should* work, but "should" and "verified"
are different claims.

```powershell
git clone <repo-url> convobox
cd convobox
.\scripts\bootstrap_windows.ps1
```

That script installs `uv` if missing, runs `uv sync --extra dev`, then
`pytest`, `mypy`, and `scripts\spike.py --list-devices` (no downloads
needed for any of those), then optionally offers to download real models
and run the TTS/STT round trip and the `spike.py` smoke test too (~300MB,
a few minutes, still no microphone needed). It ends with a clear pass/fail
table.

**Two things worth knowing before you run it:**

- If PowerShell refuses to run the script ("running scripts is disabled on
  this system"), that's the default execution policy, not a problem with
  this script — run `powershell -ExecutionPolicy Bypass -File
  .\scripts\bootstrap_windows.ps1` instead of double-clicking or invoking
  it directly, or set `Set-ExecutionPolicy -Scope CurrentUser
  RemoteSigned` once if you're comfortable doing that machine-wide.
- Unlike Linux (where `sounddevice`'s wheel is pure-Python and needs the
  system's PortAudio installed separately, e.g. `apt install
  libportaudio2`), Windows's `sounddevice` wheel is platform-tagged and
  should bundle PortAudio itself — no separate system install expected,
  but this is inferred from the wheel manifest, not confirmed by an actual
  run yet.

Whatever the bootstrap script reports, the one thing it **can't** verify
is a real microphone through `scripts/spike.py` directly (it fakes the
mic, same as `spike_smoketest.py` does on macOS) — that still needs a
real input device attached to whatever Windows machine you're testing on.

## What's not tested at all yet

- The orchestrator wired to a live OpenCode server (only tested against
  the in-repo fake server) — a real `opencode serve` instance was not
  reachable in this environment (its npm postinstall failed here) to
  test against.
- Live mic input specifically (see the mic → VAD → STT section above) —
  everything downstream of capture is now real-hardware-tested, capture
  itself still isn't.
- Everything on Linux, and everything on Windows until the bootstrap
  script above has actually been run there.
