"""A ConvoBox web session safe for a phone to talk to -- no real agent, no mic.

Purpose: hand a mobile developer (docs/HANDOFF-android-spike.md) a server
whose HTTP API is the REAL one (web/app.py's create_app(), its real auth +
CSRF middleware, the real bridges, SSE broadcaster and history DB) but whose
backend is tests/fake_claude_cli.py in a throwaway temp working directory.
Nothing here can touch your code, and typed text only ever reaches the fake.

Why this is NOT a thin wrapper over scripts/run_convobox.py: that script's
only persistent mode is the mic loop (PortAudio + Whisper + TTS), and its
--text mode exits after one utterance. Neither suits "keep a server up so a
phone can poke it". The composition below mirrors run_convobox.py's web
setup (same create_app() arguments, same token generation, same printed
URL shape) and swaps the Orchestrator/ApprovalPromptGate for two tiny
stand-ins (_FakeOrchestrator/_FakeGate).

Triggers, typed from the phone via POST /api/text:
  "approval test"  -> raises a fake pending approval (SSE `approval_request`
                      event); resolve it with POST .../approval. Nothing is
                      executed either way.
  anything else    -> goes to the fake claude CLI (see tests/fake_claude_cli.py
                      for its trigger words, e.g. "use a tool", "hang").
--approval-every N additionally raises a fake approval every N seconds
while none is pending (handy for the lock-screen experiments).

Usage:
  python scripts/run_mobile_test_server.py                 # loopback only
  python scripts/run_mobile_test_server.py --bind-address 0.0.0.0 --port 5173
Prefer binding loopback and fronting it with `tailscale serve`; see the
hand-off doc. The history DB is in-memory, the config path points at a
non-existent temp file (so /api/settings/save can only write inside the
temp dir), and everything is deleted on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.adapters.claude_code import ClaudeCodeAdapter
from convobox.config import WebConfig

log = logging.getLogger("mobile_test_server")

FAKE_CLI = _REPO_ROOT / "tests" / "fake_claude_cli.py"
APPROVAL_TRIGGER = "approval test"


class _FakeGate:
    """Stand-in for run_convobox.py's ApprovalPromptGate (ApprovalGateLike)."""

    def __init__(self) -> None:
        self.is_waiting = False
        self.pending_explanation: str | None = None

    def start_waiting(self, now: float, explanation: str | None = None) -> None:
        del now
        self.is_waiting = True
        self.pending_explanation = explanation

    def cancel_wait(self) -> None:
        self.is_waiting = False
        self.pending_explanation = None


class _FakeOrchestrator:
    """The three Orchestrator methods the web bridges call, over the fake
    backend. Approvals are bookkeeping only: nothing is ever executed."""

    def __init__(self, adapter: ClaudeCodeAdapter, gate: _FakeGate, forwarder) -> None:  # type: ignore[no-untyped-def]
        self._adapter = adapter
        self._gate = gate
        self._forwarder = forwarder

    def raise_fake_approval(self) -> None:
        if self._gate.is_waiting:
            return
        self._gate.start_waiting(
            time.monotonic(), "Fake approval raised by run_mobile_test_server.py; nothing will run."
        )
        self._forwarder(BackendEvent(
            BackendEventType.APPROVAL_REQUEST,
            content="Run shell command: rm -rf /tmp/convobox-mobile-test (FAKE, never executed)",
            tool="Bash",
            tool_input="rm -rf /tmp/convobox-mobile-test",
        ))

    async def handle_transcript(self, text: str) -> None:
        if APPROVAL_TRIGGER in text.lower():
            self.raise_fake_approval()
            return
        self._forwarder.forward_status("working", None)
        await self._adapter.send_text(text)

    async def resolve_pending_approval(self, approved: bool) -> bool:
        del approved
        return self._gate.is_waiting

    async def hard_stop(self) -> bool:
        await self._adapter.send_hard_stop()
        return True


async def _pump_backend_events(adapter: ClaudeCodeAdapter, forwarder) -> None:  # type: ignore[no-untyped-def]
    async for event in adapter.events():
        forwarder(event)
        if event.type in (BackendEventType.DONE, BackendEventType.ERROR):
            forwarder.forward_status("listening", None)


