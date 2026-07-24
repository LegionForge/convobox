from __future__ import annotations

import re
from typing import Literal

# The fifth instance of the Safeword/Confirmword/ResumeWord/PauseListening-shaped
# pattern: plain normalized-substring match, no ML, no fuzzy matching, no LLM.
# See docs/DESIGN-0.3.0-interaction-and-safety.md's Phase 2 (response tiering)
# and its shared-primitive framing (PendingPrompt): after a tiered/short
# response, ConvoBox is briefly "waiting for continue-or-not," and the next
# utterance is interpreted against this small fixed vocabulary instead of
# routed as a normal command.
#
# LOW-STAKES VOCABULARY, same tier as ResumeWordDetector/PauseListeningDetector,
# NOT ConfirmwordDetector's tier: misreading "continue" as a real command (or
# missing it) just means the user hears more detail than they wanted, or has
# to ask again -- never a destructive action. So this detector does NOT ban
# common words, unlike ConfirmwordDetector. Per the design doc's safety
# invariant, this is deliberate: don't force a low-stakes primitive to carry
# high-stakes-only restrictions it doesn't need, and never let an
# approval-vocabulary detector's strictness leak the other way either.
#
# Vocabulary round-trip verified (Piper -> faster-whisper, the same
# methodology established fixing the DEFAULT_RESUME_WORD bug) before being
# trusted as a default -- not guessed. "I'm good" was tested and REJECTED
# (mis-heard as "am Gut") and is deliberately absent from DEFAULT_DECLINE_PHRASES.

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_CONTINUE_PHRASES: tuple[str, ...] = (
    "continue",
    "resume",
    "please continue",
    "go on",
    "please go on",
)

DEFAULT_DECLINE_PHRASES: tuple[str, ...] = (
    "that's enough",
    "no thanks",
    "stop there",
)

_Outcome = Literal["continue", "decline"]


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class ContinueDetector:
    """Classifies a transcript as "continue" / "decline" / neither, for the
    response-tiering continuation prompt (docs/DESIGN-0.3.0-interaction-and-safety.md,
    Phase 2): the eyes-free "tell me more" escape hatch for a user who isn't
    looking at the TUI's always-visible full-detail pane.

    Constructed with two phrase lists (continue-phrases, decline-phrases);
    ``check()`` returns whichever side matched, or ``None`` for anything else
    (an ordinary command, silence, or an unrelated utterance -- the caller's
    silence-timeout handling covers "no answer" separately from an explicit
    decline). Refuses (raises ``ValueError``) any phrase that normalizes to
    nothing, and refuses if the same normalized phrase appears in both lists
    (an unambiguous vocabulary is the whole point).
    """

    def __init__(
        self,
        continue_phrases: list[str] | None = None,
        decline_phrases: list[str] | None = None,
    ) -> None:
        continue_list = (
            continue_phrases
            if continue_phrases is not None
            else list(DEFAULT_CONTINUE_PHRASES)
        )
        decline_list = (
            decline_phrases
            if decline_phrases is not None
            else list(DEFAULT_DECLINE_PHRASES)
        )

        empty = [p for p in (*continue_list, *decline_list) if not _normalize(p)]
        if empty:
            raise ValueError(
                f"continue/decline phrases contain phrase(s) that normalize "
                f"to nothing and would never match: {empty!r}"
            )

        self._continue = [
            (p, _normalize(p)) for p in continue_list
        ]  # list[tuple[str, str]]
        self._decline = [
            (p, _normalize(p)) for p in decline_list
        ]  # list[tuple[str, str]]

        overlap = {n for _, n in self._continue} & {
            n for _, n in self._decline
        }
        if overlap:
            raise ValueError(
                f"the same phrase(s) appear in both continue_phrases and "
                f"decline_phrases, an ambiguous vocabulary: {overlap!r}"
            )

    def check(self, transcript: str) -> _Outcome | None:
        """"continue", "decline", or None. Word-boundary aware, like the
        other detectors: a phrase is recognized embedded in a sentence but
        not as a substring of a larger word.
        """
        normalized = _normalize(transcript)
        if not normalized:
            return None
        padded = f" {normalized} "
        for _, phrase in self._continue:
            if f" {phrase} " in padded:
                return "continue"
        for _, phrase in self._decline:
            if f" {phrase} " in padded:
                return "decline"
        return None
