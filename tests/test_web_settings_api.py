from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)

import yaml
from fastapi.testclient import TestClient

from convobox.web.app import create_app
from convobox.web.history import HistoryDB

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


# --- restart_required (2026-08-07, JP asked directly): display is the
# ONLY section not consumed by the mic-loop pipeline run_convobox.py
# builds once at startup -- see SectionSpec.restart_required's own
# docstring in scripts/settings_tui.py for how that was confirmed. ---


def test_schema_marks_display_section_as_not_requiring_restart(
    client: TestClient,
) -> None:
    values = client.get("/api/settings").json()["values"]
    response = client.post("/api/settings/schema", json={"values": values})
    sections = {s["key"]: s["restart_required"] for s in response.json()["sections"]}
    assert sections["display"] is False


def test_schema_marks_every_other_section_as_requiring_restart(
    client: TestClient,
) -> None:
    values = client.get("/api/settings").json()["values"]
    response = client.post("/api/settings/schema", json={"values": values})
    sections = {s["key"]: s["restart_required"] for s in response.json()["sections"]}
    del sections["display"]
    assert sections, "expected at least one non-display section to check"
    assert all(sections.values()), sections


def test_schema_exposes_unset_sentinel_for_device_fields(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    response = client.post("/api/settings/schema", json={"values": values})
    audio_section = next(s for s in response.json()["sections"] if s["key"] == "audio")
    input_device = next(f for f in audio_section["fields"] if f["key"] == "input_device")
    assert input_device["unset_value"] == "(system default)"
    assert input_device["choices"][0] == input_device["unset_value"]


# --- backend.warn_if_working_dir_not_git (2026-09-04): only meaningful
# alongside working_dir, which opencode doesn't use -- must show for
# codex/claude-code, stay hidden for opencode. ---


def test_schema_exposes_the_git_warning_toggle_for_codex(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    values["backend"]["name"] = "codex"
    response = client.post("/api/settings/schema", json={"values": values})
    backend_section = next(s for s in response.json()["sections"] if s["key"] == "backend")
    keys = {f["key"] for f in backend_section["fields"]}
    assert "warn_if_working_dir_not_git" in keys


def test_schema_hides_the_git_warning_toggle_for_opencode(client: TestClient) -> None:
    values = client.get("/api/settings").json()["values"]
    values["backend"]["name"] = "opencode"
    response = client.post("/api/settings/schema", json={"values": values})
    backend_section = next(s for s in response.json()["sections"] if s["key"] == "backend")
    keys = {f["key"] for f in backend_section["fields"]}
    assert "warn_if_working_dir_not_git" not in keys


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


# --- /api/settings/test's own backend.command/web.bind_address escalation
# guard (2026-09-01, same incident class as GitHub issue #235's finding
# A4 above -- reported via an independently-verified cross-session
# security review): unlike /api/settings/save, this route used to call
# probe_backend()/etc. with the RAW draft config and no escalation check
# at all, so a changed backend.command reached asyncio.create_subprocess_exec
# with zero save required -- an arbitrary-command-execution gap, not just
# a config-persistence one. Fixed by applying the same
# _detect_web_settings_escalation() guard here too, before any probe
# runs. probe_backend is monkeypatched to a spy that fails the test if
# ever called, so these tests fail loudly if the guard is ever removed
# and the probe becomes reachable again, not just if the status code
# regresses.


def test_test_endpoint_rejects_a_changed_backend_command_without_probing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    async def probe_backend_must_not_be_called(config: object) -> str:
        raise AssertionError("probe_backend() must not run when the escalation guard fires")

    monkeypatch.setattr(settings_tui, "probe_backend", probe_backend_must_not_be_called)
    values = client.get("/api/settings").json()["values"]
    values["backend"]["command"] = ["rm", "-rf", "/"]
    response = client.post(
        "/api/settings/test", json={"section": "backend", "field": None, "values": values}
    )
    assert response.status_code == 403
    assert "backend.command" in response.json()["detail"]


def test_test_endpoint_rejects_a_changed_bind_address_without_probing(
    client: TestClient,
) -> None:
    # web section has no probe_* dispatch of its own (falls through to the
    # generic "configuration validated" message) -- the guard must still
    # fire before that fallback, not just before a real probe call.
    values = client.get("/api/settings").json()["values"]
    values["web"]["bind_address"] = "0.0.0.0"
    response = client.post(
        "/api/settings/test", json={"section": "web", "field": None, "values": values}
    )
    assert response.status_code == 403
    assert "bind_address" in response.json()["detail"]


def test_test_endpoint_allows_an_unchanged_backend_command_to_probe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    async def fake_probe_backend(config: object) -> str:
        return "backend probe ok"

    monkeypatch.setattr(settings_tui, "probe_backend", fake_probe_backend)
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test", json={"section": "backend", "field": None, "values": values}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "backend probe ok"}


# --- /api/settings/test's actual probe dispatch (tts/stt/backend/audio ->
# the matching scripts.settings_tui.probe_* function, else a generic
# "validated" message) and its exception-to-500 handling -- previously
# untested (only the pre-probe validation-blocks path above was covered),
# found via a real coverage-gap sweep. probe_* functions do real
# hardware/network work (device probing, backend subprocess spawn, etc.),
# so these monkeypatch them rather than exercising the real thing -- same
# reasoning tests/test_settings_tui.py already uses for its own
# hardware-touching helpers. ---


def test_test_endpoint_dispatches_tts_section_to_probe_tts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    async def fake_probe_tts(config: object) -> str:
        return "tts probe ok"

    monkeypatch.setattr(settings_tui, "probe_tts", fake_probe_tts)
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test", json={"section": "tts", "field": None, "values": values}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "tts probe ok"}


def test_test_endpoint_dispatches_stt_section_to_probe_stt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    async def fake_probe_stt(config: object) -> str:
        return "stt probe ok"

    monkeypatch.setattr(settings_tui, "probe_stt", fake_probe_stt)
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test", json={"section": "stt", "field": None, "values": values}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "stt probe ok"}


def test_test_endpoint_dispatches_backend_section_to_probe_backend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    async def fake_probe_backend(config: object) -> str:
        return "backend probe ok"

    monkeypatch.setattr(settings_tui, "probe_backend", fake_probe_backend)
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test", json={"section": "backend", "field": None, "values": values}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "backend probe ok"}


def test_test_endpoint_dispatches_audio_section_to_probe_audio_with_field(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    seen: dict[str, object] = {}

    async def fake_probe_audio(config: object, field_key: str | None = None) -> str:
        seen["field_key"] = field_key
        return "audio probe ok"

    monkeypatch.setattr(settings_tui, "probe_audio", fake_probe_audio)
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test",
        json={"section": "audio", "field": "output_device", "values": values},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "audio probe ok"}
    # Confirms the route actually forwards req.field through, not just
    # req.section -- a route that silently dropped it would still pass
    # every assertion above.
    assert seen["field_key"] == "output_device"


def test_test_endpoint_falls_back_to_generic_message_for_an_unprobed_section(
    client: TestClient,
) -> None:
    # "display" (and any other section without its own probe_*) has no
    # hardware/network check -- the route's own else-branch, not an error.
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test", json={"section": "display", "field": None, "values": values}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "display configuration validated"}


def test_test_endpoint_returns_500_when_a_probe_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import settings_tui

    async def failing_probe_tts(config: object) -> str:
        raise RuntimeError("no audio device found")

    monkeypatch.setattr(settings_tui, "probe_tts", failing_probe_tts)
    values = client.get("/api/settings").json()["values"]
    response = client.post(
        "/api/settings/test", json={"section": "tts", "field": None, "values": values}
    )
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "tts test failed" in detail
    assert "RuntimeError" in detail
    assert "no audio device found" in detail
