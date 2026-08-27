from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from convobox.adapters.base import BackendEventType
from convobox.adapters.opencode import OpenCodeAdapter, warn_if_insecure

from ._opencode_loopback import OpenCodeServer, _frame

_SESSION_ID = "ses_test123"


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
    srv = OpenCodeServer(list(_SINGLE_STEP_FRAMES))
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


async def _release_all_gates(server: OpenCodeServer, count: int) -> None:
    for _ in range(count):
        server.event_gate.release()
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
async def test_no_model_configured_posts_an_empty_session_body(
    server: OpenCodeServer,
) -> None:
    # Default, unchanged behavior: omitting model entirely lets opencode
    # pick its own default -- confirmed live, 2026-07-14, that this can
    # silently be a hosted free-tier model rather than the user's own
    # configured provider, with no error either way.
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await adapter.send_text("do the thing")
    finally:
        await adapter._client.aclose()

    assert server.created_session_bodies == [{}]


@pytest.mark.asyncio
async def test_configured_model_is_sent_on_session_creation(
    server: OpenCodeServer,
) -> None:
    # Real mechanism, confirmed against a live server's own OpenAPI spec
    # (GET /doc): POST /api/session's optional model: {providerID, id}
    # field -- NOT a CLI flag (opencode serve has no -m/--model option at
    # all, confirmed via `opencode serve --help`).
    adapter = OpenCodeAdapter(server.base_url, model="openai/gpt-5.6-sol")
    try:
        await adapter.send_text("do the thing")
    finally:
        await adapter._client.aclose()

    assert server.created_session_bodies == [
        {"model": {"providerID": "openai", "id": "gpt-5.6-sol"}}
    ]


def test_model_without_a_slash_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="provider/model-id"):
        OpenCodeAdapter("http://localhost:4096", model="gpt-5.6-sol")


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


class _RecordingClient:
    """Minimal httpx.AsyncClient stand-in that records the timeout used."""

    def __init__(self) -> None:
        self.prompt_timeout: object = "unset"
        self.session_timeout: object = "unset"
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def post(self, path: str, json: object = None, timeout: object = None) -> object:
        if path.endswith("/prompt"):
            self.prompt_timeout = timeout
        elif path == "/api/session":
            self.session_timeout = timeout

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {"data": {"id": _SESSION_ID}}

        return _Resp()


@pytest.mark.asyncio
async def test_aclose_does_not_close_an_injected_client() -> None:
    # An injected client (tests, or a shared client) is the caller's to
    # manage -- aclose must NOT close it, only a client the adapter created.
    client = _RecordingClient()
    adapter = OpenCodeAdapter("http://localhost:4096", client=client)  # type: ignore[arg-type]
    await adapter.aclose()
    assert client.closed is False


@pytest.mark.asyncio
async def test_aclose_closes_a_client_the_adapter_owns() -> None:
    adapter = OpenCodeAdapter("http://localhost:4096")  # constructs its own client
    assert adapter._owns_client is True
    await adapter.aclose()  # closes the owned httpx client; must not raise
    assert adapter._client.is_closed


@pytest.mark.asyncio
async def test_prompt_post_uses_generous_read_timeout() -> None:
    # A steer/interject to a BUSY session can take seconds to be accepted;
    # the default 5s read timeout spuriously failed it and crashed the app.
    # The prompt POST must carry an explicit, generous read timeout.
    client = _RecordingClient()
    adapter = OpenCodeAdapter("http://localhost:4096", client=client)  # type: ignore[arg-type]
    await adapter.send_interject("steer me while busy")
    assert client.prompt_timeout is not None and client.prompt_timeout != "unset"
    assert getattr(client.prompt_timeout, "read", None) == 30.0


