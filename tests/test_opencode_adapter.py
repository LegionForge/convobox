from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from convobox.adapters.base import BackendEventType
from convobox.adapters.opencode import OpenCodeAdapter, warn_if_insecure

_SESSION_ID = "ses_test123"


def _frame(seq: int, event_type: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"evt_{seq}",
        "type": event_type,
        "durable": {"aggregateID": _SESSION_ID, "seq": seq, "version": 1},
        "data": {"sessionID": _SESSION_ID, **data},
    }


# Real SSE event shapes, confirmed against a live opencode v1.17.18
# instance -- see OPENCODE_API_NOTES.md's live traces. A single-step reply
# (step.started..step.ended with finish="stop") followed by a second,
# multi-step-shaped one (step.ended with finish="tool-calls" -- confirmed
# live to mean "another step is coming", NOT done -- then a second
# step.started/ended pair that IS terminal) so tests can assert on both
# the continuing and terminal cases from the same fixed frame list.
_SINGLE_STEP_FRAMES: list[dict[str, object]] = [
    _frame(1, "session.next.step.started", {}),
    _frame(2, "session.next.text.started", {"textID": "text-0"}),
    _frame(3, "session.next.text.ended", {"textID": "text-0", "text": "hello"}),
    _frame(4, "session.next.tool.called", {"tool": "bash", "input": {"command": "ls -la"}}),
    _frame(5, "session.next.tool.success", {"structured": {"output": "file1\nfile2"}}),
    _frame(6, "session.next.step.ended", {"finish": "stop"}),
]

_MULTI_STEP_FRAMES: list[dict[str, object]] = [
    _frame(1, "session.next.step.started", {}),
    _frame(2, "session.next.tool.called", {"tool": "read", "input": {"path": "x"}}),
    _frame(3, "session.next.tool.success", {"structured": {"entries": []}}),
    # Confirmed live: this finish value means another step follows --
    # is_busy() must stay True here, not clear.
    _frame(4, "session.next.step.ended", {"finish": "tool-calls"}),
    _frame(5, "session.next.step.started", {}),
    _frame(6, "session.next.text.ended", {"textID": "text-0", "text": "done"}),
    _frame(7, "session.next.step.ended", {"finish": "stop"}),
]


class OpenCodeServer:
    """A real HTTP+SSE server on an ephemeral 127.0.0.1 port, shaped like
    the real /api/ surface (see OPENCODE_API_NOTES.md), not the originally
    assumed one.

    Streams SSE frames one at a time, each released by ``event_gate``, so the
    adapter is proven to parse frames as they arrive rather than after the
    whole body has been buffered. ``frames`` is settable per test so
    different scripted event sequences can be replayed.
    """

    def __init__(self, frames: list[dict[str, object]] | None = None) -> None:
        self.frames = frames if frames is not None else list(_SINGLE_STEP_FRAMES)
        self.created_sessions = 0
        self.posted_prompts: list[dict[str, object]] = []
        self.interrupt_count = 0
        self.event_gate = asyncio.Event()
        self.client_disconnected = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        self._closing = False
        self.port = 0
        # When True, _events sends exactly one frame then closes the
        # connection immediately instead of continuing — simulates a
        # dropped connection / server crash mid-response.
        self.close_after_first_frame = False

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            # Release anything still parked on event_gate so a handler
            # coroutine can't outlive its client -- since Python 3.12.1,
            # Server.wait_closed() genuinely waits for connection handlers
            # to finish (CPython gh-104344), so a parked handler would
            # otherwise deadlock teardown.
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

            if method == "POST" and path == "/api/session":
                await self._create_session(writer)
            elif method == "POST" and path == f"/api/session/{_SESSION_ID}/prompt":
                await self._post_prompt(writer, body)
            elif method == "GET" and path == f"/api/session/{_SESSION_ID}/event":
                await self._events(reader, writer)
            elif method == "POST" and path == f"/api/session/{_SESSION_ID}/interrupt":
                await self._interrupt(writer)
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
            await self.event_gate.wait()
            if self._closing:
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


