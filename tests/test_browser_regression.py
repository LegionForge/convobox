"""Browser regression suite (Astra repo review, 2026-09-07): drives a real
Chromium browser against a real running ConvoBox web server, exercising
``index.html``'s own JS -- approval actions, SSE stream reconnect, artifact
tab switching, and settings persistence. ``test_web_app.py``/``test_web_
settings_api.py``/etc. prove the HTTP API responds correctly at the
`httpx`/ASGI-in-process level; none of them can exercise the browser-side
code that actually consumes those responses (DOM updates, EventSource
reconnect, click handlers) -- these tests are the first coverage of that
layer.

Deliberately NOT named ``test_web_*.py``: that glob is also what CI's
``web-tests`` job (dev+web extras only, no browser) runs -- keeping this
file's name distinct means that job still collects it (harmlessly skips
via the ``playwright`` importorskip below) without conflating "the web
extra's HTTP tests" with "the separate, heavier browser suite".

Needs the `browser` extra (`uv sync --extra browser`) AND a real Chromium
binary (`uv run playwright install chromium`, a separate download step
`uv sync` alone can't do) -- both opt-in, same reasoning as `aec`/`piper`:
this is dev/CI tooling, never imported by the running product.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip(
    "fastapi",
    reason="web UI extra not installed (uv sync --extra web) -- fastapi/uvicorn "
    "are opt-in, not part of dev, so most CLI/TUI-only installs never pull them in",
)
pytest.importorskip(
    "uvicorn",
    reason="web UI extra not installed (uv sync --extra web)",
)
pytest.importorskip(
    "playwright.sync_api",
    reason="browser extra not installed (uv sync --extra browser) -- also needs "
    "`uv run playwright install chromium` to fetch the actual browser binary",
)

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Page, sync_playwright

from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.web.app import create_app
from convobox.web.bridge import WebEventForwarder
from convobox.web.history import HistoryDB
from convobox.web.stream import EventBroadcaster


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class LiveServer:
    """A real uvicorn server on a background thread, bound to a caller-
    chosen port (not an OS-assigned one) -- the reconnect test needs to
    tear one down and rebind a fresh one to the SAME port. `TestClient`/
    `httpx` (every other web test in this repo) never open a real socket
    a browser could navigate to in the first place; this is the one thing
    only a real server can give.

    Runs its own dedicated event loop rather than uvicorn's own
    `Server.run()` (a bare `asyncio.run()` internally) so `push_event`
    below has a stable loop reference to schedule work onto from the
    calling (test) thread -- `EventBroadcaster`'s internals
    (`asyncio.Queue.put_nowait`, `asyncio.create_task`) are only safe to
    call from the loop that owns them, and asyncio gives no way to
    recover that loop from outside once `Server.run()` has already
    swallowed it.
    """

    def __init__(self, app: FastAPI, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self._loop = asyncio.new_event_loop()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            # Default (None) waits indefinitely for in-flight connections
            # to close on their own -- an open SSE stream never does that,
            # so `stop()` would otherwise hang forever instead of actually
            # severing the connection the reconnect test depends on.
            timeout_graceful_shutdown=1,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._server.serve())

    def start(self, timeout_s: float = 5.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout_s
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError(f"uvicorn server on port {self.port} did not start within {timeout_s}s")
            time.sleep(0.02)

    def stop(self, timeout_s: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout_s)

    def push_event(self, forwarder: WebEventForwarder, event: BackendEvent) -> None:
        """Deliver one BackendEvent through `forwarder`, on THIS server's
        own event loop rather than the calling thread -- see this class's
        own docstring for why that's required, not just tidy."""

        async def _call() -> None:
            forwarder(event)

        future = asyncio.run_coroutine_threadsafe(_call(), self._loop)
        future.result(timeout=5)


class SubprocessLiveServer:
    """A real uvicorn server in its own OS process, for the reconnect test
    only.

    Live-verified while writing this suite: an in-thread `LiveServer` (the
    class above) whose task gets force-cancelled on `should_exit` does
    NOT reliably close an already-open client-facing SSE socket -- polled
    a real Chromium's `EventSource` for 10+ seconds after `LiveServer.
    stop()` returned (thread confirmed dead, event loop confirmed closed)
    and its status element never left "connected"/"live". Whatever socket
    cleanup normally happens on an ordinary client-initiated disconnect
    doesn't automatically happen the other way around during a forced
    shutdown of a still-open server-to-client stream. A real OS process,
    killed outright, has no such ambiguity: the kernel closes every file
    descriptor that process held, sockets included, unconditionally.
    """

    def __init__(self, port: int, db_path: str) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        script = (
            "from pathlib import Path\n"
            "from convobox.web.app import create_app\n"
            "from convobox.web.history import HistoryDB\n"
            "import uvicorn\n"
            f"app = create_app(db=HistoryDB(Path({db_path!r})))\n"
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')\n"
        )
        self._proc = subprocess.Popen(  # nosec B603 -- fixed script text built above, no shell, no untrusted input
            [sys.executable, "-c", script]
        )

    def start(self, timeout_s: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"subprocess server on port {self.port} did not accept connections within {timeout_s}s"
                    ) from None
                time.sleep(0.05)

    def kill(self, timeout_s: float = 5.0) -> None:
        self._proc.kill()
        self._proc.wait(timeout=timeout_s)


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        yield pg
        browser.close()


class _FakeApprovalBridge:
    """Same shape as tests/test_web_app.py's own _FakeApprovalBridge --
    kept separate (not imported from there) since that module isn't
    guaranteed importable without the `web` extra alone; this file has
    its own, heavier, extra-gated import chain already."""

    def __init__(self) -> None:
        self.decisions: list[bool] = []

    @property
    def is_pending(self) -> bool:
        return True

    async def decide(self, approved: bool) -> bool:
        self.decisions.append(approved)
        return True

    def extend(self) -> str | None:
        return None


