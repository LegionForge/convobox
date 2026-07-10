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

98 tests, all pure logic / mocked hardware / real-server-over-loopback —
no mic, no models to download. Covers: safeword matching (including that a
phrase normalizing to empty raises at construction instead of silently
vanishing), audio capture/playback against a mocked `sounddevice`, VAD
segmentation logic against a scripted fake model (including a regression
test for the hysteresis-band hang, and the `max_utterance_s` cap), TTS
streaming against a mocked Piper voice (proves chunks are yielded
incrementally, not buffered) plus rate/volume wiring, the
`create_tts_engine` factory's voice resolution and error messages, the
orchestrator's hard-stop/interject/fresh routing plus that it starts its
own event-drain loop and drops empty transcripts, `LanguageTracker`'s
wander-vs-genuine-switch distinction, `scripts/voice_picker.py` and
`scripts/roundtrip_smoketest.py`'s pure CLI logic (catalog search,
language-to-phrase mapping) imported directly as `scripts.*` the same way
`scripts/spike.py`'s `_resolve_device` already was, and the OpenCode
adapter against a real HTTP+SSE server on a real loopback socket
(including that `is_busy()` clears when the stream ends without a DONE
event, and that a schemeless backend URL still triggers the
insecure-transport warning). Run 2-3x if iterating on timing-sensitive
code — `test_stop_halts_in_progress_playback_promptly` in particular is a
real concurrency test, not inherently flake-proof just because it passed
once; `test_events_yield_typed_backend_events_from_sse` has also been
observed to flake once under heavy concurrent load on this machine
(passed cleanly on immediate rerun and in isolation) — a real loopback
socket test has real scheduling variance, watch for a pattern rather than
treating one flake as a regression.

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

`--voice KEY` runs the round trip with any installed voice instead of the
default (`en_US-lessac-medium`), using a phrase matched to that voice's
own language where one exists in `STT_TEST_PHRASES` (add an entry there
for a new language), and the multilingual `base` STT model pinned to that
language instead of the faster English-only `tiny.en`:

```bash
PYTHONPATH=src .venv/bin/python scripts/roundtrip_smoketest.py --voice ru_RU-irina-medium
```

This is what actually validates a voice picked with `voice_picker.py`
below is *intelligible*, not just that it produces audio — synthesizing
gibberish and having STT transcribe gibberish back would look identical
to a working round trip if the only check were "did text come out."

## Picking a voice

```bash
PYTHONPATH=src .venv/bin/python scripts/voice_picker.py
```

`TTSConfig.voice`/`rate`/`volume` existed in `config.py` from the start
but had nothing wired to them — every script constructed `PiperTTSEngine`
by hand with a hardcoded voice. `convobox.tts.create_tts_engine(config)`
(`src/convobox/tts/factory.py`) is that missing wiring, and every script
below now goes through it rather than hand-building the engine.

