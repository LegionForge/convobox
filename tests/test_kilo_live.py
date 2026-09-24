"""Live Kilo Code verification -- spawns the REAL `kilo acp` binary, not
tests/fake_acp_server.py. Every other adapter test in this suite (this
file included in spirit, but not in mechanism) runs against a fake
subprocess speaking the real wire protocol so CI can run it for free,
without anyone's credentials -- deliberate project convention, not an
oversight (see fake_acp_server.py/fake_codex_appserver.py/fake_claude_cli.py
and their own module docstrings). A fake is frozen at whatever shape it
was built against, though, so it structurally cannot catch a real Kilo
CLI update changing wire behavior. This file exists for exactly that:
turning this session's own manual, throwaway live-verification probes
(docs/ROADMAP.md, docs/field-notes/2026-09-09-acp-kilo-confirmed-...md)
into a real, repeatable pytest pass, so a future `kilo` upgrade that
breaks compatibility shows up as a test failure here, not a live-session
surprise.

Skipped automatically when `kilo` isn't installed -- CI has no kilo
install, so none of this ever runs there; it's meant to be run manually
on a machine with kilo installed and authenticated, e.g. before tagging
a release that ships ACP/Kilo support, or after upgrading kilo itself.
If kilo IS installed but not authenticated with a working model, these
tests FAIL (not skip) -- a clear, actionable signal (the exact model
default is opt-in via CONVOBOX_LIVE_KILO_MODEL below), not a silent skip
that could hide a real regression.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from convobox.adapters.acp import ACPAdapter
from convobox.adapters.base import BackendEventType

pytestmark = pytest.mark.skipif(
    shutil.which("kilo") is None,
    reason="kilo CLI not installed on this machine -- these tests spawn "
    "the real `kilo acp` process, not a fake, so they need it present "
    "(and authenticated with a working model) to mean anything. Not run "
    "in CI -- no kilo install there.",
)

# inception/mercury-2: the model live-verified working for Kilo on this
# project's own dev machine, 2026-09-08/09 (docs/ROADMAP.md) -- a fresh,
# unconfigured Kilo ACP session defaults to an unauthenticated
# Kilo-Gateway model instead (see test_fresh_session_still_defaults_to_
# an_unusable_model below), so this MUST be set explicitly for every
# other test here to avoid a 30s hang against a broken default. Override
# via env var if your own account authenticates a different provider.
_MODEL = os.environ.get("CONVOBOX_LIVE_KILO_MODEL", "inception/mercury-2")


def _adapter(tmp_path: Path, **kwargs: object) -> ACPAdapter:
    return ACPAdapter(  # type: ignore[arg-type]
        ["kilo", "acp"], backend="kilo", working_dir=str(tmp_path), **kwargs
    )


async def _drain_until_terminal(adapter: ACPAdapter, timeout: float = 40.0) -> list:
    events = []

    async def take() -> None:
        async for event in adapter.events():
            events.append(event)
            if event.type in (BackendEventType.DONE, BackendEventType.ERROR):
                return

    await asyncio.wait_for(take(), timeout=timeout)
    return events


@pytest.mark.asyncio
async def test_send_text_is_nonblocking_against_real_kilo(tmp_path: Path) -> None:
    """Live-verified 2026-09-08/09: send_text() dispatches session/prompt
    as a background task and returns immediately once a session already
    exists -- the safety-critical fix this session shipped (PR #396),
    reconfirmed here against whatever real kilo version is installed,
    not just the fake.
    """
    adapter = _adapter(tmp_path, permission_mode="permissive", model=_MODEL)
    try:
        await adapter._ensure_session()  # pay real spawn/handshake cost, not timed
        t0 = time.monotonic()
        await adapter.send_text("Say exactly: hello from the live kilo test")
        dt = time.monotonic() - t0
        assert dt < 2.0, f"send_text() took {dt:.2f}s against real kilo -- still blocking?"
        assert adapter.is_busy() is True

        events = await _drain_until_terminal(adapter)
        assert any(e.type == BackendEventType.TEXT for e in events)
        assert events[-1].type == BackendEventType.DONE
        assert adapter.is_busy() is False
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_plan_mode_blocks_a_real_write_against_real_kilo(tmp_path: Path) -> None:
    """Live-verified 2026-09-08: Kilo's own plan-mode enforcement works
    via a real rule-engine rejection (the model attempts the tool call,
    then an explicit deny-rule rejects it) -- a different mechanism than
    OpenCode's own model-self-declines-outright behavior, but the same
    practical outcome. Confirms that outcome still holds against whatever
    kilo version is currently installed.
    """
    adapter = _adapter(tmp_path, permission_mode="plan", model=_MODEL)
    target = tmp_path / "kilo_live_plan_test.txt"
    try:
        await adapter.send_text(
            f"Create a file named {target.name} in the current directory "
            "with the text hello."
        )
        events = await _drain_until_terminal(adapter)
        assert events[-1].type in (BackendEventType.DONE, BackendEventType.ERROR)
        assert not target.exists(), (
            "plan mode should have blocked this write against real kilo, "
            "but the file was created anyway"
        )
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_permissive_mode_allows_a_real_write_against_real_kilo(tmp_path: Path) -> None:
    """No-regression check paired with the plan-mode test above: full
    trust (the session's own default) must still let a real write
    through end to end.
    """
    adapter = _adapter(tmp_path, permission_mode="permissive", model=_MODEL)
    target = tmp_path / "kilo_live_permissive_test.txt"
    try:
        await adapter.send_text(
            f"Create a NEW file named {target.name} in the current "
            "directory containing the text hello. Do not read it first, "
            "it does not exist yet, just write it directly."
        )
        events = await _drain_until_terminal(adapter)
        assert events[-1].type == BackendEventType.DONE
        assert target.exists(), (
            "permissive mode should have let this write through against "
            "real kilo, but no file was created"
        )
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_real_tool_failure_yields_friendly_text_against_real_kilo(
    tmp_path: Path,
) -> None:
    """Live-verified 2026-09-08: a real tool failure (read against a
    nonexistent path) must surface the human-readable content text
    ("File not found: ...") rather than the raw rawOutput JSON blob --
    the bug this session found and fixed (PR #396) while triggering this
    exact scenario for the first time.
    """
    adapter = _adapter(tmp_path, permission_mode="permissive", model=_MODEL)
    try:
        await adapter.send_text(
            "Read the contents of a file named "
            "kilo_live_does_not_exist_xyz.txt in the current directory "
            "using your read tool."
        )
        events = await _drain_until_terminal(adapter)
        errors = [e for e in events if e.type == BackendEventType.ERROR]
        assert errors, "expected a real tool failure to surface as an ERROR event"
        assert "not found" in (errors[0].content or "").lower()
        assert not (errors[0].content or "").strip().startswith("{"), (
            "ERROR content looks like a raw JSON blob, not the friendly "
            "text extraction this session's fix was for"
        )
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_fresh_session_still_defaults_to_an_unusable_model(tmp_path: Path) -> None:
    """Live-verified 2026-09-08/09: a fresh, UNCONFIGURED Kilo ACP session
    (no backend.model set) still defaults to an unauthenticated
    Kilo-Gateway model on this account -- confirming the model-selection
    bug this session's fix addresses (PR #396) is a live, current issue,
    not stale. Deliberately does NOT send a prompt against this broken
    default (that would hang for the full 30s response timeout) -- just
    inspects session/new's own configOptions directly, matching how this
    was originally live-verified. Does the handshake manually (not via
    _ensure_session(), which would call session/new a SECOND time here
    and also try to set_mode for the default permission_mode="plan") --
    this test only wants the one raw session/new response.
    """
    adapter = _adapter(tmp_path)  # no model= -- the whole point of this test
    try:
        async with adapter._lock:
            adapter._proc = await asyncio.create_subprocess_exec(
                *adapter._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            adapter._reader_task = asyncio.create_task(adapter._read_loop(adapter._proc))
        await adapter._request(
            "initialize", {"protocolVersion": 1, "clientCapabilities": {}}
        )
        result = await adapter._request(
            "session/new", {"cwd": str(tmp_path), "mcpServers": []}
        )
        model_option = next(
            (o for o in result.get("configOptions", []) if o.get("id") == "model"),
            None,
        )
        assert model_option is not None, "expected a model configOption on session/new"
        default_model = model_option.get("currentValue", "")
        assert default_model != _MODEL, (
            f"fresh session's own default model ({default_model!r}) now "
            f"matches the known-working {_MODEL!r} -- if kilo's own "
            "default changed to something usable, this test (and the "
            "model-pinning workaround it documents) may no longer be "
            "needed; a human should re-check before treating this as a "
            "regression"
        )
    finally:
        await adapter.aclose()
