"""Is a transcript plausibly a person saying the approval phrase?

Whisper hallucinates on short or noise-like audio, and with ``stt.hotwords``
set it tends to hallucinate the hotwords themselves (live UAT 2026-11-21:
``'stop brake eject mayday listening'``, ``'brake brake brake ...'``;
reproduced offline with synthetic noise, see the PR that added this module).
Many users put the approval phrase in ``hotwords`` on purpose, so a
hallucinated recitation of that list could contain the approval phrase and
approve a tool call out of silence.

This module is used for the APPROVE decision only. It must never be applied to
the safeword / kill-phrase path: a spurious stop is the safe direction, a
missed stop is not, so those checks stay as permissive as they are.

Two content-independent checks, both conservative (they refuse an approve; they
never grant one):

- speaking rate: more words than a person can say in the audio's duration is
  not speech (a 1.5 s clip transcribed as 60 x "brake" is ~40 words/s);
- recitation: a run of consecutive words that appears in the same order in the
  hotwords string is the model repeating its prompt, not the operator
  speaking. Shuffling the hotwords so the approval words are not adjacent keeps
  a genuine three-word phrase far below the run threshold.
"""

from __future__ import annotations

import re

# Fast, deliberate human speech is about 5-6 words/s; 7 leaves headroom so a
# real approval is never refused, while loops and noise-driven word salads
# (tens of words/s) are.
MAX_WORDS_PER_SECOND = 7.0
# Below this many words the rate is too noisy to judge (one clipped word of a
# 0.1 s utterance), and a lone word is not a phrase anyway.
MIN_WORDS_FOR_RATE = 3
# A genuine approval phrase is typically 3 words; recitation needs a longer
# run than that. 4 consecutive hotwords in hotwords order is already not
# something a person says by accident.
MIN_RECITED_RUN = 4

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _recited_run(words: list[str], hotwords: list[str]) -> int:
    """Length of the longest run of consecutive ``words`` that also appears
    consecutively, in the same order, in ``hotwords``."""
    if not words or not hotwords:
        return 0
    best = 0
    for i in range(len(words)):
        for j in range(len(hotwords)):
            k = 0
            while (
                i + k < len(words)
                and j + k < len(hotwords)
                and words[i + k] == hotwords[j + k]
            ):
                k += 1
            best = max(best, k)
    return best


def implausible_approval_reason(
    transcript: str, duration_s: float, hotwords: str | None
) -> str | None:
    """A short human-readable reason this transcript must not approve, or None.

    ``duration_s`` is the length of the audio the transcript came from. A
    missing or non-positive duration disables the rate check only (fail
    towards the recitation check, never towards approving).
    """
    words = _tokens(transcript)
    if (
        duration_s
        and duration_s > 0
        and len(words) >= MIN_WORDS_FOR_RATE
        and len(words) / duration_s > MAX_WORDS_PER_SECOND
    ):
        return (
            f"{len(words)} words in {duration_s:.1f}s is faster than speech "
            f"(likely a hallucination)"
        )
    run = _recited_run(words, _tokens(hotwords or ""))
    if run >= MIN_RECITED_RUN:
        return f"{run} words in a row match the STT hotwords list (likely a recitation)"
    return None
