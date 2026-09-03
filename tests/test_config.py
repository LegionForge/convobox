from __future__ import annotations

from pathlib import Path

import pytest

from convobox.config import (
    STT_COMPUTE_TYPES,
    STT_COMPUTE_TYPES_CPU,
    STT_COMPUTE_TYPES_CUDA,
    AppConfig,
    AudioConfig,
    BackendConfig,
    DisplayConfig,
    InteractionConfig,
    SafewordConfig,
    STTConfig,
    WebConfig,
    aec_estimate_path,
    detect_working_dir_not_git,
    load_config,
    load_config_lenient,
    read_aec_estimate,
    resolve_config_path,
    write_aec_estimate,
)

# --- resolve_config_path: the explicit-path / CONVOBOX_CONFIG / default
# fallback order load_config() and settings_tui.py's default_config_path()
# both delegate to, extracted so this order lives in exactly one place. ---


def test_resolve_config_path_uses_the_explicit_path_when_given() -> None:
    assert resolve_config_path("custom.yaml") == Path("custom.yaml")


def test_resolve_config_path_uses_the_env_var_when_no_explicit_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONVOBOX_CONFIG", "from-env.yaml")
    assert resolve_config_path() == Path("from-env.yaml")


def test_resolve_config_path_defaults_to_convobox_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONVOBOX_CONFIG", raising=False)
    assert resolve_config_path() == Path("convobox.yaml")


def test_resolve_config_path_explicit_path_wins_over_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONVOBOX_CONFIG", "from-env.yaml")
    assert resolve_config_path("explicit.yaml") == Path("explicit.yaml")


def test_load_config_returns_defaults_when_the_resolved_file_does_not_exist(
    tmp_path: Path,
) -> None:
    # First-run UX: no convobox.yaml yet must produce a working default
    # AppConfig, not a FileNotFoundError -- this is the path a brand-new
    # install takes before ever running the Settings TUI.
    missing = tmp_path / "does-not-exist.yaml"
    config = load_config(missing)
    assert isinstance(config, AppConfig)
    assert config == AppConfig()


# --- AEC delay auto-tune sentinel ---


def test_aec_delay_ms_defaults_to_none() -> None:
    # None = auto-tune (the recommended default). A real int explicitly
    # overrides auto-tuning -- see AudioConfig's own field comment for
    # the 2026-07-15 incident this sentinel exists to prevent: a plain
    # model_dump() used to always write a literal 100 into convobox.yaml
    # on every Settings TUI save, permanently disabling auto-tuning
    # whether the user meant to touch that field or not.
    assert AudioConfig().aec_delay_ms is None


# --- NS/AGC (GitHub issue #323, live-tested 2026-08-31) ---


def test_aec_ns_and_agc_default_off() -> None:
    # Exposing the knob must not silently change any existing setup's
    # behavior, even though NS tested well -- see AudioConfig's own
    # field comment for the trial this defaults-off choice is based on.
    config = AudioConfig()
    assert config.aec_ns is False
    assert config.aec_agc is False
    assert config.aec_ns_level == 2
    assert config.aec_agc_mode == 1


def test_aec_ns_level_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="aec_ns_level must be 0-3"):
        AudioConfig(aec_ns_level=4)
    with pytest.raises(ValueError, match="aec_ns_level must be 0-3"):
        AudioConfig(aec_ns_level=-1)


def test_aec_agc_mode_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="aec_agc_mode must be 0-2"):
        AudioConfig(aec_agc_mode=3)
    with pytest.raises(ValueError, match="aec_agc_mode must be 0-2"):
        AudioConfig(aec_agc_mode=-1)


def test_approval_phrase_is_opt_in_and_validated() -> None:
    assert InteractionConfig().approval_phrase is None
    assert InteractionConfig(approval_phrase="cobalt night and gale").approval_phrase == "cobalt night and gale"
    with pytest.raises(ValueError, match="common affirmations"):
        InteractionConfig(approval_phrase="yes")


# --- AEC estimate sidecar: run_convobox.py's diagnostic write, the
# Settings TUI's read -- deliberately NOT part of convobox.yaml itself
# (see write_aec_estimate's docstring for why). ---


