from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable

import numpy as np
import pytest

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.audio.playback import AudioPlayer
from convobox.orchestrator.orchestrator import (
    Orchestrator,
    render_approval_request_for_speech,
    render_question_for_speech,
    strip_code_for_speech,
)
from convobox.safeword.detector import SafewordDetector
from convobox.tts.base import TTSEngine


class FakeBackendAdapter(BackendAdapter):
    def __init__(self, busy: bool = False, events_to_yield: list[BackendEvent] | None = None) -> None:
        self._busy = busy
        self.sent_text: list[str] = []
        self.sent_interject: list[str] = []
        self.hard_stops = 0
        self.force_kills = 0
        # None (the default) keeps every existing test's behavior
        # byte-identical -- events() ends immediately, same as before this
        # param existed. A real list is for tests that need to drive
        # _consume_events()'s actual async-for loop (see
        # test_consume_events_dispatches_real_events_through_the_task_loop
        # below): every OTHER test in this file calls orch._on_event(...)
        # directly, which never exercises that loop body at all.
        self._events_to_yield = events_to_yield
        # How many of the next events() calls should raise instead of
        # iterating -- for _consume_events()'s retry-on-exception tests.
        # Decremented on each call; 0 (default) never raises, matching
        # every existing test's behavior exactly.
        self.fail_events_calls = 0
        self.events_call_count = 0
        self.resolved_approvals: list[bool] = []
        self.resolve_pending_approval_returns = True

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_interject(self, text: str) -> None:
        self.sent_interject.append(text)

    async def send_hard_stop(self) -> None:
        self.hard_stops += 1

    async def force_kill(self) -> None:
        # Deliberately does NOT clear _busy (matching send_hard_stop()'s
        # own comment above -- is_busy() must be read BEFORE this is
        # called to mean anything) and deliberately does NOT delegate to
        # aclose() the way BackendAdapter's own default does -- this fake
        # tracks the call directly so tests can assert force_kill() (not
        # send_hard_stop()) is what actually fired.
        self.force_kills += 1

    def is_busy(self) -> bool:
        return self._busy

    async def resolve_pending_approval(self, approved: bool) -> bool:
        self.resolved_approvals.append(approved)
        return self.resolve_pending_approval_returns

    async def events(self) -> AsyncGenerator[BackendEvent, None]:
        self.events_call_count += 1
        if self.fail_events_calls > 0:
            self.fail_events_calls -= 1
            raise ConnectionError("simulated transient backend failure")
        if not self._events_to_yield:  # pragma: no cover -- the default, exercised everywhere else
            return
        for event in self._events_to_yield:
            await asyncio.sleep(0)  # a real yield point, not a synchronous drain
            yield event


class FakeTTSEngine(TTSEngine):
    def __init__(self, fail_with: Exception | None = None) -> None:
        # fail_with=None (the default) keeps every existing test's behavior
        # unchanged -- a real exception here simulates a synthesis failure
        # mid-response (e.g. kokoro-onnx's own ~510-phoneme batch-limit
        # crash) for _speak()'s exception-handling tests.
        self.synthesized: list[str] = []
        self.stop_calls = 0
        self._speaking = False
        self._fail_with = fail_with

    @property
    def sample_rate(self) -> int:
        return 16000

    async def synthesize_stream(self, text: str):
        # The recording lives here (not in synthesize) because streaming is
        # the path Orchestrator._speak actually uses now; the inherited
        # synthesize() convenience still funnels through this.
        self.synthesized.append(text)
        if self._fail_with is not None:
            raise self._fail_with
        yield np.ones(4, dtype=np.float32)

    def stop(self) -> None:
        self.stop_calls += 1

    def is_speaking(self) -> bool:
        return self._speaking


class FakePlayer(AudioPlayer):
    """Overrides AudioPlayer's real playback with in-memory recording.

    Subclasses the concrete AudioPlayer (rather than duck-typing) since
    Orchestrator's player param is typed as the concrete class, matching
    this codebase's house style of not introducing an ABC/Protocol for a
    single-implementation role.
    """

    def __init__(self) -> None:
        super().__init__()
        self.played: list[tuple[np.ndarray, int]] = []
        self.stop_calls = 0

    def play(self, samples: np.ndarray, sample_rate: int) -> None:
        self.played.append((samples, sample_rate))

    async def play_stream(self, chunks, sample_rate) -> None:  # type: ignore[no-untyped-def]
        # Records the concatenated stream under the same attribute play()
        # uses, so every existing "what got played" assertion keeps
        # working unchanged against the streamed path.
        collected = [chunk async for chunk in chunks]
        if collected:
            self.played.append((np.concatenate(collected), sample_rate))

    def stop(self) -> None:
        self.stop_calls += 1

    def is_playing(self) -> bool:
        return False


class FakeApprovalGate:
    """Minimal stand-in for run_convobox.py's ApprovalPromptGate, covering
    only the one method Orchestrator.hard_stop() needs (see
    ApprovalWaitCanceler in orchestrator.py). Real hard_stop()-vs-
    approval-gate coverage lives here rather than in run_convobox.py's own
    tests since this is a pure Orchestrator behavior, independent of any
    particular caller's wiring."""

    def __init__(self) -> None:
        self.cancel_wait_calls = 0

    def cancel_wait(self) -> None:
        self.cancel_wait_calls += 1


def make_orchestrator(
    busy: bool,
    with_tts: bool = False,
    on_event: Callable[[BackendEvent], None] | None = None,
    tier_responses: bool = False,
    approval_phrase: str | None = None,
    approval_gate: object | None = None,
    safeword_phrases: list[str] | None = None,
    kill_phrase: str | None = None,
    on_kill_phrase: Callable[[], None] | None = None,
) -> tuple[Orchestrator, FakeBackendAdapter, FakeTTSEngine | None, FakePlayer | None]:
    adapter = FakeBackendAdapter(busy=busy)
    safeword = SafewordDetector(safeword_phrases or ["stop stop stop"])
    if not with_tts:
        return (
            Orchestrator(
                adapter, safeword, on_event=on_event, tier_responses=tier_responses,
                approval_phrase=approval_phrase, approval_gate=approval_gate,
                kill_phrase=kill_phrase, on_kill_phrase=on_kill_phrase,
            ),
            adapter,
            None,
            None,
        )
    tts = FakeTTSEngine()
    player = FakePlayer()
    return (
        Orchestrator(
            adapter, safeword, tts=tts, player=player, on_event=on_event,
            tier_responses=tier_responses, approval_phrase=approval_phrase,
            approval_gate=approval_gate, kill_phrase=kill_phrase,
            on_kill_phrase=on_kill_phrase,
        ),
        adapter,
        tts,
        player,
    )