@pytest.mark.asyncio
async def test_session_creation_post_uses_generous_read_timeout() -> None:
    # Real live incident (2026-07-15): _ensure_session()'s POST /api/session
    # had no explicit timeout, so it used httpx's bare 5s default -- and a
    # busy/cold opencode server took longer than that to respond, raising
    # httpx.ReadTimeout inside Orchestrator._consume_events()'s task
    # uncaught, silently killing event consumption for over a minute of a
    # live session. Same generous-timeout treatment as the prompt POST.
    client = _RecordingClient()
    adapter = OpenCodeAdapter("http://localhost:4096", client=client)  # type: ignore[arg-type]
    await adapter.send_text("first message, creates the session")
    assert client.session_timeout is not None and client.session_timeout != "unset"
    assert getattr(client.session_timeout, "read", None) == 30.0


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
async def test_events_sse_stall_logs_warning_and_still_yields_the_frame(
    server: OpenCodeServer, caplog: pytest.LogCaptureFixture
) -> None:
    # Found live, 2026-08-15 (docs/field-notes/2026-08-15-force-kill-
    # command-matching-fallback-and-opencode-freeze-diagnostic-gap.md):
    # events()'s SSE read had zero stall diagnostic, unlike codex.py's/
    # claude_code.py's readline() calls (PR #274) -- a real instrumentation
    # gap on a structurally identical unbounded wait (read=None). This
    # proves anext_with_stall_diagnostic() actually fires on a real delayed
    # frame, not just on paper, and that the frame is still delivered
    # correctly once it arrives (a stall is diagnosed, not abandoned).
    adapter = OpenCodeAdapter(server.base_url)
    events = []

    async def collect() -> None:
        async for event in adapter.events():
            events.append(event)

    collector = asyncio.ensure_future(collect())
    try:
        with caplog.at_level("WARNING"):
            # No frame released yet -- the SSE read is genuinely pending.
            # _READLINE_STALL_FIRST_WARNING_S is 0.5s; wait past it.
            await asyncio.sleep(0.7)
            await _release_all_gates(server, len(_SINGLE_STEP_FRAMES))
            await asyncio.wait_for(collector, timeout=5)
    finally:
        collector.cancel()
        await adapter._client.aclose()

    stall_warnings = [r for r in caplog.records if "anext() still pending" in r.message]
    recovered = [r for r in caplog.records if "anext() finally returned" in r.message]
    assert stall_warnings, "expected at least one stall warning during the 0.7s delay"
    assert recovered, "expected a recovery log line once the frame arrived"
    assert all("opencode SSE events()" in r.message for r in stall_warnings + recovered)
    # The stall must not have dropped or corrupted the eventual frame.
    assert [e.type for e in events] == [
        BackendEventType.TEXT,
        BackendEventType.TOOL_CALL,
        BackendEventType.TOOL_RESULT,
    ]


@pytest.mark.asyncio
async def test_malformed_sse_frame_is_skipped_not_crashed(server: OpenCodeServer) -> None:
    # A genuinely malformed data line (not valid JSON) must not crash the
    # generator or the events it's driving for -- _safe_json_loads()
    # returns None for it and events() just continues to the next frame.
    # Untested before this: every other test's frames are always real
    # dicts that round-trip cleanly through json.dumps.
    server.frames = [
        "not valid json at all {{{",
        _frame(1, "session.next.text.ended", {"textID": "text-0", "text": "still works"}),
    ]
    adapter = OpenCodeAdapter(server.base_url)
    events = []

    async def collect() -> None:
        async for event in adapter.events():
            events.append(event)

    collector = asyncio.ensure_future(collect())
    try:
        await _release_all_gates(server, len(server.frames))
        await asyncio.wait_for(collector, timeout=5)
    finally:
        collector.cancel()
        await adapter._client.aclose()

    assert [e.type for e in events] == [BackendEventType.TEXT]
    assert events[0].content == "still works"


@pytest.mark.asyncio
async def test_tool_failed_event_maps_to_error_event(server: OpenCodeServer) -> None:
    # Shape inferred from OpenCode's OpenAPI spec (SessionNextToolFailed),
    # not empirically observed live -- see the comment in opencode.py.
    # Untested before this: no test ever sent a tool.failed frame.
    server.frames = [_frame(1, "session.next.tool.failed", {"error": "command not found"})]
    adapter = OpenCodeAdapter(server.base_url)
    events = []

    async def collect() -> None:
        async for event in adapter.events():
            events.append(event)

    collector = asyncio.ensure_future(collect())
    try:
        await _release_all_gates(server, len(server.frames))
        await asyncio.wait_for(collector, timeout=5)
    finally:
        collector.cancel()
        await adapter._client.aclose()

    assert [e.type for e in events] == [BackendEventType.ERROR]
    assert json.loads(events[0].tool_output or "null") == "command not found"


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
async def test_send_hard_stop_survives_a_failed_interrupt_request(
    server: OpenCodeServer,
) -> None:
    # The safeword must never raise even if the interrupt POST itself
    # fails (network blip, server 500) -- a hard stop that crashes on its
    # own failure path would be strictly worse than the thing it's trying
    # to abort. Untested before this: every other hard-stop test hits a
    # server that always succeeds.
    server.interrupt_should_fail = True
    adapter = OpenCodeAdapter(server.base_url)
    try:
        await adapter.send_text("go")
        await adapter.send_hard_stop()  # must not raise
        assert adapter.is_busy() is False
        assert server.interrupt_count == 1
    finally:
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
        server.event_gate.release()
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
async def test_wait_listening_returns_immediately_when_already_set(
    server: OpenCodeServer,
) -> None:
    # The early return (self._listening.is_set() -> return, before ever
    # touching asyncio.wait_for) -- untested before this, since every
    # other wait_listening test calls it exactly once per adapter.
    adapter = OpenCodeAdapter(server.base_url)
    stream = adapter.events()
    try:
        first_future = asyncio.ensure_future(stream.__anext__())
        await asyncio.wait_for(adapter.wait_listening(timeout=5.0), timeout=4.0)

        # Second call, already listening -- must return well inside a tiny
        # timeout, not fall through to the wait_for/TimeoutError path.
        await asyncio.wait_for(adapter.wait_listening(timeout=5.0), timeout=0.1)

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
