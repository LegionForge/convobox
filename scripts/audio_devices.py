"""List audio devices and play test tones -- find the right one for convobox.yaml.

Windows exposes the same physical jack through several host APIs (MME,
DirectSound, WASAPI, WDM-KS), each with different latency and sample-rate
behavior -- and that split is exactly where ConvoBox device configuration
goes wrong. This tool makes the choice visible and testable:

    python scripts/audio_devices.py                 # list output + input devices
    python scripts/audio_devices.py --inputs        # input devices only
    python scripts/audio_devices.py --test 5        # play a test tone to device 5
    python scripts/audio_devices.py --test "Headphones (Realtek(R) Audio), MME"

Then pin the one you actually hear in convobox.yaml -- INCLUDING the host
API, so it resolves to exactly one device:

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
import sys
from pathlib import Path
from typing import Any

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


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--inputs", action="store_true", help="show input devices only")
    parser.add_argument("--outputs", action="store_true", help="show output devices only")
    parser.add_argument(
        "--test", metavar="INDEX|NAME", help="play a test tone to this output device"
    )
    args = parser.parse_args()

    import sounddevice as sd

    if args.test is not None:
        index, error = resolve_spec(args.test, collect_devices(sd, "output"))
        if index is None:
            print(error)
            raise SystemExit(1)
        play_test_tone(sd, index)
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