@pytest.mark.asyncio
async def test_idle_normal_transcript_sends_text() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=False)
    await orch.handle_transcript("add a login button")
    assert adapter.sent_text == ["add a login button"]
    assert adapter.sent_interject == []
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_busy_normal_transcript_sends_interject_not_text() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=True)
    await orch.handle_transcript("oh also add tests")
    assert adapter.sent_interject == ["oh also add tests"]
    assert adapter.sent_text == []
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_empty_transcript_is_dropped() -> None:
    # VAD can trigger on background noise that STT then transcribes to
    # nothing (seen live: a movie playing in the room produced ''). That
    # must not reach the backend as an empty command or interject.
    orch, adapter, _, _ = make_orchestrator(busy=False)
    await orch.handle_transcript("")
    await orch.handle_transcript("   ")
    assert adapter.sent_text == []
    assert adapter.sent_interject == []
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_empty_transcript_is_dropped_when_busy() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=True)
    await orch.handle_transcript("")
    assert adapter.sent_interject == []
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_hard_stop_when_idle() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=False)
    await orch.handle_transcript("stop stop stop")
    assert adapter.hard_stops == 1
    assert adapter.sent_text == []
    assert adapter.sent_interject == []


@pytest.mark.asyncio
async def test_hard_stop_wins_over_busy_interject() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=True)
    await orch.handle_transcript("please stop stop stop now")
    assert adapter.hard_stops == 1
    assert adapter.sent_interject == []
    assert adapter.sent_text == []


@pytest.mark.asyncio
async def test_hard_stop_method_runs_the_same_sequence_directly() -> None:
    # hard_stop() is what handle_transcript()'s safeword branch delegates
    # to (see test_hard_stop_when_idle above) -- this exercises it as a
    # standalone entry point, the shape a non-transcript trigger (e.g. the
    # web UI's Stop button) needs: no matching text, just call it.
    orch, adapter, tts, player = make_orchestrator(busy=False, with_tts=True)
    await orch.hard_stop()
    assert adapter.hard_stops == 1
    assert tts.stop_calls == 1
    assert player.stop_calls == 1


@pytest.mark.asyncio
async def test_hard_stop_method_without_tts_or_player_does_not_raise() -> None:
    # Matches handle_transcript()'s own None-guards on self._tts/self._player
    # (Orchestrator is constructible without either, e.g. --text --mute).
    orch, adapter, _, _ = make_orchestrator(busy=False, with_tts=False)
    await orch.hard_stop()
    assert adapter.hard_stops == 1


@pytest.mark.asyncio
async def test_hard_stop_method_cancels_the_event_loop() -> None:
    # PR #191 (2026-07-31, safety-critical): hard-stopping the adapter
    # alone still let an already-in-flight turn's trailing TEXT event
    # reach _on_event() and get spoken after the stop -- stop_event_loop()
    # (cancelling _events_task) is what actually prevents that. Direct
    # coverage on hard_stop() itself, not just through handle_transcript()'s
    # safeword branch, now that other callers (the web UI's Pause/Stop
    # buttons) delegate to it too.
    orch, _adapter, _, _ = make_orchestrator(busy=False)
    orch.start_event_loop()
    assert orch._events_task is not None
    await orch.hard_stop()
    assert orch._events_task is None


@pytest.mark.asyncio
async def test_hard_stop_cancels_a_pending_approval_gate_wait() -> None:
    # B4: a safeword/web-Stop firing while a voice approval was pending
    # used to leave approval_gate.is_waiting True until its own timeout
    # (up to approval_timeout_s later) -- hard_stop() now cancels it
    # immediately, same as it already resets everything else (playback,
    # TTS, event consumption) on this exact "stop stop stop" path.
    gate = FakeApprovalGate()
    orch, _, _, _ = make_orchestrator(busy=False, approval_gate=gate)
    await orch.hard_stop()
    assert gate.cancel_wait_calls == 1


@pytest.mark.asyncio
async def test_hard_stop_returns_whether_a_turn_was_actually_busy() -> None:
    # The "honesty fix" from docs/KNOWN-ISSUES.md's "A hard-stop does not
    # guarantee an in-flight tool call actually stops" entry: hard_stop()
    # now reports whether it interrupted a turn that was genuinely in
    # flight, so a caller (web bridge, run_convobox.py's pause branch) can
    # avoid implying "everything stopped" when a tool call's underlying
    # process may still be finishing in the background. FakeBackendAdapter's
    # send_hard_stop() deliberately does NOT clear _busy (unlike every real
    # adapter), matching how is_busy() must be read BEFORE send_hard_stop()
    # is called to mean anything.
    orch_busy, _, _, _ = make_orchestrator(busy=True)
    assert await orch_busy.hard_stop() is True

    orch_idle, _, _, _ = make_orchestrator(busy=False)
    assert await orch_idle.hard_stop() is False


@pytest.mark.asyncio
async def test_hard_stop_logs_a_caveat_only_when_a_turn_was_busy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="convobox.orchestrator.orchestrator")

    orch_idle, _, _, _ = make_orchestrator(busy=False)
    await orch_idle.hard_stop()
    assert "not guaranteed to have stopped" not in caplog.text

    caplog.clear()
    orch_busy, _, _, _ = make_orchestrator(busy=True)
    await orch_busy.hard_stop()
    assert "not guaranteed to have stopped" in caplog.text


@pytest.mark.asyncio
async def test_hard_stop_without_an_approval_gate_does_not_raise() -> None:
    # Matches the existing without-tts-or-player coverage above --
    # approval_gate is optional (most sessions have no approval_phrase
    # configured at all, see InteractionConfig.approval_phrase), and
    # hard_stop() must stay a no-op-safe call in that case.
    orch, adapter, _, _ = make_orchestrator(busy=False)
    await orch.hard_stop()
    assert adapter.hard_stops == 1


@pytest.mark.asyncio
async def test_hard_stop_via_safeword_also_cancels_the_approval_gate() -> None:
    # Same behavior reached through handle_transcript()'s safeword branch,
    # not just the direct hard_stop() entry point -- both delegate to the
    # identical sequence (see hard_stop()'s own docstring), so both must
    # cancel the gate.
    gate = FakeApprovalGate()
    orch, adapter, _, _ = make_orchestrator(busy=False, approval_gate=gate)
    await orch.handle_transcript("stop stop stop")
    assert adapter.hard_stops == 1
    assert gate.cancel_wait_calls == 1


# --- force_kill(): "option 2 (escalating force-kill)", built 2026-08-14
# after three live freeze incidents in one session where send_hard_stop()'s
# own polite interrupt could not reach a wedged backend subprocess. Same
# test shape as hard_stop()'s own suite above -- direct entry point first,
# then reached through handle_transcript()'s kill-phrase branch. ---


