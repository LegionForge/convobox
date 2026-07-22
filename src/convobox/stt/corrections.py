from __future__ import annotations

import re
from collections.abc import Mapping

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class TranscriptCorrector:
    """Apply an operator-maintained glossary of known STT mistakes.

    This is intentionally deterministic: configured source phrases are matched
    case-insensitively at word boundaries and replaced with the operator's
    exact target text.  It is not a language-model cleanup pass, so a user can
    inspect the config and predict every rewrite.

    The caller must keep safety-critical checks (hard stop, pause/resume, and
    approval decisions) on the raw transcript.  This class is for ordinary
    command routing only.
    """

    def __init__(self, corrections: Mapping[str, str] | None = None) -> None:
        entries = corrections or {}
        normalized_sources: dict[str, tuple[str, str]] = {}
        for source, replacement in entries.items():
            normalized_source = _normalize(source)
            if not normalized_source:
                raise ValueError(
                    f"stt.corrections source {source!r} normalizes to nothing"
                )
            if not _normalize(replacement):
                raise ValueError(
                    f"stt.corrections replacement for {source!r} normalizes to nothing"
                )
            if normalized_source in normalized_sources:
                previous = normalized_sources[normalized_source][0]
                raise ValueError(
                    "stt.corrections contains sources that normalize to the same "
                    f"phrase: {previous!r} and {source!r}"
                )
            normalized_sources[normalized_source] = (source, replacement)

        # Longest first means a precise phrase wins over one of its shorter
        # components (for example, "yellow garden" before "garden").
        alternatives: list[str] = []
        self._replacements: dict[str, str] = {}
        for normalized_source, (_, replacement) in sorted(
            normalized_sources.items(), key=lambda item: len(item[0]), reverse=True
        ):
            words = normalized_source.split()
            alternatives.append(r"[\W_]+".join(re.escape(word) for word in words))
            self._replacements[normalized_source] = replacement
        self._pattern = (
            re.compile(
                rf"(?<![a-z0-9])(?:{'|'.join(alternatives)})(?![a-z0-9])",
                re.IGNORECASE,
            )
            if alternatives
            else None
        )

    def correct(self, transcript: str) -> str:
        """Return ``transcript`` with every configured correction applied."""
        if self._pattern is None:
            return transcript

        def replace(match: re.Match[str]) -> str:
            return self._replacements[_normalize(match.group())]

        return self._pattern.sub(replace, transcript)
