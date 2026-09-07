"""convobox-doctor: diagnose a ConvoBox configuration and environment
before a real voice session hits the problem live.

Two tiers, by design:

- **Static checks** (always run, fast, no side effects): does convobox.yaml
  parse and validate; does it carry any of the known-dangerous config
  combinations config.py's own detect_*() functions already catch
  (backend.permission_mode conflicts, claude-code's approval-hook gap,
  backend.working_dir not being a git repo); and, for whichever
  engines/features THIS config actually turns on, is the extra it needs
  installed (audio.echo_cancellation -> aec, web.enabled -> web,
  tts.engine=piper -> piper) -- the same three-line story
  scripts/check_venv_extras.py already tells for the dev checkout, but
  scoped to only what this specific config uses, not every extra that
  exists.
- **Live checks** (opt-in via flags, since they have real side effects --
  an STT/TTS model download, real audio device I/O, spawning the actual
  backend CLI): reuse scripts/settings_tui.py's own probe_audio/probe_stt/
  probe_tts/probe_backend directly -- the exact functions the Settings
  TUI's [t] Test hotkey and the web UI's "Test this section" button
  already exercise, not a reimplementation.

Nothing here mutates convobox.yaml -- this is read-only diagnosis. Fixing
a finding is still a manual edit (Settings TUI/web UI, or by hand).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Reused as-is, not reimplemented -- see this module's own docstring.
from settings_tui import (  # type: ignore[import-not-found]
    probe_audio,
    probe_backend,
    probe_stt,
    probe_tts,
)

from convobox.config import (
    AppConfig,
    detect_claude_code_approval_gap,
    detect_permission_conflict,
    detect_working_dir_not_git,
    load_config_lenient,
    resolve_config_path,
)

Level = str  # "ok" | "warn" | "fail" | "info"


@dataclass(frozen=True)
class Finding:
    check: str
    level: Level
    message: str


def static_config_findings(config: AppConfig, problems: list[str]) -> list[Finding]:
    findings = [Finding("config", "fail", problem) for problem in problems]

    conflict = detect_permission_conflict(config.backend)
    if conflict:
        findings.append(Finding("backend.permission_mode", "fail", conflict))

    gap = detect_claude_code_approval_gap(config.backend, config.interaction)
    if gap:
        findings.append(Finding("backend.permission_mode", "fail", gap))

    not_git = detect_working_dir_not_git(config.backend)
    if not_git:
        findings.append(Finding("backend.working_dir", "warn", not_git))

    return findings


def extras_findings(config: AppConfig) -> list[Finding]:
    """Only checks the extra(s) THIS config actually needs, not every
    extra that exists (that's check_venv_extras.py's job, for the dev
    checkout as a whole)."""
    findings: list[Finding] = []

    if config.audio.echo_cancellation:
        try:
            import aec_audio_processing  # noqa: F401
        except ImportError:
            findings.append(
                Finding(
                    "audio.echo_cancellation",
                    "fail",
                    "enabled, but the 'aec' extra is not installed -- install it with: "
                    'uv pip install -e ".[aec]" (Windows wheels; other platforms may need '
                    "a source build, see docs/KNOWN-ISSUES.md)",
                )
            )

    if config.web.enabled:
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            findings.append(
                Finding(
                    "web.enabled",
                    "fail",
                    "enabled, but the 'web' extra is not installed -- install it with: "
                    "uv sync --extra web",
                )
            )

    if config.tts.engine == "piper":
        try:
            import piper  # noqa: F401
        except ImportError:
            findings.append(
                Finding(
                    "tts.engine",
                    "fail",
                    "set to 'piper', but the 'piper' extra is not installed -- install it "
                    "with: uv sync --extra piper",
                )
            )

    return findings


async def live_findings(config: AppConfig, which: set[str]) -> list[Finding]:
    findings: list[Finding] = []

    if "audio" in which:
        # probe_audio() never raises -- device-not-found is reported IN
        # its own returned string, not via exception (see its own
        # docstring) -- so this is "info", not a strict ok/fail like the
        # other three probes below.
        try:
            message = await probe_audio(config)
            findings.append(Finding("audio", "info", message))
        except Exception as exc:  # noqa: BLE001 -- report every probe's own failure, keep checking the rest
            findings.append(Finding("audio", "fail", f"{type(exc).__name__}: {exc}"))

    if "stt" in which:
        try:
            message = await probe_stt(config)
            findings.append(Finding("stt", "ok", message))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("stt", "fail", f"{type(exc).__name__}: {exc}"))

    if "tts" in which:
        try:
            message = await probe_tts(config)
            findings.append(Finding("tts", "ok", message))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("tts", "fail", f"{type(exc).__name__}: {exc}"))

    if "backend" in which:
        try:
            message = await probe_backend(config)
            findings.append(Finding("backend", "ok", message))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("backend", "fail", f"{type(exc).__name__}: {exc}"))

    return findings


_TAG = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "info": "INFO"}


def _print(findings: list[Finding]) -> None:
    for f in findings:
        print(f"[{_TAG[f.level]}] {f.check}: {f.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None, help="path to a convobox.yaml config file")
    parser.add_argument("--audio", action="store_true", help="also test the configured mic/speaker devices")
    parser.add_argument(
        "--stt", action="store_true", help="also test the STT engine (may download a model, first run only)"
    )
    parser.add_argument(
        "--tts", action="store_true", help="also test the TTS engine (may download a voice, first run only)"
    )
    parser.add_argument(
        "--backend", action="store_true", help="also test the backend adapter (spawns the real CLI briefly)"
    )
    parser.add_argument("--all", action="store_true", help="run every live check above")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    exists_note = "" if config_path.exists() else " (not found -- using built-in defaults)"
    print(f"Config: {config_path}{exists_note}")

    config, _raw, problems = load_config_lenient(args.config)

    findings = static_config_findings(config, problems)
    findings.extend(extras_findings(config))

    which: set[str] = {"audio", "stt", "tts", "backend"} if args.all else set()
    for flag in ("audio", "stt", "tts", "backend"):
        if getattr(args, flag):
            which.add(flag)

    if "backend" in which and sys.platform == "win32":
        # Pre-existing, documented, Windows-only asyncio wart (see
        # BackendAdapter.aclose()'s own docstring, src/convobox/adapters/
        # base.py) -- NOT something today's spawn-and-tear-down-in-one-
        # shot check can avoid: a subprocess adapter's pipe transport can
        # still be finalized after this process's own short-lived asyncio
        # loop has closed, and on Windows that finalizer's own warning
        # message construction can itself crash formatting an already-
        # closed socket's repr. Cosmetic -- does not affect the finding
        # below or this command's exit code -- but printed BEFORE running
        # the check so a stray traceback afterward doesn't read as this
        # check having silently failed.
        print(
            "Note: on Windows, testing the backend can print a harmless "
            "'Exception ignored in ... Event loop is closed' or 'unclosed "
            "transport' traceback afterward -- a known asyncio/Windows "
            "cosmetic issue, not a real failure. It does not affect the "
            "result below.\n"
        )

    if which:
        findings.extend(asyncio.run(live_findings(config, which)))

    print()
    if findings:
        _print(findings)
    else:
        print("No static issues found.")

    if not which:
        print("\n(pass --audio/--stt/--tts/--backend, or --all, to also test them live)")

    failed = [f for f in findings if f.level == "fail"]
    print()
    if failed:
        print(f"{len(failed)} problem(s) found -- see FAIL lines above.")
        return 1
    print("No blocking problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
