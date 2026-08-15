from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Protocol

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.audio.playback import AudioPlayer
from convobox.response_tiering import ResponseTierState
from convobox.safeword.detector import SafewordDetector
from convobox.tts.base import TTSEngine

logger = logging.getLogger(__name__)

_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Markdown link: speak the text, never the URL.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Emphasis/bullet asterisks -- heard live in UAT as Piper saying "asterisk
# asterisk" through every bold phrase in the backend's markdown.
_MD_ASTERISK_RE = re.compile(r"\*+")
# Underscore emphasis, guarded so snake_case identifiers keep their
# underscores (only strip runs not attached to word/path characters).
_MD_UNDERSCORE_RE = re.compile(r"(?<![\w/])_+|_+(?![\w/])")
# Heading markers and blockquote markers at line starts; list bullets
# ("- item", "+ item" -- "* item" is already covered by the asterisk rule).
_MD_LINE_NOISE_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-+][ \t]+)", re.MULTILINE
)
_COLLAPSE_SPACE_RE = re.compile(r"[ \t]{2,}")
_COLLAPSE_BLANK_RE = re.compile(r"\n{3,}")


def strip_code_for_speech(text: str) -> str:
    """Turn backend markdown into something worth saying out loud.

    Code is dropped entirely (nobody wants a for-loop recited); markdown
    DECORATION is stripped while the decorated words are kept. Slashes are
    deliberately untouched (paths read fine, per UAT). Literal math like
    "3 * 4" loses its operator -- acceptable collateral: the backends emit
    emphasis asterisks constantly and multiplication rarely, and a spoken
    "asterisk" is wrong in both cases anyway.
    """
    text = _FENCED_CODE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_LINE_NOISE_RE.sub("", text)
    text = _MD_ASTERISK_RE.sub("", text)
    text = _MD_UNDERSCORE_RE.sub("", text)
    text = _COLLAPSE_SPACE_RE.sub(" ", text)
    return _COLLAPSE_BLANK_RE.sub("\n\n", text).strip()


# _consume_events()'s reconnect backoff (see that method's own docstring):
# starts fast (matching the 2026-07-15 transient-timeout incident this
# retry loop exists for) and doubles on each consecutive failure with no
# event received in between, capped so a permanently unreachable backend
# doesn't retry-and-log forever at a fixed fast interval.
_RECONNECT_BACKOFF_INITIAL_S = 1.0
_RECONNECT_BACKOFF_MAX_S = 30.0

# opencode's interactive question tool (multiple-choice prompts back to the
# user). Live UAT finding [L9]: when the backend calls it, the whole voice
# session deadlocks unless the question is surfaced -- the tool blocks the
# turn, steered speech queues invisibly behind it, and nothing tells the
# user an answer is expected. Slice 1 of docs/DESIGN-backend-questions.md:
# announce it; answering by voice is a later slice.
_QUESTION_TOOL = "question"


def render_question_for_speech(tool_input: str | None) -> str | None:
    """Spoken announcement for a backend's interactive `question` tool call.

    ``tool_input`` is the adapter's JSON-encoded tool input. The real shape
    (read live off a blocked session during [L9]):
    ``{"questions": [{"question": ..., "options": [{"label": ...,
    "description": ...}, ...], ...}, ...]}``. Option descriptions are
    deliberately NOT spoken -- labels keep the announcement short enough to
    answer; the ladder's later tiers are where "more detail" belongs.
    Returns None when nothing speakable can be extracted (malformed input
    must never crash event consumption).
    """
    if not tool_input:
        return None
    try:
        parsed = json.loads(tool_input)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    questions = parsed.get("questions")
    if not isinstance(questions, list):
        return None
    parts: list[str] = []
    for entry in questions:
        if not isinstance(entry, dict):
            continue
        question = entry.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        piece = question.strip()
        options = entry.get("options")
        if isinstance(options, list):
            labels: list[str] = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                label = option.get("label")
                if isinstance(label, str) and label.strip():
                    labels.append(label.strip())
            if labels:
                numbered = ". ".join(
                    f"Option {index}: {label}"
                    for index, label in enumerate(labels, start=1)
                )
                piece = f"{piece} {numbered}."
        parts.append(piece)
    if not parts:
        return None
    return "The agent is asking: " + " ".join(parts)


