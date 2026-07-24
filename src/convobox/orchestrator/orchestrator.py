from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable

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
    ) -> None:
        self._adapter = adapter
        self._safeword = safeword
        self._tts = tts
        self._player = player
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
            logger.info("hard stop matched safeword %r", matched)
            if self._player is not None:
                self._player.stop()
            if self._tts is not None:
                self._tts.stop()
            await self._adapter.send_hard_stop()
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

    async def stop_event_loop(self) -> None:
        self._cancel_speak_task()
        if self._events_task is None:
            return
        self._events_task.cancel()
        try:
            await self._events_task
        except asyncio.CancelledError:
            pass
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
        """
        while True:
            try:
                async for event in self._adapter.events():
                    self._on_event(event)
            except Exception:
                logger.warning(
                    "backend event stream failed; resubscribing", exc_info=True
                )
                await asyncio.sleep(1.0)
                continue
            return

    def _on_event(self, event: BackendEvent) -> None:
        logger.debug(
            "backend event type=%s tool=%s", event.type.value, event.tool
        )
        if self._on_event_hook is not None:
            try:
                self._on_event_hook(event)
            except Exception:  # noqa: BLE001
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
        await self._player.play_stream(
            self._tts.synthesize_stream(text), self._tts.sample_rate
        )
