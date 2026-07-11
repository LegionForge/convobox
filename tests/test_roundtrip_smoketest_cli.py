from __future__ import annotations

from scripts.roundtrip_smoketest import STT_TEST_PHRASES, _phrases_for_voice


def test_known_language_returns_its_own_phrases_no_fallback() -> None:
    lang, phrases, used_fallback = _phrases_for_voice("ru_RU-irina-medium")

    assert lang == "ru"
    assert phrases == STT_TEST_PHRASES["ru"]
    assert used_fallback is False


def test_english_voice_returns_english_phrases() -> None:
    lang, phrases, used_fallback = _phrases_for_voice("en_US-lessac-medium")

    assert lang == "en"
    assert phrases == STT_TEST_PHRASES["en"]
    assert used_fallback is False


def test_unlisted_language_falls_back_to_english_phrases_and_flags_it() -> None:
    lang, phrases, used_fallback = _phrases_for_voice("ar_JO-kareem-medium")

    assert lang == "ar"
    assert phrases == STT_TEST_PHRASES["en"]
    assert used_fallback is True


def test_every_configured_language_has_at_least_one_phrase() -> None:
    for lang, phrases in STT_TEST_PHRASES.items():
        assert phrases, f"{lang!r} has an empty phrase list"