def render_approval_request_for_speech(tool: str | None, approval_phrase: str | None) -> str:
    """Spoken announcement for a pending BackendEventType.APPROVAL_REQUEST
    (Phase 3, docs/DESIGN-0.3.0-interaction-and-safety.md).

    Deliberately does NOT read the command/file path aloud (an earlier
    version of this function did, via tool_input) -- commands can be
    long, misleading out of context, or contain sensitive values; the
    TUI/log warning (see run_convobox.py's _on_backend_event) shows the
    exact request, speech only announces the high-stakes state, WHICH
    tool it's for, and the operator-controlled vocabulary needed to
    resolve it. ``approval_phrase`` is None when interactive approvals
    are enabled but no phrase is configured (shouldn't normally happen --
    ApprovalDetector requires one to construct -- but this must still
    produce a safe, sensible sentence rather than crashing on a caller
    bug).
    """
    name = tool or "a tool"
    phrase = approval_phrase or "your approval phrase"
    return f"Approval needed to run {name}. Say {phrase} to approve, or say no to deny."


class ApprovalWaitCanceler(Protocol):
    """The one method hard_stop() needs from run_convobox.py's
    ApprovalPromptGate. A Protocol (structural typing) rather than a real
    import: ApprovalPromptGate lives in scripts/run_convobox.py, which is
    a script, not part of the installed convobox package -- src/ code
    must not import from scripts/ (that dependency direction only ever
    runs the other way, scripts importing from src). Same reasoning as
    web/bridge.py's ApprovalGateLike."""

    def cancel_wait(self) -> None: ...


