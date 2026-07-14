from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable

import numpy as np
import pytest

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.audio.playback import AudioPlayer
from convobox.orchestrator.orchestrator import Orchestrator, strip_code_for_speech
from convobox.safeword.detector import SafewordDetector
from convobox.tts.base import TTSEngine


class FakeBackendAdapter(BackendAdapter):
    def __init__(self, busy: bool = False) -> None:
        self._busy = busy
        self.sent_text: list[str] = []
        self.sent_interject: list[str] = []
        self.hard_stops = 0

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_interject(self, text: str) -> None:
        self.sent_interject.append(text)

    async def send_hard_stop(self) -> None:
        self.hard_stops += 1

    def is_busy(self) -> bool:
        return self._busy

    async def events(self) -> AsyncGenerator[BackendEvent, None]:  # pragma: no cover
        return
        yield


class FakeTTSEngine(TTSEngine):
    def __init__(self) -> None:
        self.synthesized: list[str] = []
        self.stop_calls = 0
        self._speaking = False

    @property
    def sample_rate(self) -> int:
        return 16000

    async def synthesize_stream(self, text: str):
        # The recording lives here (not in synthesize) because streaming is
        # the path Orchestrator._speak actually uses now; the inherited
        # synthesize() convenience still funnels through this.
        self.synthesized.append(text)
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


def make_orchestrator(
    busy: bool,
    with_tts: bool = False,
    on_event: Callable[[BackendEvent], None] | None = None,
    tier_responses: bool = False,
) -> tuple[Orchestrator, FakeBackendAdapter, FakeTTSEngine | None, FakePlayer | None]:
    adapter = FakeBackendAdapter(busy=busy)
    safeword = SafewordDetector(["stop stop stop"])
    if not with_tts:
        return (
            Orchestrator(adapter, safeword, on_event=on_event, tier_responses=tier_responses),
            adapter,
            None,
            None,
        )
    tts = FakeTTSEngine()
    player = FakePlayer()
    return (
        Orchestrator(
            adapter, safeword, tts=tts, player=player, on_event=on_event,
            tier_responses=tier_responses,
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