@pytest.mark.asyncio
async def test_force_kill_method_runs_the_sequence_directly() -> None:
    orch, adapter, tts, player = make_orchestrator(busy=False, with_tts=True)
    await orch.force_kill()
    assert adapter.force_kills == 1
    assert adapter.hard_stops == 0  # never the polite interrupt -- see force_kill()'s own docstring
    assert tts.stop_calls == 1
    assert player.stop_calls == 1


@pytest.mark.asyncio
async def test_force_kill_method_without_tts_or_player_does_not_raise() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=False, with_tts=False)
    await orch.force_kill()
    assert adapter.force_kills == 1


@pytest.mark.asyncio
async def test_force_kill_method_cancels_the_event_loop() -> None:
    # Same safety-critical reasoning as test_hard_stop_method_cancels_the_
    # event_loop above: a trailing TEXT event from the just-killed turn
    # must never reach _on_event() and get spoken.
    orch, _adapter, _, _ = make_orchestrator(busy=False)
    orch.start_event_loop()
    assert orch._events_task is not None
    await orch.force_kill()
    assert orch._events_task is None


@pytest.mark.asyncio
async def test_force_kill_cancels_a_pending_approval_gate_wait() -> None:
    gate = FakeApprovalGate()
    orch, _, _, _ = make_orchestrator(busy=False, approval_gate=gate)
    await orch.force_kill()
    assert gate.cancel_wait_calls == 1


@pytest.mark.asyncio
async def test_force_kill_returns_whether_a_turn_was_actually_busy() -> None:
    orch_busy, _, _, _ = make_orchestrator(busy=True)
    assert await orch_busy.force_kill() is True

    orch_idle, _, _, _ = make_orchestrator(busy=False)
    assert await orch_idle.force_kill() is False


@pytest.mark.asyncio
async def test_force_kill_logs_a_warning_only_when_a_turn_was_busy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="convobox.orchestrator.orchestrator")

    orch_idle, _, _, _ = make_orchestrator(busy=False)
    await orch_idle.force_kill()
    assert "any in-flight work is gone" not in caplog.text

    caplog.clear()
    orch_busy, _, _, _ = make_orchestrator(busy=True)
    await orch_busy.force_kill()
    assert "any in-flight work is gone" in caplog.text


@pytest.mark.asyncio
async def test_kill_phrase_via_handle_transcript_calls_force_kill_not_hard_stop() -> None:
    orch, adapter, _, _ = make_orchestrator(
        busy=False,
        safeword_phrases=["stop stop stop", "eject eject eject"],
        kill_phrase="eject eject eject",
    )
    await orch.handle_transcript("eject eject eject")
    assert adapter.force_kills == 1
    assert adapter.hard_stops == 0


@pytest.mark.asyncio
async def test_other_safewords_still_use_hard_stop_when_kill_phrase_configured() -> None:
    # The kill-phrase branch must not swallow every safeword -- only the
    # one exact phrase configured as kill_phrase escalates; every other
    # configured safeword keeps today's polite-interrupt behavior.
    orch, adapter, _, _ = make_orchestrator(
        busy=False,
        safeword_phrases=["stop stop stop", "eject eject eject"],
        kill_phrase="eject eject eject",
    )
    await orch.handle_transcript("stop stop stop")
    assert adapter.hard_stops == 1
    assert adapter.force_kills == 0


@pytest.mark.asyncio
async def test_kill_phrase_fires_the_on_kill_phrase_callback() -> None:
    calls = 0

    def on_kill() -> None:
        nonlocal calls
        calls += 1

    orch, adapter, _, _ = make_orchestrator(
        busy=False,
        safeword_phrases=["stop stop stop", "eject eject eject"],
        kill_phrase="eject eject eject",
        on_kill_phrase=on_kill,
    )
    await orch.handle_transcript("eject eject eject")
    assert calls == 1
    assert adapter.force_kills == 1


@pytest.mark.asyncio
async def test_on_kill_phrase_callback_not_called_for_a_normal_safeword() -> None:
    calls = 0

    def on_kill() -> None:
        nonlocal calls
        calls += 1

    orch, adapter, _, _ = make_orchestrator(
        busy=False,
        safeword_phrases=["stop stop stop", "eject eject eject"],
        kill_phrase="eject eject eject",
        on_kill_phrase=on_kill,
    )
    await orch.handle_transcript("stop stop stop")
    assert calls == 0
    assert adapter.hard_stops == 1


@pytest.mark.asyncio
async def test_kill_phrase_without_a_callback_configured_does_not_raise() -> None:
    # on_kill_phrase is optional (run_convobox.py always wires one, but
    # Orchestrator itself must not assume that) -- same "None is a safe
    # default" contract as tts/player/approval_gate elsewhere in this file.
    orch, adapter, _, _ = make_orchestrator(
        busy=False,
        safeword_phrases=["stop stop stop", "eject eject eject"],
        kill_phrase="eject eject eject",
    )
    await orch.handle_transcript("eject eject eject")
    assert adapter.force_kills == 1


@pytest.mark.asyncio
async def test_handle_transcript_starts_event_loop_automatically() -> None:
    # Regression test: is_busy() only stays fresh while _consume_events() is
    # draining adapter.events(). If a caller forgets to call
    # start_event_loop() separately, is_busy() goes stale after the first
    # send. handle_transcript must not depend on the caller remembering that.
    orch, _, _, _ = make_orchestrator(busy=False)
    assert orch._events_task is None

    await orch.handle_transcript("add a login button")

    assert orch._events_task is not None
    await orch.stop_event_loop()


@pytest.mark.asyncio
async def test_stop_event_loop_before_start_is_a_safe_noop() -> None:
    # A caller can legitimately call stop_event_loop() without ever having
    # started one (e.g. a shutdown path that runs unconditionally). Every
    # other stop_event_loop() call in this file follows a real
    # start_event_loop()/handle_transcript() -- this is the only test that
    # exercises the early-return branch for "there was never one running."
    orch, _, _, _ = make_orchestrator(busy=False)
    assert orch._events_task is None
    await orch.stop_event_loop()  # must not raise
    assert orch._events_task is None


# --- stop_event_loop()'s retry-cancel mitigation (docs/field-notes/2026-08-
# 15-opencode-freeze-*.md): a task suspended inside adapter.events()'s SSE
# read can silently fail to honor its first cancel() -- live-observed
# hanging 15-90s, never self-resolving, on the opencode backend. A stubborn
# fake task (swallows N cancellations before actually stopping) stands in
# for that live behavior; _EVENTS_TASK_CANCEL_RETRY_TIMEOUT_S is
# monkeypatched down so these don't burn real wall-clock seconds. ---


