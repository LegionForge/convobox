from __future__ import annotations

from pathlib import Path

from scripts.check_venv_extras import dist_info_present, find_broken_distributions


def _make_dist_info(site_packages: Path, dirname: str, files: list[str]) -> Path:
    dist_info = site_packages / dirname
    dist_info.mkdir(parents=True)
    for name in files:
        (dist_info / name).write_text("x")
    return dist_info


def test_intact_dist_info_is_not_flagged(tmp_path: Path) -> None:
    _make_dist_info(tmp_path, "widget-1.0.dist-info", ["RECORD", "METADATA", "WHEEL"])
    assert find_broken_distributions(tmp_path) == []


def test_missing_record_and_metadata_is_flagged(tmp_path: Path) -> None:
    # The exact shape of the 2026-09-06 aec-audio-processing incident:
    # only a licenses/ subfolder survived, RECORD/METADATA/WHEEL gone.
    dist_info = tmp_path / "aec_audio_processing-1.0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "licenses").mkdir()

    broken = find_broken_distributions(tmp_path)

    assert len(broken) == 1
    assert "aec_audio_processing-1.0.1.dist-info" in broken[0]
    assert "RECORD" in broken[0]
    assert "METADATA" in broken[0]


def test_missing_only_one_file_is_still_flagged(tmp_path: Path) -> None:
    _make_dist_info(tmp_path, "widget-1.0.dist-info", ["METADATA", "WHEEL"])
    broken = find_broken_distributions(tmp_path)
    assert broken == ["widget-1.0.dist-info: missing RECORD"]


def test_non_dist_info_entries_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget-1.0.dist-info.bak").write_text("not a directory's dist-info")
    assert find_broken_distributions(tmp_path) == []


def test_dist_info_present_matches_by_underscored_prefix(tmp_path: Path) -> None:
    _make_dist_info(tmp_path, "aec_audio_processing-1.0.1.dist-info", ["RECORD", "METADATA"])
    assert dist_info_present([tmp_path], "aec_audio_processing") is True
    assert dist_info_present([tmp_path], "some_other_package") is False
