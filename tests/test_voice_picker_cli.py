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


# --- numbered-reference resolution and command suggestions ---

from scripts.voice_picker import resolve_key, suggest_command  # noqa: E402


def test_resolve_key_number_picks_from_last_list() -> None:
    key, error = resolve_key("2", ["a-voice", "b-voice", "c-voice"])
    assert key == "b-voice"
    assert error is None


def test_resolve_key_number_without_a_list_explains() -> None:
    key, error = resolve_key("2", [])
    assert key is None
    assert error is not None and "search" in error


def test_resolve_key_number_out_of_range_explains_bounds() -> None:
    key, error = resolve_key("9", ["a-voice", "b-voice"])
    assert key is None
    assert error is not None and "between 1 and 2" in error


def test_resolve_key_passes_literal_keys_through() -> None:
    key, error = resolve_key("en_GB-alba-medium", [])
    assert key == "en_GB-alba-medium"
    assert error is None


def test_suggest_command_catches_near_misses() -> None:
    assert suggest_command("serach") == "search"
    assert suggest_command("paly") == "play"
    assert suggest_command("qiut") == "quit"


def test_suggest_command_none_for_gibberish() -> None:
    assert suggest_command("xyzzy123") is None


# --- voice deletion ---

from scripts.voice_picker import delete_voice  # noqa: E402


def test_delete_voice_removes_onnx_and_json(tmp_path: Path) -> None:
    (tmp_path / "aa_BB-test-low.onnx").write_bytes(b"model")
    (tmp_path / "aa_BB-test-low.onnx.json").write_text("{}")
    removed = delete_voice("aa_BB-test-low", tmp_path)
    assert len(removed) == 2
    assert not any(tmp_path.glob("aa_BB-test-low*"))


def test_delete_voice_tolerates_missing_json(tmp_path: Path) -> None:
    (tmp_path / "aa_BB-test-low.onnx").write_bytes(b"model")
    removed = delete_voice("aa_BB-test-low", tmp_path)
    assert len(removed) == 1


def test_delete_voice_never_touches_other_files(tmp_path: Path) -> None:
    (tmp_path / "aa_BB-test-low.onnx").write_bytes(b"model")
    (tmp_path / "voices.json").write_text("{}")          # catalog cache
    (tmp_path / "zz_ZZ-other-low.onnx").write_bytes(b"model")
    delete_voice("aa_BB-test-low", tmp_path)
    assert (tmp_path / "voices.json").exists()
    assert (tmp_path / "zz_ZZ-other-low.onnx").exists()
