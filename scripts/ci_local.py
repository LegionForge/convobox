"""Run the same checks CI runs, locally, in one command.

    .venv/Scripts/python.exe scripts/ci_local.py        (Windows)
    .venv/bin/python scripts/ci_local.py                (POSIX)

Exists because "green on my machine" has diverged from CI twice in live
sessions (a venv missing CI's type stubs; device tests that only worked
where PortAudio was installed). Agents should run this before every
push; it mirrors the repo's dev-rig CI jobs' commands, so keep the two
in sync when .github/workflows/ci.yml changes.
"""

from __future__ import annotations

import subprocess
import sys

CHECKS: list[tuple[str, list[str]]] = [
    ("ruff", [sys.executable, "-m", "ruff", "check", "src", "scripts", "tests"]),
    ("mypy", [sys.executable, "-m", "mypy", "src/convobox"]),
    ("pytest", [sys.executable, "-m", "pytest", "-q"]),
]


def main() -> int:
    failed: list[str] = []
    for name, cmd in CHECKS:
        print(f"--- {name}: {' '.join(cmd[1:])}", flush=True)
        if subprocess.run(cmd).returncode != 0:  # noqa: S603
            failed.append(name)
    if failed:
        print(f"ci_local: FAILED -> {', '.join(failed)}", flush=True)
        return 1
    print("ci_local: all checks passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
