from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from convobox.stt.base import STTEngine, TranscriptResult
from scripts.run_convobox import _transcribe_with_timeout

# --- _transcribe_with_timeout: offloads transcriber.transcribe() to a
# thread (so a slow/stuck call can't freeze the caller's event loop) with
# an optional timeout that abandons and invalidates the engine instead of
# waiting forever. Live-hit 2026-08-06, see docs/field-notes/
# 2026-08-06-resume-word-hallucination-and-runaway-repetition.md.


class _FastEngine(STTEngine):
    """Returns immediately -- exercises the plain-success path."""

    def __init__(self) -> None:
        self.invalidated = False

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        return TranscriptResult(
            text="hello", language="en", language_probability=0.9,
            latency_ms=1.0, duration_s=1.0, avg_logprob=-0.1,
        )

    def invalidate(self) -> None:
        self.invalidated = True


class _SlowEngine(STTEngine):
    """Blocks (a plain time.sleep, same as a real synchronous native
    call would) for longer than the test's configured timeout."""

    def __init__(self, sleep_s: float) -> None:
        self.sleep_s = sleep_s
        self.invalidated = False
        self.completed = False

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        time.sleep(self.sleep_s)
        self.completed = True
        return TranscriptResult(
            text="too late", language="en", language_probability=0.9,
            latency_ms=1.0, duration_s=1.0, avg_logprob=-0.1,
        )

    def invalidate(self) -> None:
        self.invalidated = True


@pytest.mark.asyncio
async def test_returns_the_result_on_a_normal_fast_call() -> None:
    engine = _FastEngine()
    result = await _transcribe_with_timeout(engine, np.zeros(16000, dtype=np.float32), None)
    assert result is not None
    assert result.text == "hello"
    assert engine.invalidated is False


@pytest.mark.asyncio
async def test_no_timeout_configured_waits_indefinitely_for_a_slow_call() -> None:
    # timeout_s=None must behave exactly like a plain await -- no
    # behavior change for anyone who hasn't opted into the new field.
    engine = _SlowEngine(sleep_s=0.3)
    result = await _transcribe_with_timeout(engine, np.zeros(16000, dtype=np.float32), None)
    assert result is not None
    assert result.text == "too late"
    assert engine.invalidated is False


@pytest.mark.asyncio
async def test_timeout_abandons_the_call_and_invalidates_the_engine() -> None:
    engine = _SlowEngine(sleep_s=0.5)
    start = time.monotonic()
    result = await _transcribe_with_timeout(engine, np.zeros(16000, dtype=np.float32), 0.05)
    elapsed = time.monotonic() - start

    assert result is None
    assert engine.invalidated is True
    # The caller gets control back near the timeout, not after the full
    # (much longer) real call eventually finishes.
    assert elapsed < 0.4


@pytest.mark.asyncio
async def test_timeout_does_not_block_other_concurrent_work() -> None:
    # The whole point of the thread offload: a stuck/slow call must not
    # freeze the event loop for anything else running concurrently (the
    # heartbeat watchdog, SSE broadcasts, TUI redraw, ...).
    engine = _SlowEngine(sleep_s=2.0)
    ticks = []

    async def ticker() -> None:
        for _ in range(3):
            await asyncio.sleep(0.02)
            ticks.append(time.monotonic())

    await asyncio.gather(
        _transcribe_with_timeout(engine, np.zeros(16000, dtype=np.float32), 0.05),
        ticker(),
    )
    assert len(ticks) == 3


@pytest.mark.asyncio
async def test_a_slow_call_that_eventually_completes_does_not_affect_the_timed_out_result() -> None:
    # The abandoned background thread is allowed to keep running (Python
    # can't kill it) -- confirm its eventual completion doesn't somehow
    # surface later or raise into the caller.
    engine = _SlowEngine(sleep_s=0.15)
    result = await _transcribe_with_timeout(engine, np.zeros(16000, dtype=np.float32), 0.02)
    assert result is None
    assert engine.completed is False  # hasn't finished yet at this point
    await asyncio.sleep(0.2)
    assert engine.completed is True  # finished in the background, harmlessly
