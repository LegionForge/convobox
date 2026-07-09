from __future__ import annotations

from scripts.spike import _resolve_device


def test_cli_device_takes_priority_over_config() -> None:
    assert _resolve_device("MyMic", "ConfigMic") == "MyMic"


def test_falls_back_to_config_when_no_cli_device() -> None:
    assert _resolve_device(None, "ConfigMic") == "ConfigMic"


def test_numeric_device_string_becomes_int_index() -> None:
    assert _resolve_device("3", None) == 3


def test_numeric_config_device_becomes_int_index() -> None:
    assert _resolve_device(None, "2") == 2


def test_no_device_configured_anywhere_is_none() -> None:
    assert _resolve_device(None, None) is None


def test_device_name_that_looks_alphanumeric_stays_a_string() -> None:
    assert _resolve_device("USB Mic 2", None) == "USB Mic 2"
