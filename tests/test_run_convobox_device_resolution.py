"""_validate_audio_device / _resolve_device (2026-09-02, JP live on macOS):
a Bluetooth headset configured in convobox.yaml then disconnected made
BOTH --tui and --web crash on startup -- sd.InputStream()/OutputStream()
raise immediately for a device spec matching nothing currently connected,
and nothing on this path caught it. These tests fake `audio_devices` and
`sounddevice` at the module level (real hardware enumeration isn't
available/stable in CI) to prove the fallback-to-default behavior without
needing a real device.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from scripts.run_convobox import _resolve_device, _validate_audio_device

_FAKE_DEVICES = [
    {"index": 0, "name": "MacBook Pro Microphone", "hostapi": "Core Audio"},
    {"index": 1, "name": "MacBook Pro Speakers", "hostapi": "Core Audio"},
]


@pytest.fixture
def fake_audio_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installs fake `audio_devices`/`sounddevice` modules so
    _validate_audio_device's `import audio_devices as ad` / `import
    sounddevice as sd` resolve to controlled fakes instead of touching
    real hardware. real resolve_spec() logic (digit vs name matching) is
    reused as-is via a minimal reimplementation matching its contract --
    only collect_devices() is stubbed, since that's the one real call
    that would otherwise enumerate actual connected hardware.
    """
    fake_ad = types.ModuleType("audio_devices")

    def _collect_devices(sd: Any, kind: str) -> list[dict[str, Any]]:
        return _FAKE_DEVICES

    def _resolve_spec(spec: str, devices: list[dict[str, Any]]) -> tuple[int | None, str | None]:
        spec = spec.strip()
        if spec.isdigit():
            index = int(spec)
            if any(d["index"] == index for d in devices):
                return index, None
            return None, f"no device with index {index}"
        needle = spec.lower()
        matches = [d for d in devices if needle in f"{d['name']}, {d['hostapi']}".lower()]
        if not matches:
            return None, f"no device matching {spec!r}"
        return matches[0]["index"], None

    fake_ad.collect_devices = _collect_devices  # type: ignore[attr-defined]
    fake_ad.resolve_spec = _resolve_spec  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "audio_devices", fake_ad)
    monkeypatch.setitem(sys.modules, "sounddevice", types.ModuleType("sounddevice"))


def test_a_disconnected_device_falls_back_to_none_with_a_warning(
    fake_audio_devices: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        result = _validate_audio_device("AirPods Pro, Core Audio", "input")
    assert result is None
    assert "not found among currently connected devices" in caplog.text
    assert "AirPods Pro" in caplog.text


def test_a_currently_connected_device_passes_through_unchanged(
    fake_audio_devices: None,
) -> None:
    result = _validate_audio_device("MacBook Pro Microphone, Core Audio", "input")
    assert result == "MacBook Pro Microphone, Core Audio"


def test_a_stale_numeric_index_also_falls_back_to_none(fake_audio_devices: None) -> None:
    # The bug wasn't name-specs-only -- a numeric index can go stale too
    # (a device unplugged/replugged can get reassigned a different index
    # on some platforms), and the old code's `.isdigit()` shortcut skipped
    # validation entirely for this case.
    result = _validate_audio_device(99, "output")
    assert result is None


def test_none_device_passes_through_without_touching_audio_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unset (system default) is the common case -- must not even attempt
    # to import audio_devices/sounddevice for it.
    monkeypatch.delitem(sys.modules, "audio_devices", raising=False)
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    assert _validate_audio_device(None, "input") is None


def test_resolve_device_falls_back_to_none_for_a_stale_config_value(
    fake_audio_devices: None,
) -> None:
    # The actual call site (config.audio.input_device) -- proves the full
    # path from a stale convobox.yaml value to a safe None, not just the
    # lower-level helper in isolation.
    assert _resolve_device(None, "Nonexistent Bluetooth Headset, Core Audio") is None


def test_resolve_device_still_honors_a_cli_override_that_is_currently_connected(
    fake_audio_devices: None,
) -> None:
    assert (
        _resolve_device("MacBook Pro Speakers, Core Audio", "some stale config value")
        == "MacBook Pro Speakers, Core Audio"
    )


def test_validation_failure_itself_does_not_crash(
    fake_audio_devices: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If device enumeration itself raises (a flaky PortAudio backend, not
    # just "no match"), the original device value passes through
    # unchanged rather than the validation step becoming a NEW crash --
    # matches _device_choices' own "must never crash" stance elsewhere in
    # this repo.
    def _broken_collect_devices(sd: object, kind: str) -> list[dict[str, object]]:
        raise RuntimeError("simulated PortAudio enumeration failure")

    monkeypatch.setattr(sys.modules["audio_devices"], "collect_devices", _broken_collect_devices)
    result = _validate_audio_device("some device", "input")
    assert result == "some device"
