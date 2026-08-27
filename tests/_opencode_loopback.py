"""A real HTTP+SSE server shaped like opencode's real `/api/` surface, for
tests -- extracted from test_opencode_adapter.py (2026-08-25) so a second
test file (test_backend_adapter_conformance.py) can drive OpenCodeAdapter
against it too, without a second implementation.

Not itself a test file (no `test_*` functions) -- an importable fixture
module, same role fake_claude_cli.py/fake_codex_appserver.py play for the
other two backends. Unlike those two, this isn't a subprocess: opencode's
real transport is HTTP+SSE over a loopback socket, so a real
asyncio.start_server on an ephemeral 127.0.0.1 port is the equivalent
"real transport, not a mock" discipline (see OpenCodeAdapter's own
docstring on why the bugs live in transport handling, not parsing).
"""

from __future__ import annotations

import asyncio
import json

_SESSION_ID = "ses_test123"


def _frame(seq: int, event_type: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"evt_{seq}",
        "type": event_type,
        "durable": {"aggregateID": _SESSION_ID, "seq": seq, "version": 1},
        "data": {"sessionID": _SESSION_ID, **data},
    }


class OpenCodeServer:
    """A real HTTP+SSE server on an ephemeral 127.0.0.1 port, shaped like
    the real /api/ surface (see OPENCODE_API_NOTES.md), not the originally
    assumed one.

    Streams SSE frames one at a time, each released by ``event_gate``, so the
    adapter is proven to parse frames as they arrive rather than after the
    whole body has been buffered. ``frames`` is settable per test so
    different scripted event sequences can be replayed.
    """

    def __init__(self, frames: list[dict[str, object] | str] | None = None) -> None:
        self.frames: list[dict[str, object] | str] = frames if frames is not None else []
        self.created_sessions = 0
        self.created_session_bodies: list[dict[str, object]] = []
        self.posted_prompts: list[dict[str, object]] = []
        self.interrupt_count = 0
        # A Semaphore, not an Event: _release_all_gates below fires
        # repeated releases in a tight loop without confirming the
        # consumer woke between them. Event.set() is a no-op when already
        # set, so two releases fired before the consumer's _events loop
        # gets scheduled back to wait() collapse into a single wakeup and
        # permanently strand the last frame -- confirmed live as an
        # intermittent full-suite-only hang (never reproduced running
        # this file or any single test alone, since only full-suite
        # contention delays the consumer enough to hit the window).
        # Semaphore.release() queues properly regardless of scheduling
        # order, which is what "release exactly one frame, one at a time"
        # actually needs.
        self.event_gate = asyncio.Semaphore(0)
        self.client_disconnected = asyncio.Event()
        self._closing = False
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        # When True, _events sends exactly one frame then closes the
        # connection immediately instead of continuing — simulates a
        # dropped connection / server crash mid-response.
        self.close_after_first_frame = False
        # When True, /interrupt responds 500 instead of 204 -- simulates a
        # network/server failure on the safeword's hard-stop request itself.
        self.interrupt_should_fail = False

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            # Release any _events handler still parked on the gate (e.g. after
            # a hard stop closed the client mid-stream, when no further frame
            # is ever released). Since Python 3.12.1, Server.wait_closed()
            # waits for connection handlers to actually finish (gh-104344), so
            # a parked handler deadlocks teardown — on 3.11 this leak existed
            # too but wait_closed() returned without waiting, masking it.
            self._closing = True
            self.event_gate.release()
            await self._server.wait_closed()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, path, _ = request_line.decode().split(" ", 2)
            content_length = 0
            while True:
                header = await reader.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
                name, _, value = header.decode().partition(":")
                if name.strip().lower() == "content-length":
                    content_length = int(value.strip())
            body = await reader.readexactly(content_length) if content_length else b""

            if method == "POST" and path == "/api/session":
                await self._create_session(writer, body)
            elif method == "POST" and path == f"/api/session/{_SESSION_ID}/prompt":
                await self._post_prompt(writer, body)
            elif method == "GET" and path == f"/api/session/{_SESSION_ID}/event":
                await self._events(reader, writer)
            elif method == "POST" and path == f"/api/session/{_SESSION_ID}/interrupt":
                await self._interrupt(writer)
            else:
                await self._respond(writer, 404, b'{"error":"not found"}')
        except (asyncio.IncompleteReadError, ConnectionResetError):
            # The client (the adapter under test) closing its connection
            # mid-request -- e.g. after a hard-stop/aclose() tears down the
            # HTTP client while this handler is still reading -- is an
            # expected, benign shutdown path for a test loopback server,
            # not a real error worth surfacing. finally still closes the
            # writer either way.
            pass
        finally:
            writer.close()

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, body: bytes
    ) -> None:
        writer.write(
            f"HTTP/1.1 {status} OK\r\n"
            "content-type: application/json\r\n"
            f"content-length: {len(body)}\r\n"
            "connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    async def _create_session(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        self.created_sessions += 1
        self.created_session_bodies.append(json.loads(body) if body else {})
        await self._respond(writer, 200, json.dumps({"data": {"id": _SESSION_ID}}).encode())

    async def _post_prompt(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        self.posted_prompts.append(json.loads(body))
        response = {
            "data": {
                "admittedSeq": len(self.posted_prompts),
                "id": f"msg_{len(self.posted_prompts)}",
                "sessionID": _SESSION_ID,
                "delivery": "queue",
                "timeCreated": 0,
            }
        }
        await self._respond(writer, 200, json.dumps(response).encode())

    async def _interrupt(self, writer: asyncio.StreamWriter) -> None:
        self.interrupt_count += 1
        if self.interrupt_should_fail:
            await self._respond(writer, 500, b'{"error":"boom"}')
        else:
            await self._respond(writer, 204, b"")

    async def _events(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"content-type: text/event-stream\r\n"
            b"cache-control: no-cache\r\n"
            b"connection: keep-alive\r\n\r\n"
        )
        await writer.drain()

        for frame in self.frames:
            await self.event_gate.acquire()
            if self._closing:
                return
            if reader.at_eof():
                self.client_disconnected.set()
                return
            # A str frame is written as a raw SSE data line, unencoded --
            # for tests that need to inject a genuinely malformed payload
            # (real dict frames always round-trip through json.dumps).
            payload = frame if isinstance(frame, str) else json.dumps(frame)
            try:
                writer.write(f"data: {payload}\n\n".encode())
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                self.client_disconnected.set()
                return

            if self.close_after_first_frame:
                writer.close()
                return
