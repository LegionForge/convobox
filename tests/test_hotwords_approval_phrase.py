"""Warn when the approval phrase sits in stt.hotwords as consecutive words.

Live UAT 2026-11-21 (docs/field-notes/2026-11-21-hotword-list-is-recited-on-
noise.md): on noise Whisper can emit the hotwords list itself as the
transcript. If the approval phrase is in that list in order, a recited list
reads as the approval phrase. This check is the early, cheap defence; it asks
the real ApprovalDetector so normalization and fuzzy matching cannot drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import doctor  # type: ignore[import-not-found]
import settings_tui  # type: ignore[import-not-found]

from convobox.approval import ApprovalDetector
from convobox.config import (
    AppConfig,
    InteractionConfig,
    STTConfig,
    detect_approval_phrase_in_hotwords,
)

PHRASE = "alpha bravo delta"
# The 2026-11-21 UAT list shape: the phrase's three words sit together.
IN_ORDER = "stop brake eject mayday listening resume alpha bravo delta halt abort"
SHUFFLED = "delta stop alpha mayday bravo halt brake abort eject"


def _cfg(hotwords: str | None, phrase: str | None = PHRASE) -> AppConfig:
    return AppConfig(
        stt=STTConfig(hotwords=hotwords),
        interaction=InteractionConfig(approval_phrase=phrase),
    )


def test_warns_when_the_phrase_is_in_the_list_in_order() -> None:
    config = _cfg(IN_ORDER)
    message = detect_approval_phrase_in_hotwords(config.stt, config.interaction)
    assert message is not None
    assert "approval_phrase" in message


def test_quiet_when_the_phrase_words_are_apart_and_out_of_order() -> None:
    config = _cfg(SHUFFLED)
    assert detect_approval_phrase_in_hotwords(config.stt, config.interaction) is None


def test_quiet_when_the_words_are_apart_even_if_in_the_right_relative_order() -> None:
    config = _cfg("alpha stop bravo mayday delta")
    assert detect_approval_phrase_in_hotwords(config.stt, config.interaction) is None


@pytest.mark.parametrize(
    ("hotwords", "phrase"),
    [(None, PHRASE), ("", PHRASE), (IN_ORDER, None)],
)
def test_quiet_when_either_side_is_unset(
    hotwords: str | None, phrase: str | None
) -> None:
    config = _cfg(hotwords, phrase)
    assert detect_approval_phrase_in_hotwords(config.stt, config.interaction) is None


def test_punctuation_and_case_do_not_hide_the_phrase() -> None:
    # Same normalization as the real detector: "Alpha, Bravo; DELTA!" is the phrase.
    config = _cfg("stop, brake. Alpha, Bravo; DELTA! halt")
    assert (
        detect_approval_phrase_in_hotwords(config.stt, config.interaction) is not None
    )


def test_the_check_agrees_with_the_real_detector_on_the_same_text() -> None:
    # The function must not be a second opinion that can drift: whatever it
    # flags, the real detector approves, and the reverse.
    for text in (IN_ORDER, SHUFFLED, "alpha bravo", "bravo delta alpha"):
        config = _cfg(text)
        flagged = (
            detect_approval_phrase_in_hotwords(config.stt, config.interaction)
            is not None
        )
        assert flagged == (ApprovalDetector(PHRASE).check(text) == "approve")


def test_doctor_reports_it_as_a_warning_not_a_failure() -> None:
    findings = doctor.static_config_findings(_cfg(IN_ORDER), [])
    hits = [f for f in findings if f.check == "stt.hotwords"]
    assert len(hits) == 1
    assert hits[0].level == "warn"


def test_doctor_is_quiet_for_a_shuffled_list() -> None:
    assert doctor.static_config_findings(_cfg(SHUFFLED), []) == []


def test_settings_validation_warns_but_does_not_error() -> None:
    report = settings_tui.validate_config(_cfg(IN_ORDER))
    assert any("approval_phrase" in w for w in report.warnings)
    assert not any("approval_phrase" in e for e in report.errors)
