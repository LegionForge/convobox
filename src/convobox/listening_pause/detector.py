from __future__ import annotations

import re

# Same shape as SafewordDetector (a list of phrases, deterministic
# normalized-substring match, no ML/fuzzy/LLM) because entering the paused
# state is itself a hard-stop-class event -- JP's framing: "like interrupt,
# but interrupt and stop processing all speech, then listen for wake word."
# See docs/DESIGN-barge-in.md, "Pause/resume listening".
#
# SAME SAFETY TIER AS SAFEWORD, NOT ConfirmwordDetector: accidentally
# saying "stop listening" is benign -- you just say the wake word again to
# resume -- not destructive. So this detector does NOT ban common
# affirmations the way ConfirmwordDetector does; only "doesn't normalize to
# nothing" is a construction-time error, same guard as SafewordDetector.

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_PAUSE_PHRASES: tuple[str, ...] = ("stop listening", "pause listening")


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class PauseListeningDetector:
    """Detects a phrase that should pause listening (docs/DESIGN-barge-in.md).

    Constructed with a list of phrases (default ``DEFAULT_PAUSE_PHRASES``);
    any one matching pauses ConvoBox into a wake-word-only listening state
    (the orchestration for that state lives outside this class -- this is a
    pure detector, same division of labor as SafewordDetector vs. the main
    loop). Fails loudly at construction (``ValueError``) on any phrase that
    normalizes to nothing, since that phrase could never fire.
    """

    def __init__(self, pause_phrases: list[str] | None = None) -> None:
        phrases = pause_phrases if pause_phrases is not None else list(DEFAULT_PAUSE_PHRASES)
        empty = [phrase for phrase in phrases if not _normalize(phrase)]
        if empty:
            raise ValueError(
                f"pause_phrases contains phrase(s) that normalize to "
                f"nothing and would never match: {empty!r}"
            )
        self._phrases: list[tuple[str, str]] = [
            (phrase, _normalize(phrase)) for phrase in phrases
        ]

    def check(self, transcript: str) -> str | None:
        """The original matched phrase, or None. Word-boundary aware."""
        normalized = _normalize(transcript)
        if not normalized:
            return None
        padded = f" {normalized} "
        for original, normalized_phrase in self._phrases:
            if f" {normalized_phrase} " in padded:
                return original
        return None
