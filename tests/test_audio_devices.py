from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import yaml

from scripts.audio_devices import (
    _default_index,
    _input_choice_from_key,
    _qualified_name,
    _resample_audio,
    _suggest_next,
    _ync_from_key,
    collect_devices,
    format_devices,
    format_level,
    level_meter,
    resolve_spec,
    write_device_to_config,
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
    # Real sounddevice accepts both no-arg (all) and indexed (one) forms.
    def q_devices(index: int | None = None) -> Any:
        return devices if index is None else devices[index]

    def q_hostapis(index: int | None = None) -> Any:
        return hostapis if index is None else hostapis[index]

    return SimpleNamespace(
        default=SimpleNamespace(device=(default_in, default_out)),
        query_hostapis=q_hostapis,
        query_devices=q_devices,
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


# --- input-level metering (the --test-input / --setup mic check) ---


def test_level_meter_silence_floors() -> None:
    rms, peak = level_meter(np.zeros(1000, dtype=np.float32))
    assert rms == -120.0 and peak == -120.0


def test_level_meter_empty_buffer() -> None:
    assert level_meter(np.zeros(0, dtype=np.float32)) == (-120.0, -120.0)


def test_level_meter_full_scale_sine() -> None:
    t = np.linspace(0.0, 1.0, 16000, endpoint=False)
    sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)  # peak ~1.0, rms ~0.707
    rms, peak = level_meter(sine)
    assert peak == pytest.approx(0.0, abs=0.5)   # ~0 dBFS
    assert rms == pytest.approx(-3.0, abs=1.0)   # 0.707 -> ~-3 dBFS


def test_format_level_verdicts() -> None:
    assert "CLIPPING" in format_level(-10.0, -0.5)
    assert "SILENT" in format_level(-60.0, -58.0)
    assert "very quiet" in format_level(-45.0, -42.0)
    assert "good" in format_level(-20.0, -12.0)


def test_format_level_bar_matches_width() -> None:
    out = format_level(-30.0, -25.0, width=20)
    bar = out[out.index("[") + 1 : out.index("]")]
    assert len(bar) == 20


# --- writing the chosen device to config ---


def test_write_device_to_config_creates_output(tmp_path: Path) -> None:
    p = tmp_path / "convobox.yaml"
    write_device_to_config("output", "Speakers, MME", p)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["audio"]["output_device"] == "Speakers, MME"


def test_write_device_to_config_merges_and_preserves_header(tmp_path: Path) -> None:
    p = tmp_path / "convobox.yaml"
    p.write_text(
        "# my header -- keep me\n"
        "backend:\n  name: opencode\n"
        "audio:\n  echo_cancellation: true\n",
        encoding="utf-8",
    )
    write_device_to_config("input", "Mic, MME", p)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# my header -- keep me")
    data = yaml.safe_load(text)
    assert data["audio"]["input_device"] == "Mic, MME"
    assert data["audio"]["echo_cancellation"] is True   # other audio keys preserved
    assert data["backend"]["name"] == "opencode"        # other sections preserved


def test_write_device_result_loads_through_real_config_loader(tmp_path: Path) -> None:
    from convobox.config import load_config

    p = tmp_path / "convobox.yaml"
    write_device_to_config("output", "Headphones (Realtek), MME", p)
    config = load_config(p)
    assert config.audio.output_device == "Headphones (Realtek), MME"


def test_qualified_name_includes_host_api() -> None:
    sd = _fake_sd()
    assert _qualified_name(sd, 4) == "Headphones (Realtek), Windows WASAPI"
    assert _qualified_name(sd, 1) == "Headphones (Realtek), MME"


# --- default-first setup: auto-detected device selection ---


def test_default_index_output_and_input() -> None:
    sd = _fake_sd(default_out=2, default_in=0)
    assert _default_index(sd, "output") == 2
    assert _default_index(sd, "input") == 0


def test_default_index_none_when_unset() -> None:
    sd = _fake_sd()
    sd.default.device = (-1, -1)  # no system default
    assert _default_index(sd, "output") is None
    assert _default_index(sd, "input") is None


# --- three-way y/n/c device choice ---


def test_ync_from_key_keep_choose_ignore() -> None:
    assert _ync_from_key("y") == "keep"
    assert _ync_from_key("Y") == "keep"
    assert _ync_from_key("n") == "choose"   # don't hear it -> pick another
    assert _ync_from_key("c") == "choose"   # test others anyway -> also the chooser
    assert _ync_from_key("x") is None       # unrecognized -> ignore, keep waiting
    assert _ync_from_key("ENTER") is None   # ENTER is not a y/n/c answer


def test_input_choice_from_key_adds_replay_and_again() -> None:
    assert _input_choice_from_key("y") == "keep"
    assert _input_choice_from_key("n") == "choose"
    assert _input_choice_from_key("c") == "choose"
    assert _input_choice_from_key("r") == "replay"      # hear the recording again
    assert _input_choice_from_key("a") == "again"       # record a fresh sample
    assert _input_choice_from_key("z") is None          # unrecognized -> ignore


# --- playback resampling (the mic-playback -9997 fix) ---


def test_resample_audio_noop_when_rates_match() -> None:
    a = np.arange(100, dtype=np.float32)
    assert np.array_equal(_resample_audio(a, 16000, 16000), a)


def test_resample_audio_empty_stays_empty() -> None:
    assert len(_resample_audio(np.zeros(0, dtype=np.float32), 16000, 48000)) == 0


def test_resample_audio_upsamples_16k_to_48k() -> None:
    a = np.ones(1600, dtype=np.float32)   # 0.1s at 16kHz
    out = _resample_audio(a, 16000, 48000)
    assert len(out) == 4800               # 0.1s at 48kHz
    assert out.dtype == np.float32


# --- chooser suggestion (ENTER = try the suggested device) ---


def test_suggest_next_prefers_untried_system_default() -> None:
    devices = _outputs()  # indices 1,2,3,4
    assert _suggest_next(devices, tried=set(), default_idx=3) == 3


def test_suggest_next_skips_already_tried_default() -> None:
    # the default was tested first and rejected -> suggest a different device
    devices = _outputs()
    assert _suggest_next(devices, tried={1}, default_idx=1) == 2


def test_suggest_next_first_untried_when_no_default() -> None:
    devices = _outputs()
    assert _suggest_next(devices, tried={1, 2}, default_idx=None) == 3


def test_suggest_next_falls_back_to_first_when_all_tried() -> None:
    devices = _outputs()
    assert _suggest_next(devices, tried={1, 2, 3, 4}, default_idx=1) == 1


def test_suggest_next_none_for_empty_device_list() -> None:
    assert _suggest_next([], tried=set(), default_idx=None) is None
