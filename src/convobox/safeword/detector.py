from __future__ import annotations

import re

# This detector is deliberately a plain normalized-substring match: no ML,
# no fuzzy matching, no LLM. A hard stop is a safety-critical abort, so the
# check must be simple and auditable — a human reading this file must be able
# to predict exactly when it fires. Fuzzy/model-based matching would trade
# that guarantee for recall we don't want here.

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class SafewordDetector:
    def __init__(self, hard_stop_phrases: list[str]) -> None:
        # Fail loudly rather than silently dropping a safety-critical phrase:
        # an operator who configures a phrase that normalizes to nothing
        # (e.g. pure punctuation) must find out at startup, not discover at
        # the worst possible moment that their hard stop never fires.
        empty = [phrase for phrase in hard_stop_phrases if not _normalize(phrase)]
        if empty:
            raise ValueError(
                f"hard_stop_phrases contains phrase(s) that normalize to "
                f"nothing and would never match: {empty!r}"
            )
        self._phrases: list[tuple[str, str]] = [
            (phrase, _normalize(phrase)) for phrase in hard_stop_phrases
        ]

    def check(self, transcript: str) -> str | None:
        normalized = _normalize(transcript)
        if not normalized:
            return None
        padded = f" {normalized} "
        for original, normalized_phrase in self._phrases:
            if f" {normalized_phrase} " in padded:
                return original
        return None
