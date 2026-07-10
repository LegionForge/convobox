from __future__ import annotations

from pathlib import Path

from scripts.voice_picker import Catalog, describe, installed_voices, search_catalog

_CATALOG: Catalog = {
    "en_US-lessac-medium": {
        "language": {"name_english": "English", "code": "en_US", "country_english": "United States"},
        "quality": "medium",
        "num_speakers": 1,
    },
    "fr_FR-siwis-medium": {
        "language": {"name_english": "French", "code": "fr_FR", "country_english": "France"},
        "quality": "medium",
        "num_speakers": 1,
    },
    "fr_FR-mls-medium": {
        "language": {"name_english": "French", "code": "fr_FR", "country_english": "France"},
        "quality": "medium",
        "num_speakers": 125,
    },
    "ru_RU-irina-medium": {
        "language": {"name_english": "Russian", "code": "ru_RU", "country_english": "Russia"},
        "quality": "medium",
        "num_speakers": 1,
    },
}


def test_search_matches_by_voice_key_substring() -> None:
    assert search_catalog(_CATALOG, "siwis") == ["fr_FR-siwis-medium"]


def test_search_matches_by_language_name_case_insensitive() -> None:
    assert search_catalog(_CATALOG, "FRENCH") == ["fr_FR-mls-medium", "fr_FR-siwis-medium"]


def test_search_matches_by_language_code() -> None:
    assert search_catalog(_CATALOG, "ru_ru") == ["ru_RU-irina-medium"]


def test_search_empty_query_matches_everything_sorted() -> None:
    assert search_catalog(_CATALOG, "") == sorted(_CATALOG)


def test_search_no_match_returns_empty_list() -> None:
    assert search_catalog(_CATALOG, "klingon") == []


def test_describe_includes_language_country_and_quality() -> None:
    text = describe(_CATALOG, "en_US-lessac-medium")
    assert "English" in text
    assert "United States" in text
    assert "medium" in text


def test_describe_notes_multi_speaker_voices() -> None:
    assert "125 speakers" in describe(_CATALOG, "fr_FR-mls-medium")


def test_describe_omits_speaker_note_for_single_speaker_voices() -> None:
    assert "speakers" not in describe(_CATALOG, "en_US-lessac-medium")


def test_describe_unknown_key_falls_back_to_the_key_itself() -> None:
    assert describe(_CATALOG, "xx_XX-nobody-low") == "xx_XX-nobody-low"


def test_installed_voices_lists_onnx_stems_sorted(tmp_path: Path) -> None:
    (tmp_path / "fr_FR-siwis-medium.onnx").write_bytes(b"")
    (tmp_path / "fr_FR-siwis-medium.onnx.json").write_bytes(b"")
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"")

    assert installed_voices(tmp_path) == ["en_US-lessac-medium", "fr_FR-siwis-medium"]


def test_installed_voices_on_nonexistent_dir_is_empty(tmp_path: Path) -> None:
    assert installed_voices(tmp_path / "does-not-exist") == []