async def _stubborn_task(ignore_cancels: int) -> None:
    """Sleeps "forever," swallowing the first `ignore_cancels`
    CancelledErrors it receives (simulating a task that doesn't honor
    cancel()) before finally letting one propagate."""
    remaining = ignore_cancels
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            if remaining <= 0:
                raise
            remaining -= 1


@pytest.mark.asyncio
async def test_stop_event_loop_retries_when_the_task_ignores_the_first_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import convobox.orchestrator.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "_EVENTS_TASK_CANCEL_RETRY_TIMEOUT_S", 0.05)

    orch, _, _, _ = make_orchestrator(busy=False)
    orch._events_task = asyncio.create_task(_stubborn_task(ignore_cancels=1))

    await asyncio.wait_for(orch.stop_event_loop(), timeout=2.0)  # must not hang

    assert orch._events_task is None


@pytest.mark.asyncio
async def test_stop_event_loop_does_not_retry_when_the_first_cancel_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The common case (no stall at all) must stay on the fast path -- no
    # wasted retry-timeout wait when a single cancel() already worked.
    # Setting the retry timeout absurdly long (instead of short, the other
    # tests' approach) makes this the test that would actually go slow --
    # and therefore fail via timeout -- if a retry were wrongly attempted.
    import convobox.orchestrator.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "_EVENTS_TASK_CANCEL_RETRY_TIMEOUT_S", 30.0)

    orch, _, _, _ = make_orchestrator(busy=False)
    orch._events_task = asyncio.create_task(_stubborn_task(ignore_cancels=0))

    await asyncio.wait_for(orch.stop_event_loop(), timeout=1.0)

    assert orch._events_task is None


@pytest.mark.asyncio
async def test_stop_event_loop_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bounds an indefinite freeze, not eliminates the possibility of one
    # entirely -- a task that NEVER honors cancel must still let
    # stop_event_loop() return (with the task abandoned, not awaited
    # forever) rather than retry without limit.
    import convobox.orchestrator.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "_EVENTS_TASK_CANCEL_RETRY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(orch_mod, "_EVENTS_TASK_CANCEL_MAX_ATTEMPTS", 2)

    orch, _, _, _ = make_orchestrator(busy=False)
    stubborn = asyncio.create_task(_stubborn_task(ignore_cancels=1_000_000))
    orch._events_task = stubborn

    await asyncio.wait_for(orch.stop_event_loop(), timeout=2.0)  # must not hang

    assert orch._events_task is None
    stubborn.cancel()  # actually clean up the still-alive fake task
    with contextlib.suppress(asyncio.CancelledError):
        await stubborn


@pytest.mark.asyncio
async def test_consume_events_dispatches_real_events_through_the_task_loop() -> None:
    # Every other TEXT/TOOL_CALL/etc. dispatch test in this file calls
    # orch._on_event(...) directly -- realistic for testing _on_event's own
    # branching, but it means the ACTUAL production path (a real adapter's
    # events() async generator, drained by _consume_events()'s async-for
    # loop inside a background asyncio.Task) had never been exercised
    # end-to-end by anything in this file. This drives a real fake
    # adapter's events() through start_event_loop() exactly the way
    # handle_transcript() wires it, and confirms dispatch (TEXT -> speech,
    # TOOL_CALL -> no speech) genuinely happens via that path, not just via
    # calling the handler function directly.
    events = [
        BackendEvent(type=BackendEventType.TOOL_CALL, tool="bash"),
        BackendEvent(type=BackendEventType.TEXT, content="done."),
    ]
    adapter = FakeBackendAdapter(busy=False, events_to_yield=events)
    safeword = SafewordDetector(["stop stop stop"])
    tts = FakeTTSEngine()
    player = FakePlayer()
    orch = Orchestrator(adapter, safeword, tts=tts, player=player)

    orch.start_event_loop()
    assert orch._events_task is not None
    await orch._events_task  # the fake adapter's generator ends on its own

    assert tts.synthesized == ["done."]
    assert len(player.played) == 1
    await orch.stop_event_loop()


# --- _consume_events() resubscribes on failure instead of dying silently
# (real live incident, 2026-07-15: an uncaught httpx.ReadTimeout from
# OpenCodeAdapter.events() killed this task with no clear log line, and
# the user's own response sat unlogged for over a minute) ---


