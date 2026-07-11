from __future__ import annotations

from convobox.stt.language_tracker import LanguageTracker


def test_no_dominant_before_any_observation() -> None:
    tracker = LanguageTracker()
    assert tracker.dominant is None
    assert tracker.agrees("en")  # nothing to disagree with yet


def test_low_confidence_detections_do_not_set_dominant() -> None:
    tracker = LanguageTracker(lock_threshold=0.6)
    tracker.observe("pl", 0.3)
    tracker.observe("ar", 0.2)
    assert tracker.dominant is None


def test_confident_detection_becomes_dominant() -> None:
    tracker = LanguageTracker(lock_threshold=0.6)
    tracker.observe("ru", 0.91)
    assert tracker.dominant == "ru"
    assert tracker.agrees("ru")


def test_low_confidence_wander_does_not_break_agreement_with_dominant() -> None:
    # Reproduces the live-observed pattern: one confident "ru" detection,
    # then several low-confidence wanders into other languages mid-session.
    # The dominant language must stay "ru" throughout -- a low-confidence
    # detection must never be able to redefine it.
    tracker = LanguageTracker(lock_threshold=0.6)
    tracker.observe("ru", 0.91)
    for lang, prob in [("hi", 0.16), ("pl", 0.40), ("pl", 0.24), ("pl", 0.22)]:
        tracker.observe(lang, prob)
        assert tracker.dominant == "ru"
        assert not tracker.agrees(lang)  # each wander should be flaggable
    tracker.observe("ru", 0.90)
    assert tracker.agrees("ru")


def test_genuine_language_switch_updates_dominant() -> None:
    # A real switch (repeated confident detections of a different language)
    # must be able to move the dominant language -- this is not a hard pin.
    tracker = LanguageTracker(lock_threshold=0.6, window=3)
    tracker.observe("en", 0.95)
    tracker.observe("en", 0.90)
    tracker.observe("fr", 0.88)
    tracker.observe("fr", 0.92)
    tracker.observe("fr", 0.85)
    assert tracker.dominant == "fr"
    assert tracker.agrees("fr")
    assert not tracker.agrees("en")


def test_window_bounds_history() -> None:
    tracker = LanguageTracker(lock_threshold=0.5, window=2)
    tracker.observe("en", 0.9)
    tracker.observe("en", 0.9)
    tracker.observe("fr", 0.9)
    tracker.observe("fr", 0.9)
    # Only the last 2 confident observations are retained.
    assert tracker.dominant == "fr"
