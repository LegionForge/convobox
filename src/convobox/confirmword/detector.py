from __future__ import annotations

import re

# The inverse of the SafewordDetector. Where the safeword is a hard STOP that
# must fire easily, the confirmword is a deliberate GO that must fire only on
# purpose: it gates destructive-classed actions requested by voice, where a
# misheard "yes" must never be able to approve anything. Same discipline as
# the safeword -- plain normalized-substring match, no ML, no fuzzy matching,
# no LLM -- so a human reading this file can predict exactly when it fires.
#
# The safety property lives at CONSTRUCTION, not at match time: the approval
# phrase must contain at least one distinctive token that casual speech would
# never produce. A phrase made up entirely of common affirmations/fillers
# ("yes", "sure", "okay", "oui", "uh huh") is REJECTED loudly at startup, so
# an operator can't accidentally arm an approval word that everyday speech
# would trip. See docs/ROADMAP.md, "Safety tiers for destructive actions".

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")

# Common affirmations and fillers, across the handful of languages JP works
# in, plus the obvious English ones. NOT exhaustive and not meant to be: the
# real protection is that the approval phrase must carry a distinctive token.
# This set only stops the obvious "yes"-class words from BEING the whole
# approval phrase. Kept small and auditable on purpose.
_COMMON_AFFIRMATIONS: frozenset[str] = frozenset(
    {
        # English affirmations
        "yes", "yeah", "yep", "yup", "ya", "yah", "yea", "aye",
        "sure", "ok", "okay", "okey", "fine", "right", "correct",
        "affirmative", "definitely", "absolutely", "certainly",
        # English fillers / hesitations that STT emits for "uh-huh"-class sounds
        "uh", "huh", "uhhuh", "mmhmm", "mhm", "hmm", "mm", "yep",
        "please", "do", "it", "go", "now", "and", "the",
        # other languages JP uses (roadmap called these out explicitly)
        "oui",              # French
        "si", "sisi",       # Spanish / Italian
        "da",               # Russian
        "ja",               # German
        "hai",              # Japanese
        "oke", "okei",      # informal
    }
)


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class ConfirmwordDetector:
    """Detects a user-chosen approval phrase in a transcript.

    Constructed with a single approval phrase. Refuses (raises ``ValueError``)
    a phrase that normalizes to nothing, or one whose every token is a common
    affirmation/filler -- because such a phrase could be tripped by casual
    speech, defeating the purpose of an approval gate.
    """

    def __init__(self, approval_phrase: str) -> None:
        normalized = _normalize(approval_phrase)
        if not normalized:
            raise ValueError(
                f"approval_phrase {approval_phrase!r} normalizes to nothing "
                "and could never match"
            )
        tokens = normalized.split(" ")
        if all(token in _COMMON_AFFIRMATIONS for token in tokens):
            raise ValueError(
                f"approval_phrase {approval_phrase!r} is made up entirely of "
                "common affirmations/fillers; casual speech could approve a "
                "destructive action. Choose a phrase with a distinctive word "
                "(e.g. a code word) that you would not say by accident."
            )
        self._original = approval_phrase
        self._normalized = normalized

    @property
    def approval_phrase(self) -> str:
        """The original (un-normalized) approval phrase this detector matches."""
        return self._original

    def check(self, transcript: str) -> bool:
        """True when the approval phrase appears in ``transcript``.

        Word-boundary aware (padded match), like the safeword, so the phrase
        is recognized embedded in a sentence but not as a substring of a
        larger word.
        """
        normalized = _normalize(transcript)
        if not normalized:
            return False
        return f" {self._normalized} " in f" {normalized} "
