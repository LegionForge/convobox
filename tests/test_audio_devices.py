from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from scripts.audio_devices import (
    collect_devices,
    format_devices,
    resolve_spec,
)


def _fake_sd(default_out: int = 1, default_in: int = 0) -> SimpleNamespace:
    """A fake sounddevice exposing the real Realtek-style multi-host-API mess."""
    hostapis = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]
    devices = [
        {"name": "Mic (Realtek)", "hostapi": 0, "max_output_channels": 0, "max_input_channels": 2,
         "default_samplerate": 44100.0},
        {"name": "Headphones (Realtek)", "hostapi": 0, "max_output_channels": 2, "max_input_channels": 0,
         "default_samplerate": 44100.0},
        {"name": "Speakers (Realtek)", "hostapi": 0, "max_output_channels": 2, "max_input_channels": 0,
         "default_samplerate": 44100.0},
        {"name": "Headphones (Realtek)", "hostapi": 1, "max_output_channels": 2, "max_input_channels": 0,
         "default_samplerate": 44100.0},
        {"name": "Headphones (Realtek)", "hostapi": 2, "max_output_channels": 2, "max_input_channels": 0,
         "default_samplerate": 48000.0},
    ]
    return SimpleNamespace(
        default=SimpleNamespace(device=(default_in, default_out)),
        query_hostapis=lambda: hostapis,
        query_devices=lambda: devices,
    )


def _outputs() -> list[dict[str, Any]]:
    return collect_devices(_fake_sd(), "output")


def test_collect_devices_filters_output_direction() -> None:
    outs = collect_devices(_fake_sd(), "output")
    assert [d["index"] for d in outs] == [1, 2, 3, 4]  # the mic (index 0) is excluded
    assert all(d["channels"] == 2 for d in outs)


def test_collect_devices_filters_input_direction() -> None:
    ins = collect_devices(_fake_sd(), "input")
    assert [d["index"] for d in ins] == [0]
    assert ins[0]["name"] == "Mic (Realtek)"


def test_collect_devices_marks_default_and_hostapi_and_rate() -> None:
    outs = collect_devices(_fake_sd(default_out=1), "output")
    by_index = {d["index"]: d for d in outs}
    assert by_index[1]["default"] is True
    assert by_index[2]["default"] is False
    assert by_index[4]["hostapi"] == "Windows WASAPI"
    assert by_index[4]["samplerate"] == 48000


def test_format_devices_marks_default_and_hints_host_api() -> None:
    text = format_devices(_outputs(), "OUTPUT")
    assert "OUTPUT devices:" in text
    assert "*" in text  # the default is marked
    assert "Windows WASAPI" in text
    assert "host API" in text  # the pin-the-full-name hint


def test_format_devices_empty() -> None:
    assert format_devices([], "OUTPUT") == "(no OUTPUT devices found)"


def test_resolve_spec_by_index() -> None:
    assert resolve_spec("2", _outputs()) == (2, None)


def test_resolve_spec_bad_index() -> None:
    index, error = resolve_spec("99", _outputs())
    assert index is None
    assert error is not None and "99" in error


def test_resolve_spec_by_host_api_qualified_name() -> None:
    index, error = resolve_spec("Headphones (Realtek), Windows WASAPI", _outputs())
    assert error is None
    assert index == 4  # the WASAPI Headphones


def test_resolve_spec_ambiguous_name_lists_host_api_options() -> None:
    # The exact Windows trap: a bare name matches three host APIs. The
    # error must list all qualified options so the user can pick.
    index, error = resolve_spec("Headphones (Realtek)", _outputs())
    assert index is None
    assert error is not None
    assert "multiple devices" in error
    assert "Windows WASAPI" in error and "Windows DirectSound" in error and "MME" in error


def test_resolve_spec_unique_partial_match() -> None:
    # "Speakers" is unique among outputs -> resolves without qualification.
    index, error = resolve_spec("Speakers", _outputs())
    assert error is None
    assert index == 2


def test_resolve_spec_no_match() -> None:
    index, error = resolve_spec("Nonexistent Device", _outputs())
    assert index is None
    assert error is not None and "no device matching" in error
