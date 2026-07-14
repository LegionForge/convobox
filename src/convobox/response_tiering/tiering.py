"""Client-side response tiering (docs/DESIGN-0.3.0-interaction-and-safety.md,
Phase 2): "Voice always gives the tiered/short version... ContinueDetector
is the eyes-free escape hatch... v1 is pure client-side truncation (first
paragraph/sentence vs. full text)... no backend round-trip, since the full
response was already received."

Pure logic only -- no TTS, no orchestration, no silence-timing. Deliberately
scoped the same way ContinueDetector was: the primitive first, wiring into
Orchestrator/run_convobox.py's main loop as its own follow-up once this is
reviewed on its own.

Paragraph, not sentence, is the v1 split unit: reliable sentence boundary
detection has to handle abbreviations, decimals, ellipses, and code
fragments correctly, which is genuinely hard; paragraph splitting (blank
line) is simple, robust, and already the boundary
`Orchestrator.strip_code_for_speech` collapses onto. This also degrades
correctly for the common case: a short, single-paragraph response (most
coding-agent replies) has nothing to hide -- tier 0 IS the whole thing, and
there's no "tell me more" to offer.
"""

from __future__ import annotations


def split_tiers(text: str) -> list[str]:
    """Split into paragraph-sized chunks, in order. Never empty for
    non-whitespace input: a response with no blank-line breaks becomes a
    single one-item list (the whole text is tier 0, nothing held back)."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs if paragraphs else ([text.strip()] if text.strip() else [])


class ResponseTierState:
    """Tracks how much of the CURRENT response has been revealed.

    One instance covers one response at a time -- call `start()` when a new
    response arrives (this replaces whatever was being tiered before, same
    as the TUI's full-detail pane resetting per-turn: an old response's
    remaining tiers are moot once a new one exists). `reveal_more()` is the
    `ContinueDetector.check() == "continue"` action; returns None once
    there's nothing left, so the caller can tell "no more to give" apart
    from "gave more."
    """

    def __init__(self) -> None:
        self._tiers: list[str] = []
        self._revealed = 0

    def start(self, full_text: str) -> str:
        """New response. Returns tier 0 (what to actually speak first)."""
        self._tiers = split_tiers(full_text)
        self._revealed = 1 if self._tiers else 0
        return self._tiers[0] if self._tiers else ""

    def has_more(self) -> bool:
        return self._revealed < len(self._tiers)

    def reveal_more(self) -> str | None:
        if not self.has_more():
            return None
        chunk = self._tiers[self._revealed]
        self._revealed += 1
        return chunk
