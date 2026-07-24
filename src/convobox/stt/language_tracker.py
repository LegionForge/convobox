from __future__ import annotations

from collections import Counter, deque


class LanguageTracker:
    """Tracks a session's dominant detected language, purely for observability.

    This NEVER feeds back into what language the decoder is asked to
    assume -- STT stays real per-utterance auto-detection regardless of
    what this reports. Forcing a detected/locked language back into
    ``transcribe(language=...)`` is exactly what silently mangled non-
    English speech during live testing (Russian speech decoded as
    English-shaped nonsense once ``--language en`` was pinned). This class
    exists to distinguish, after the fact, "the speaker genuinely switched
    languages" from "the decoder is wandering on one language it can't
    place" -- both look like a language change in the raw output, but only
    the tracker's disagreement signal tells them apart.

    Only detections at or above ``lock_threshold`` count as evidence: a
    low-confidence detection is exactly the kind of guess the wander
    problem produces, so it must not itself get to redefine the session's
    dominant language.
    """

    def __init__(self, lock_threshold: float = 0.6, window: int = 5) -> None:
        self._lock_threshold = lock_threshold
        self._history: deque[str] = deque(maxlen=window)

    def observe(self, language: str, probability: float) -> None:
        if probability >= self._lock_threshold:
            self._history.append(language)

    @property
    def dominant(self) -> str | None:
        """The session's established language, or None before confident detection."""
        if not self._history:
            return None
        return Counter(self._history).most_common(1)[0][0]

    def agrees(self, language: str) -> bool:
        """True if ``language`` matches the dominant language, or none is established yet."""
        dominant = self.dominant
        return dominant is None or dominant == language