@pytest.mark.asyncio
async def test_consume_events_retries_after_an_exception_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import convobox.orchestrator.orchestrator as orch_mod

    async def fast_sleep(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(orch_mod.asyncio, "sleep", fast_sleep)

    events = [BackendEvent(type=BackendEventType.TEXT, content="recovered.")]
    adapter = FakeBackendAdapter(busy=False, events_to_yield=events)
    adapter.fail_events_calls = 1  # first call raises; second succeeds
    safeword = SafewordDetector(["stop stop stop"])
    tts = FakeTTSEngine()
    player = FakePlayer()
    orch = Orchestrator(adapter, safeword, tts=tts, player=player)

    orch.start_event_loop()
    assert orch._events_task is not None
    await orch._events_task

    assert adapter.events_call_count == 2  # failed once, retried once
    assert tts.synthesized == ["recovered."]
    await orch.stop_event_loop()


@pytest.mark.asyncio
async def test_consume_events_backoff_doubles_on_consecutive_failures_and_resets_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live-confirmed, 2026-07-29: a permanently unreachable backend.url
    # (a real misconfiguration, not the 2026-07-15 transient-timeout
    # incident this retry loop was originally built for) retried and
    # logged an identical traceback every ~1s forever -- ~90 in under 5
    # minutes. Backoff must grow across consecutive failures, and reset
    # back to the fast interval the moment a real event gets through.
    sleep_calls: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    # This module's own top-level `import asyncio` IS orchestrator.py's
    # asyncio (same module object) -- no need for a second import of
    # convobox.orchestrator.orchestrator just to reach it.
    monkeypatch.setattr(asyncio, "sleep", recording_sleep)

    events = [BackendEvent(type=BackendEventType.TEXT, content="recovered.")]
    adapter = FakeBackendAdapter(busy=False, events_to_yield=events)
    adapter.fail_events_calls = 3  # three consecutive failures, then succeeds
    safeword = SafewordDetector(["stop stop stop"])
    tts = FakeTTSEngine()
    player = FakePlayer()
    orch = Orchestrator(adapter, safeword, tts=tts, player=player)

    orch.start_event_loop()
    assert orch._events_task is not None
    await orch._events_task

    # patching asyncio.sleep globally also captures FakeBackendAdapter.
    # events()'s own internal `await asyncio.sleep(0)` yield-point (a
    # real yield, not a backoff) -- filter those 0s out, they're not
    # what this test is checking.
    backoff_calls = [s for s in sleep_calls if s != 0]
    assert backoff_calls == [1.0, 2.0, 4.0]  # doubles each consecutive failure
    assert tts.synthesized == ["recovered."]  # the event still got through
    await orch.stop_event_loop()

    # A second, later failure (simulating the backend dropping again after
    # recovering) must start back at the fast interval, not continue from
    # where the earlier streak left off.
    sleep_calls.clear()
    adapter2 = FakeBackendAdapter(busy=False, events_to_yield=events)
    adapter2.fail_events_calls = 1
    orch2 = Orchestrator(adapter2, safeword, tts=FakeTTSEngine(), player=FakePlayer())
    orch2.start_event_loop()
    assert orch2._events_task is not None
    await orch2._events_task
    assert [s for s in sleep_calls if s != 0] == [1.0]
    await orch2.stop_event_loop()


@pytest.mark.asyncio
async def test_consume_events_does_not_retry_when_it_ends_without_an_error() -> None:
    # Preserves each adapter's own lazy-respawn contract (claude_code.py/
    # codex.py's events() call _ensure_proc()/_ensure_thread() and are
    # meant to respawn on the NEXT send, not be proactively re-subscribed
    # here) -- a plain generator return must NOT trigger a retry, only a
    # genuine exception should.
    events = [BackendEvent(type=BackendEventType.TEXT, content="done.")]
    adapter = FakeBackendAdapter(busy=False, events_to_yield=events)
    safeword = SafewordDetector(["stop stop stop"])
    orch = Orchestrator(adapter, safeword)

    orch.start_event_loop()
    assert orch._events_task is not None
    await orch._events_task

    assert adapter.events_call_count == 1  # no retry after a clean end
    await orch.stop_event_loop()


@pytest.mark.asyncio
async def test_consume_events_retry_loop_is_still_cancellable() -> None:
    # A persistently-failing backend must not prevent shutdown: cancelling
    # _events_task while it's asleep between retries must still work.
    adapter = FakeBackendAdapter(busy=False)
    adapter.fail_events_calls = 1_000_000  # effectively "always fails"
    safeword = SafewordDetector(["stop stop stop"])
    orch = Orchestrator(adapter, safeword)

    orch.start_event_loop()
    await asyncio.sleep(0.05)  # let it fail at least once and start sleeping

    await asyncio.wait_for(orch.stop_event_loop(), timeout=2.0)  # must not hang

    assert orch._events_task is None


@pytest.mark.asyncio
async def test_tier_state_start_returning_no_tiers_does_not_speak() -> None:
    # split_tiers()'s own docstring guarantees it's "never empty for
    # non-whitespace input," and _on_event only ever calls
    # tier_state.start() with already-non-empty `spoken` text -- so this
    # branch (orchestrator.py's "if not spoken: return" after tiering) is
    # unreachable via any real input today. Still worth confirming the
    # defensive guard actually holds if that invariant ever changes:
    # monkeypatch the tier state directly rather than hunting for a real
    # input that can't exist.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None
    assert orch._tier_state is not None
    orch._tier_state.start = lambda full_text: ""  # type: ignore[method-assign]

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="hello"))
    await asyncio.sleep(0)

    assert orch._speak_task is None
    assert tts.synthesized == []
    assert player.played == []


@pytest.mark.asyncio
async def test_announce_after_delay_waits_before_speaking() -> None:
    # Requested for the approval flow (2026-07-20): a delayed confirmation
    # so the announcement doesn't land right as a just-approved tool call
    # starts running.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch.announce_after_delay("Approval confirmed.", 0.05)
    assert tts.synthesized == []  # not yet -- still within the delay

    assert orch._speak_task is not None
    await orch._speak_task
    assert tts.synthesized == ["Approval confirmed."]


@pytest.mark.asyncio
async def test_announce_after_delay_is_a_noop_without_tts() -> None:
    orch, _, _, _ = make_orchestrator(busy=False, with_tts=False)
    orch.announce_after_delay("Approval confirmed.", 0.05)
    assert orch._speak_task is None


@pytest.mark.asyncio
async def test_announce_after_delay_replaces_any_pending_speech() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="first response"))
    orch.announce_after_delay("Approval confirmed.", 0.05)
    assert orch._speak_task is not None
    await orch._speak_task

    # The pre-empted first announcement never got to synthesize anything --
    # replaced before it ran, not spoken then talked over.
    assert tts.synthesized == ["Approval confirmed."]


@pytest.mark.asyncio
async def test_text_event_synthesizes_and_plays_stripped_speech() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="Run `pytest` please.")
    )
    assert orch._speak_task is not None
    await orch._speak_task

    assert tts.synthesized == [strip_code_for_speech("Run `pytest` please.")]
    assert len(player.played) == 1
    samples, sample_rate = player.played[0]
    assert sample_rate == tts.sample_rate
    assert samples.shape == (4,)


@pytest.mark.asyncio
async def test_non_text_event_does_not_trigger_speech() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TOOL_CALL, tool="bash"))
    await asyncio.sleep(0)

    assert orch._speak_task is None
    assert tts.synthesized == []
    assert player.played == []


@pytest.mark.asyncio
async def test_text_event_without_tts_configured_is_noop() -> None:
    orch, _, _, _ = make_orchestrator(busy=False)  # no tts/player

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="hello"))
    await asyncio.sleep(0)

    assert orch._speak_task is None


@pytest.mark.asyncio
async def test_on_event_hook_sees_every_event_type() -> None:
    # The observer hook exists so a caller (e.g. a live TUI) can see the
    # real backend activity -- not just TEXT (which _on_event itself acts
    # on for speech), but TOOL_CALL/TOOL_RESULT/DONE/ERROR too.
    seen: list[BackendEvent] = []
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, on_event=seen.append)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TOOL_CALL, tool="bash"))
    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="hello"))
    await asyncio.sleep(0)

    assert [e.type for e in seen] == [BackendEventType.TOOL_CALL, BackendEventType.TEXT]
    # And the hook doesn't interfere with the existing TTS behavior.
    assert tts.synthesized == ["hello"]


@pytest.mark.asyncio
async def test_on_event_hook_none_is_a_noop() -> None:
    # Default construction (no on_event passed) must behave exactly as
    # before this hook existed.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="hello"))
    await asyncio.sleep(0)

    assert tts.synthesized == ["hello"]


@pytest.mark.asyncio
async def test_on_event_hook_exception_does_not_break_speech_or_crash() -> None:
    # A buggy observer must not take down event consumption (is_busy()
    # staleness) or block the core TTS/speech responsibility it's just
    # observing, not gating.
    def broken_hook(event: BackendEvent) -> None:
        raise RuntimeError("observer bug")

    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, on_event=broken_hook)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="hello"))
    assert orch._speak_task is not None
    await orch._speak_task

    assert tts.synthesized == ["hello"]
    assert len(player.played) == 1


