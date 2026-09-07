"""Pure, unit-testable helpers for what happens once at process startup:
resolving the installed package version and building the spoken "I'm
ready" announcement from it.

Split out of scripts/run_convobox.py (2026-09-07, a repository review's
"extract one bounded presentation/startup-wiring concern" recommendation)
-- both are pure functions with zero dependency on the mic loop, audio
stack, or orchestrator, and belong with the rest of the actual convobox
package rather than the launcher script that happens to call them.
"""

from __future__ import annotations

import importlib.metadata


def _resolve_convobox_version() -> str:
    """Best-effort package version for the startup announcement.

    Falls back to "dev" rather than raising -- a source checkout without
    installed metadata (e.g. a fresh clone before `uv sync`/`pip install
    -e .` has registered the package) must never crash startup over a
    cosmetic version string.
    """
    try:
        return importlib.metadata.version("legionforge-convobox")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def startup_announcement(version: str) -> str:
    """The spoken "I'm ready" line, once STT/TTS/backend setup is done.

    Exists because the FIRST utterance being silently discarded (root
    cause: cuBLAS delay-loading on the first real transcribe() call,
    fixed at its source in LocalTranscriber._warm_up) still left no
    signal for the user that ConvoBox was actually ready to hear them --
    "say something and see if it works" isn't a great first experience.
    A pure function (not inlined at the call site) so the exact wording
    is unit-testable without a real TTS/audio stack.
    """
    return f"LegionForge ConvoBox, version {version}, ready and standing by."