def test_approval_actions_approve_button_calls_the_bridge(page: Page, tmp_path: Path) -> None:
    port = _free_port()
    broadcaster = EventBroadcaster()
    forwarder = WebEventForwarder("live", history=None, broadcaster=broadcaster)
    bridge = _FakeApprovalBridge()
    app = create_app(
        db=HistoryDB(tmp_path / "events.db"),
        broadcaster=broadcaster,
        approval_bridge=bridge,
        web_forwarder=forwarder,
    )
    server = LiveServer(app, port)
    server.start()
    try:
        page.goto(server.url)
        page.wait_for_selector("#status.connected", timeout=5000)

        server.push_event(
            forwarder,
            BackendEvent(type=BackendEventType.APPROVAL_REQUEST, content="rm -rf /tmp/incident-captures"),
        )
        page.wait_for_selector(".approve-btn", timeout=5000)
        page.click(".approve-btn")
        page.wait_for_selector(".approval-note:has-text('Approved.')", timeout=5000)

        assert bridge.decisions == [True]
    finally:
        server.stop()


def test_stream_shows_reconnecting_then_recovers_when_the_server_restarts(
    page: Page, tmp_path: Path
) -> None:
    port = _free_port()
    server = SubprocessLiveServer(port, str(tmp_path / "events.db"))
    server.start()
    try:
        page.goto(server.url)
        page.wait_for_selector("#status.connected", timeout=5000)

        # A real kill, not a simulated one: the OS closes every socket
        # the process held, ending the in-flight SSE response out from
        # under the browser's EventSource the same way a crash would --
        # distinct from EventBroadcaster.close_all()'s own deliberate
        # "session ended" signal (index.html's handleSessionEnded()),
        # which closes the EventSource for good and is NOT what this
        # test is after. See SubprocessLiveServer's own docstring for why
        # this needs a real process, not an in-thread server.
        server.kill()
        page.wait_for_selector("#status.disconnected", timeout=5000)

        # A fresh process on the SAME port -- EventSource's own native
        # retry (it never gave up; only the status text/class changed)
        # should reach it without any page reload or extra JS on this
        # test's part.
        server2 = SubprocessLiveServer(port, str(tmp_path / "events2.db"))
        server2.start()
        try:
            page.wait_for_selector("#status.connected", timeout=15000)
        finally:
            server2.kill()
    finally:
        if server._proc.poll() is None:
            server.kill()


def test_artifact_tabs_switch_between_two_open_artifacts(page: Page, tmp_path: Path) -> None:
    working_dir = tmp_path / "workdir"
    working_dir.mkdir()
    (working_dir / "first.md").write_text("# First artifact\n\nHello from the first file.\n")
    (working_dir / "second.md").write_text("# Second artifact\n\nHello from the second file.\n")

    port = _free_port()
    broadcaster = EventBroadcaster()
    forwarder = WebEventForwarder("live", history=None, broadcaster=broadcaster)
    app = create_app(
        db=HistoryDB(tmp_path / "events.db"),
        broadcaster=broadcaster,
        web_forwarder=forwarder,
        working_dir=working_dir,
    )
    server = LiveServer(app, port)
    server.start()
    try:
        page.goto(server.url)
        page.wait_for_selector("#status.connected", timeout=5000)

        server.push_event(
            forwarder, BackendEvent(type=BackendEventType.ARTIFACT, artifact_path="first.md")
        )
        page.wait_for_selector("#artifact-pane-title:has-text('first.md')", timeout=5000)

        server.push_event(
            forwarder, BackendEvent(type=BackendEventType.ARTIFACT, artifact_path="second.md")
        )
        page.wait_for_selector("#artifact-pane-title:has-text('second.md')", timeout=5000)
        # Two distinct artifacts open -- the tab strip only ever renders
        # once there's more than one (renderArtifactTabs()'s own
        # `artifactTabsEl.hidden = artifactTabs.length < 2`).
        page.wait_for_selector("#artifact-tabs:not([hidden])", timeout=5000)

        # Switch back to the first tab and confirm the pane's title (and
        # therefore its content) actually changed, not just the tab's own
        # active/inactive styling.
        page.click("#artifact-tabs >> text=first.md")
        page.wait_for_selector("#artifact-pane-title:has-text('first.md')", timeout=5000)
    finally:
        server.stop()


def test_settings_change_actually_persists_to_the_config_file(page: Page, tmp_path: Path) -> None:
    config_path = tmp_path / "convobox.yaml"
    port = _free_port()
    server = LiveServer(
        create_app(db=HistoryDB(tmp_path / "events.db"), config_path=config_path), port
    )
    server.start()
    try:
        page.goto(server.url)
        page.wait_for_selector("#status.connected", timeout=5000)
        assert not config_path.exists()

        page.click("#settings-btn")
        # "Display" is the one section a plain browser refresh picks up
        # (scripts/settings_tui.py's own SectionSpec docstring) -- not
        # load-bearing for this test, just the simplest real field to
        # drive: a plain optional_str, not a device/choice picker whose
        # available options depend on this machine's own hardware.
        page.click("#settings-tabs >> text=Display")
        page.wait_for_selector("#field-display-assistant_name", timeout=5000)
        page.fill("#field-display-assistant_name", "Test Assistant Name")
        page.click("#settings-save-btn")
        page.wait_for_selector("#settings-status:has-text('Saved')", timeout=5000)

        assert config_path.exists()
        import yaml

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        assert saved.get("display", {}).get("assistant_name") == "Test Assistant Name"
    finally:
        server.stop()