# --- response tiering (docs/DESIGN-0.3.0-interaction-and-safety.md, Phase 2) ---


@pytest.mark.asyncio
async def test_tiering_disabled_by_default_speaks_full_text() -> None:
    # No behavior change for existing callers: tier_responses defaults to
    # False, so a multi-paragraph response is still spoken in full.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="first paragraph.\n\nsecond paragraph.")
    )
    await orch._speak_task

    assert tts.synthesized == ["first paragraph.\n\nsecond paragraph."]


@pytest.mark.asyncio
async def test_tiering_enabled_speaks_only_first_tier() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="first paragraph.\n\nsecond paragraph.")
    )
    await orch._speak_task

    assert tts.synthesized == ["first paragraph."]


@pytest.mark.asyncio
async def test_tiering_single_paragraph_response_speaks_the_whole_thing() -> None:
    # The common case: nothing held back for a short reply.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="just one short reply."))
    await orch._speak_task

    assert tts.synthesized == ["just one short reply."]
    assert orch.has_more_to_reveal() is False


# --- _speak()'s TTS/playback failure handling, 2026-07-29 -- confirmed live
# that a synthesis failure mid-response (kokoro-onnx's own ~510-phoneme
# batch-limit crash, surfaced by KokoroTTSEngine as a RuntimeError after its
# 30s timeout) previously vanished completely: _speak_task is a bare
# fire-and-forget asyncio.create_task() with nothing ever awaiting or
# checking it, so an uncaught exception there was silently lost -- no log,
# no UI signal, just an unexplained gap where the rest of the response
# should have been spoken. ---


@pytest.mark.asyncio
async def test_speak_failure_is_caught_and_surfaced_as_an_error_event() -> None:
    events: list[BackendEvent] = []
    adapter = FakeBackendAdapter(busy=False)
    safeword = SafewordDetector(["stop stop stop"])
    tts = FakeTTSEngine(fail_with=RuntimeError("Kokoro synthesis stalled"))
    player = FakePlayer()
    orch = Orchestrator(
        adapter, safeword, tts=tts, player=player, on_event=events.append,
    )

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="first paragraph.\n\nsecond paragraph."))
    # Must not raise -- this is the actual regression: the exception used
    # to propagate nowhere (task never awaited) rather than raise here, but
    # asserting a clean await is still the right proof the task itself
    # completed instead of being left in a permanently-failed, unretrieved
    # state.
    await orch._speak_task

    error_events = [e for e in events if e.type == BackendEventType.ERROR]
    assert len(error_events) == 1
    assert "Kokoro synthesis stalled" in (error_events[0].content or "")


@pytest.mark.asyncio
async def test_has_more_to_reveal_true_after_multi_paragraph_response() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None
    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="first.\n\nsecond.\n\nthird.")
    )
    await orch._speak_task
    assert orch.has_more_to_reveal() is True


@pytest.mark.asyncio
async def test_has_more_to_reveal_false_when_tiering_disabled() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=False)
    assert tts is not None and player is not None
    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="first.\n\nsecond."))
    await orch._speak_task
    assert orch.has_more_to_reveal() is False


def test_has_more_to_reveal_false_before_any_response() -> None:
    orch, _, _, _ = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert orch.has_more_to_reveal() is False


@pytest.mark.asyncio
async def test_speak_more_speaks_the_next_tier_and_returns_true() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="first.\n\nsecond.\n\nthird.")
    )
    await orch._speak_task
    assert tts.synthesized == ["first."]

    said_more = await orch.speak_more()
    assert said_more is True
    await orch._speak_task
    assert tts.synthesized == ["first.", "second."]


@pytest.mark.asyncio
async def test_speak_more_returns_false_once_nothing_left() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="first.\n\nsecond."))
    await orch._speak_task
    assert await orch.speak_more() is True
    await orch._speak_task

    assert await orch.speak_more() is False
    assert tts.synthesized == ["first.", "second."]


@pytest.mark.asyncio
async def test_speak_more_returns_false_when_tiering_disabled() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=False)
    assert tts is not None and player is not None

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="first.\n\nsecond."))
    await orch._speak_task

    assert await orch.speak_more() is False


@pytest.mark.asyncio
async def test_speak_more_returns_false_without_tts_configured() -> None:
    orch, _, _, _ = make_orchestrator(busy=False, with_tts=False, tier_responses=True)
    assert await orch.speak_more() is False


@pytest.mark.asyncio
async def test_a_new_response_replaces_the_previous_ones_remaining_tiers() -> None:
    # An old response's held-back tiers are moot once a new one arrives --
    # same principle as the TUI's full-detail pane resetting per-turn.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="old first.\n\nold second.")
    )
    await orch._speak_task
    assert orch.has_more_to_reveal() is True

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="new reply, single paragraph."))
    await orch._speak_task

    assert tts.synthesized == ["old first.", "new reply, single paragraph."]
    assert orch.has_more_to_reveal() is False


@pytest.mark.asyncio
async def test_second_text_event_cancels_the_first_speak_task_before_it_completes() -> None:
    # Real bug, live-confirmed 2026-07-14: a single backend turn can emit
    # MULTIPLE TEXT events (tool-calling responses interleave text with
    # tool work -- "let me check that file" ... [tool work] ... "found
    # it, fixing now"). Before this fix, _speak_task was silently
    # overwritten here without cancelling whatever the PREVIOUS TEXT
    # segment's task was still doing -- letting it keep synthesizing (and,
    # via EchoAwarePlayer's playback_ended_at tracking in
    # scripts/run_convobox.py, keep advancing a shared "when did playback
    # end" timestamp) for audio nobody ever actually heard, since
    # AudioPlayer.play_stream() already replaces the underlying audio
    # thread/stream regardless. Observed live as an entire multi-minute
    # UAT session where nearly every utterance got dropped by the overlap
    # gate as "echo" (see docs/KNOWN-ISSUES.md).
    adapter = FakeBackendAdapter(busy=False)
    safeword = SafewordDetector(["stop stop stop"])
    release_first = asyncio.Event()

    class SlowFirstSegmentTTSEngine(FakeTTSEngine):
        async def synthesize_stream(self, text: str):  # type: ignore[override]
            if text == "first segment":
                # Blocks here until the test lets it go -- simulates a
                # still-synthesizing first segment when the second
                # segment's TEXT event arrives, the exact race that
                # corrupted playback_ended_at live.
                await release_first.wait()
            self.synthesized.append(text)
            yield np.ones(4, dtype=np.float32)

    tts = SlowFirstSegmentTTSEngine()
    player = FakePlayer()
    orch = Orchestrator(adapter, safeword, tts=tts, player=player)

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="first segment"))
    first_task = orch._speak_task
    assert first_task is not None
    await asyncio.sleep(0)  # let it actually start and reach the blocking await

    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content="second segment"))
    second_task = orch._speak_task
    assert second_task is not None and second_task is not first_task

    await second_task
    with pytest.raises(asyncio.CancelledError):
        await first_task

    assert first_task.cancelled()
    # "first segment" never finished synthesizing -- it never actually
    # played, so it must never be counted as spoken.
    assert tts.synthesized == ["second segment"]
    assert len(player.played) == 1


