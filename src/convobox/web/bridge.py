"""Bridges Orchestrator's on_event hook AND the user's own transcripts into
the web UI (docs/WEB-UI-ARCHITECTURE.md). Kept as its own small callable,
not inlined into run_convobox.py's already-large on_event chain, so "how a
BackendEvent/transcript becomes a web history row / SSE broadcast" is
unit-testable without spinning up the whole voice loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.audio.playback import AudioPlayer
from convobox.orchestrator.orchestrator import Orchestrator
from convobox.tts.base import TTSEngine
from convobox.web.history import HistoryDB, event_to_dict
from convobox.web.stream import EventBroadcaster

# Mirrors WEB-UI-ARCHITECTURE.md's event_type vocabulary ("tool_call",
# "response", etc.) for the history row's own event_type column.
# APPROVAL_REQUEST is deliberately its own string ("approval_request"), not
# folded into "response" -- a pending decision isn't a spoken reply.
_EVENT_TYPE_NAMES: dict[BackendEventType, str] = {
    BackendEventType.TEXT: "response",
    BackendEventType.TOOL_CALL: "tool_call",
    BackendEventType.TOOL_RESULT: "tool_result",
    BackendEventType.ERROR: "error",
    BackendEventType.DONE: "done",
    BackendEventType.APPROVAL_REQUEST: "approval_request",
}


class WebEventForwarder:
    """Callable Orchestrator ``on_event`` hook.

    ``history``/``broadcaster`` are each independently optional (None means
    "skip that half") -- matches WebConfig's two separate opt-ins:
    ``web.enabled`` alone gets live SSE streaming with no persistence,
    ``web.history_tracking_enabled`` on top of that adds SQLite storage.
    Constructing this with both None is a valid, harmless no-op, matching
    web.enabled=False (the default) making zero behavioral difference.
    """

    def __init__(
        self,
        session_id: str,
        history: HistoryDB | None,
        broadcaster: EventBroadcaster | None,
    ) -> None:
        self._session_id = session_id
        self._history = history
        self._broadcaster = broadcaster

    def __call__(self, event: BackendEvent) -> None:
        if self._history is not None:
            self._history.append_event(
                self._session_id,
                _EVENT_TYPE_NAMES.get(event.type, event.type.value),
                backend_event=event,
            )
        self._broadcast(event_to_dict(event))

    def forward_transcript(self, text: str) -> None:
        """Called with the user's own recognized speech, separately from
        __call__ -- Orchestrator's on_event hook only ever sees BackendEvents
        (backend responses/tool calls), never the transcript that PROMPTED
        one, so a transcript needs its own entry point. Callers: every
        run_convobox.py call site that invokes Orchestrator.handle_transcript
        (the main mic loop, --text mode, and queued-interjection delivery)."""
        if self._history is not None:
            self._history.append_event(self._session_id, "transcript", user_transcript=text)
        self._broadcast({"type": "transcript", "content": text})

    def _broadcast(self, payload: dict[str, object]) -> None:
        if self._broadcaster is not None:
            # Orchestrator._on_event calls its hook synchronously from
            # inside a running event loop (_consume_events' async-for), and
            # forward_transcript is called the same way from run_convobox.py
            # -- both contracts are sync, so the broadcast is scheduled
            # rather than awaited, same reasoning as _events.put_nowait()
            # elsewhere in the adapters.
            asyncio.ensure_future(self._broadcaster.broadcast(payload))


class ApprovalGateLike(Protocol):
    """The slice of run_convobox.py's ApprovalPromptGate that
    WebApprovalBridge needs. A Protocol (structural typing) rather than a
    real import: ApprovalPromptGate lives in scripts/run_convobox.py, which
    is a script, not part of the installed convobox package -- src/ code
    must not import from scripts/ (that dependency direction only ever
    runs the other way, scripts importing from src)."""

    @property
    def is_waiting(self) -> bool: ...

    @property
    def pending_explanation(self) -> str | None: ...

    def start_waiting(self, now: float, explanation: str | None = None) -> None: ...

    def cancel_wait(self) -> None: ...


class WebApprovalBridge:
    """Lets the web UI's approve/deny/explain buttons
    (POST /api/sessions/{id}/approval) answer the same pending backend
    approval a spoken phrase would.

    Constructed with no targets (create_app() needs something to hand
    FastAPI's route closures at server-startup time, before the real
    Orchestrator/ApprovalPromptGate exist yet), then wired via
    set_targets() a few lines later in run_convobox.py's run(), once both
    are built. Every method degrades to "nothing pending" if called before
    set_targets() runs (a request landing in that startup gap), rather
    than raising.
    """

    def __init__(self) -> None:
        self._orchestrator: Orchestrator | None = None
        self._gate: ApprovalGateLike | None = None

    def set_targets(self, orchestrator: Orchestrator, gate: ApprovalGateLike | None) -> None:
        self._orchestrator = orchestrator
        self._gate = gate

    @property
    def is_pending(self) -> bool:
        return self._gate is not None and self._gate.is_waiting

    @property
    def pending_explanation(self) -> str | None:
        return self._gate.pending_explanation if self._gate is not None else None

    async def decide(self, approved: bool) -> bool:
        """Approve or deny the pending request. Returns False (nothing
        changed) if there was no pending request, or the backend no longer
        had the expected one (same fail-closed case
        run_convobox.py's own voice path handles) -- either way the caller
        should surface that as "nothing to decide" rather than a decision
        having been made."""
        if self._orchestrator is None or self._gate is None or not self._gate.is_waiting:
            return False
        resolved = await self._orchestrator.resolve_pending_approval(approved)
        if resolved:
            self._gate.cancel_wait()
        return resolved

    def extend(self) -> str | None:
        """Keep the pending request open without deciding it -- the button
        equivalent of the voice path's "explain"/"discuss" outcomes, which
        both reset the timeout clock so a request stays answerable while
        the operator reads it instead of auto-denying out from under them.
        Returns the explanation text (if any), or None if nothing is
        pending."""
        if self._gate is None or not self._gate.is_waiting:
            return None
        explanation = self._gate.pending_explanation
        self._gate.start_waiting(time.monotonic(), explanation)
        return explanation


class ListeningGateLike(Protocol):
    """The slice of run_convobox.py's ListeningGate that WebListeningBridge
    needs -- same reasoning as ApprovalGateLike above: ListeningGate lives
    in scripts/run_convobox.py, a script, not the installed package.
    is_paused is a plain settable attribute on the real class (not a
    property), so it's declared the same way here."""

    is_paused: bool


class WebListeningBridge:
    """Lets the web UI's Stop/Resume listening button do exactly what a
    spoken pause/resume phrase does (ListeningGate.observe()'s "pause"/
    "resume" branches in run_convobox.py) -- pausing hard-stops in-flight
    playback and backend work, it does not just gate future transcripts.
    Voice and the browser can both pause/resume; both act on the SAME
    ListeningGate, so whichever happens first is simply what's true next.

    Constructed with no targets (create_app() needs something to hand its
    route closures at server-startup time, before the real ListeningGate/
    player/tts/adapter exist), wired via set_targets() once they're built
    -- same pattern as WebApprovalBridge. Every method degrades to "did
    nothing" (returns False) if called before set_targets() runs, rather
    than raising.

    Deliberately does NOT touch a TUI's ConversationTuiState (unlike the
    voice path, which logs a system-turn line there) -- that's a
    terminal-only nicety, and wiring it in here would need yet another
    script-local Protocol for a cosmetic gap, not a functional one.
    """

    def __init__(self) -> None:
        self._gate: ListeningGateLike | None = None
        self._player: AudioPlayer | None = None
        self._tts: TTSEngine | None = None
        self._adapter: BackendAdapter | None = None

    def set_targets(
        self,
        gate: ListeningGateLike,
        player: AudioPlayer,
        tts: TTSEngine,
        adapter: BackendAdapter,
    ) -> None:
        self._gate = gate
        self._player = player
        self._tts = tts
        self._adapter = adapter

    @property
    def is_ready(self) -> bool:
        return self._gate is not None

    @property
    def is_paused(self) -> bool:
        return self._gate is not None and self._gate.is_paused

    async def pause(self) -> bool:
        """Returns False (nothing changed) if called before set_targets()
        (no live session) or already paused -- a second pause is a no-op,
        matching ListeningGate.observe()'s own behavior: once is_paused is
        true, it only ever checks for the resume word, it never re-enters
        the "pause" branch to re-run these side effects."""
        if self._gate is None or self._player is None or self._tts is None or self._adapter is None:
            return False
        if self._gate.is_paused:
            return False
        self._gate.is_paused = True
        self._player.stop()
        self._tts.stop()
        await self._adapter.send_hard_stop()
        return True

    def resume(self) -> bool:
        """Voice resume only clears the flag (ListeningGate.observe()'s
        "resume" branch) -- nothing was hard-stopped to undo, so nothing
        to redo here either."""
        if self._gate is None:
            return False
        if not self._gate.is_paused:
            return False
        self._gate.is_paused = False
        return True
