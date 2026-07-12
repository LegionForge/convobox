"""List audio devices and play test tones -- find the right one for convobox.yaml.

Windows exposes the same physical jack through several host APIs (MME,
DirectSound, WASAPI, WDM-KS), each with different latency and sample-rate
behavior -- and that split is exactly where ConvoBox device configuration
goes wrong. This tool makes the choice visible and testable:

    python scripts/audio_devices.py                 # list output + input devices
    python scripts/audio_devices.py --inputs        # input devices only
    python scripts/audio_devices.py --test 5        # play a test tone to device 5
    python scripts/audio_devices.py --test-input 1  # record from device 1, show a level meter
    python scripts/audio_devices.py --setup         # GUIDED: test your default
                                                    # speaker & mic, save to convobox.yaml

Most people should just run --setup. It tries your system's default
speaker and microphone first and tests each one -- a looping tone you
either hear or don't, a live level meter you watch move when you speak --
and only shows a list of other devices if the default doesn't work. It
writes the working choices to convobox.yaml. No need to know anything
about devices, host APIs, or sample rates.

Or pin a device by hand in convobox.yaml -- INCLUDING the host API, so it
resolves to exactly one device:

    audio:
      output_device: "Headphones (Realtek(R) Audio), MME"

Host-API notes, learned the hard way (see docs/DESIGN-echo-and-barge-in.md):
  MME          resamples gracefully; highest latency; the safe default.
  DirectSound  resamples, moderate latency (can silently mishandle some
               rates on some drivers).
  WASAPI       lowest latency (3-10ms) BUT rejects any rate the device
               does not natively support -- ConvoBox's playback resampling
               is what makes it usable.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any

# ConvoBox captures the mic at this rate (config.audio.sample_rate); testing
# the input here means testing what the app will actually use.
_CAPTURE_RATE = 16000

# Inserted (not relied on as a package import) so this file works identically
# run directly (`python scripts/audio_devices.py`) and imported as
# scripts.audio_devices (e.g. from a pytest test).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import use_utf8_console


def collect_devices(sd: Any, kind: str) -> list[dict[str, Any]]:
    """Normalize sounddevice's device table for one direction.

    Thin wrapper over the live ``sounddevice`` module; the formatting and
    resolution logic below is pure (takes this list), so it stays unit
    testable with a fake ``sd``. ``kind`` is "output" or "input".
    """
    ch_key = "max_output_channels" if kind == "output" else "max_input_channels"
    default_idx = sd.default.device[1 if kind == "output" else 0]
    hostapis = sd.query_hostapis()
    devices: list[dict[str, Any]] = []
    for index, d in enumerate(sd.query_devices()):
        if d[ch_key] <= 0:
            continue
        devices.append(
            {
                "index": index,
                "name": d["name"],
                "hostapi": hostapis[d["hostapi"]]["name"],
                "channels": d[ch_key],
                "samplerate": int(d["default_samplerate"]),
                "default": index == default_idx,
            }
        )
    return devices


def format_devices(devices: list[dict[str, Any]], kind_label: str) -> str:
    """Render a device list as an aligned table (pure)."""
    if not devices:
        return f"(no {kind_label} devices found)"
    name_w = max(len(d["name"]) for d in devices)
    host_w = max(len(d["hostapi"]) for d in devices)
    lines = [f"{kind_label} devices:"]
    for d in devices:
        mark = "*" if d["default"] else " "
        lines.append(
            f" {mark} [{d['index']:>2}] {d['name']:<{name_w}}  "
            f"{d['hostapi']:<{host_w}}  {d['channels']}ch  {d['samplerate']}Hz"
        )
    lines.append(
        '   (* = system default. Pin the full name INCLUDING the host API '
        '-- e.g. "Name, Windows WASAPI" -- in convobox.yaml.)'
    )
    return "\n".join(lines)


def resolve_spec(spec: str, devices: list[dict[str, Any]]) -> tuple[int | None, str | None]:
    """Turn a device spec (index or name) into a device index (pure).

    Returns ``(index, None)`` on success or ``(None, message)`` on
    failure. On an ambiguous name match -- the "Multiple devices found"
    situation that derails ConvoBox setup on Windows -- the message lists
    the host-API-qualified options so the user can pick one.
    """
    spec = spec.strip()
    if spec.isdigit():
        index = int(spec)
        if any(d["index"] == index for d in devices):
            return index, None
        return None, f"no device with index {index}"

    needle = spec.lower()
    exact = [
        d
        for d in devices
        if needle in (d["name"].lower(), f"{d['name']}, {d['hostapi']}".lower())
    ]
    partial = [d for d in devices if needle in f"{d['name']}, {d['hostapi']}".lower()]
    chosen = exact or partial
    if not chosen:
        return None, f"no device matching {spec!r}"
    if len(chosen) > 1:
        options = "\n".join(f'    "{d["name"]}, {d["hostapi"]}"' for d in chosen)
        return None, (
            f"{spec!r} matches multiple devices -- qualify it with the host API:\n{options}"
        )
    return chosen[0]["index"], None


def play_test_tone(sd: Any, index: int, seconds: float = 1.0) -> None:
    """Play a 440Hz tone at the DEVICE'S native rate.

    Generating the tone at the device's own rate means no resampling is
    needed and every host API (including WASAPI, which rejects foreign
    rates) opens cleanly -- so a silent result means the wrong device, not
    a rate problem.
    """
    import numpy as np

    info = sd.query_devices(index)
    rate = int(info["default_samplerate"])
    t = np.linspace(0.0, seconds, int(rate * seconds), endpoint=False)
    tone = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    print(f"playing {seconds:.0f}s 440Hz tone to [{index}] {info['name']} @ {rate}Hz ...")
    sd.play(tone, samplerate=rate, device=index, blocking=True)
    print("done -- if you heard it, that's your device (pin its full name in convobox.yaml)")


def _dbfs(amplitude: float) -> float:
    """Amplitude (0..1) to dBFS. Silence floors at -120 to avoid -inf."""
    if amplitude <= 1e-6:
        return -120.0
    return 20.0 * math.log10(min(amplitude, 1.0))


def level_meter(samples: Any) -> tuple[float, float]:
    """Return (rms_dbfs, peak_dbfs) for a float32 [-1, 1] buffer (pure)."""
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    if samples.size == 0:
        return (-120.0, -120.0)
    rms = float(np.sqrt(np.mean(np.square(samples))))
    peak = float(np.max(np.abs(samples)))
    return (_dbfs(rms), _dbfs(peak))


def _level_verdict(rms_db: float, peak_db: float) -> str:
    if peak_db >= -1.0:
        return "CLIPPING -- too loud, lower the input gain"
    if rms_db <= -55.0:
        return "SILENT -- no signal; wrong device, or the mic is muted?"
    if rms_db < -40.0:
        return "very quiet -- raise the input gain or move closer"
    return "good"


def format_level(rms_db: float, peak_db: float, width: int = 30) -> str:
    """A text VU meter: RMS bar over a -60..0 dBFS range, plus a verdict (pure)."""
    floor_db = -60.0
    frac = max(0.0, min(1.0, (rms_db - floor_db) / (0.0 - floor_db)))
    filled = int(round(frac * width))
    bar = "#" * filled + "-" * (width - filled)
    return (
        f"[{bar}] rms {rms_db:6.1f} dBFS  peak {peak_db:6.1f} dBFS  "
        f"-- {_level_verdict(rms_db, peak_db)}"
    )


def record_test(sd: Any, index: int, seconds: float = 3.0, rate: int = _CAPTURE_RATE) -> Any:
    """Record `seconds` of mono float32 audio from an input device."""
    import numpy as np

    recording = sd.rec(
        int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=index
    )
    sd.wait()
    return np.asarray(recording, dtype=np.float32).reshape(-1)


def test_input_device(
    sd: Any, index: int, seconds: float = 3.0, playback_device: int | None = None
) -> tuple[float, float]:
    """Record from an input device, show a level meter, and play it back.

    Records at ConvoBox's actual capture rate so the test reflects real
    use; falls back to the device's native rate (with a note) if it won't
    accept the capture rate. Returns (rms_dbfs, peak_dbfs) so the wizard
    can judge success.
    """
    info = sd.query_devices(index)
    print(f"recording {seconds:.0f}s from [{index}] {info['name']} -- say something...")
    try:
        audio = record_test(sd, index, seconds, _CAPTURE_RATE)
        rate = _CAPTURE_RATE
    except Exception:  # noqa: BLE001 -- fall back to the device's own rate
        rate = int(info["default_samplerate"])
        print(
            f"  (device wouldn't record at {_CAPTURE_RATE}Hz; used {rate}Hz -- ConvoBox "
            f"captures at {_CAPTURE_RATE}Hz, so a device that accepts it is preferable)"
        )
        audio = record_test(sd, index, seconds, rate)
    rms_db, peak_db = level_meter(audio)
    print(format_level(rms_db, peak_db))
    _play_recording(sd, audio, rate, playback_device)
    return rms_db, peak_db


def default_config_path() -> Path:
    """The file load_config() reads: CONVOBOX_CONFIG or ./convobox.yaml."""
    import os

    return Path(os.environ.get("CONVOBOX_CONFIG", "convobox.yaml"))


def write_device_to_config(kind: str, value: str, config_path: Path) -> Path:
    """Write audio.output_device / audio.input_device into the config.

    Merges: only the one key changes; other sections and the file's leading
    comment block survive (a plain yaml round-trip would drop the header).
    Pure file I/O -- unit-tested.
    """
    import yaml

    key = "output_device" if kind == "output" else "input_device"
    leading: list[str] = []
    data: dict[str, Any] = {}
    if config_path.exists():
        raw = config_path.read_text(encoding="utf-8")
        for line in raw.splitlines():
            if line.lstrip().startswith("#") or not line.strip():
                leading.append(line)
            else:
                break
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            data = loaded
    audio = data.get("audio")
    if not isinstance(audio, dict):
        audio = {}
        data["audio"] = audio
    audio[key] = value
    header = ("\n".join(leading) + "\n") if leading else ""
    config_path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_path


def _hostapi_name(sd: Any, index: int) -> str:
    info = sd.query_devices(index)
    return str(sd.query_hostapis(info["hostapi"])["name"])


def _qualified_name(sd: Any, index: int) -> str:
    """The host-API-qualified device name to pin in config (resolves uniquely)."""
    return f"{sd.query_devices(index)['name']}, {_hostapi_name(sd, index)}"


def _prompt_index(prompt: str, devices: list[dict[str, Any]]) -> int | None:
    """Read a device number/name from the user; None if they skip (blank/q)."""
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            return None
        if raw.lower() in ("", "q", "skip"):
            return None
        index, error = resolve_spec(raw, devices)
        if index is not None:
            return index
        print(error)


def _default_index(sd: Any, kind: str) -> int | None:
    """The system default device index for a direction, or None if unset."""
    idx = sd.default.device[1 if kind == "output" else 0]
    return idx if isinstance(idx, int) and idx >= 0 else None


def _read_key() -> str:
    """Block for a single keypress: 'ENTER', 'ESC', or the lowercased char.

    Single-key (no Enter needed) so the prompts feel immediate. Falls back
    to a line read if stdin isn't a real terminal (piped input) -- the
    first char of the line is used.
    """
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # arrow/function-key prefix: consume + ignore
            msvcrt.getwch()
            return ""
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x1b":
            return "ESC"
        return ch.lower()

    import termios
    import tty

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except (termios.error, ValueError):  # not a tty (e.g. piped stdin)
        line = sys.stdin.readline()
        return "ENTER" if line in ("\n", "") else line.strip()[:1].lower()
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch == "\x1b":
        return "ESC"
    return ch.lower()


def _ync_from_key(key: str) -> str | None:
    """Map a y/n/c keypress to an outcome (pure): keep | choose | None (ignore).

    - y: I hear/see it -- keep this device.
    - n: I don't -- go find another.
    - c: may or may not work, but I want to change / test other devices.
    n and c both open the device list; the split just lets the user say
    which situation they're in without pressing "no" when a device is fine.
    """
    k = key.lower()
    if k == "y":
        return "keep"
    if k in ("n", "c"):
        return "choose"
    return None


def _read_ync(prompt: str) -> str:
    """Print the y/n/c menu and block until a valid choice; echoes the key."""
    print(prompt)
    print("  [y] yes, use this device   [n] no, pick another   "
          "[c] change / test others\n> ", end="", flush=True)
    while True:
        key = _read_key()
        outcome = _ync_from_key(key)
        if outcome is not None:
            print(key.lower())
            return outcome


def _confirm_output(sd: Any, index: int) -> str:
    """Test one output device. Returns 'keep' or 'choose'.

    Nothing plays until the user opts in -- a sudden beep is startling --
    so we name the device and wait for ENTER to play (or ESC to skip). The
    soft beeps then loop while the user answers y / n / c.
    """
    import numpy as np

    info = sd.query_devices(index)
    print(f"\nSpeaker to test:  {info['name']}")
    print("You'll hear a few soft beeps on this device.")
    print("Press ENTER to play them, or ESC to skip this device: ", end="", flush=True)
    if _read_key() == "ESC":
        print("skip")
        return "choose"
    print()

    rate = int(info["default_samplerate"])
    tone = 0.2 * np.sin(2 * np.pi * 660 * np.linspace(0.0, 0.3, int(rate * 0.3), endpoint=False))
    pattern = np.concatenate([tone.astype(np.float32), np.zeros(int(rate * 0.5), dtype=np.float32)])
    try:
        sd.play(pattern, samplerate=rate, device=index, loop=True)
    except Exception as exc:  # noqa: BLE001 -- setup tool: report, don't crash
        print(f"  (couldn't play on this device: {exc})")
        return "choose"
    try:
        return _read_ync("Playing test beeps. Do you hear them?")
    finally:
        sd.stop()


def _record_with_meter(
    sd: Any, index: int, seconds: float = 5.0
) -> tuple[Any, int, float] | None:
    """Record from `index` while showing a live meter.

    Returns (audio, rate, seen_peak_dbfs), or None if the device can't
    record. Captures the samples (not just the level) so the caller can
    play them back.
    """
    import numpy as np

    info = sd.query_devices(index)
    chunks: list[Any] = []
    latest = {"rms": -120.0, "peak": -120.0}

    def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        chunks.append(indata[:, 0].copy())
        latest["rms"], latest["peak"] = level_meter(indata[:, 0])

    rate = _CAPTURE_RATE
    try:
        stream = sd.InputStream(samplerate=rate, channels=1, device=index, callback=callback)
    except Exception:  # noqa: BLE001 -- fall back to the device's own rate
        rate = int(info["default_samplerate"])
        try:
            stream = sd.InputStream(samplerate=rate, channels=1, device=index, callback=callback)
        except Exception as exc:  # noqa: BLE001
            print(f"  (couldn't record on this device: {exc})")
            return None
    print(f"Speak normally -- recording ~{seconds:.0f}s (watch the bar):")
    seen_peak = -120.0
    with stream:
        for _ in range(int(seconds / 0.1)):
            sys.stdout.write("\r  " + format_level(latest["rms"], latest["peak"]))
            sys.stdout.flush()
            seen_peak = max(seen_peak, latest["peak"])
            time.sleep(0.1)
    print()
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return audio, rate, seen_peak


def _resample_audio(audio: Any, src_rate: int, dst_rate: int) -> Any:
    """Linear-interpolation resample of a mono float32 buffer (pure).

    Playback-grade, like convobox.audio.playback's resampler -- and needed
    for the same reason: a device may reject a foreign rate (WASAPI does),
    so a 16kHz recording must be converted to the OUTPUT device's rate
    before it can be played.
    """
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32)
    if src_rate == dst_rate or audio.size == 0:
        return audio
    n_dst = int(round(len(audio) * dst_rate / src_rate))
    if n_dst <= 0:
        return np.zeros(0, dtype=np.float32)
    src_x = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    dst_x = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(dst_x, src_x, audio).astype(np.float32)


def _output_rate(sd: Any, playback_device: int | None) -> int:
    """The native rate of the output device a recording will play through."""
    device = playback_device if playback_device is not None else sd.default.device[1]
    try:
        return int(sd.query_devices(device)["default_samplerate"])
    except Exception:  # noqa: BLE001 -- fall back if the device can't be queried
        return _CAPTURE_RATE


def _play_recording(sd: Any, audio: Any, rate: int, playback_device: int | None) -> None:
    """Play a recorded buffer back so the user can HEAR the mic's quality.

    Resamples from the capture rate to the OUTPUT device's native rate
    first -- otherwise a WASAPI/exclusive output rejects the 16kHz buffer
    (PaErrorCode -9997), the same rate mismatch ConvoBox's own playback
    resamples around.
    """
    if getattr(audio, "size", 0) == 0:
        print("  (nothing recorded to play back)")
        return
    out_rate = _output_rate(sd, playback_device)
    playable = _resample_audio(audio, rate, out_rate)
    print("Playing back what your mic captured...")
    try:
        sd.play(playable, samplerate=out_rate, device=playback_device, blocking=True)
    except Exception as exc:  # noqa: BLE001 -- setup tool: report, don't crash
        print(f"  (couldn't play back: {exc})")


def _input_choice_from_key(key: str) -> str | None:
    """Map a mic-test keypress to an outcome (pure).

    Adds replay/again to y/n/c so the user can HEAR each mic -- and re-hear
    it, or re-record -- before choosing. With several mics, playback is the
    only way to tell them apart.
    """
    k = key.lower()
    if k == "y":
        return "keep"
    if k in ("n", "c"):
        return "choose"
    if k == "r":
        return "replay"
    if k == "a":
        return "again"
    return None


def _read_input_choice() -> str:
    """Print the mic menu and block until a valid choice; echoes the key."""
    print(
        "  [y] keep this mic   [n] pick another   [c] test others   "
        "[r] replay   [a] record again\n> ",
        end="",
        flush=True,
    )
    while True:
        key = _read_key()
        outcome = _input_choice_from_key(key)
        if outcome is not None:
            print(key.lower())
            return outcome


def _confirm_input(sd: Any, index: int, playback_device: int | None = None) -> str:
    """Test one input device. Returns 'keep' or 'choose'.

    Waits for ENTER (or ESC to skip), records a short sample with a live
    meter, then PLAYS IT BACK so the user hears the mic's actual sound.
    The menu lets them replay [r] or re-record [a] and compare devices
    before keeping [y] / picking another [n] / testing others [c].
    Playback uses the speaker chosen earlier in setup.
    """
    info = sd.query_devices(index)
    print(f"\nMicrophone to test:  {info['name']}")
    print(
        "Press ENTER to record a short sample, or ESC to skip this device: ",
        end="",
        flush=True,
    )
    if _read_key() == "ESC":
        print("skip")
        return "choose"
    print()

    recorded = _record_with_meter(sd, index)
    if recorded is None:
        return "choose"
    audio, rate, seen_peak = recorded
    if seen_peak <= -55.0:
        print("  (no sound detected -- if you were speaking, try a different device)")
    _play_recording(sd, audio, rate, playback_device)
    while True:
        choice = _read_input_choice()
        if choice == "replay":
            _play_recording(sd, audio, rate, playback_device)
            continue
        if choice == "again":
            recorded = _record_with_meter(sd, index)
            if recorded is not None:
                audio, rate, seen_peak = recorded
                _play_recording(sd, audio, rate, playback_device)
            continue
        return choice  # 'keep' or 'choose'


def _setup_direction(
    sd: Any, kind: str, label: str, playback_device: int | None = None
) -> int | None:
    """Default-first: test the auto-detected device; reveal the chooser only
    if it fails or the user asks for others (progressive disclosure).

    `playback_device` (the speaker chosen earlier) is where mic recordings
    play back during input tests.
    """

    def _test(idx: int) -> str:
        if kind == "output":
            return _confirm_output(sd, idx)
        return _confirm_input(sd, idx, playback_device)

    default_idx = _default_index(sd, kind)
    if default_idx is not None and _test(default_idx) == "keep":
        return default_idx

    # The full device list appears ONLY now -- a regular user who confirmed
    # the default never sees it.
    print(f"\nHere are all your {label} options:")
    devices = collect_devices(sd, kind)
    print(format_devices(devices, kind.upper()))
    while True:
        idx = _prompt_index(f"\n{label.capitalize()} -- number to test (blank to skip):  ", devices)
        if idx is None:
            return None
        if _test(idx) == "keep":
            return idx
        print("ok, let's try another.")


def guided_setup(sd: Any, config_path: Path | None = None) -> None:
    """Default-first audio setup a regular user can run without knowing
    anything about devices, host APIs, or sample rates.

    Tries the system's default speaker and mic first and tests each one
    CONTINUOUSLY (a looping tone / a live level meter); the device chooser
    stays hidden until the default doesn't work or the user asks for a
    different one (the [n]o path in _setup_direction). Advanced users can
    still use --test / --test-input and pin devices by hand.
    """
    config_path = config_path or default_config_path()
    print("ConvoBox audio setup")
    print("Let's make sure your speaker and microphone work -- takes about a minute.")

    chosen_out = _setup_direction(sd, "output", "speaker")
    # Mic recordings play back through the speaker just confirmed, so the
    # user hears each mic through the same output while comparing.
    chosen_in = _setup_direction(sd, "input", "microphone", playback_device=chosen_out)

    if chosen_out is None and chosen_in is None:
        print("\nnothing selected -- convobox.yaml unchanged.")
        return
    saved: list[str] = []
    if chosen_out is not None:
        name = _qualified_name(sd, chosen_out)
        write_device_to_config("output", name, config_path)
        saved.append(f"output_device: {name!r}")
    if chosen_in is not None:
        name = _qualified_name(sd, chosen_in)
        write_device_to_config("input", name, config_path)
        saved.append(f"input_device: {name!r}")
    print(f"\nAll set -- saved to {config_path}:")
    for line in saved:
        print(f"  {line}")
    print("\nRun  python scripts/run_convobox.py  to start talking.")


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="guided: pick + test a speaker and a mic, then save to convobox.yaml",
    )
    parser.add_argument("--inputs", action="store_true", help="show input devices only")
    parser.add_argument("--outputs", action="store_true", help="show output devices only")
    parser.add_argument(
        "--test", metavar="INDEX|NAME", help="play a test tone to this output device"
    )
    parser.add_argument(
        "--test-input",
        metavar="INDEX|NAME",
        help="record from this input device and show a level meter + playback",
    )
    args = parser.parse_args()

    import sounddevice as sd

    if args.setup:
        guided_setup(sd)
        return

    if args.test is not None:
        index, error = resolve_spec(args.test, collect_devices(sd, "output"))
        if index is None:
            print(error)
            raise SystemExit(1)
        play_test_tone(sd, index)
        return

    if args.test_input is not None:
        index, error = resolve_spec(args.test_input, collect_devices(sd, "input"))
        if index is None:
            print(error)
            raise SystemExit(1)
        test_input_device(sd, index)
        return

    show_out = not args.inputs
    show_in = not args.outputs
    if show_out:
        print(format_devices(collect_devices(sd, "output"), "OUTPUT"))
    if show_out and show_in:
        print()
    if show_in:
        print(format_devices(collect_devices(sd, "input"), "INPUT"))


if __name__ == "__main__":
    main()