@pytest.mark.asyncio
async def test_tiering_tiers_the_stripped_text_not_raw_markdown() -> None:
    # split_tiers() expects "\n\n"-separated paragraphs; strip_code_for_speech
    # already collapses 3+ newlines down to exactly that, so tiering must
    # run AFTER stripping, not on the raw event content.
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, tier_responses=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(
            type=BackendEventType.TEXT,
            content="**first** paragraph.\n\n\n\n*second* paragraph.",
        )
    )
    await orch._speak_task

    assert tts.synthesized == ["first paragraph."]


@pytest.mark.asyncio
async def test_on_event_hook_sees_full_untiered_text() -> None:
    # The TUI's full-detail pane (docs/DESIGN-0.3.0-interaction-and-safety.md:
    # "The TUI always shows the full, untruncated response") must never be
    # affected by tiering -- the observer hook fires with the raw event
    # before any tiering happens.
    seen: list[BackendEvent] = []
    orch, _, tts, player = make_orchestrator(
        busy=False, with_tts=True, on_event=seen.append, tier_responses=True
    )
    assert tts is not None and player is not None

    full_text = "first paragraph.\n\nsecond paragraph."
    orch._on_event(BackendEvent(type=BackendEventType.TEXT, content=full_text))
    await orch._speak_task

    assert seen[0].content == full_text
    assert tts.synthesized == ["first paragraph."]


@pytest.mark.asyncio
async def test_text_event_that_is_pure_code_does_not_speak() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(type=BackendEventType.TEXT, content="```python\nx = 1\n```")
    )
    await asyncio.sleep(0)

    assert orch._speak_task is None
    assert tts.synthesized == []


@pytest.mark.asyncio
async def test_hard_stop_stops_tts_and_player() -> None:
    orch, adapter, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    await orch.handle_transcript("stop stop stop")

    assert adapter.hard_stops == 1
    assert player.stop_calls == 1
    assert tts.stop_calls == 1
    await orch.stop_event_loop()


@pytest.mark.asyncio
async def test_hard_stop_suppresses_a_trailing_response_from_before_the_stop() -> None:
    # Live UAT, 2026-07-31 (docs/UAT-checklist.md [P1]/[P5],
    # docs/KNOWN-ISSUES.md): a response was heard 1-10+ seconds after a
    # pause/hard-stop was logged, with no resume in between. Root cause,
    # confirmed in every adapter's own send_hard_stop() comments (e.g.
    # claude_code.py: "the in-flight turn's own terminal result DOES
    # still arrive"): hard-stop only resets the local busy counter -- it
    # never stopped _consume_events() from reading and speaking whatever
    # trailing TEXT event the backend still delivered for the turn that
    # was already in flight when hard-stop fired. Reproduces that exact
    # race: a TEXT event arriving on the adapter's stream AFTER hard-stop
    # was requested must never be spoken.
    gate = asyncio.Event()

    class GatedAdapter(BackendAdapter):
        def __init__(self) -> None:
            self.hard_stops = 0

        async def send_text(self, text: str) -> None:
            pass

        async def send_interject(self, text: str) -> None:
            pass

        async def send_hard_stop(self) -> None:
            self.hard_stops += 1

        def is_busy(self) -> bool:
            return False

        async def events(self) -> AsyncGenerator[BackendEvent, None]:
            yield BackendEvent(type=BackendEventType.TOOL_CALL, tool="bash")
            # Parks here until the test releases it, simulating the
            # backend's trailing result arriving late -- exactly the
            # window every adapter's send_hard_stop() comment describes.
            await gate.wait()
            yield BackendEvent(
                type=BackendEventType.TEXT,
                content="the response that should never be heard",
            )

    adapter = GatedAdapter()
    safeword = SafewordDetector(["stop stop stop"])
    tts = FakeTTSEngine()
    player = FakePlayer()
    orch = Orchestrator(adapter, safeword, tts=tts, player=player)

    orch.start_event_loop()
    await asyncio.sleep(0)  # TOOL_CALL processed; task now parked on gate.wait()

    await orch.handle_transcript("stop stop stop")
    assert adapter.hard_stops == 1

    gate.set()  # the "trailing result" finally arrives, AFTER hard-stop
    await asyncio.sleep(0.05)  # give a misbehaving events task a chance to speak it

    assert tts.synthesized == []
    assert player.played == []


def test_strip_code_for_speech_removes_fenced_block() -> None:
    text = "Here is the fix:\n```python\nprint('hi')\n```\nThat should work."
    result = strip_code_for_speech(text)
    assert "print" not in result
    assert "Here is the fix:" in result
    assert "That should work." in result


def test_strip_code_for_speech_removes_inline_span() -> None:
    result = strip_code_for_speech("Call the `run()` function to start.")
    assert "run()" not in result
    assert "Call the" in result
    assert "function to start." in result


# --- markdown decoration stripping (UAT: Piper spoke "asterisk asterisk") ---


def test_strip_removes_bold_and_italic_asterisks() -> None:
    assert strip_code_for_speech("**important** and *quick* note") == "important and quick note"


def test_strip_removes_bullet_asterisks_and_dashes() -> None:
    assert (
        strip_code_for_speech("* first thing\n- second thing\n+ third thing")
        == "first thing\nsecond thing\nthird thing"
    )


def test_strip_removes_heading_and_quote_markers() -> None:
    assert strip_code_for_speech("## Summary\n> quoted advice") == "Summary\nquoted advice"


def test_strip_speaks_link_text_not_url() -> None:
    assert (
        strip_code_for_speech("see [the docs](https://example.com/x?y=1) for details")
        == "see the docs for details"
    )


def test_strip_removes_underscore_emphasis() -> None:
    assert strip_code_for_speech("this is _really_ important") == "this is really important"


def test_strip_keeps_snake_case_identifiers() -> None:
    assert strip_code_for_speech("run my_func_name now") == "run my_func_name now"


def test_strip_keeps_slashes_and_paths() -> None:
    # UAT decision: slashes read fine; paths must survive untouched.
    assert strip_code_for_speech("edit src/convobox/config.py") == "edit src/convobox/config.py"


