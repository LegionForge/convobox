from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from convobox.adapters.base import BackendEventType
from convobox.adapters.opencode import OpenCodeAdapter, warn_if_insecure

_SESSION_ID = "sess-123"

_EVENT_FRAMES: list[dict[str, object]] = [
    {"type": "text", "content": "hello"},
    {
        "type": "tool_call",
        "tool": "bash",
        "toolInput": "ls -la",
        "toolOutput": None,
    },
    {"type": "done"},
]


class OpenCodeServer:
    """A real HTTP+SSE server on an ephemeral 127.0.0.1 port.

    Streams SSE frames one at a time, each released by ``event_gate``, so the
    adapter is proven to parse frames as they arrive rather than after the whole
    body has been buffered.
    """

    def __init__(self) -> None:
        self.created_sessions = 0
        self.posted_messages: list[dict[str, object]] = []
        self.event_gate = asyncio.Event()
        self.client_disconnected = asyncio.Event()
        self._closing = False
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        # When True, _events sends exactly one frame then closes the
        # connection immediately instead of continuing to DONE — simulates
        # a dropped connection / server crash mid-response.
        self.close_after_first_frame = False

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
            self.event_gate.set()
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

            if method == "POST" and path == "/api/sessions":
                await self._create_session(writer)
            elif method == "POST" and path == f"/api/sessions/{_SESSION_ID}/messages":
                await self._post_message(writer, body)
            elif method == "GET" and path == f"/api/sessions/{_SESSION_ID}/events":
                await self._events(reader, writer)
            else:
                await self._respond(writer, 404, b'{"error":"not found"}')
        except (asyncio.IncompleteReadError, ConnectionResetError):
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

    async def _create_session(self, writer: asyncio.StreamWriter) -> None:
        self.created_sessions += 1
        await self._respond(writer, 200, json.dumps({"id": _SESSION_ID}).encode())

    async def _post_message(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        self.posted_messages.append(json.loads(body))
        await self._respond(writer, 200, b'{"ok":true}')

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

        for frame in _EVENT_FRAMES:
            await self.event_gate.wait()
            if self._closing:
                # stop() set the gate to reap parked handlers; don't clear it,
                # any other parked handler needs to wake from it too.
                return
            self.event_gate.clear()
            if reader.at_eof():
                self.client_disconnected.set()
                return
            try:
                writer.write(f"data: {json.dumps(frame)}\n\n".encode())
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                self.client_disconnected.set()
                return

            if self.close_after_first_frame:
                writer.close()
                return


def test_warn_if_insecure_flags_plaintext_non_local(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        warn_if_insecure("http://example.com:4096")
    assert any("unencrypted" in r.message for r in caplog.records)


def test_warn_if_insecure_silent_for_https(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        warn_if_insecure("https://example.com:4096")
    assert caplog.records == []


def test_warn_if_insecure_silent_for_localhost(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        warn_if_insecure("http://localhost:4096")
    assert caplog.records == []


def test_warn_if_insecure_flags_schemeless_url(caplog: pytest.LogCaptureFixture) -> None:
    # Regression test: urlparse("somehost:4096") without "//" mistakes the
    # host for the scheme (scheme="somehost", hostname=None), which used to
    # silently bypass the check entirely since scheme != "http" — even
    # though httpx accepts such a URL and would make plaintext requests.
    with caplog.at_level("WARNING"):
        warn_if_insecure("somehost.example.com:4096")
    assert any("no recognized http/https scheme" in r.message for r in caplog.records)


@pytest_asyncio.fixture
async def server() -> AsyncIterator[OpenCodeServer]:
    srv = OpenCodeServer()
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_send_text_creates_session_and_posts_message(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await adapter.send_text("do the thing")
    finally:
        await adapter._client.aclose()

    assert server.created_sessions == 1
    assert server.posted_messages == [
        {"messages": [{"role": "user", "content": "do the thing"}]}
    ]
    assert adapter.is_busy() is True


@pytest.mark.asyncio
async def test_events_yield_typed_backend_events_from_sse(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)
    events = []

    async def collect() -> None:
        async for event in adapter.events():
            events.append(event)

    collector = asyncio.ensure_future(collect())
    try:
        for _ in _EVENT_FRAMES:
            await asyncio.sleep(0.02)
            server.event_gate.set()
        await asyncio.wait_for(collector, timeout=5)
    finally:
        collector.cancel()
        await adapter._client.aclose()

    assert [e.type for e in events] == [
        BackendEventType.TEXT,
        BackendEventType.TOOL_CALL,
        BackendEventType.DONE,
    ]
    assert events[0].content == "hello"
    assert events[1].tool == "bash"
    assert events[1].tool_input == "ls -la"


@pytest.mark.asyncio
async def test_is_busy_true_after_send_text_and_false_after_done(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)

    async def drain() -> None:
        async for _ in adapter.events():
            pass

    try:
        await adapter.send_text("go")
        assert adapter.is_busy() is True

        collector = asyncio.ensure_future(drain())
        for _ in _EVENT_FRAMES:
            await asyncio.sleep(0.02)
            server.event_gate.set()
        await asyncio.wait_for(collector, timeout=5)
        assert adapter.is_busy() is False
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_is_busy_clears_when_stream_ends_without_done(
    server: OpenCodeServer,
) -> None:
    # Regression test: a dropped connection / server crash mid-response
    # used to leave is_busy() latched True forever, since the only place
    # that cleared it was the DONE/ERROR branch inside events() — never hit
    # if the stream ends any other way.
    server.close_after_first_frame = True
    adapter = OpenCodeAdapter(server.base_url)

    async def drain() -> None:
        async for _ in adapter.events():
            pass

    try:
        await adapter.send_text("go")
        assert adapter.is_busy() is True

        collector = asyncio.ensure_future(drain())
        server.event_gate.set()
        await asyncio.wait_for(collector, timeout=5)

        assert adapter.is_busy() is False
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_send_hard_stop_closes_sse_without_raising(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)

    stream = adapter.events()
    try:
        server.event_gate.set()
        first = await asyncio.wait_for(stream.__anext__(), timeout=5)
        assert first.type == BackendEventType.TEXT
        assert adapter._sse_context is not None

        await adapter.send_hard_stop()
        assert adapter.is_busy() is False
        assert adapter._sse_context is None

        # Not asserting server.client_disconnected here: httpx.AsyncClient
        # pools connections by default, so closing the SSE response stream
        # doesn't reliably close the underlying TCP socket within any fixed
        # timeout — that would test httpx's pooling behavior, not the
        # adapter. The state asserted above is what "closes cleanly" means
        # from the adapter's own perspective.
    finally:
        await stream.aclose()
        await adapter._client.aclose()
