"""Settings-editing REST routes (web UI v2 slice 3).

Full parity with scripts/settings_tui.py's own edit/validate/save/test
contract -- this module reuses that file's SECTION_SPECS, _visible_fields_
for_section, _choices_for, validate_config, save_with_backup, and probe_*
functions directly rather than reimplementing any of them, so the web UI
and the TUI can never silently drift apart on what counts as valid or how
a save is written (exclude_defaults, backup-then-atomic-replace, etc. --
see docs/UAT-settings-tui.md for why that contract matters).

scripts.settings_tui is imported lazily inside each route body, not at
this module's top level: importing it pulls in faster-whisper/kokoro/
sounddevice (real core deps, not optional -- but still needless weight
for routes, tests, or app.py imports that never touch settings at all).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from convobox.config import AppConfig, load_config

# scripts/ is a real package (see pyproject.toml's `convobox-settings`
# entry point and tests/test_settings_tui.py's `from scripts import
# settings_tui`), but only if the repo root is on sys.path -- guaranteed
# under pytest's own rootdir insertion, NOT guaranteed when this module is
# reached via `python scripts/run_convobox.py` (that script inserts its
# own dir and src/, not the repo root). Insert it defensively, same
# reasoning as settings_tui.py's own sys.path inserts at its top.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class SettingsDraft(BaseModel):
    values: dict[str, Any]


class SettingsTestRequest(BaseModel):
    section: str
    field: str | None = None
    values: dict[str, Any]


def _draft_config(values: dict[str, Any]) -> AppConfig:
    try:
        return AppConfig.model_validate(values)
    except Exception as exc:  # noqa: BLE001 -- surfaced to the client as 422
        raise HTTPException(422, f"invalid settings payload: {exc}") from None


def _authorized_draft_config(values: dict[str, Any], config_path: Path) -> AppConfig:
    """Build a draft AppConfig AND enforce the escalation guard in one
    step -- the single entry point every route that can reach a
    dangerous downstream sink (save_with_backup's disk write, or a
    probe_*'s real subprocess spawn / device I/O) must use instead of
    calling `_draft_config` directly.

    Why this exists (2026-09-01): `_detect_web_settings_escalation` was
    added 2026-08-08 for GitHub issue #235's finding A4, but only wired
    into /api/settings/save. /api/settings/test sat right next to it in
    this same file, already reachable with an attacker-controlled
    backend.command, and calling probe_backend() -> real
    asyncio.create_subprocess_exec() with zero save required -- an
    arbitrary-command-execution gap that went unnoticed for almost a
    month (reported via an independently cross-verified security
    review, fixed same day: see git blame on this function). The bug
    wasn't a missing check so much as a missing check that was opt-in
    per route -- easy to add once, easy to forget to add again next to
    it. Centralizing it here removes that failure mode structurally: a
    THIRD dangerous route added later can't reopen this class just by
    someone forgetting to call the guard, because there is no way to
    get a validated config out of this function without it.

    /api/settings/validate and /api/settings/schema deliberately do NOT
    use this -- they never reach a dangerous sink (pure validation /
    field enumeration), so requiring it there would add friction with
    no safety benefit.
    """
    config = _draft_config(values)
    current = load_config(config_path)
    escalation = _detect_web_settings_escalation(current, config)
    if escalation is not None:
        raise HTTPException(403, escalation)
    return config


def _detect_web_settings_escalation(current: AppConfig, draft: AppConfig) -> str | None:
    """Return an error message if `draft` changes a field this route
    must never be allowed to change, else None.

    Found via autonomous codebase review, 2026-08-08 (GitHub issue #235,
    finding A4). Only ever called through `_authorized_draft_config`
    above, not directly -- see that function's own docstring for why
    (2026-09-01: this same check existed but wasn't wired into
    /api/settings/test for almost a month, an arbitrary-command-
    execution gap that didn't even need a save to trigger; centralizing
    the call site is what actually closes that class of mistake, not
    just this one instance of it). Every route reachable through that
    helper shares this whole web UI's no-auth, loopback-only trust
    boundary -- fine for most settings, but two fields here are
    categorically higher-stakes than the rest:
    backend.command is a list passed straight to
    asyncio.create_subprocess_exec (arbitrary-command-execution-on-next-
    start), and web.bind_address controls whether this same unauthenticated
    server is reachable beyond loopback at all (self-escalation to LAN
    exposure). Both stay fully editable via the TUI (scripts/
    settings_tui.py), which requires real local console access -- this
    restriction is specific to the web route, not the underlying config
    model or the save mechanism itself.

    Comparing against the currently-saved config, not just checking
    whether the draft "contains" these fields: the web UI's own save
    flow round-trips the FULL current config back with edits merged in
    (see get_settings()), so the fields are always present in a normal
    payload -- only a real attempted CHANGE should be rejected, not an
    unmodified pass-through.
    """
    if draft.backend.command != current.backend.command:
        return (
            "backend.command cannot be changed via the web UI -- it's "
            "passed directly to a subprocess call, and this server has no "
            "authentication. Use the settings TUI (real local console "
            "access) instead."
        )
    if draft.web.bind_address != current.web.bind_address:
        return (
            "web.bind_address cannot be changed via the web UI -- this "
            "server has no authentication, so changing it could expose "
            "an unauthenticated control surface beyond loopback. Use the "
            "settings TUI (real local console access) instead."
        )
    return None


def _field_to_dict(spec: Any, config: AppConfig, settings_tui: Any) -> dict[str, Any]:
    """`choices` come straight from the TUI's own live enumeration
    (real connected devices, real downloaded voices, ...). `unset_value`/
    `unavailable_value` surface the TUI's own sentinel strings (e.g.
    "(system default)" for a None device) so the frontend can map a
    picked sentinel back to null/disabled instead of hardcoding those
    literal strings itself and silently drifting if settings_tui.py's
    constants ever change.
    """
    d: dict[str, Any] = {
        "section": spec.section,
        "key": spec.key,
        "label": spec.label,
        "kind": spec.kind,
        "help_text": spec.help_text,
        "choices": list(settings_tui._choices_for(spec, config)),
        "unset_value": None,
        "unavailable_value": None,
    }
    if spec.kind == "device":
        d["unset_value"] = settings_tui._SYSTEM_DEFAULT
    elif spec.kind == "piper_speaker":
        d["unset_value"] = settings_tui._PIPER_SPEAKER_DEFAULT
        d["unavailable_value"] = settings_tui._PIPER_SPEAKER_UNAVAILABLE
    elif spec.kind == "kokoro_voice":
        d["unavailable_value"] = settings_tui._KOKORO_VOICE_UNAVAILABLE
    elif spec.kind == "piper_voice":
        d["unavailable_value"] = settings_tui._PIPER_VOICE_UNAVAILABLE
    return d


def add_settings_routes(app: FastAPI, config_path: Path) -> None:
    """Registers /api/settings/* on `app`. Always reads/writes `config_path`
    directly (reloading fresh on every GET) rather than the in-memory config
    a live session started with -- same as reopening the TUI: there is no
    hot-reload (`scripts/run_convobox.py` only reads convobox.yaml at
    startup), so every save here needs a manual restart to take effect,
    exactly like a TUI save does.
    """

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        config = load_config(config_path)
        return {"values": config.model_dump(mode="json")}

    @app.post("/api/settings/schema")
    async def get_settings_schema(draft: SettingsDraft) -> dict[str, Any]:
        from scripts import settings_tui

        config = _draft_config(draft.values)
        sections = []
        for section in settings_tui.SECTION_SPECS:
            fields = settings_tui._visible_fields_for_section(config, section)
            sections.append(
                {
                    "key": section.key,
                    "label": section.label,
                    "restart_required": section.restart_required,
                    "fields": [_field_to_dict(spec, config, settings_tui) for spec in fields],
                }
            )
        return {"sections": sections}

    @app.post("/api/settings/validate")
    async def validate_settings(draft: SettingsDraft) -> dict[str, Any]:
        from scripts import settings_tui

        config = _draft_config(draft.values)
        report = settings_tui.validate_config(config)
        return {"errors": report.errors, "warnings": report.warnings}

    @app.post("/api/settings/save")
    async def save_settings(draft: SettingsDraft) -> dict[str, Any]:
        from scripts import settings_tui

        config = _authorized_draft_config(draft.values, config_path)
        report = settings_tui.validate_config(config)
        if report.errors:
            raise HTTPException(422, {"errors": report.errors, "warnings": report.warnings})
        backup = settings_tui.save_with_backup(config_path, config)
        return {
            "status": "saved",
            "backup": str(backup) if backup else None,
            "warnings": report.warnings,
        }

    @app.post("/api/settings/test")
    async def test_settings(req: SettingsTestRequest) -> dict[str, str]:
        from scripts import settings_tui

        config = _authorized_draft_config(req.values, config_path)
        report = settings_tui.validate_config(config)
        if report.errors:
            raise HTTPException(409, f"test blocked: {report.errors[0]}")
        try:
            if req.section == "tts":
                status = await settings_tui.probe_tts(config)
            elif req.section == "stt":
                status = await settings_tui.probe_stt(config)
            elif req.section == "backend":
                status = await settings_tui.probe_backend(config)
            elif req.section == "audio":
                status = await settings_tui.probe_audio(config, req.field)
            else:
                status = f"{req.section} configuration validated"
        except Exception as exc:
            raise HTTPException(
                500, f"{req.section} test failed: {type(exc).__name__}: {exc}"
            ) from exc
        return {"status": status}
