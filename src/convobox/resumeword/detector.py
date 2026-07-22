from __future__ import annotations

import re

# The third instance of the Safeword/Confirmword-shaped pattern: plain
# normalized-substring match, no ML, no fuzzy matching, no LLM -- so a
# human reading this file can predict exactly when it fires. See
# docs/DESIGN-barge-in.md, "Resume word = the push-word trigger".
#
# DELIBERATELY DIFFERENT SAFETY TIER FROM ConfirmwordDetector: a resume word
# is the push-word trigger for BARGE-IN (attention/interrupt), not an
# approval gate for a destructive action. A false-fire here means the
# assistant stops talking and listens when the user didn't quite mean it --
# annoying, not dangerous. Per docs/DESIGN-0.3.0-interaction-and-safety.md's
# safety invariant ("never let a PendingPrompt for an approval reuse the
# low-stakes continue/barge-in vocabulary matching" -- and the inverse: don't
# force a low-stakes primitive to carry high-stakes-only restrictions it
# doesn't need), this detector does NOT ban common affirmations the way
# ConfirmwordDetector does. Whether a chosen word is a *good* resume word
# (distinctive, multisyllabic, rarely said by accident, reliably
# transcribed) is setup-time UX guidance -- it needs live audio to actually
# test transcription reliability, which a text-only constructor can't do --
# not a hard construction-time rejection.

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE_RE = re.compile(r"\s+")

# The built-in default. CORRECTED 2026-07-13: the original choice,
# "ConvoBox" (the product's own name -- the smart-speaker convention, "Alexa"/
# "Siri"/"Cortana"), was never actually verified against real speech-to-text
# before shipping and turned out to be a real, live-reproducing bug --
# faster-whisper confidently (0.93) mis-transcribes it as "Control Box"
# every time (a compound/portmanteau word splitting into two real English
# words Whisper's language model prefers), so the resume-word check silently
# never matched and users got stuck unable to resume from a paused state.
# Confirmed via a round-trip TTS-to-STT test through the real pipeline
# (Piper -> faster-whisper), the same discipline this codebase uses
# everywhere else ("verify against the real thing before shipping").
#
# "Athena" was chosen the same way: 5/5 correct transcriptions across varied
# phrasings ("Athena", "hey Athena", "Athena, stop", "okay Athena",
# "Athena?") through the same real pipeline. A single ordinary dictionary
# word, not a portmanteau -- "Copilot"/"co-pilot" and "Voicebox"/"Boyspicks"
# failed the same round-trip test for the same underlying reason as
# "ConvoBox". Public-domain (deliberately not a trademarked AI-character
# name like "Jarvis", which also tested well but carries IP baggage as a
# shipped default). Multisyllabic, essentially never said in ordinary
# coding-agent conversation, unlike "computer" (people say that constantly
# about their own machine while coding -- a real false-fire risk specific
# to this domain, and the classic sci-fi wake-word archetype named in
# docs/ROADMAP.md's Wake word section (that's the FUTURE acoustic spotter
# engine, a different feature from this resume word), ruled out for
# exactly that reason).
#
# Any user-CHOSEN resume word still needs the same verification -- that's the
# setup-wizard "test-transcribe a few times" UX named in
# docs/DESIGN-barge-in.md, not yet built. This constant being wrong for two
# PRs is the concrete argument for building it.
DEFAULT_RESUME_WORD = "Athena"

# Words that FAILED the real Piper -> faster-whisper round-trip test above
# (normalized form). Not a construction-time rejection -- the safety-tier
# note above still holds, and a user's own STT stack may differ -- but
# anything offering resume-word configuration (Settings TUI) should warn
# when one of these is chosen, because on the shipped pipeline they were
# confidently mis-transcribed every time and the resume word never matched.
ROUNDTRIP_REJECTED_RESUME_WORDS = frozenset({
    "convobox",  # -> "Control Box" (0.93)
    "copilot",  # -> "co-pilot"
    "co pilot",
    "voicebox",  # -> "Boyspicks"
})


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


class ResumeWordDetector:
    """Detects a user-chosen resume word/phrase in a transcript.

    The push-word barge-in trigger (docs/DESIGN-barge-in.md): with this
    trigger active, only an utterance containing the resume word counts as an
    attempt to interrupt playback -- everything else is just speech, not a
    barge-in attempt. Constructed with a single wake phrase; refuses (raises
    ``ValueError``) only a phrase that normalizes to nothing, since that
    could never match. Ship ``DEFAULT_RESUME_WORD`` for users who don't want
    to choose one themselves; naming the assistant ("Hey Athena") is welcome
    personalization for users who do.
    """

    def __init__(self, resume_word: str = DEFAULT_RESUME_WORD) -> None:
        normalized = _normalize(resume_word)
        if not normalized:
            raise ValueError(
                f"resume_word {resume_word!r} normalizes to nothing and could never match"
            )
        self._original = resume_word
        self._normalized = normalized

    @property
    def resume_word(self) -> str:
        """The original (un-normalized) resume word/phrase this detector matches."""
        return self._original

    @property
    def normalized_resume_word(self) -> str:
        """The normalized form actually matched -- the form
        ``ROUNDTRIP_REJECTED_RESUME_WORDS`` entries are keyed by."""
        return self._normalized

    def check(self, transcript: str) -> bool:
        """True when the resume word appears in ``transcript``.

        Word-boundary aware (padded match), like the safeword and
        confirmword, so the phrase is recognized embedded in a sentence but
        not as a substring of a larger word.
        """
        normalized = _normalize(transcript)
        if not normalized:
            return False
        return f" {self._normalized} " in f" {normalized} "