async def _approval_ticker(orch: _FakeOrchestrator, every_s: float) -> None:
    while True:
        await asyncio.sleep(every_s)
        orch.raise_fake_approval()


async def run(args: argparse.Namespace) -> None:
    import uvicorn

    from convobox.web.app import create_app
    from convobox.web.bridge import (
        WebApprovalBridge,
        WebEventForwarder,
        WebListeningBridge,
        WebSafewordBridge,
        WebTextInputBridge,
    )
    from convobox.web.history import HistoryDB
    from convobox.web.stream import EventBroadcaster

    # Reuse WebConfig's own validator for the bind address (loopback or an
    # explicit 0.0.0.0 only) instead of re-implementing the rule.
    web_cfg = WebConfig(enabled=True, bind_address=args.bind_address, port=args.port)

    with tempfile.TemporaryDirectory(prefix="convobox-mobile-test-") as tmp:
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        adapter = ClaudeCodeAdapter(
            command=[sys.executable, str(FAKE_CLI)],
            permission_mode="plan",
            working_dir=str(workdir),
        )
        broadcaster = EventBroadcaster()
        history = HistoryDB(Path(":memory:"))
        forwarder = WebEventForwarder("mobile-test", history=history, broadcaster=broadcaster)
        gate = _FakeGate()
        orch = _FakeOrchestrator(adapter, gate, forwarder)

        approval_bridge = WebApprovalBridge()
        approval_bridge.set_targets(orch, gate)  # type: ignore[arg-type]
        text_bridge = WebTextInputBridge()
        text_bridge.set_targets(orch, None, forwarder)  # type: ignore[arg-type]
        safeword_bridge = WebSafewordBridge()
        safeword_bridge.set_targets(orch)  # type: ignore[arg-type]

        # Same token scheme as run_convobox.py's --web path.
        web_ui_token = secrets.token_hex(16)
        artifact_token = secrets.token_hex(16)
        loop_task = asyncio.current_task()
        app = create_app(
            db=history,
            broadcaster=broadcaster,
            approval_bridge=approval_bridge,
            listening_bridge=WebListeningBridge(),  # never wired: POST /api/listening -> 503
            safeword_bridge=safeword_bridge,
            text_bridge=text_bridge,
            quit_handler=lambda: loop_task.cancel() if loop_task else None,
            config_path=Path(tmp) / "convobox.yaml",  # deliberately absent -> defaults
            working_dir=workdir,
            mcp_token=None,
            web_forwarder=forwarder,
            web_ui_token=web_ui_token,
            artifact_token=artifact_token,
            port=web_cfg.port,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host=web_cfg.bind_address, port=web_cfg.port, log_level="warning")
        )
        pump = asyncio.ensure_future(_pump_backend_events(adapter, forwarder))
        ticker = (
            asyncio.ensure_future(_approval_ticker(orch, args.approval_every))
            if args.approval_every
            else None
        )
        serve = asyncio.ensure_future(server.serve())
        host = "127.0.0.1" if web_cfg.bind_address == "0.0.0.0" else web_cfg.bind_address  # nosec B104
        # Printed (not logged) for the same reason run_convobox.py does.
        print(f"web UI: open http://{host}:{web_cfg.port}/?token={web_ui_token}&atoken={artifact_token}", flush=True)
        print(f"token: {web_ui_token}", flush=True)
        print(f"fake backend: {FAKE_CLI.name}; temp dir {workdir}; Ctrl+C to stop", flush=True)
        try:
            await serve
        except asyncio.CancelledError:
            pass
        finally:
            server.should_exit = True
            for task in (pump, ticker):
                if task is not None:
                    task.cancel()
            await broadcaster.close_all()
            await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bind-address", default="127.0.0.1", help="127.0.0.1 (default) or 0.0.0.0")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument(
        "--approval-every", type=float, default=0.0, metavar="SECONDS",
        help="also raise a fake pending approval every SECONDS while none is pending",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("exiting")


if __name__ == "__main__":
    main()
