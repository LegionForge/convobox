from __future__ import annotations

import re
from typing import Literal

from convobox.confirmword.detector import ConfirmwordDetector

# The sixth instance of the Safeword/Confirmword/Wakeword/PauseListening/
# ContinueDetector-shaped pattern: plain normalized-substring match, no ML,
# no fuzzy matching, no LLM. See docs/DESIGN-0.3.0-interaction-and-safety.md's
# Phase 3 (approvals) and its shared-primitive framing (PendingPrompt).
#
# TWO SAFETY TIERS IN ONE DETECTOR, DELIBERATELY NOT UNIFIED:
# - Approve reuses ConfirmwordDetector's existing, strict construction-time
#   guard (a user-chosen phrase, common-affirmation-only phrases rejected at
#   construction) -- misreading approval must never be possible via casual
#   speech. See ConfirmwordDetector's own docstring for why.
# - Deny is deliberately the OPPOSITE tier: a bare "no" is not just allowed,
#   it's the expected common case, because misfiring towards deny is the
#   SAFE direction (worst case: an approval that should have gone through
#   gets asked again). Never let this asymmetry invert -- see the design
#   doc's explicit invariant: "silence on an approval prompt must never be
#   treated as consent."
# - Anything that is neither the approval phrase nor a deny phrase is
#   "discuss" (the user is asking a question about the pending action, not
#   deciding yet) -- NOT "neither"/None the way ContinueDetector treats an
#   unmatched utterance as "pass through to normal routing." An approval
#   prompt must stay open across a discuss exchange (confirmed live against
#   a real codex app-server, see the design doc's Phase 3 section), so
#   unmatched speech here is a positive branch, not a fall-through.
#
# Deny vocabulary round-trip verified (Piper -> faster-whisper, the same
# methodology established fixing DEFAULT_WAKE_WORD and response tiering's
# continue/decline phrases) before being trusted as a default: "no", "deny",
# "decline", "cancel", "don't", "no don't", "not now", "hold off", "reject"
# all round-tripped correctly. DEFAULT_DENY_PHRASES below is a subset of
# that verified set, sized to match DEFAULT_CONTINUE_PHRASES/
# DEFAULT_DECLINE_PHRASES.

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_DENY_PHRASES: tuple[str, ...] = (
    "no",
    "deny",
    "decline",
    "cancel",
    "not now",
)

_Outcome = Literal["approve", "deny", "discuss"]


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class ApprovalDetector:
    """Classifies a transcript as "approve" / "deny" / "discuss", for the
    Phase 3 approval prompt (docs/DESIGN-0.3.0-interaction-and-safety.md):
    ConvoBox has surfaced a pending destructive-action approval request and
    is waiting for the user's voice decision.

    Constructed with a `ConfirmwordDetector`-shaped approval phrase (required
    -- there is no safe default, the whole point is a phrase the operator
    chose deliberately) and an optional deny-phrase list (defaults to
    ``DEFAULT_DENY_PHRASES``). ``check()`` never returns ``None``: any
    non-empty transcript that isn't the approval phrase or a deny phrase is
    "discuss", since a pending approval must stay answerable across a
    clarifying exchange rather than being dropped or routed elsewhere. An
    empty/whitespace-only transcript returns ``None`` (no signal at all --
    the caller's own silence-timeout handling covers "no answer").
    """

    def __init__(
        self,
        approval_phrase: str,
        deny_phrases: list[str] | None = None,
    ) -> None:
        self._confirm = ConfirmwordDetector(approval_phrase)

        deny_list = deny_phrases if deny_phrases is not None else list(DEFAULT_DENY_PHRASES)
        empty = [p for p in deny_list if not _normalize(p)]
        if empty:
            raise ValueError(
                f"deny_phrases contain phrase(s) that normalize to nothing "
                f"and would never match: {empty!r}"
            )
        self._deny: list[tuple[str, str]] = [(p, _normalize(p)) for p in deny_list]

        approval_normalized = _normalize(approval_phrase)
        overlap = {n for _, n in self._deny if n == approval_normalized}
        if overlap:
            raise ValueError(
                f"approval_phrase {approval_phrase!r} also appears in "
                f"deny_phrases, an ambiguous vocabulary: {overlap!r}"
            )

    @property
    def approval_phrase(self) -> str:
        """The original (un-normalized) approval phrase this detector matches."""
        return self._confirm.approval_phrase

    def check(self, transcript: str) -> _Outcome | None:
        """"approve", "deny", "discuss", or None for an empty transcript.

        Word-boundary aware for approve/deny, like the other detectors: a
        phrase is recognized embedded in a sentence but not as a substring
        of a larger word. Anything else non-empty is "discuss".
        """
        normalized = _normalize(transcript)
        if not normalized:
            return None
        if self._confirm.check(transcript):
            return "approve"
        padded = f" {normalized} "
        for _, phrase in self._deny:
            if f" {phrase} " in padded:
                return "deny"
        return "discuss"