Piper's catalog has 163 voices across 44 languages
([full list](https://huggingface.co/rhasspy/piper-voices)) — notably no
Japanese, though Chinese, Russian, French, and most other languages
exercised in the live-mic UAT session above are covered.
`voice_picker.py` with no flags opens an interactive picker: `search
TERM` browses the catalog (cached locally after the first fetch, matched
against the voice key, language name, or language code), `get KEY`
downloads a voice, `play KEY` auditions one through real speakers
(offering to download it first if needed), `text ...`/`rate F`/`volume
F` adjust what gets auditioned, and `use KEY` + `quit` prints the
`convobox.yaml` snippet for whichever voice you land on. Flag mode does
the same things non-interactively for scripting (`--list-installed`,
`--search TERM`, `--download KEY`, `--audition KEY --text "..."`).

**Windows console gotcha, found live testing this tool:** the default
Windows console codepage (cp1252 etc.) can't print or read most non-Latin
script — `--text` with Cyrillic crashed with `UnicodeEncodeError` before
this was fixed, and typing Japanese into the interactive `text` prompt
came back mojibake'd through stdin. `scripts/_console.py`'s
`use_utf8_console()` reconfigures stdin/stdout/stderr to UTF-8 at startup
and is called by both `voice_picker.py` and `roundtrip_smoketest.py` — a
no-op on platforms/terminals already UTF-8 (most Linux/macOS terminals,
and Windows Terminal in its default codepage), a real fix for anything
that still defaults to a legacy codepage.

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

## Live clarity dashboard (TUI)

```bash
PYTHONPATH=src .venv/bin/python scripts/voice_tui.py [--language en] [--min-confidence 0.4]
```

Same pipeline as `scripts/spike.py`, rendered as a live terminal dashboard
instead of log lines: an input level meter and capture state (so mic and
gain problems are visible before any transcript arrives), a per-utterance
verdict, and session stats. Stdlib-only on purpose: a test utility should
not add dependencies to the project it tests. Exit via the safeword or
Ctrl+C; a plain-text session summary prints on exit.

The verdict thresholds come from the live Windows session below: detected
language probability >= 0.80 was consistently faithful, below ~0.40 was
usually a hallucination (sometimes in a different script entirely), and
rtf >= 1.0 marks Whisper's temperature-fallback re-decode. The dashboard
also shows per-utterance queue wait separately from decode latency,
because STT is serial and bursts of short utterances compound delay.

**`language_probability` goes blind under `--language`.** Pinning the
language (the fix for the auto-detect wander below) makes faster-whisper
report a hardcoded `1.0` for it — there's no detection happening to be
confident about. Caught live: speaking Russian into a `--language en`
session logged `GOOD en 1.00` on every utterance, none of which was
correct English. The dashboard's verdict now also weighs a second, always-
meaningful signal: the mean of each segment's `avg_logprob` from the
decoder itself (shown as the `dec` column, `exp(avg_logprob)` so it reads
0-1 like the other confidence figures; ~0.70+ solid, ~0.45-0.70 shaky,
below that unreliable — Whisper's conventional avg_logprob cutoffs). In
pinned mode the verdict is driven by `dec` alone; unpinned, both signals
must agree for GOOD/FAIR. `language_probability` and `dec` are answering
different questions — "what language is this" vs. "how sure was the
decoder of these words" — which is why pinning silences only the first.

Useful flags: `--language en` pins STT to one language (kills the
auto-detect wander described below, but see the `dec` column note just
above for why confidence still needs watching), `--min-confidence 0.4`
drops low-confidence transcripts by `language_probability`
(`stt.min_language_probability` in config; the safeword is always checked
on the raw transcript first, so the gate can never swallow a hard stop).
**Known gap:** this gate compares against `language_probability`, which is
pinned to `1.0` in `--language` mode, so it never drops anything while
pinned — watch the `dec` column yourself in that mode rather than relying
on `--min-confidence`. Making the config-level gate decoder-confidence
aware (so it works pinned too) is a reasonable next step, not done here.

## Testing on Windows

**First verified 2026-07-09 on Windows 11 (i7-13650HX):** `uv sync`,
63/63 tests (69 after this round's additions), mypy, PortAudio device
enumeration, the TTS/STT round trip, both smoke tests, real speaker
playback with barge-in (stop latency 240ms vs 290ms on the macOS
baseline), and — for the first time on any platform — live microphone
capture through `scripts/spike.py`, including a real spoken-safeword exit.
`sounddevice`'s Windows wheel does bundle PortAudio; no system install was
needed.

That run also surfaced one real bug, not Windows-specific: the fake
OpenCode server's SSE handler could outlive its client (parked on
`event_gate.wait()` after a hard stop), and since Python 3.12.1
`asyncio.Server.wait_closed()` genuinely waits for connection handlers
([gh-104344](https://github.com/python/cpython/issues/104344)), so the
`server` fixture teardown deadlocked the suite. On Python <= 3.12.0 the
same leak existed but `wait_closed()` returned immediately, which is why
macOS runs never saw it. Fixed in the fixture: `stop()` now wakes parked
handlers before awaiting `wait_closed()`.

Live-mic findings from that session that shaped config defaults and the
TUI's verdict bands: language auto-detect is per-utterance with no session
stickiness, so accented or non-native speech can wander across languages
mid-monologue. **Pinning `stt.language` is not the recommended fix for
this** — a pin doesn't detect and reject mismatched speech, it forces
every utterance to decode AS that language, and does so with fake
confidence (`language_probability` reports a hardcoded `1.0` when pinned,
whether or not the words are real). Confirmed live: speaking Russian into
a `--language en` session produced `GOOD en 1.00` transcripts like "I am
not a man, I am a man" for "I don't understand Russian" — worse than
useless, since it *hides* the mismatch instead of surfacing it. Two
non-forcing alternatives, both left on by default: `LanguageTracker`
(`src/convobox/stt/language_tracker.py`) tracks the session's dominant
*confidently-detected* language purely for display — it never feeds back
into what the decoder is asked to assume, so it can't mangle a genuine
language switch, but it flags (the `~` marker in `voice_tui.py`) when an
utterance breaks from that dominant language, which is what a wander
looks like; and the decoder-confidence signal below (`avg_logprob`) stays
meaningful with or without a pin, so low `dec` scores catch a wandering
decoder either way. Pin only for a genuinely single-language deployment
where you've accepted that tradeoff, not as a default fix for wander.
Also from that session: over-articulating makes language ID worse, not
better (it strips the prosodic cues the detector relies on); detection
confidence below ~0.4 usually means a hallucinated transcript
(`stt.min_language_probability` gates these, though see the known gap
above about it going blind while pinned); single-word utterances are
the least reliable and most expensive input shape, so spoken confirmation
phrases should be multi-word by design; and background noise can VAD-trigger
and transcribe to empty, which the orchestrator now drops instead of
forwarding to the backend.

To reproduce the setup from scratch, every native dependency (`torch`,
`onnxruntime`, `ctranslate2`, `sounddevice`, `piper-tts`) ships a prebuilt
Windows wheel, and this codebase has zero platform-specific code (no
`sys.platform`/`platform.system` branches, no subprocess/shell-outs,
`pathlib` everywhere).

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
mic, same as `spike_smoketest.py` does on macOS) — that needs a real
input device (verified on Windows 2026-07-09, see above; still unverified
on macOS, which has no mic on the dev machine).

## What's not tested at all yet

- The orchestrator wired to a live OpenCode server (only tested against
  the in-repo fake server) — a real `opencode serve` instance was not
  reachable in this environment (its npm postinstall failed here) to
  test against.
- Everything on Linux.
