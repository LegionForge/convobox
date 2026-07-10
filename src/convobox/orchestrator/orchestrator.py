from __future__ import annotations

import asyncio
import logging
import re

from convobox.adapters.base import BackendAdapter, BackendEvent, BackendEventType
from convobox.audio.playback import AudioPlayer
from convobox.safeword.detector import SafewordDetector
from convobox.tts.base import TTSEngine

logger = logging.getLogger(__name__)

_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_COLLAPSE_BLANK_RE = re.compile(r"\n{3,}")


def strip_code_for_speech(text: str) -> str:
    without_fenced = _FENCED_CODE_RE.sub(" ", text)
    without_inline = _INLINE_CODE_RE.sub(" ", without_fenced)
    return _COLLAPSE_BLANK_RE.sub("\n\n", without_inline).strip()


class Orchestrator:
    def __init__(
        self,
        adapter: BackendAdapter,
        safeword: SafewordDetector,
        tts: TTSEngine | None = None,
        player: AudioPlayer | None = None,
    ) -> None:
        self._adapter = adapter
        self._safeword = safeword
        self._tts = tts
        self._player = player
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
        # visible content.
        if not transcript.strip():
            return

        if self._adapter.is_busy():
            await self._adapter.send_interject(transcript)
        else:
            await self._adapter.send_text(transcript)

    def start_event_loop(self) -> None:
        if self._events_task is None or self._events_task.done():
            self._events_task = asyncio.create_task(self._consume_events())

    async def stop_event_loop(self) -> None:
        if self._speak_task is not None:
            self._speak_task.cancel()
            self._speak_task = None
        if self._events_task is None:
            return
        self._events_task.cancel()
        try:
            await self._events_task
        except asyncio.CancelledError:
            pass
        self._events_task = None

    async def _consume_events(self) -> None:
        async for event in self._adapter.events():
            self._on_event(event)

    def _on_event(self, event: BackendEvent) -> None:
        logger.debug(
            "backend event type=%s tool=%s", event.type.value, event.tool
        )
        if event.type != BackendEventType.TEXT or not event.content:
            return
        if self._tts is None or self._player is None:
            return
        spoken = strip_code_for_speech(event.content)
        if spoken:
            # Fire-and-forget rather than awaited inline: synthesis can take
            # noticeably longer than draining the next backend event (e.g. a
            # DONE right behind this TEXT), and is_busy() staying fresh
            # matters more than serializing speech with event consumption.
            # AudioPlayer.play() is itself non-blocking (own thread), so this
            # task's own work is just the synthesize() await.
            self._speak_task = asyncio.create_task(self._speak(spoken))

    async def _speak(self, text: str) -> None:
        assert self._tts is not None and self._player is not None
        audio = await self._tts.synthesize(text)
        if audio.size:
            self._player.play(audio, self._tts.sample_rate)