class Orchestrator:
    def __init__(
        self,
        adapter: BackendAdapter,
        safeword: SafewordDetector,
        tts: TTSEngine | None = None,
        player: AudioPlayer | None = None,
        on_event: Callable[[BackendEvent], None] | None = None,
        tier_responses: bool = False,
        approval_phrase: str | None = None,
        approval_gate: ApprovalWaitCanceler | None = None,
        kill_phrase: str | None = None,
        on_kill_phrase: Callable[[], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._safeword = safeword
        self._tts = tts
        self._player = player
        # Which safeword (if any) escalates to force_kill() instead of the
        # normal hard_stop() -- see force_kill()'s own docstring. Caller
        # (run_convobox.py) is responsible for keeping this in sync with
        # config.safeword.kill_phrase; SafewordConfig's own validator
        # already ensures it's one of the phrases `safeword` itself was
        # constructed with, so `matched == self._kill_phrase` below can
        # only ever fire on a transcript the safeword detector already
        # confirmed matched a real configured phrase.
        self._kill_phrase = kill_phrase
        # Fired (synchronously, no await) after force_kill() returns, so
        # the caller can end the whole session -- Phase 1's scope (JP's
        # own call 2026-08-14): a kill-phrase match ends ConvoBox itself,
        # it does not try to keep the session alive. Deliberately NOT
        # awaited/composed into force_kill() itself: what "end the
        # session" means is inherently caller-specific (run_convobox.py's
        # own shutdown signal), not something Orchestrator should own.
        self._on_kill_phrase = on_kill_phrase
        # Response tiering (docs/DESIGN-0.3.0-interaction-and-safety.md,
        # Phase 2): "voice always gives the tiered/short version." Off by
        # default (existing callers speak the full text exactly as before)
        # -- opt-in via tier_responses=True. One ResponseTierState covers
        # the CURRENT response only; a new TEXT event replaces it (see
        # ResponseTierState.start()'s own docstring for why: an old
        # response's remaining tiers are moot once a new one exists).
        self._tier_state: ResponseTierState | None = (
            ResponseTierState() if tier_responses else None
        )
        # Optional observer for every backend event (TEXT, TOOL_CALL,
        # TOOL_RESULT, DONE, ERROR -- the full stream, not just the TEXT
        # events _on_event itself acts on). Orchestrator's own job is
        # routing transcripts and speaking TEXT content; a caller that
        # wants to know what actually happened (e.g. a live TUI showing
        # the real response, not just "backend busy or not") has no other
        # way to see it -- events() is drained internally by
        # _consume_events(), never exposed to callers of handle_transcript.
        # Deliberately a plain synchronous callback, not another asyncio
        # queue/generator: the caller decides how to buffer/render: this
        # is a hook, not a second consumer contending for the same events.
        self._on_event_hook = on_event
        self._approval_phrase = approval_phrase
        # Optional -- only set when the session actually has voice-gated
        # approval configured (see hard_stop()'s own docstring for why
        # this needs clearing on every hard stop, not just left to its own
        # timeout).
        self._approval_gate = approval_gate
        self._events_task: asyncio.Task[None] | None = None
        self._speak_task: asyncio.Task[None] | None = None

    async def handle_transcript(self, transcript: str) -> None:
        # is_busy() only reflects live state while _consume_events() is
        # draining adapter.events() (that's what clears it back to False on
        # DONE/ERROR/disconnect). Ensuring the loop is running here — rather
        # than requiring a caller to remember a separate wiring step — is
        # what keeps is_busy() from going stale after the very first send.
        self.start_event_loop()

        # Hard stop is checked first and unconditionally: it is a safety-critical
        # abort that must win over busy/idle routing, never downgraded to an interject.
        matched = self._safeword.check(transcript)
        if matched is not None:
            if self._kill_phrase is not None and matched == self._kill_phrase:
                logger.warning("kill phrase matched %r -- force-killing backend", matched)
                await self.force_kill()
                if self._on_kill_phrase is not None:
                    self._on_kill_phrase()
                return
            logger.info("hard stop matched safeword %r", matched)
            await self.hard_stop()
            return

        # Background noise can trigger VAD yet transcribe to nothing (observed
        # live on Windows: a movie playing in the room produced transcript='').
        # Dropped here so noise never becomes a spurious empty command or
        # interject to the backend. Checked after the safeword on purpose,
        # though it could never shadow one: SafewordDetector rejects phrases
        # that normalize to empty at construction, so a hard stop always has
        # visible content. Also checked before wait_listening below -- no
        # point waiting on the event subscription for input we're dropping.
        if not transcript.strip():
            return

        # Sends wait (best-effort, bounded) for the event subscription the
        # loop above just started to actually be established: events a
        # backend emits before its stream is subscribed can be lost
        # entirely (OpenCode's SSE endpoint has no replay), turning the
        # whole response silent. Deliberately NOT done for the hard-stop
        # path above -- aborting must never wait on anything.
        await self._adapter.wait_listening()

        if self._adapter.is_busy():
            await self._adapter.send_interject(transcript)
        else:
            await self._adapter.send_text(transcript)

    def start_event_loop(self) -> None:
        if self._events_task is None or self._events_task.done():
            self._events_task = asyncio.create_task(self._consume_events())

    def _cancel_speak_task(self) -> None:
        """Cancel and clear any in-flight _speak_task.

        Idempotent (a no-op if there's none, or it already finished) --
        Task.cancel() on a done task is a safe no-op, so every call site
        can call this unconditionally rather than checking first.
        """
        if self._speak_task is not None:
            self._speak_task.cancel()
            self._speak_task = None

    async def _speak_after_delay(self, text: str, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        await self._speak(text)

    def announce_after_delay(self, text: str, delay_s: float) -> None:
        """Speak `text` after `delay_s`, once, replacing any pending speech.

        Requested for the approval flow specifically (2026-07-20): a Codex
        turn resumes IMMEDIATELY once an approval is granted, so announcing
        "approved" with no gap risks the announcement itself landing right
        as the tool call starts -- a self-barge-in on that overlap could
        then interrupt the tool call, not just the announcement. A short
        delay gives the resumed turn a moment to get underway first. Only
        the requester (run_convobox.py's approval-resolution path) calls
        this today; no other announcement in this class needs a delay.
        """
        if self._tts is None or self._player is None:
            return
        self._cancel_speak_task()
        self._speak_task = asyncio.create_task(self._speak_after_delay(text, delay_s))

    async def hard_stop(self) -> bool:
        """Stop playback/TTS, tell the backend to abort the in-flight turn,
        and cancel event consumption -- the exact sequence handle_transcript's
        safeword branch already ran inline, extracted so any other trigger
        with the same "stop stop stop" semantics (e.g. the web UI's Stop
        button) can call the identical, already-safety-verified sequence
        rather than a second hand-copied one.

        Returns whether the adapter was actually busy (a turn in flight)
        at the moment this was called -- the "honesty fix" flagged in
        docs/KNOWN-ISSUES.md's "A hard-stop does not guarantee an in-flight
        tool call actually stops" entry (2026-08-09 field note). This does
        NOT mean the interrupt failed to stop anything; every adapter's own
        send_hard_stop() confirms the CONVERSATIONAL turn genuinely aborts.
        What it does NOT confirm is that an already-dispatched tool call's
        underlying OS subprocess stopped -- that keeps running to its own
        completion on all three backends (see the field note), and this
        method's own docstring/stop_event_loop() already ensure that
        trailing result is discarded rather than spoken. A caller that
        surfaces this to the user (log line, TUI/web turn) should use this
        return value to avoid implying "everything stopped" when a tool
        call may still be finishing in the background -- exactly the gap
        the field note's "Option 1" asked for. Deliberately just a bool,
        not a richer pending-cleanup state machine: option 2 (escalating
        force-kill) is the follow-up that would actually resolve the
        underlying gap; this only makes what ConvoBox already knows
        honest, per JP's own scoping of the two options as separate work.
        Deliberately does NOT touch listening/pause state -- unlike
        run_convobox.py's ListeningGate "pause" branch (which also enters a
        paused-until-resume-word state on top of this same sequence), the
        safeword itself never pauses listening; it aborts the current turn
        and the session immediately keeps listening normally. Callers that
        need the pause behavior too still own that decision themselves.

        Live UAT, 2026-07-31: found via the equivalent "pause" hard-stop in
        scripts/run_convobox.py's main loop leaking a trailing response
        1-10+ seconds after the pause was logged. Every adapter's own
        send_hard_stop() comments confirm the in-flight turn's terminal
        result still arrives even after hard-stop (it only resets the local
        busy counter) -- and _on_event() has no awareness of hard-stop
        having just happened, so that trailing TEXT event would
        unconditionally spawn a fresh _speak_task here too. stop_event_loop()
        cancels both the in-flight _speak_task and event-consumption task,
        so nothing from the aborted turn can reach _on_event() --
        handle_transcript()'s own start_event_loop() call restarts a fresh
        subscription on the NEXT call, so this is safe to call every time,
        not just the first.

        Also cancels any pending voice-approval wait (if approval_gate was
        configured at construction). Distinct from the adapter's own
        pending-approval-writer reset (ClaudeCodeAdapter.send_hard_stop()):
        this is the backend-agnostic voice/web gate's OWN wait state.
        Without this, a safeword or web-Stop fired while an approval was
        pending left approval_gate.is_waiting True until its own timeout
        (up to approval_timeout_s later) -- ApprovalPromptGate.cancel_wait()
        was already built for exactly this "decision arrived through a
        different channel" case (see its own docstring), just never called
        from here.
        """
        was_busy = self._adapter.is_busy()
        if self._player is not None:
            self._player.stop()
        if self._tts is not None:
            self._tts.stop()
        await self._adapter.send_hard_stop()
        await self.stop_event_loop()
        if self._approval_gate is not None:
            self._approval_gate.cancel_wait()
        if was_busy:
            logger.info(
                "hard-stop interrupted a turn that was still busy -- if it "
                "included a tool call, the underlying process is not "
                "guaranteed to have stopped; any result it eventually "
                "produces will be discarded, not spoken"
            )
        return was_busy

    async def force_kill(self) -> bool:
        """Escalate beyond hard_stop(): genuinely terminate the backend's
        OS process (BackendAdapter.force_kill()), not just ask it to abort
        over its own channel.

        This is "option 2 (escalating force-kill)", named as the real
        follow-up in hard_stop()'s own docstring since 2026-08-09 and
        built 2026-08-14 after three live freeze incidents in one session
        where the backend subprocess itself was wedged (a blocking
        readline() with no timeout -- docs/KNOWN-ISSUES.md) and
        send_hard_stop()'s own polite interrupt, riding that SAME channel,
        could not reach it either.

        Deliberately does NOT call send_hard_stop() first -- waiting on a
        channel that may itself be the thing that's stuck defeats the
        whole purpose of this method existing. Stops playback/TTS and
        cancels event consumption exactly like hard_stop(), then kills
        the process directly, no RPC round-trip involved.

        Returns whether the adapter was actually busy, same "honesty"
        reasoning as hard_stop()'s own return value -- here it also means
        "a real OS process was just killed while doing something," worth
        a louder log level than hard_stop()'s equivalent case.

        Callers are expected to end the whole ConvoBox session immediately
        after this returns (Phase 1's scope, JP's own call 2026-08-14) --
        this method only guarantees the backend subprocess is dead, not
        that a fresh one gets reconnected to the same conversation (Phase
        2: codex's real thread/resume RPC makes that technically possible
        for the codex adapter specifically, confirmed via `codex app-server
        generate-json-schema` 2026-08-14; claude-code's adapter runs with
        --no-session-persistence and has no equivalent to reconnect to
        even in principle). Not implemented here -- BackendAdapter.
        force_kill()'s own docstring scaffolds the seam without building
        it, per JP's own "ask smaller" scoping of this into two phases.
        """
        was_busy = self._adapter.is_busy()
        if self._player is not None:
            self._player.stop()
        if self._tts is not None:
            self._tts.stop()
        await self._adapter.force_kill()
        await self.stop_event_loop()
        if self._approval_gate is not None:
            self._approval_gate.cancel_wait()
        if was_busy:
            logger.warning(
                "force-kill terminated the backend process while a turn "
                "was still busy -- any in-flight work is gone, not just "
                "discarded"
            )
        return was_busy

    async def stop_event_loop(self) -> None:
        # Retries cancel() up to 3x (3s each) rather than a single
        # cancel-and-await, per 2026-08-15 live evidence against the
        # opencode backend: a task suspended inside adapter.events()'s SSE
        # read can silently fail to honor its FIRST cancel() -- observed
        # hanging 15-90s with a single cancel, every time, never
        # self-resolving -- but a SECOND cancel() reliably unstuck it
        # within seconds, 7/7 replicated trials. Root cause not confirmed
        # (suspected: httpcore's AutoBackend always uses anyio's asyncio
        # backend, a known interop gap for bare Task.cancel()), but this
        # bounds what was an indefinite freeze to a few seconds regardless.
        # See docs/field-notes/2026-08-15-opencode-freeze-*.md for the full
        # investigation. asyncio.shield() protects `task` itself from this
        # wait_for's own timeout -- only the wait is abandoned, not the
        # task, so it stays alive to be re-cancelled and re-awaited.
        self._cancel_speak_task()
        if self._events_task is None:
            return
        task = self._events_task
        task.cancel()
        for attempt in range(3):
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                break
            except asyncio.CancelledError:
                break
            except TimeoutError:
                if task.done():
                    break
                logger.warning(
                    "events task did not honor cancel() after 3s "
                    "(attempt %d/3), re-cancelling -- see docs/field-notes/"
                    "2026-08-15-opencode-freeze-repeated-cancel-mitigates-"
                    "mechanism-and-workaround-candidate.md",
                    attempt + 1,
                )
                task.cancel()
        self._events_task = None

    async def _consume_events(self) -> None:
        """Drains the adapter's event stream, resubscribing immediately if
        it ever fails with an exception rather than dying silently.

        Real live incident (2026-07-15): OpenCodeAdapter.events() raised
        httpx.ReadTimeout mid-session (opencode itself slow to respond
        while busy). Unhandled, that killed this whole task with only
        asyncio's own generic "Task exception was never retrieved"
        warning -- not a clear log line -- and nothing re-created the task
        until the NEXT handle_transcript() call happened to notice
        _events_task was done and started a fresh one. In that session the
        user's own response sat unlogged for over a minute, only
        surfacing once a second, unrelated utterance incidentally
        triggered a fresh subscription. Retrying here closes that gap
        immediately instead of depending on some later, unrelated call.

        Deliberately does NOT retry when events() ends WITHOUT an
        exception (a plain generator return/StopAsyncIteration) -- that's
        each adapter's own documented lazy-respawn contract (e.g. a dead
        subprocess: claude_code.py/codex.py's events() call
        _ensure_proc()/_ensure_thread() internally and are meant to
        respawn on the NEXT send, not be proactively re-subscribed here;
        existing tests already pin that contract). Eagerly retrying on
        every normal return would silently change that to "respawn the
        instant the process dies," a real behavior change for adapters
        this incident has no evidence about. Only the exception case -- an
        unambiguous failure with no such contract, confirmed live for
        OpenCodeAdapter -- gets this treatment.

        Backoff, added 2026-07-29 (live-confirmed while verifying against
        a deliberately unreachable backend.url): the first retry stays at
        _RECONNECT_BACKOFF_INITIAL_S (1s -- matching the 2026-07-15
        incident's own "close the gap immediately" intent for a genuine
        transient hiccup), but consecutive failures with no successful
        event in between double the wait, capped at
        _RECONNECT_BACKOFF_MAX_S -- a PERMANENTLY unreachable backend
        (a real misconfigured backend.url, not a transient timeout)
        otherwise retries and logs an identical traceback forever at a
        fixed fast interval, confirmed live: ~90 tracebacks in under 5
        minutes against a dead URL. Resets to the initial fast interval
        the moment a real event actually arrives, so a genuinely
        transient failure still recovers as quickly as before.
        """
        backoff_s = _RECONNECT_BACKOFF_INITIAL_S
        while True:
            try:
                async for event in self._adapter.events():
                    backoff_s = _RECONNECT_BACKOFF_INITIAL_S
                    self._on_event(event)
            except Exception:
                logger.warning(
                    "backend event stream failed; resubscribing in %.0fs",
                    backoff_s, exc_info=True,
                )
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, _RECONNECT_BACKOFF_MAX_S)
                continue
            return

    def _on_event(self, event: BackendEvent) -> None:
        logger.debug(
            "backend event type=%s tool=%s", event.type.value, event.tool
        )
        if self._on_event_hook is not None:
            try:
                self._on_event_hook(event)
            except Exception:
                # Called synchronously from inside _consume_events()'s
                # async-for loop -- an uncaught exception here would kill
                # _events_task, silently stopping event consumption
                # (is_busy() goes stale forever, no more speech, no more
                # TUI updates) over a bug in an OBSERVER, not the core
                # routing/speech responsibility this class exists for.
                logger.warning("on_event observer raised; ignoring", exc_info=True)
        if event.type == BackendEventType.APPROVAL_REQUEST:
            # Phase 3: a gated tool call is blocked on a voice decision
            # (see claude_code.py's module docstring for the mechanism).
            # Always logged (the honest status a silent hook-blocked
            # stdout can't give any other way -- same reasoning as the
            # question-tool announcement below), and spoken when a voice
            # path exists. Not tiered, same as the question tool: a
            # pending decision must be delivered whole. Deliberately
            # doesn't read event.tool_input aloud -- see
            # render_approval_request_for_speech's own docstring for why
            # (commands can be long/sensitive/misleading out of context);
            # the TUI/log warning (run_convobox.py's _on_backend_event)
            # carries the actual detail.
            approval_announcement = render_approval_request_for_speech(
                event.tool, self._approval_phrase
            )
            logger.info("%s", approval_announcement)
            if self._tts is not None and self._player is not None:
                self._cancel_speak_task()
                self._speak_task = asyncio.create_task(self._speak(approval_announcement))
            return
        if event.type == BackendEventType.TOOL_CALL and event.tool == _QUESTION_TOOL:
            # [L9]: this tool BLOCKS the backend's turn until answered, and
            # an unheard question deadlocks the whole voice session. Always
            # log it (the honest status the heartbeat couldn't give), and
            # speak it when a voice path exists. Deliberately not tiered:
            # a question must be delivered whole or it can't be answered.
            announcement = render_question_for_speech(event.tool_input)
            if announcement is not None:
                logger.info(
                    "backend is waiting for YOUR answer -- %s", announcement
                )
                if self._tts is not None and self._player is not None:
                    self._cancel_speak_task()
                    self._speak_task = asyncio.create_task(self._speak(announcement))
            return
        if event.type != BackendEventType.TEXT or not event.content:
            return
        if self._tts is None or self._player is None:
            return
        spoken = strip_code_for_speech(event.content)
        if not spoken:
            return
        if self._tier_state is not None:
            # Tier on the ALREADY-STRIPPED text, not the raw event content:
            # strip_code_for_speech already collapses 3+ newlines down to
            # exactly "\n\n" (_COLLAPSE_BLANK_RE), so its output's paragraph
            # boundaries are exactly what split_tiers() expects -- tiering
            # the raw markdown would risk splitting mid-code-block or on a
            # blank line that strip_code_for_speech was about to remove
            # anyway. start() REPLACES any previous tier state: a new TEXT
            # event is a new response to tier, not a continuation of the
            # last one's held-back tiers (matches on_event_hook seeing the
            # untiered, full raw content above -- the TUI's full-detail
            # pane is never affected by tiering, by design).
            spoken = self._tier_state.start(spoken)
            if not spoken:
                return
        # Real bug, live-confirmed 2026-07-14: a single backend turn can
        # emit MULTIPLE TEXT events (text interleaved with tool calls --
        # "Let me check that file" ... [tool work] ... "Found it, fixing
        # now" ...), and _speak_task used to get silently overwritten here
        # without cancelling whatever the PREVIOUS TEXT segment's task was
        # still doing. The new play_stream() call already replaces the
        # OLD task's AUDIO via AudioPlayer.stop() (same thread/stream, so
        # only one text is ever actually heard) -- but the old task's own
        # coroutine kept running uncancelled, continuing to pull chunks
        # from ITS (now-superseded) synthesize_stream() and, critically,
        # continuing to advance EchoAwarePlayer.playback_ended_at
        # (scripts/run_convobox.py) for audio that was never written to
        # the device. That corrupted timestamp fed the overlap gate,
        # making it think playback was ongoing/had just ended far longer
        # than reality -- observed live as an entire multi-minute UAT
        # session where nearly every utterance got dropped as
        # "overlapped" (reported as "AEC seems to be misfiring," but AEC
        # itself was never the mechanism doing the dropping -- see
        # docs/KNOWN-ISSUES.md). Cancelling here stops the wasted
        # synthesis work too, not just the metadata corruption.
        #
        # Fire-and-forget rather than awaited inline: synthesis can take
        # noticeably longer than draining the next backend event (e.g. a
        # DONE right behind this TEXT), and is_busy() staying fresh
        # matters more than serializing speech with event consumption.
        # AudioPlayer.play() is itself non-blocking (own thread), so this
        # task's own work is just the synthesize() await.
        self._cancel_speak_task()
        self._speak_task = asyncio.create_task(self._speak(spoken))

    async def resolve_pending_approval(self, approved: bool) -> bool:
        """Passthrough to the adapter's own resolve_pending_approval (see
        BackendAdapter's docstring) -- kept on Orchestrator so callers
        (run_convobox.py's main loop) don't reach into ._adapter directly,
        same encapsulation as has_more_to_reveal/speak_more."""
        return await self._adapter.resolve_pending_approval(approved)

    def has_more_to_reveal(self) -> bool:
        """Whether the current (most recently tiered) response has
        held-back tiers left. Lets a caller (the main loop's
        ContinueDetector wiring) decide whether it's even worth listening
        for "continue" after a response -- a response that already said
        everything shouldn't prompt for more."""
        return self._tier_state is not None and self._tier_state.has_more()

    async def speak_more(self) -> bool:
        """The ContinueDetector "continue" action: speak the next
        held-back tier of the current response, if any. Returns whether
        there was anything to speak (False: nothing left, or tiering
        isn't enabled, or TTS isn't configured -- the caller doesn't need
        to distinguish why, just whether it should have said something).
        """
        if self._tier_state is None or self._tts is None or self._player is None:
            return False
        chunk = self._tier_state.reveal_more()
        if chunk is None:
            return False
        # Same cancellation as _on_event's TEXT handling (see its comment):
        # by the time a caller reaches "continue," the prior tier's
        # _speak_task should already be done (that's what let the
        # continue-prompt gate start waiting in the first place), so this
        # is defense-in-depth for the general case, not the common path.
        self._cancel_speak_task()
        self._speak_task = asyncio.create_task(self._speak(chunk))
        return True

    async def _speak(self, text: str) -> None:
        # SECURITY EXCEPTION: B101 (assert stripped under python -O) -- this is
        # a type-narrowing assertion, not a security boundary. handle_backend_event
        # (the only caller) already returns early when either is None; _speak
        # can't be reached otherwise. If that invariant were ever violated, -O
        # would surface an AttributeError two lines down instead of this
        # clearer message -- same failure, not a behavior change.
        # Mitigation: single private call site, guarded immediately before use.
        assert self._tts is not None and self._player is not None  # nosec B101
        # Streamed, not synthesize-then-play: audio starts on the first
        # synthesized chunk (typically the first sentence), so
        # time-to-first-audio is proportional to one sentence instead of
        # the whole response. play_stream replaces any current playback,
        # same as play() did.
        try:
            await self._player.play_stream(
                self._tts.synthesize_stream(text), self._tts.sample_rate
            )
        except Exception as exc:
            # _speak_task is a bare asyncio.create_task() with nothing ever
            # awaiting or checking it (fire-and-forget, so a slow/failed
            # synthesis never blocks the mic loop) -- which means an
            # uncaught exception here previously vanished completely: no
            # log line, no UI signal, just silence where the rest of the
            # response should have been. Confirmed live, 2026-07-28/29:
            # this is exactly what a text long enough to hit kokoro's
            # ~510-phoneme batch limit produced (KokoroTTSEngine's own
            # 30s-timeout-then-RuntimeError, added specifically to make
            # this "a real, catchable error" -- but nothing was catching
            # it until now). Log it AND surface it as a real event so
            # both the TUI and the web UI show something failed, instead
            # of an unexplained gap in what was spoken.
            logger.exception("TTS synthesis/playback failed mid-response")
            self._on_event(
                BackendEvent(
                    type=BackendEventType.ERROR,
                    content=f"speech synthesis failed partway through this response: {exc}",
                )
            )