def test_aec_estimate_path_is_a_sidecar_next_to_the_config(tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    assert aec_estimate_path(config_path) == tmp_path / "convobox.yaml.aec-estimate.json"


def test_write_then_read_aec_estimate_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    write_aec_estimate(config_path, 222, 180.3, 32.1)

    result = read_aec_estimate(config_path)

    assert result is not None
    assert result["delay_ms"] == 222
    assert result["output_latency_ms"] == pytest.approx(180.3)
    assert result["input_latency_ms"] == pytest.approx(32.1)
    assert "measured_at" in result


def test_read_aec_estimate_returns_none_when_never_written(tmp_path: Path) -> None:
    assert read_aec_estimate(tmp_path / "convobox.yaml") is None


def test_read_aec_estimate_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    aec_estimate_path(config_path).write_text("not valid json {{{")

    assert read_aec_estimate(config_path) is None


def test_write_aec_estimate_never_raises_when_the_directory_does_not_exist(
    tmp_path: Path,
) -> None:
    # A diagnostic write must never crash the voice loop -- best-effort
    # only, same discipline as _memory_diagnostic() in the STT module.
    config_path = tmp_path / "nonexistent-dir" / "convobox.yaml"
    write_aec_estimate(config_path, 222, 180.0, 32.0)  # must not raise
    assert read_aec_estimate(config_path) is None


# --- InteractionConfig.approval_phrase: voice-gated tool approval (Phase 3)
# is OFF by default, and reuses ConfirmwordDetector's own construction-time
# safety guard -- no dedicated logic duplicated here. ---


def test_approval_phrase_defaults_to_none() -> None:
    assert InteractionConfig().approval_phrase is None


def test_approval_phrase_accepts_a_distinctive_phrase() -> None:
    assert InteractionConfig(approval_phrase="alpha bravo delta").approval_phrase == (
        "alpha bravo delta"
    )


def test_approval_phrase_rejects_a_common_affirmation_only_phrase() -> None:
    with pytest.raises(ValueError, match="common affirmations"):
        InteractionConfig(approval_phrase="yes")


def test_approval_timeout_s_has_a_sane_default() -> None:
    assert InteractionConfig().approval_timeout_s == 30.0


# --- SafewordConfig.kill_phrase: opt-in escalation to
# Orchestrator.force_kill() -- see that method's own docstring. Must be
# one of hard_stop_phrases, since it can't fire a hard stop it isn't
# configured to be a safeword for. ---


def test_kill_phrase_defaults_to_none() -> None:
    assert SafewordConfig().kill_phrase is None


def test_kill_phrase_accepts_a_phrase_already_in_hard_stop_phrases() -> None:
    config = SafewordConfig(
        hard_stop_phrases=["stop stop stop", "eject eject eject"],
        kill_phrase="eject eject eject",
    )
    assert config.kill_phrase == "eject eject eject"


def test_kill_phrase_rejects_a_phrase_not_in_hard_stop_phrases() -> None:
    with pytest.raises(ValueError, match="must also be listed"):
        SafewordConfig(
            hard_stop_phrases=["stop stop stop"],
            kill_phrase="eject eject eject",
        )


# --- detect_working_dir_not_git (2026-09-04, JP): a nudge, not a hard
# error like detect_permission_conflict's siblings -- a working_dir kept
# deliberately outside version control is legitimate, so this is a
# warning + an off switch, not a block. Live-verified against real `git`
# subprocess calls (real repos and real non-repos on disk), not mocked --
# the whole point is confirming the actual `git rev-parse` invocation
# behaves as expected, not just that this code calls *something*. ---


def test_warns_when_working_dir_is_a_real_directory_with_no_git(tmp_path: Path) -> None:
    backend = BackendConfig(working_dir=str(tmp_path))
    warning = detect_working_dir_not_git(backend)
    assert warning is not None
    assert str(tmp_path) in warning
    assert "git init" in warning


def test_no_warning_when_working_dir_is_a_real_git_repo(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    backend = BackendConfig(working_dir=str(tmp_path))
    assert detect_working_dir_not_git(backend) is None


def test_no_warning_when_the_toggle_is_off(tmp_path: Path) -> None:
    backend = BackendConfig(working_dir=str(tmp_path), warn_if_working_dir_not_git=False)
    assert detect_working_dir_not_git(backend) is None


def test_no_warning_when_working_dir_is_unset() -> None:
    assert detect_working_dir_not_git(BackendConfig(working_dir=None)) is None


def test_no_warning_when_working_dir_does_not_exist_yet(tmp_path: Path) -> None:
    # A nonexistent directory is a different, already-handled problem
    # (run_convobox.py's own _check_backend_working_dir SystemExits on
    # it) -- this check must not ALSO fire a confusing git warning about
    # a directory that doesn't exist at all.
    missing = tmp_path / "does-not-exist-yet"
    backend = BackendConfig(working_dir=str(missing))
    assert detect_working_dir_not_git(backend) is None


def test_warn_if_working_dir_not_git_defaults_to_true() -> None:
    assert BackendConfig().warn_if_working_dir_not_git is True


def test_no_warning_when_git_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing `git` binary must never produce a false "not a repo"
    # warning -- _is_git_repo's own contract: None (couldn't determine)
    # is not the same as False (confirmed not a repo).
    import convobox.config as config_module

    monkeypatch.setattr(config_module.shutil, "which", lambda cmd: None)
    backend = BackendConfig(working_dir=str(tmp_path))
    assert detect_working_dir_not_git(backend) is None


def test_kill_phrase_rejects_against_the_default_hard_stop_phrases() -> None:
    # No hard_stop_phrases override -- kill_phrase is still checked
    # against whatever the field's own default_factory produces.
    with pytest.raises(ValueError, match="must also be listed"):
        SafewordConfig(kill_phrase="eject eject eject")


def test_approval_explanation_mode_rejects_an_unrecognized_value() -> None:
    # Fail fast at config load, not at the first live approval prompt --
    # same discipline as approval_phrase's own validator just above.
    assert InteractionConfig().approval_explanation_mode == "plain"
    assert InteractionConfig(approval_explanation_mode="verbose").approval_explanation_mode == (
        "verbose"
    )
    with pytest.raises(ValueError, match="plain or verbose"):
        InteractionConfig(approval_explanation_mode="chatty")


# --- STTConfig.compute_type: picker in both TUI and web UI, fail fast on an
# unknown value rather than a runtime ctranslate2 error at Test/session time.


def test_compute_type_defaults_to_default() -> None:
    assert STTConfig().compute_type == "default"


def test_compute_type_accepts_every_known_ctranslate2_precision() -> None:
    for value in STT_COMPUTE_TYPES:
        assert STTConfig(compute_type=value).compute_type == value


def test_compute_type_rejects_an_unrecognized_value() -> None:
    # Fail fast at config load, not at the first live Test/session --
    # same discipline as approval_explanation_mode's own validator above.
    with pytest.raises(ValueError, match="compute_type must be one of"):
        STTConfig(compute_type="float64")


# --- STTConfig.compute_type vs. device: an incompatible pairing (e.g.
# float16 on cpu) previously passed config validation cleanly and only
# failed three layers deep inside ctranslate2's Whisper constructor with a
# raw traceback -- live-hit 2026-08-03 hand-editing convobox.yaml after a
# device swap (compute_type: float16 left over from a cuda config, device
# switched to cpu). These reproduce that exact crash pre-fix.


def test_compute_type_float16_rejected_on_cpu() -> None:
    with pytest.raises(ValueError, match="not supported on device 'cpu'"):
        STTConfig(device="cpu", compute_type="float16")


def test_compute_type_bfloat16_rejected_on_cpu() -> None:
    with pytest.raises(ValueError, match="not supported on device 'cpu'"):
        STTConfig(device="cpu", compute_type="bfloat16")


def test_compute_type_int16_rejected_on_cuda() -> None:
    with pytest.raises(ValueError, match="not supported on device 'cuda'"):
        STTConfig(device="cuda", compute_type="int16")


def test_compute_type_default_is_always_valid_regardless_of_device() -> None:
    assert STTConfig(device="cpu", compute_type="default").compute_type == "default"
    assert STTConfig(device="cuda", compute_type="default").compute_type == "default"


def test_compute_type_auto_device_skips_the_cross_check() -> None:
    # device: auto resolves its real target at construction time (cuda if
    # present, else cpu) -- nothing to validate statically against, so a
    # cuda-only compute_type must not be rejected just because it might
    # end up running on a cpu-only box.
    assert STTConfig(device="auto", compute_type="float16").compute_type == "float16"


def test_compute_type_every_cpu_supported_value_is_accepted_on_cpu() -> None:
    for value in STT_COMPUTE_TYPES_CPU:
        assert STTConfig(device="cpu", compute_type=value).compute_type == value


def test_compute_type_every_cuda_supported_value_is_accepted_on_cuda() -> None:
    for value in STT_COMPUTE_TYPES_CUDA:
        assert STTConfig(device="cuda", compute_type=value).compute_type == value


# --- load_config_lenient: settings_tui.py's own startup load, which must
# never raise -- a bad on-disk value should fall the affected SECTION back
# to defaults (not the whole file), with the caller told what/why. Live
# UAT, 2026-08-06: a leftover stt.compute_type: float16 / stt.device: cpu
# (left over from PR #210's own live-test) crashed settings_tui.py's
# plain load_config() with an unhandled traceback -- the one tool meant to
# fix a bad config couldn't even open with one.


def test_load_config_lenient_matches_load_config_for_a_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "convobox.yaml"
    path.write_text("stt:\n  device: cpu\n  compute_type: int8\n", encoding="utf-8")
    config, raw, problems = load_config_lenient(path)
    assert problems == []
    assert config == load_config(path)
    assert raw == {"stt": {"device": "cpu", "compute_type": "int8"}}


def test_load_config_lenient_returns_defaults_when_the_file_does_not_exist(
    tmp_path: Path,
) -> None:
    config, raw, problems = load_config_lenient(tmp_path / "does-not-exist.yaml")
    assert config == AppConfig()
    assert raw == {}
    assert problems == []


def test_load_config_lenient_resets_only_the_bad_section_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "convobox.yaml"
    path.write_text(
        "stt:\n  device: cpu\n  compute_type: float16\n"
        "tts:\n  engine: piper\n  voice: en_GB-alba-medium\n",
        encoding="utf-8",
    )
    config, raw, problems = load_config_lenient(path)
    # The bad section falls back to its schema default...
    assert config.stt == STTConfig()
    # ...but an unrelated, valid section is untouched.
    assert config.tts.engine == "piper"
    assert config.tts.voice == "en_GB-alba-medium"
    # raw is the as-parsed file, unchanged -- callers can still show what
    # the rejected value actually was.
    assert raw["stt"]["compute_type"] == "float16"
    assert len(problems) == 1
    assert problems[0].startswith("stt:")
    assert "float16" in problems[0]
    assert "cpu" in problems[0]


def test_load_config_lenient_never_raises_regardless_of_how_broken_the_section_is(
    tmp_path: Path,
) -> None:
    path = tmp_path / "convobox.yaml"
    path.write_text("stt:\n  device: cpu\n  compute_type: not-a-real-value\n", encoding="utf-8")
    config, _raw, problems = load_config_lenient(path)
    assert config.stt == STTConfig()
    assert len(problems) == 1


# --- WebConfig: off/loopback-only by default (docs/WEB-UI-ARCHITECTURE.md's
# "no authentication, local-device trust model" -- a wrong default here
# means an unauthenticated view of live transcripts/tool calls reachable
# from the network). ---


def test_web_config_defaults_to_disabled_and_loopback() -> None:
    web = WebConfig()
    assert web.enabled is False
    assert web.bind_address == "127.0.0.1"
    assert web.history_tracking_enabled is False


def test_web_config_accepts_loopback_addresses() -> None:
    assert WebConfig(bind_address="127.0.0.1").bind_address == "127.0.0.1"
    assert WebConfig(bind_address="localhost").bind_address == "localhost"
    assert WebConfig(bind_address="127.5.5.5").bind_address == "127.5.5.5"
    assert WebConfig(bind_address="::1").bind_address == "::1"


def test_web_config_allows_0_0_0_0_as_an_explicit_choice() -> None:
    # Deliberately allowed (e.g. reachable from another device on the same
    # LAN) -- only a SPECIFIC non-loopback address is rejected, since that
    # usually means a typo'd real IP rather than an intentional "all
    # interfaces" choice.
    assert WebConfig(bind_address="0.0.0.0").bind_address == "0.0.0.0"


def test_web_config_rejects_a_specific_non_loopback_address() -> None:
    with pytest.raises(ValueError, match="non-loopback"):
        WebConfig(bind_address="203.0.113.50")


def test_web_config_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        WebConfig(port=0)
    with pytest.raises(ValueError, match="between 1 and 65535"):
        WebConfig(port=70000)


def test_app_config_wires_a_default_web_config() -> None:
    assert isinstance(AppConfig().web, WebConfig)
    assert AppConfig().web.enabled is False


# --- DisplayConfig: per-role web UI bubble color/name overrides -------------


def test_display_config_colors_default_to_none() -> None:
    display = DisplayConfig()
    assert display.user_color is None
    assert display.assistant_color is None


def test_display_config_names_default_to_none() -> None:
    display = DisplayConfig()
    assert display.user_name is None
    assert display.assistant_name is None


def test_display_config_accepts_arbitrary_names() -> None:
    display = DisplayConfig(user_name="JP", assistant_name="Athena")
    assert display.user_name == "JP"
    assert display.assistant_name == "Athena"


def test_display_config_accepts_3_and_6_digit_hex_colors() -> None:
    assert DisplayConfig(user_color="#fff").user_color == "#fff"
    assert DisplayConfig(user_color="#2e7dfb").user_color == "#2e7dfb"
    assert DisplayConfig(assistant_color="#F0F0F2").assistant_color == "#F0F0F2"


def test_display_config_rejects_a_non_hex_color() -> None:
    with pytest.raises(ValueError, match="not a valid hex color"):
        DisplayConfig(user_color="blue")
    with pytest.raises(ValueError, match="not a valid hex color"):
        DisplayConfig(assistant_color="#12345")


def test_app_config_wires_a_default_display_config() -> None:
    assert isinstance(AppConfig().display, DisplayConfig)
    assert AppConfig().display.user_color is None