async def _release_all_gates(server: OpenCodeServer, count: int) -> None:
    for _ in range(count):
        server.event_gate.set()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_send_text_creates_session_and_posts_prompt_with_queue_delivery(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await adapter.send_text("do the thing")
    finally:
        await adapter._client.aclose()

    assert server.created_sessions == 1
    assert server.posted_prompts == [
        {"prompt": {"text": "do the thing"}, "delivery": "queue"}
    ]
    assert adapter.is_busy() is True


@pytest.mark.asyncio
async def test_send_interject_uses_steer_delivery(server: OpenCodeServer) -> None:
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await adapter.send_interject("oh also")
    finally:
        await adapter._client.aclose()

    assert server.posted_prompts == [
        {"prompt": {"text": "oh also"}, "delivery": "steer"}
    ]


@pytest.mark.asyncio
async def test_events_yield_typed_backend_events_from_real_shape(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)
    events = []

    async def collect() -> None:
        async for event in adapter.events():
            events.append(event)

    collector = asyncio.ensure_future(collect())
    try:
        await _release_all_gates(server, len(_SINGLE_STEP_FRAMES))
        await asyncio.wait_for(collector, timeout=5)
    finally:
        collector.cancel()
        await adapter._client.aclose()

    # step.started/step.ended and text.started carry no BackendEventType
    # slot -- only 3 of the 6 real frames yield anything.
    assert [e.type for e in events] == [
        BackendEventType.TEXT,
        BackendEventType.TOOL_CALL,
        BackendEventType.TOOL_RESULT,
    ]
    assert events[0].content == "hello"
    assert events[1].tool == "bash"
    assert json.loads(events[1].tool_input or "{}") == {"command": "ls -la"}
    assert json.loads(events[2].tool_output or "{}") == {"output": "file1\nfile2"}


@pytest.mark.asyncio
async def test_is_busy_true_after_send_text_and_false_after_terminal_step_ended(
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
        await _release_all_gates(server, len(_SINGLE_STEP_FRAMES))
        await asyncio.wait_for(collector, timeout=5)

        assert adapter.is_busy() is False
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_is_busy_stays_true_through_tool_calls_finish_reason(
    server: OpenCodeServer,
) -> None:
    # The key behavior this whole design is built around: a step.ended
    # with finish="tool-calls" means another step follows -- confirmed
    # live -- so is_busy() must NOT clear there, only at the second,
    # finish="stop" step.ended later in the same response. Records
    # is_busy() at each yielded event (not by cancelling the generator
    # mid-stream, which would trigger events()'s own last-resort
    # safety-net clear in its finally block and give a false pass).
    server.frames = list(_MULTI_STEP_FRAMES)
    adapter = OpenCodeAdapter(server.base_url)
    busy_after_each_yield: list[bool] = []

    async def drain_and_record() -> None:
        async for _ in adapter.events():
            busy_after_each_yield.append(adapter.is_busy())

    try:
        await adapter.send_text("go")
        collector = asyncio.ensure_future(drain_and_record())
        await _release_all_gates(server, len(_MULTI_STEP_FRAMES))
        await asyncio.wait_for(collector, timeout=5)
    finally:
        await adapter._client.aclose()

    # tool.called and tool.success (frames 2, 3) are the only
    # BackendEvent-yielding frames before the tool-calls step.ended (frame
    # 4, non-yielding) -- is_busy() must still be True after both.
    # text.ended (frame 6) is the only yielding frame after the second
    # step.started -- also still True there, since the terminal step.ended
    # (frame 7, finish="stop") hasn't happened yet.
    assert busy_after_each_yield == [True, True, True]
    # Only after the whole stream (including the terminal step.ended) has
    # been processed does is_busy() actually clear.
    assert adapter.is_busy() is False


@pytest.mark.asyncio
async def test_send_hard_stop_calls_interrupt_and_leaves_sse_open(
    server: OpenCodeServer,
) -> None:
    # send_hard_stop must NOT tear down the SSE subscription: the stream is
    # owned by the task iterating events(), and closing it from the
    # hard-stop caller's task raises "anext(): asynchronous generator is
    # already running" (observed live on the first real Orchestrator-driven
    # hard stop). The session also survives an interrupt, so the same
    # subscription must keep serving whatever the user asks next.
    adapter = OpenCodeAdapter(server.base_url)

    stream = adapter.events()
    try:
        await adapter.send_text("go")
        # __anext__() must be in flight (opening the SSE connection) before
        # releasing gates -- events() doesn't open the connection until
        # first iterated, so releasing gates first sets/clears them on a
        # server handler that isn't listening yet.
        first_future = asyncio.ensure_future(stream.__anext__())
        await _release_all_gates(server, 3)  # past step.started/text.started to text.ended
        first = await asyncio.wait_for(first_future, timeout=5)
        assert first.type == BackendEventType.TEXT
        assert adapter._sse_context is not None

        # Park a consumer mid-__anext__ (suspended inside aiter_sse, exactly
        # the state the Orchestrator's consumer loop lives in), then hard
        # stop from this task -- the crash scenario.
        next_future = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)  # let it reach the suspended-read state

        await adapter.send_hard_stop()
        assert adapter.is_busy() is False
        assert adapter._sse_context is not None  # subscription intact
        assert server.interrupt_count == 1

        # The parked consumer survived (before the fix, send_hard_stop
        # blew up with RuntimeError before these asserts were reached);
        # unpark it cleanly.
        next_future.cancel()
        try:
            await next_future
        except asyncio.CancelledError:
            pass
    finally:
        await stream.aclose()
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_send_hard_stop_is_safe_with_no_prior_prompt(server: OpenCodeServer) -> None:
    # send_hard_stop must not require a session/prompt to already exist --
    # e.g. a stray safeword before anything was ever sent.
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await adapter.send_hard_stop()
        assert adapter.is_busy() is False
        assert server.interrupt_count == 0  # no session was ever created to interrupt
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_is_busy_clears_when_stream_ends_without_terminal_step(
    server: OpenCodeServer,
) -> None:
    # Regression test: if the connection drops mid-response before a
    # terminal step.ended ever arrives, is_busy() would otherwise latch
    # True forever with nothing left to clear it. A dropped SSE connection
    # isn't itself proof of completion, but latching forever is the worse
    # failure mode -- events()'s finally block clears busy as a
    # last-resort safety net for exactly this case (on top of the normal,
    # faster finish-reason-driven clear -- see the other is_busy tests).
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

        # Connection dropped after 1 frame (step.started, non-terminal) --
        # the finish-reason path never fired, but the safety net in
        # events()'s finally block did.
        assert adapter.is_busy() is False
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_concurrent_send_and_events_share_one_session(
    server: OpenCodeServer,
) -> None:
    # The exact shape of Orchestrator.handle_transcript's first call: the
    # event-consumer task and the first send start concurrently, and both
    # reach _ensure_session while _session_id is still None. Without the
    # session lock each created its own session -- the prompt landed in one,
    # the SSE subscription in the other, and zero events were ever
    # delivered (found live on the first real Orchestrator-level run).
    adapter = OpenCodeAdapter(server.base_url)

    async def drain() -> None:
        async for _ in adapter.events():
            pass

    collector = asyncio.ensure_future(drain())
    try:
        await adapter.send_text("go")
        await _release_all_gates(server, len(_SINGLE_STEP_FRAMES))
        await asyncio.wait_for(collector, timeout=5)

        assert server.created_sessions == 1
    finally:
        if not collector.done():
            collector.cancel()
            try:
                await collector
            except asyncio.CancelledError:
                pass
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_wait_listening_returns_once_sse_subscription_starts(
    server: OpenCodeServer,
) -> None:
    adapter = OpenCodeAdapter(server.base_url)
    stream = adapter.events()
    try:
        first_future = asyncio.ensure_future(stream.__anext__())
        # Must resolve well inside its own timeout once events() has begun
        # dispatching the SSE request -- no gates need releasing for that
        # (frames are gated, the subscription itself is not), and no
        # response headers are needed either: the real opencode server
        # holds SSE headers back until the first event exists.
        await asyncio.wait_for(adapter.wait_listening(timeout=5.0), timeout=4.0)

        first_future.cancel()
        try:
            await first_future
        except asyncio.CancelledError:
            pass
    finally:
        await stream.aclose()
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_wait_listening_times_out_gracefully_without_consumer(
    server: OpenCodeServer,
) -> None:
    # A caller that never consumes events() (bare send_text usage) must get
    # a bounded wait and a clean return, never a deadlock or an exception.
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await asyncio.wait_for(adapter.wait_listening(timeout=0.1), timeout=2.0)
    finally:
        await adapter._client.aclose()
