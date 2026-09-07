"""Verify the active venv's installed packages are actually usable, not
just present.

Why this exists: 2026-09-06, a full `uv sync --extra web --extra dev
--extra aec --extra cuda --extra piper --extra calibration` on Helios/
Windows left `aec-audio-processing`'s dist-info missing RECORD/METADATA
(only a `licenses/` subfolder survived) while its compiled `.pyd`/`.dll`
content was untouched -- consistent with the sync being interrupted
partway through (Application log showed a power-source-change event in
the same window). `uv sync` re-runs afterward reported success every
time: it saw the right version already "installed" and skipped it,
never noticing the dist-info was hollow. The package imported fine too
(Python treated it as an empty namespace package) -- only the exact
`from aec_audio_processing import AudioProcessor` line `aec.py` actually
uses raised ImportError, at `--web` launch time, hours later. Same class
of silent-partial-sync failure the UAT checkout's own `_uat-sync.ps1`
already guards against (see its "Verify the venv actually works" step);
this script brings an equivalent guard to this checkout.

Usage: `python scripts/check_venv_extras.py` after any `uv sync`. Exits
0 if every installed distribution is intact and every extra actually
present in this venv imports the real symbol product code uses (not
just a bare `import package_name`), 1 otherwise with the exact
`uv sync --reinstall-package <name>` fix.
"""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def site_packages_dirs() -> list[Path]:
    paths = sysconfig.get_paths()
    seen: set[Path] = set()
    for key in ("purelib", "platlib"):
        raw = paths.get(key)
        if raw and Path(raw).is_dir():
            seen.add(Path(raw))
    return sorted(seen)


def find_broken_distributions(site_packages: Path) -> list[str]:
    """dist-info dirs missing RECORD or METADATA -- the exact shape of
    an install that was interrupted partway through."""
    broken = []
    for entry in sorted(site_packages.glob("*.dist-info")):
        if not entry.is_dir():
            continue
        missing = [name for name in ("RECORD", "METADATA") if not (entry / name).exists()]
        if missing:
            broken.append(f"{entry.name}: missing {', '.join(missing)}")
    return broken


def dist_info_present(site_packages_list: list[Path], underscored_name: str) -> bool:
    """Whether a dist-info dir for this package exists at all (installed,
    whether or not it's intact) -- used to tell "not installed, skip" apart
    from "installed but broken, fail"."""
    return any(
        any(sp.glob(f"{underscored_name}-*.dist-info")) for sp in site_packages_list
    )


def _probe_core() -> None:
    import faster_whisper  # noqa: F401
    import kokoro_onnx  # noqa: F401
    import numpy  # noqa: F401
    import silero_vad  # noqa: F401
    import sounddevice  # noqa: F401


def _probe_aec() -> None:
    from aec_audio_processing import AudioProcessor  # noqa: F401


def _probe_web() -> None:
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401


def _probe_piper() -> None:
    import piper  # noqa: F401


def _probe_cuda() -> None:
    import nvidia.cublas  # noqa: F401


def _probe_calibration() -> None:
    import pycaw  # noqa: F401


def _probe_dev() -> None:
    import mypy  # noqa: F401
    import pytest  # noqa: F401
    import ruff  # noqa: F401


def _probe_browser() -> None:
    import playwright.sync_api  # noqa: F401


@dataclass(frozen=True)
class ExtraCheck:
    extra: str
    label: str
    dist_name: str  # underscored, as it appears in <name>-<version>.dist-info
    probe: Callable[[], None]
    windows_only: bool = False


EXTRA_CHECKS: tuple[ExtraCheck, ...] = (
    ExtraCheck("aec", "AEC (echo cancellation)", "aec_audio_processing", _probe_aec),
    ExtraCheck("web", "web UI (fastapi/uvicorn)", "fastapi", _probe_web),
    ExtraCheck("piper", "Piper TTS", "piper_tts", _probe_piper),
    ExtraCheck("cuda", "CUDA (GPU STT)", "nvidia_cublas_cu12", _probe_cuda),
    ExtraCheck(
        "calibration", "Windows volume calibration", "pycaw", _probe_calibration, windows_only=True
    ),
    ExtraCheck("dev", "dev tooling (pytest/mypy/ruff)", "pytest", _probe_dev),
    ExtraCheck(
        "browser",
        "browser regression suite (playwright)",
        "playwright",
        _probe_browser,
    ),
)


def _run_probe(probe: Callable[[], None]) -> str | None:
    """Returns None on success, else the failure reason."""
    try:
        probe()
    except Exception as exc:  # noqa: BLE001 -- diagnostic tool: report, don't crash
        return str(exc)
    return None


def main() -> int:
    site_dirs = site_packages_dirs()
    ok = True

    print("=== Distribution integrity (RECORD/METADATA present) ===")
    broken: list[str] = []
    for sp in site_dirs:
        broken.extend(find_broken_distributions(sp))
    if broken:
        ok = False
        for entry in broken:
            print(f"BROKEN: {entry}")
    else:
        print("OK: no dist-info directories missing RECORD/METADATA")

    print("\n=== Product-critical imports ===")
    core_failure = _run_probe(_probe_core)
    if core_failure is None:
        print("OK: core runtime deps (numpy/sounddevice/silero-vad/faster-whisper/kokoro-onnx)")
    else:
        ok = False
        print(f"FAIL: core runtime deps -- {core_failure}")

    for check in EXTRA_CHECKS:
        if check.windows_only and sys.platform != "win32":
            continue
        if not dist_info_present(site_dirs, check.dist_name):
            print(f"skip: {check.label} ({check.extra} extra not installed)")
            continue
        failure = _run_probe(check.probe)
        if failure is None:
            print(f"OK: {check.label} ({check.extra} extra)")
        else:
            ok = False
            print(f"FAIL: {check.label} ({check.extra} extra) -- {failure}")
            print(f"      fix: uv sync --reinstall-package {check.dist_name.replace('_', '-')}")

    print()
    if ok:
        print("All checks passed.")
        return 0
    print("One or more checks FAILED -- see 'fix:' lines above.")
    print("This usually means a prior uv sync was interrupted partway through")
    print("(e.g. a power event, a closed terminal) and left a package's")
    print("dist-info hollow while `uv sync` itself kept reporting success.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
