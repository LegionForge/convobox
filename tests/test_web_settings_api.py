from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from convobox.web.app import create_app  # noqa: E402
from convobox.web.history import HistoryDB  # noqa: E402

# Required by app.py's require_csrf_header middleware on every mutating
# request (see its own docstring, GitHub issue #235 finding A3) -- set as
# this client's default headers so every test call carries it without
# repeating it at each call site.
_CSRF_HEADERS = {"X-ConvoBox-Client": "1"}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "convobox.yaml"


@pytest.fixture
def client(config_path: Path) -> TestClient:
    app = create_app(db=HistoryDB(Path(":memory:")), config_path=config_path)
    return TestClient(app, headers=_CSRF_HEADERS)


def test_get_settings_defaults_when_no_file_exists(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    values = response.json()["values"]
    assert values["backend"]["name"] == "opencode"
    assert values["tts"]["engine"] == "kokoro"


def test_schema_shows_kokoro_fields_for_kokoro_engine(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    response = client.post("/api/settings/schema", json={"values": values})
    assert response.status_code == 200
    tts_section = next(s for s in response.json()["sections"] if s["key"] == "tts")
    keys = {f["key"] for f in tts_section["fields"]}
    assert "model_path" in keys
    assert "voices_path" in keys
    assert "speaker" not in keys
    assert "volume" not in keys


def test_schema_swaps_to_piper_fields_when_engine_is_piper(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    values["tts"]["engine"] = "piper"
    response = client.post("/api/settings/schema", json={"values": values})
    tts_section = next(s for s in response.json()["sections"] if s["key"] == "tts")
    keys = {f["key"] for f in tts_section["fields"]}
    assert "speaker" in keys
    assert "volume" in keys
    assert "model_path" not in keys


def test_schema_exposes_unset_sentinel_for_device_fields(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    response = client.post("/api/settings/schema", json={"values": values})
    audio_section = next(s for s in response.json()["sections"] if s["key"] == "audio")
    input_device = next(f for f in audio_section["fields"] if f["key"] == "input_device")
    assert input_device["unset_value"] == "(system default)"
    assert input_device["choices"][0] == input_device["unset_value"]


def test_validate_reports_error_for_unsupported_backend(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    values["backend"]["name"] = "not-a-real-backend"
    response = client.post("/api/settings/validate", json={"values": values})
    assert response.status_code == 200
    body = response.json()
    assert body["errors"]
    assert any("not-a-real-backend" in e for e in body["errors"])


def test_save_blocks_on_validation_errors(client: TestClient, config_path: Path) -> None:
    values = client.get("/api/settings").json()["values"]
    values["backend"]["name"] = "not-a-real-backend"
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 422
    assert not config_path.exists()


def test_save_only_writes_changed_fields(client: TestClient, config_path: Path) -> None:
    # Real incident this guards against (docs/UAT-settings-tui.md): a plain
    # model_dump() used to write EVERY field on every save, silently baking
    # in stale defaults. Saving an all-defaults draft should write next to
    # nothing.
    values = client.get("/api/settings").json()["values"]
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 200
    assert config_path.exists()
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved == {}

    values["tts"]["voice"] = "af_bella"
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 200
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved == {"tts": {"voice": "af_bella"}}

    reloaded = client.get("/api/settings").json()["values"]
    assert reloaded["tts"]["voice"] == "af_bella"
    assert reloaded["backend"]["name"] == "opencode"


def test_save_writes_backup_of_prior_config(client: TestClient, config_path: Path) -> None:
    values = client.get("/api/settings").json()["values"]
    values["tts"]["voice"] = "af_bella"
    client.post("/api/settings/save", json={"values": values})

    values["tts"]["voice"] = "af_sarah"
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 200
    backup = response.json()["backup"]
    assert backup is not None
    assert Path(backup).exists()
    assert "af_bella" in Path(backup).read_text(encoding="utf-8")


# --- /api/settings/save's backend.command/web.bind_address escalation
# guard (GitHub issue #235, finding A4): this web route shares the whole
# no-auth, loopback-only web UI trust boundary, but these two fields are
# categorically higher-stakes than the rest (arbitrary-command-execution-
# on-next-start, and self-exposure of an unauthenticated server beyond
# loopback) -- rejected here specifically, still fully editable via the
# settings TUI.


def test_save_rejects_a_changed_backend_command(client: TestClient, config_path: Path) -> None:
    values = client.get("/api/settings").json()["values"]
    values["backend"]["command"] = ["rm", "-rf", "/"]
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 403
    assert "backend.command" in response.json()["detail"]
    assert not config_path.exists()


def test_save_rejects_a_changed_bind_address(client: TestClient, config_path: Path) -> None:
    values = client.get("/api/settings").json()["values"]
    values["web"]["bind_address"] = "0.0.0.0"
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 403
    assert "bind_address" in response.json()["detail"]
    assert not config_path.exists()


def test_save_allows_an_unchanged_backend_command_to_pass_through(
    client: TestClient, config_path: Path
) -> None:
    # The web UI's own save flow round-trips the full current config back
    # with edits merged in -- backend.command is always PRESENT in a
    # normal payload, so only a real attempted CHANGE should be rejected.
    values = client.get("/api/settings").json()["values"]
    values["tts"]["voice"] = "af_bella"  # an unrelated, allowed change
    response = client.post("/api/settings/save", json={"values": values})
    assert response.status_code == 200


def test_test_endpoint_blocks_on_invalid_draft_without_probing(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    values["backend"]["name"] = "not-a-real-backend"
    response = client.post(
        "/api/settings/test", json={"section": "backend", "field": None, "values": values}
    )
    assert response.status_code == 409
    assert "test blocked" in response.json()["detail"]
