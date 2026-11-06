"""Spoken summaries of long replies (src/convobox/summary, and the Orchestrator
path that uses them). The model call is faked everywhere except the
ClaudeCliSummarizer subprocess tests, which use a stub executable standing in
for `claude` -- no network, no login. Whether the real `claude` produces a good
summary is a live check, not something these tests claim."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from convobox.adapters.base import BackendEvent, BackendEventType
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.response_tiering import ResponseTierState
from convobox.safeword.detector import SafewordDetector
from convobox.summary import ClaudeCliSummarizer, finalize_summary, word_count
from tests.test_orchestrator import FakeBackendAdapter, FakePlayer, FakeTTSEngine

LONG = " ".join(["word"] * 100) + "."


# --- finalize_summary: the safeguards that do not trust the model ---------


def test_finalize_cleans_whitespace_and_caps_length() -> None:
    out = finalize_summary("  one\n two   three  ", "src")
    assert out == "one two three"
    capped = finalize_summary("word " * 400, "src")
    assert capped is not None and len(capped) <= 604 and capped.endswith("...")


def test_finalize_rejects_empty() -> None:
    assert finalize_summary(None, "src") is None
    assert finalize_summary("   ", "src") is None


def test_finalize_refuses_a_summary_that_hides_a_failure() -> None:
    source = "I ran the suite. Two tests failed with a timeout. Want me to retry?"
    assert finalize_summary("Everything looks fine now.", source) is None
    kept = finalize_summary("Two tests failed on a timeout.", source)
    assert kept is not None and "failed" in kept


def test_finalize_ignores_failure_words_when_the_source_has_none() -> None:
    assert finalize_summary("All good.", "I renamed the variable and updated the docs.") == "All good."


def test_finalize_appends_the_closing_question_verbatim_when_dropped() -> None:
    source = "I changed the parser and added a test. Should I also update the changelog?"
    out = finalize_summary("The parser is fixed and tested.", source)
    assert out == "The parser is fixed and tested. Should I also update the changelog?"


def test_finalize_does_not_duplicate_a_question_the_summary_kept() -> None:
    source = "Done with the parser. Should I also update the changelog?"
    out = finalize_summary("Parser done. Should I also update the changelog?", source)
    assert out is not None and out.count("changelog") == 1


def test_word_count() -> None:
    assert word_count("a b  c\nd") == 4
    assert word_count("") == 0


# --- ResponseTierState.start_summarized ------------------------------------


def test_summarized_tiers_speak_the_summary_then_the_full_reply_in_order() -> None:
    state = ResponseTierState()
    assert state.start_summarized("short", "para one\n\npara two") == "short"
    assert state.has_more()
    assert state.reveal_more() == "para one"
    assert state.reveal_more() == "para two"
    assert state.reveal_more() is None


# --- ClaudeCliSummarizer against a stub `claude` ---------------------------


def _stub(tmp_path: Path, body: str) -> str:
    path = tmp_path / "claude"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


async def test_cli_summarizer_passes_text_on_stdin_and_locks_down_the_call(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.txt"
    stdin_log = tmp_path / "stdin.txt"
    cmd = _stub(tmp_path, f"""
        import sys
        open({str(argv_log)!r}, "w").write("\\n".join(sys.argv[1:]))
        open({str(stdin_log)!r}, "w").write(sys.stdin.read())
        print("A short summary.")
    """)
    out = await ClaudeCliSummarizer(command=cmd, timeout_s=10).summarize("SECRET-REPLY-TEXT")
    assert out == "A short summary."
    argv = argv_log.read_text().split("\n")
    assert "SECRET-REPLY-TEXT" not in argv  # untrusted text never on argv
    assert "SECRET-REPLY-TEXT" in stdin_log.read_text()
    assert argv[argv.index("--tools") + 1] == ""  # every tool disabled
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in argv


async def test_cli_summarizer_returns_none_on_nonzero_exit(tmp_path: Path) -> None:
    cmd = _stub(tmp_path, "import sys\nprint('Not logged in')\nsys.exit(1)\n")
    assert await ClaudeCliSummarizer(command=cmd).summarize("x") is None


async def test_cli_summarizer_returns_none_for_a_missing_binary(tmp_path: Path) -> None:
    assert await ClaudeCliSummarizer(command=str(tmp_path / "nope")).summarize("x") is None


async def test_cli_summarizer_times_out_and_kills_the_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    cmd = _stub(tmp_path, f"""
        import os, time
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        time.sleep(30)
    """)
    out = await ClaudeCliSummarizer(command=cmd, timeout_s=0.5).summarize("x")
    assert out is None
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # reaped, not left running


async def test_cli_summarizer_cancellation_kills_the_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    cmd = _stub(tmp_path, f"""
        import os, time
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        time.sleep(30)
    """)
    task = asyncio.create_task(ClaudeCliSummarizer(command=cmd, timeout_s=20).summarize("x"))
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    task.cancel()  # what a stop phrase does to the speak task
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


# --- Orchestrator integration ----------------------------------------------


class FakeSummarizer:
    def __init__(self, result: str | None = "Short summary.", delay: float = 0.0,
                 raises: Exception | None = None) -> None:
        self.result, self.delay, self.raises = result, delay, raises
        self.calls: list[str] = []

    async def summarize(self, text: str) -> str | None:
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.result


def make_orch(summarizer: FakeSummarizer | None, tier_responses: bool = False,
              min_words: int = 50):
    adapter = FakeBackendAdapter(busy=False)
    tts, player = FakeTTSEngine(), FakePlayer()
    orch = Orchestrator(
        adapter, SafewordDetector(["stop stop stop"]), tts=tts, player=player,
        tier_responses=tier_responses, summarizer=summarizer, summary_min_words=min_words,
    )
    return orch, tts


def text_event(text: str) -> BackendEvent:
    return BackendEvent(type=BackendEventType.TEXT, content=text)


async def settle(orch: Orchestrator) -> None:
    await asyncio.sleep(0)
    if orch._speak_task is not None:
        await orch._speak_task


async def test_a_long_reply_is_spoken_as_its_summary() -> None:
    summ = FakeSummarizer("Short summary.")
    orch, tts = make_orch(summ)
    orch._on_event(text_event(LONG))
    await settle(orch)
    assert tts.synthesized == ["Short summary."]
    assert summ.calls == [LONG]
    assert orch.has_more_to_reveal()  # the full reply is still reachable by voice


async def test_continue_after_a_summary_speaks_the_full_reply() -> None:
    orch, tts = make_orch(FakeSummarizer("Short summary."))
    orch._on_event(text_event(LONG))
    await settle(orch)
    assert await orch.speak_more()
    await settle(orch)
    assert tts.synthesized == ["Short summary.", LONG]
    assert not orch.has_more_to_reveal()


async def test_a_short_reply_is_not_summarized_and_nothing_is_held_back() -> None:
    summ = FakeSummarizer()
    orch, tts = make_orch(summ)
    orch._on_event(text_event("Done. All set."))
    await settle(orch)
    assert summ.calls == []
    assert tts.synthesized == ["Done. All set."]
    assert not orch.has_more_to_reveal()


@pytest.mark.parametrize("result,raises", [(None, None), ("", None), (None, RuntimeError("boom"))])
async def test_failure_falls_back_to_speaking_the_full_reply(result, raises) -> None:
    orch, tts = make_orch(FakeSummarizer(result, raises=raises))
    orch._on_event(text_event(LONG))
    await settle(orch)
    assert tts.synthesized == [LONG]
    assert not orch.has_more_to_reveal()


async def test_failure_with_tiering_falls_back_to_the_tiered_first_paragraph() -> None:
    reply = LONG + "\n\n" + LONG
    orch, tts = make_orch(FakeSummarizer(None), tier_responses=True)
    orch._on_event(text_event(reply))
    await settle(orch)
    assert tts.synthesized == [LONG]
    assert orch.has_more_to_reveal()


async def test_a_summary_that_would_hide_a_failure_is_not_spoken() -> None:
    failing = LONG + " Two tests failed with a timeout."
    orch, tts = make_orch(FakeSummarizer("Everything is fine."))
    orch._on_event(text_event(failing))
    await settle(orch)
    assert tts.synthesized == [failing]


async def test_nothing_stale_can_be_continued_while_a_summary_is_pending() -> None:
    orch, _tts = make_orch(FakeSummarizer("Second summary.", delay=0.2))
    orch._tier_state.start_summarized("old", "old para one\n\nold para two")  # type: ignore[union-attr]
    assert orch.has_more_to_reveal()
    orch._on_event(text_event(LONG))
    assert not orch.has_more_to_reveal()  # cleared immediately, before the model returns
    assert not await orch.speak_more()
    await settle(orch)


async def test_a_newer_reply_cancels_the_pending_summary() -> None:
    first = FakeSummarizer("First summary.", delay=5)
    orch, tts = make_orch(first)
    orch._on_event(text_event(LONG))
    await asyncio.sleep(0.05)
    first_task = orch._speak_task
    orch._on_event(text_event("Quick note."))  # short: speaks directly
    await settle(orch)
    assert first_task is not None and first_task.cancelled()
    assert tts.synthesized == ["Quick note."]


async def test_without_a_summarizer_behaviour_is_unchanged() -> None:
    orch, tts = make_orch(None)
    assert orch._tier_state is None
    orch._on_event(text_event(LONG))
    await settle(orch)
    assert tts.synthesized == [LONG]


async def test_a_second_long_reply_cancels_the_first_pending_summary() -> None:
    class Sequenced:
        def __init__(self) -> None:
            self.calls = 0

        async def summarize(self, text: str) -> str | None:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(5)  # the slow first summary
                return "First summary."
            return "Second summary."

    orch, tts = make_orch(Sequenced())  # type: ignore[arg-type]
    orch._on_event(text_event(LONG))
    await asyncio.sleep(0.05)
    first_task = orch._speak_task
    orch._on_event(text_event(LONG))
    await settle(orch)
    assert first_task is not None and first_task.cancelled()
    assert tts.synthesized == ["Second summary."]  # the stale one never speaks


def test_finalize_does_not_ask_twice_when_the_summary_already_asks() -> None:
    source = "The loader is slow. Which of the two would you prefer I go with?"
    out = finalize_summary("The loader is slow, so which do you want?", source)
    assert out == "The loader is slow, so which do you want?"