def test_strip_full_markdown_response_sounds_like_prose() -> None:
    text = (
        "## Plan\n"
        "1. **Read** the [config](docs/config.md) file\n"
        "2. Run `pytest` with *coverage*\n"
        "```python\nprint('never spoken')\n```\n"
        "Done."
    )
    result = strip_code_for_speech(text)
    assert "*" not in result and "#" not in result and "`" not in result
    assert "[" not in result and "(" not in result.replace("(docs", "")  # no link syntax
    assert "never spoken" not in result
    assert "Read the config file" in result
    assert "Done." in result


# --- backend interactive questions (docs/DESIGN-backend-questions.md,
# slice 1: announce; UAT finding [L9]) ---

_QUESTION_INPUT = (
    '{"questions": [{"question": "What kind of testing do you want?", '
    '"header": "Test scope", "multiple": false, "options": ['
    '{"label": "Automated unit tests", "description": "Run pytest."}, '
    '{"label": "Live hardware UAT", "description": "Real mic and speakers."}]}]}'
)


def test_render_question_speaks_question_and_numbered_labels() -> None:
    spoken = render_question_for_speech(_QUESTION_INPUT)
    assert spoken == (
        "The agent is asking: What kind of testing do you want? "
        "Option 1: Automated unit tests. Option 2: Live hardware UAT."
    )
    # Descriptions deliberately not spoken (kept short enough to answer).
    assert "pytest" not in spoken


def test_render_question_without_options_speaks_question_alone() -> None:
    spoken = render_question_for_speech(
        '{"questions": [{"question": "Proceed?", "options": []}]}'
    )
    assert spoken == "The agent is asking: Proceed?"


def test_render_question_handles_malformed_input() -> None:
    # Malformed input must never crash event consumption ([L9] happened
    # mid-session; an exception here would kill the event task).
    assert render_question_for_speech(None) is None
    assert render_question_for_speech("") is None
    assert render_question_for_speech("not json") is None
    assert render_question_for_speech('{"questions": "nope"}') is None
    assert render_question_for_speech('{"questions": [{"options": []}]}') is None
    # Valid JSON, but the top level isn't an object at all.
    assert render_question_for_speech("[1, 2, 3]") is None
    # A non-dict entry inside "questions" is skipped, not a crash.
    assert render_question_for_speech('{"questions": ["not a dict"]}') is None
    # A non-dict entry inside "options" is skipped; the question itself
    # still speaks (its options list just ends up empty).
    assert render_question_for_speech(
        '{"questions": [{"question": "Proceed?", "options": ["not a dict"]}]}'
    ) == "The agent is asking: Proceed?"


# --- voice-gated tool approval (Phase 3,
# docs/DESIGN-0.3.0-interaction-and-safety.md; mechanism in
# claude_code.py) ---


def test_render_approval_request_speaks_tool_and_phrase() -> None:
    # Deliberately does NOT read tool_input/command content aloud (an
    # earlier version did) -- commands can be long, misleading out of
    # context, or contain sensitive values. The TUI/log warning carries
    # the actual detail; speech only announces the high-stakes state.
    spoken = render_approval_request_for_speech("Bash", "alpha bravo delta")
    assert spoken == "Approval needed to run Bash. Say alpha bravo delta to approve, or say no to deny."


def test_render_approval_request_without_a_tool_name_says_a_tool() -> None:
    spoken = render_approval_request_for_speech(None, "alpha bravo delta")
    assert spoken.startswith("Approval needed to run a tool.")


def test_render_approval_request_without_a_phrase_uses_a_generic_fallback() -> None:
    # Shouldn't normally happen (ApprovalDetector requires a phrase to
    # construct) but must still produce a sensible sentence, not crash.
    spoken = render_approval_request_for_speech("Bash", None)
    assert spoken == "Approval needed to run Bash. Say your approval phrase to approve, or say no to deny."


@pytest.mark.asyncio
async def test_approval_request_event_is_announced_via_tts() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True, approval_phrase="alpha bravo delta")
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(
            type=BackendEventType.APPROVAL_REQUEST,
            tool="Bash",
            tool_input='{"command": "rm -rf build"}',
        )
    )
    assert orch._speak_task is not None
    await orch._speak_task

    assert len(tts.synthesized) == 1
    assert tts.synthesized[0] == (
        "Approval needed to run Bash. Say alpha bravo delta to approve, or say no to deny."
    )
    # The command itself is never read aloud -- see the function's own docstring.
    assert "rm -rf" not in tts.synthesized[0]
    assert len(player.played) == 1


@pytest.mark.asyncio
async def test_approval_request_event_without_tts_does_not_crash() -> None:
    orch, _, _, _ = make_orchestrator(busy=False)  # no tts/player

    orch._on_event(BackendEvent(type=BackendEventType.APPROVAL_REQUEST, tool="Bash"))
    await asyncio.sleep(0)

    assert orch._speak_task is None


@pytest.mark.asyncio
async def test_resolve_pending_approval_delegates_to_the_adapter() -> None:
    orch, adapter, _, _ = make_orchestrator(busy=False)

    assert await orch.resolve_pending_approval(True) is True
    assert adapter.resolved_approvals == [True]

    adapter.resolve_pending_approval_returns = False
    assert await orch.resolve_pending_approval(False) is False
    assert adapter.resolved_approvals == [True, False]


@pytest.mark.asyncio
async def test_question_tool_call_is_announced_via_tts() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(
            type=BackendEventType.TOOL_CALL,
            tool="question",
            tool_input=_QUESTION_INPUT,
        )
    )
    assert orch._speak_task is not None
    await orch._speak_task

    assert len(tts.synthesized) == 1
    assert tts.synthesized[0].startswith("The agent is asking:")
    assert len(player.played) == 1


@pytest.mark.asyncio
async def test_question_tool_call_without_tts_does_not_crash() -> None:
    orch, _, _, _ = make_orchestrator(busy=False)  # no tts/player

    orch._on_event(
        BackendEvent(
            type=BackendEventType.TOOL_CALL,
            tool="question",
            tool_input=_QUESTION_INPUT,
        )
    )
    await asyncio.sleep(0)

    assert orch._speak_task is None


@pytest.mark.asyncio
async def test_question_tool_call_with_malformed_input_stays_silent() -> None:
    orch, _, tts, player = make_orchestrator(busy=False, with_tts=True)
    assert tts is not None and player is not None

    orch._on_event(
        BackendEvent(
            type=BackendEventType.TOOL_CALL, tool="question", tool_input="not json"
        )
    )
    await asyncio.sleep(0)

    assert orch._speak_task is None
    assert tts.synthesized == []
