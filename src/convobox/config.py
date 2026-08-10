from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from convobox.approval import ApprovalDetector
from convobox.interrupt_presets import resolve_preset
from convobox.listening_pause import DEFAULT_PAUSE_PHRASES
from convobox.resumeword import DEFAULT_RESUME_WORD


class AudioConfig(BaseModel):
    input_device: str | None = None
    output_device: str | None = None
    sample_rate: int = 16000
    # Acoustic echo cancellation (WebRTC APM via the optional [aec]
    # extra). Off by default: it needs the extra installed, and its
    # value depends on the speaker/mic arrangement -- see
    # docs/DESIGN-echo-and-barge-in.md.
    echo_cancellation: bool = False
    # Hint for the canceller: expected ms between writing audio to the
    # output device and hearing it back in the mic (device buffers +
    # acoustic path). None (the default) means auto-tune: run_convobox.py
    # measures the real output+input stream latencies on first playback
    # and uses that instead -- confirmed live (2026-07-15) that a wrong
    # fixed hint (measured ~222ms vs a stale 100ms) keeps WebRTC AEC3 from
    # converging, so the assistant's own voice leaks into the mic and
    # trips the barge-in overlap gate. Set an explicit int only if you've
    # measured a genuinely better fixed value for this exact hardware and
    # want it to override auto-tuning.
    aec_delay_ms: int | None = None


class VADConfig(BaseModel):
    threshold: float = 0.5
    min_silence_ms: int = 500
    min_speech_ms: int = 250
    # Force-emit an utterance that exceeds this many seconds of audio even if
    # no silence gap has occurred. None = unlimited (the pre-existing
    # behavior). Without a cap, continuous speech means an unbounded buffer
    # and no transcript at all until the speaker pauses; observed live as a
    # 30.5s single utterance whose transcript only arrived after it ended.
    max_utterance_s: float | None = None
    # Off by default, deliberately separate from --verbose/DEBUG logging:
    # logs every single Silero window call's duration (~31/s during active
    # audio) at DEBUG level -- genuinely too noisy for normal --verbose use,
    # even though DEBUG is the level it logs at. Added 2026-08-07 live, at
    # JP's own suggestion, after a live UAT gap (~47s, no Processing audio
    # lines) produced none of feed_async()'s existing >0.5s stall warnings
    # either -- leaving it ambiguous whether that gap was genuine silence
    # (VAD correctly saw nothing to process) or something stalled upstream
    # of Silero entirely (mic capture, AEC) that never even reached a
    # feed_async() call to warn about. Per-call tracing resolves that:
    # a real freeze inside Silero shows continuous fast calls right up to
    # a gap in the trace itself; a stalled call is caught by the existing
    # feed_async() warnings; and genuine silence shows... also nothing,
    # which is the point -- if it's on and NOTHING logs during a "frozen"
    # period, the stall is upstream of the VAD windowing loop entirely, an
    # entirely different investigation than this fix's own scope. See
    # docs/KNOWN-ISSUES.md's VAD segmenter freeze entry.
    trace_silero_calls: bool = False


# ctranslate2's real supported precisions (verified against the installed
# 4.8.1 via ctranslate2.get_supported_compute_types("cpu")/("cuda")) --
# hardcoded rather than queried at validation time: importing ctranslate2
# costs 2+ seconds on first touch (confirmed empirically), and STTConfig
# gets constructed everywhere, including hundreds of tests and every CLI
# path that never touches STT at all. faster_whisper.utils.available_models
# (settings_tui.py's own STT model picker) already eagerly pulls in
# ctranslate2 for its own reasons, so that cost is separately unavoidable
# there -- this list intentionally doesn't ride on it, to keep config.py
# itself fast regardless of what else happens to import it.
STT_COMPUTE_TYPES: tuple[str, ...] = (
    "default",
    "int8",
    "int8_float16",
    "int8_float32",
    "int8_bfloat16",
    "int16",
    "float16",
    "bfloat16",
    "float32",
)

# Per-device breakdown of the same real ctranslate2 4.8.1 precisions, from
# the same verification method (ctranslate2.get_supported_compute_types(...)),
# so an incompatible device/compute_type pairing (e.g. compute_type: float16
# with device: cpu) can be rejected with a clear config-level error instead
# of a raw ValueError three layers deep inside ctranslate2's Whisper
# constructor. "default" is deliberately excluded from both -- it's a
# sentinel resolved internally (int8 on cpu, float16 on cuda), never a real
# compute type passed to ctranslate2 directly, so it's valid on any device.
STT_COMPUTE_TYPES_CPU: tuple[str, ...] = ("float32", "int16", "int8", "int8_float32")
STT_COMPUTE_TYPES_CUDA: tuple[str, ...] = (
    "bfloat16",
    "float16",
    "float32",
    "int8",
    "int8_bfloat16",
    "int8_float16",
    "int8_float32",
)


class STTConfig(BaseModel):
    # Which STT engine to build (see convobox.stt.factory). Only
    # faster-whisper is implemented today; the field exists so STT is
    # selectable/pluggable symmetrically with tts.engine.
    engine: str = "faster-whisper"
    model: str = "base"
    # "auto"/"default" delegate straight to faster-whisper's own device and
    # compute-type selection (ctranslate2.get_cuda_device_count() under the
    # hood) -- confirmed on this machine's real NVIDIA 4060 to pick CUDA
    # automatically when present, CPU otherwise. Set explicit values
    # ("cpu"/"int8") only to force a side away from what's auto-detected,
    # e.g. to keep a GPU free for another process.
    device: str = "auto"
    compute_type: str = "default"
    language: str | None = None
    # Drop transcripts whose detected-language probability falls below this
    # (0.0 = disabled). Live testing showed detections under ~0.4 on accented
    # or ambiguous audio are usually hallucinations, sometimes in an entirely
    # different script. Only meaningful when ``language`` is None (a pinned
    # language reports probability 1.0). Consumers must still check the
    # safeword on the raw transcript BEFORE applying this gate: a confidence
    # filter must never be able to swallow a hard stop.
    min_language_probability: float = 0.0
    # Exact, operator-maintained fixes for recurring STT mistakes.  Applied
    # only to ordinary command routing after raw safeword/pause/approval
    # checks; see convobox.stt.corrections.TranscriptCorrector.  Keeping the
    # glossary in config makes every rewrite inspectable and portable, rather
    # than silently training on a user's voice data.
    corrections: dict[str, str] = Field(default_factory=dict)
    # Passed straight through to faster-whisper's own transcribe(hotwords=...)
    # -- a free-text prompt bias toward words/phrases the model should
    # recognize more readily. Live UAT, 2026-08-02: a short, out-of-vocabulary
    # word (a configured resume_word) was repeatedly hallucinated as unrelated
    # fluent sentences ("We'll see you on the other side.", Cyrillic text)
    # rather than being misheard as something similar -- the well-documented
    # Whisper failure mode on short/low-signal clips. Operators should
    # include their resume_word, safeword.hard_stop_phrases, and
    # interaction.approval_phrase here (space-separated) to bias toward the
    # exact short phrases most likely to hit this failure mode. Deliberately
    # not auto-derived from those configs: STTConfig has no dependency on
    # InteractionConfig/SafewordConfig today, and hotwords is a real-word
    # accuracy nudge, not a safety mechanism -- the safeword/resume-word
    # checks themselves still run on the raw transcript regardless of
    # whether this helped or not.
    hotwords: str | None = None
    # None (default) leaves faster-whisper's own condition_on_previous_text
    # default (True) untouched -- zero behavior change unless explicitly
    # set. False is the second of the three related levers flagged
    # alongside hotwords (SOTA STT research pass, 2026-08-03): disabling it
    # stops a low-signal/short utterance's decode from being biased by
    # whatever fluent text the PREVIOUS segment produced, which is one
    # documented contributor to the hallucinate-a-fluent-unrelated-sentence
    # failure mode this project hit live with a short resume_word. Unlike
    # hotwords (a clear, low-risk accuracy nudge), this is a real tradeoff
    # -- worth testing, not yet validated live -- so it stays opt-in rather
    # than a new default.
    condition_on_previous_text: bool = True
    # None (default) leaves faster-whisper's own temperature fallback
    # ladder ([0.0, 0.2, 0.4, ... 1.0]) untouched -- zero behavior change
    # unless explicitly set. A float pins decoding to that single
    # temperature instead -- 0.0 (fully deterministic, no higher-randomness
    # retries) is the specific value the same research pass flagged: on an
    # already-short/low-signal clip, the fallback ladder's higher-
    # temperature retries are themselves a plausible source of the
    # hallucinated-fluent-sentence failure mode, not just a recovery
    # mechanism. Also not yet validated live -- opt-in, not a new default,
    # same reasoning as condition_on_previous_text above.
    temperature: float | None = None
    # None (default) leaves faster-whisper's own repetition_penalty=1.0
    # (no penalty) untouched -- zero behavior change unless explicitly
    # set. Real bug, found live (2026-08-07, JP's own UAT session): a
    # 1.056s clip of "That's right." (once) decoded as "that's right"
    # repeated 8 times -- a single transcribe() call, one audio buffer,
    # the repetition was already baked into the decoder's own output,
    # not an app-level duplication bug (see
    # docs/field-notes/2026-08-06-resume-word-hallucination-and-runaway-
    # repetition.md for the first, hotword-specific instance of this same
    # failure class -- this incident proves it's not hotword-specific).
    # faster-whisper exposes this exact parameter to penalize repeated
    # tokens during decoding; > 1.0 discourages repetition. Not yet
    # validated live -- opt-in, same reasoning as temperature above, not
    # a new default (an untested penalty value could make normal decodes
    # worse, not just fix the rare repetition case).
    repetition_penalty: float | None = None
    # None (default) leaves faster-whisper's own no_repeat_ngram_size=0
    # (disabled) untouched -- zero behavior change unless explicitly set.
    # The second lever for the same repetition-loop failure mode above:
    # blocks the decoder from repeating any n-gram of this length twice
    # in one decode (e.g. blocks "that's right that's" from recurring).
    # A blunter, more mechanical guard than repetition_penalty's soft
    # scoring nudge -- worth having both available to test independently
    # rather than assuming one subsumes the other. Not yet validated
    # live -- opt-in, not a new default, same reasoning throughout this
    # section.
    no_repeat_ngram_size: int | None = None
    # None (default) means no timeout: transcribe() is offloaded to a
    # thread (see run_convobox.py's mic loop) but awaited indefinitely,
    # same behavior as before this field existed. A real number caps how
    # long any single utterance's transcribe() call is allowed to run
    # before it's abandoned and treated as unheard -- live-hit 2026-08-06:
    # a stuck transcribe() call, run synchronously on the main event loop
    # with no offload at the time, froze the ENTIRE app (mic loop, web UI,
    # TUI, even the once-a-second background watchdog) while mic capture
    # kept running on its own separate thread, unaffected -- confirmed via
    # AEC-dump frame-count forensic cross-check showing continuous capture
    # straight through the "frozen" window. The thread-offload alone (see
    # run_convobox.py) fixes the "everything else freezes too" half of
    # that regardless of whether this timeout is set; this field additionally
    # lets the mic loop itself recover and move on to the next utterance
    # instead of waiting forever for a call that may never return (Python
    # cannot force-kill a native thread, so an abandoned call's background
    # thread may keep running -- see LocalTranscriber.invalidate()).
    transcribe_timeout_s: float | None = None

    @field_validator("compute_type")
    @classmethod
    def _validate_compute_type(cls, v: str) -> str:
        if v not in STT_COMPUTE_TYPES:
            raise ValueError(
                f"compute_type must be one of {STT_COMPUTE_TYPES}, not {v!r}"
            )
        return v

    @field_validator("corrections")
    @classmethod
    def _validate_corrections(cls, v: dict[str, str]) -> dict[str, str]:
        # Constructing the corrector performs normalization-aware validation
        # (empty sources/targets and duplicate normalized sources).  Import
        # lazily to keep config's existing import surface lightweight.
        from convobox.stt.corrections import TranscriptCorrector

        TranscriptCorrector(v)
        return v

    @model_validator(mode="after")
    def _validate_compute_type_matches_device(self) -> STTConfig:
        # Only device: cpu/cuda are checked -- device: auto (or anything
        # else) resolves its real target at construction time, so there's
        # nothing to validate against here. compute_type: default is always
        # valid on any device (see STT_COMPUTE_TYPES_CPU/CUDA's docstring).
        # Without this, an incompatible pairing (e.g. float16 on cpu) passes
        # config validation cleanly and only fails three layers deep inside
        # ctranslate2's Whisper constructor with a raw traceback -- live-hit
        # 2026-08-03 hand-editing convobox.yaml after a device swap.
        if self.compute_type == "default":
            return self
        if self.device == "cpu" and self.compute_type not in STT_COMPUTE_TYPES_CPU:
            raise ValueError(
                f"compute_type {self.compute_type!r} is not supported on "
                f"device 'cpu' -- use one of {STT_COMPUTE_TYPES_CPU} "
                "or 'default'"
            )
        if self.device == "cuda" and self.compute_type not in STT_COMPUTE_TYPES_CUDA:
            raise ValueError(
                f"compute_type {self.compute_type!r} is not supported on "
                f"device 'cuda' -- use one of {STT_COMPUTE_TYPES_CUDA} "
                "or 'default'"
            )
        return self


class TTSConfig(BaseModel):
    # Kokoro is the permissively licensed default. Piper remains available
    # as an explicit opt-in extra because piper-tts is GPL-3.0.
    engine: str = "kokoro"
    voice: str | None = "af_sarah"
    rate: float = 1.0
    volume: float = 1.0
    # kokoro only: the shared model/voice bundle and phonemizer language.
    model_path: str = ".models/kokoro/kokoro-v1.0.onnx"
    voices_path: str = ".models/kokoro/voices-v1.0.bin"
    language: str = "en-us"
    # piper only: select a speaker for a multi-speaker voice, by name
    # (matching the voice's own speaker_id_map, e.g. "prudence" for
    # en_GB-semaine-medium) or a raw numeric index. None (default) uses
    # the voice's own default speaker (index 0) -- unchanged behavior
    # for the single-speaker voices this project has used until now.
    # Real, not hypothetical: several already-downloaded Piper voices in
    # this repo (en_GB-semaine-medium: 4 named speakers, en_GB-aru-medium:
    # 12, en_GB-vctk-medium: 109, en_US-libritts-high: 904) are genuinely
    # multi-speaker and this had no way to select anything but the
    # implicit default. No pydantic-level format validation here -- unlike
    # backend.model's cheap "/" check, resolving a speaker name requires
    # the actual voice model loaded (PiperVoice.load), which only happens
    # in PiperTTSEngine's own construction; see that class for the real
    # validation and error message.
    speaker: str | None = None


class TTSProfileConfig(BaseModel):
    # Per-engine memory for the settings TUI, same shape/purpose as
    # BackendProfileConfig: switching tts.engine (kokoro <-> piper) stages
    # the OTHER engine's fields here first, so switching back restores
    # them instead of losing whatever voice/settings you had -- and lets
    # the Settings TUI build a real engine for EITHER one (e.g. for a
    # side-by-side comparison) without cross-contaminating the currently
    # active engine's own fields.
    voice: str | None = None
    model_path: str | None = None
    voices_path: str | None = None
    language: str | None = None
    rate: float | None = None
    volume: float | None = None
    speaker: str | None = None


class InteractionConfig(BaseModel):
    # What happens when the user talks while a response is playing --
    # one of the named presets in convobox.interrupt_presets.PRESETS
    # (docs/DESIGN-barge-in.md's two-axis grid: on_current_turn x
    # on_new_words). Default is "do-not-disturb" (let-finish + drop) --
    # behaviorally identical to the old interrupt_mode="none" default
    # (half-duplex: overlapping speech is dropped) -- deliberately NOT
    # switched to "conversational" by this migration. Whether
    # "conversational" should become the shipped default is a real
    # product decision flagged for live UAT, not something a schema
    # refactor should silently decide (docs/DESIGN-0.3.0-interaction-and-safety.md's
    # open questions). Non-"do-not-disturb"/"halt" presets need
    # audio.echo_cancellation (or headphones) -- see
    # docs/DESIGN-echo-and-barge-in.md -- without it the assistant's own
    # voice trips the VAD and it interrupts itself.
    interrupt_preset: str = "do-not-disturb"
    # Sustained speech required before barge-in fires, so a cough or a
    # chair creak doesn't kill a response.
    barge_in_min_speech_ms: int = 250

    @field_validator("interrupt_preset")
    @classmethod
    def _validate_interrupt_preset(cls, v: str) -> str:
        resolve_preset(v)  # raises ValueError listing valid choices
        return v
    # Shared by two independent features (docs/DESIGN-barge-in.md, "Pause/
    # resume listening"): the push-word barge-in trigger (future work) and
    # resuming from the paused listening state (below) both use this word.
    resume_word: str = DEFAULT_RESUME_WORD
    # Saying one of these hard-stops in-flight backend work (same as the
    # safeword) and enters a paused state where only resume_word is heard,
    # until it's said and normal listening resumes.
    pause_listening_phrases: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PAUSE_PHRASES)
    )
    # P8 (docs/DESIGN-barge-in.md, "Open questions"): an optional audio cue
    # on pause/resume so a paused session doesn't feel silently dead. "tone"
    # synthesizes a short ascending/descending arpeggio (convobox.audio.
    # ack_tones) -- no external asset. "file" (a user-supplied sound) is
    # intentionally not implemented yet -- not offered as a choice anywhere
    # until it is, so there's no dead-end option that just errors.
    pause_resume_ack: str = "none"
    # Response tiering (docs/DESIGN-0.3.0-interaction-and-safety.md, Phase
    # 2): "voice always gives the tiered/short version." Off by default --
    # existing sessions hear the full response exactly as before. When on,
    # only the first paragraph of a multi-paragraph response is spoken;
    # ContinueDetector's "continue"/"go on"/a bare "yes" within
    # continue_timeout_s of the response finishing speaks the rest.
    # Silence past the timeout implies "no" -- never treated as consent to
    # keep talking, same non-auto-approve spirit as approval prompts, just
    # for a much lower-stakes decision.
    tier_responses: bool = False
    # 1-4s range per the design doc; 2.5s split-the-difference default,
    # not yet live-UAT-tuned against a real "did that feel laggy or
    # naggy" pass.
    continue_timeout_s: float = 2.5
    # Phase 3 (docs/DESIGN-0.3.0-interaction-and-safety.md): the voice
    # phrase that gates a pending destructive-action tool call/command.
    # None (default) leaves voice approval OFF -- existing sessions behave
    # exactly as before (backend.permission_mode's own default, "plan",
    # already keeps every backend read-only regardless of this field).
    # There is no safe default phrase, same reasoning as
    # ConfirmwordDetector's own construction-time guard: this must be a
    # phrase the operator chose deliberately, not one this project picked
    # for them. Honored by both backends that can answer a real approval
    # request at runtime -- Codex's app-server (accept/decline) and Claude
    # Code (a PreToolUse hook, since headless mode has no native per-call
    # channel -- see claude_code.py's module docstring) -- when
    # backend.permission_mode is "approve" (see BackendConfig below).
    approval_phrase: str | None = None
    # Silence is an explicit denial, never consent -- long enough to read
    # the pending action and decide, but bounded so a forgotten request
    # doesn't leave an agent turn hanging indefinitely
    # (ApprovalPromptGate.observe_timeout). Longer than continue_timeout_s
    # on purpose: deciding whether to approve a real destructive action
    # deserves more time than a quick "continue/stop" reflex.
    approval_timeout_s: float = 30.0
    # "verbose" = raw approval data (tool name + JSON input, technical).
    # "plain" = human-friendly intent extraction (file paths, command names).
    # Only affects the explanation spoken back when the operator says "explain"
    # during a pending approval -- not the automatic approval announcement.
    approval_explanation_mode: str = "plain"

    @field_validator("approval_phrase")
    @classmethod
    def _validate_approval_phrase(cls, v: str | None) -> str | None:
        if v is not None:
            # Raises ValueError (with the real reason) if the phrase is
            # empty, made up entirely of common affirmations/fillers, or
            # collides with a deny phrase -- fail fast at config load, not
            # at the first live approval prompt. ApprovalDetector (which
            # wraps ConfirmwordDetector) is the authority on this; not
            # duplicated here.
            ApprovalDetector(v)
        return v

    @field_validator("approval_explanation_mode")
    @classmethod
    def _validate_explanation_mode(cls, v: str) -> str:
        if v not in ("plain", "verbose"):
            modes = "plain or verbose"
            raise ValueError(
                f"approval_explanation_mode must be {modes}, not {v!r}"
            )
        return v

    @field_validator("pause_resume_ack")
    @classmethod
    def _validate_pause_resume_ack(cls, v: str) -> str:
        if v not in ("none", "tone"):
            raise ValueError(
                f"pause_resume_ack must be none or tone, not {v!r}"
            )
        return v


class SafewordConfig(BaseModel):
    # Each phrase is a word tripled, not a single word -- the anti-false-
    # positive mechanism (a bare "stop" is common in normal conversation;
    # nobody says a word three times in a row by accident). "abort" and
    # "halt" were added 2026-08-09 as universal, en-US-broad emergency-
    # stop vocabulary with minimal overlap with this project's OWN
    # domain vocabulary (unlike candidates considered and rejected --
    # "kill"/"freeze" -- both of which are extremely common phrasing in
    # normal conversation ABOUT a coding-agent tool, e.g. "kill the
    # process"/"it froze", elevating false-positive risk specifically
    # here). Deliberately NOT added to any default stt.hotwords list --
    # docs/field-notes/2026-08-06-resume-word-hallucination-and-runaway-
    # repetition.md validated-live that hotwording a phrase makes it
    # both easier to say AND easier for the STT decoder to hallucinate
    # into a runaway repetition loop that falls through into a real
    # hard-stop; more default safewords is a deliberate stop-coverage
    # tradeoff, not something to also amplify via hotwords by default.
    hard_stop_phrases: list[str] = Field(
        default_factory=lambda: ["stop stop stop", "abort abort abort", "halt halt halt"]
    )


class BackendConfig(BaseModel):
    name: str = "opencode"
    # Used by HTTP-based backends (opencode).
    url: str = "http://localhost:4096"
    # Used by subprocess-based backends (claude-code): the base command to
    # spawn, e.g. ["claude"] or ["claude", "--model", "claude-haiku-4-5"].
    # The adapter appends the protocol flags it needs itself.
    command: list[str] | None = None
    # opencode only: pin which model a NEW session uses, "provider/model-id"
    # (matches `opencode models`' own output format, e.g.
    # "openai/gpt-5.6-sol"). None (default) leaves it to opencode's own
    # default -- confirmed live, 2026-07-14, that this can silently be a
    # hosted free-tier model (OpenCode Zen's own default) rather than the
    # user's own configured provider, with no error or warning either way.
    # NOT a CLI flag: `opencode serve` (the mode this adapter connects to)
    # has no -m/--model option at all (confirmed via `opencode serve
    # --help`) -- that flag only exists on `opencode run`/the interactive
    # TUI, neither of which this project's HTTP+SSE adapter uses. The real
    # mechanism, confirmed against a live server's own OpenAPI spec
    # (`GET /doc`), is `POST /api/session`'s optional `model: {providerID,
    # id}` field -- see OpenCodeAdapter._ensure_session().
    model: str | None = None
    # The directory the spawned coding agent (codex, claude-code) runs in --
    # i.e. where it reads and WRITES files. SECURITY-RELEVANT: a coding
    # agent edits its working directory, so pointing it at ConvoBox's own
    # source (the default when unset -- the subprocess inherits ConvoBox's
    # cwd) lets a voice conversation silently modify the product's own code
    # mid-session. Set this to an isolated workspace (e.g. a scratch/UAT
    # directory separate from any repo you care about) so the agent's edits
    # land there, not on your source. Overridable per-run with
    # `run_convobox.py --working-dir PATH`. Does NOT apply to the opencode
    # backend, whose directory is fixed by wherever `opencode serve` was
    # launched (not a subprocess ConvoBox spawns) -- see
    # docs/DESIGN-backend-sandboxing.md.
    working_dir: str | None = None

    # How much the spawned coding agent is allowed to DO -- the single
    # source of truth for the backend's write/execute posture, translated
    # per-backend at spawn (see convobox.adapters). SECURITY-relevant:
    #   plan       - read-only; the agent investigates but cannot write or
    #                run commands (the safe default).
    #   approve    - the agent may act, but every write/command requires
    #                voice approval (the approval_phrase gate). Real on both
    #                Codex (its app-server has a native per-call approval
    #                channel this adapter answers directly) and Claude Code
    #                (headless mode has no NATIVE per-call channel, so this
    #                adapter builds one: a PreToolUse hook + a local IPC
    #                channel -- see claude_code.py's module docstring for
    #                the mechanism and its live verification).
    #   permissive - BYPASSES ALL PERMISSIONS: the agent acts without
    #                asking on every tool call (Bash, WebFetch/WebSearch,
    #                MCP tools, file edits, everything), not just writes.
    #                Opt-in, dangerous.
    # opencode is unaffected (its permissions are fixed by wherever
    # `opencode serve` was launched) -- a warning is logged if this is set
    # for opencode. See docs/DESIGN-backend-sandboxing.md.
    permission_mode: str = "plan"

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str | None) -> str | None:
        if v is not None and "/" not in v:
            raise ValueError(
                f"backend.model {v!r} must be \"provider/model-id\" "
                f"(e.g. \"openai/gpt-5.6-sol\") -- see `opencode models` "
                f"for the full list"
            )
        return v

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, v: str) -> str:
        if v not in PERMISSION_MODES:
            raise ValueError(
                f"backend.permission_mode {v!r} must be one of "
                f"{', '.join(PERMISSION_MODES)}"
            )
        return v


# The valid backend.permission_mode values (see BackendConfig above).
PERMISSION_MODES = ("plan", "approve", "permissive")

# Permission-POSTURE flags that would fight backend.permission_mode if a
# user also put them in backend.command. Tool-SCOPING flags
# (--allowedTools/--disallowedTools) are deliberately excluded: they are
# orthogonal to the write/execute posture and compose fine with any mode.
_PERMISSION_CONFLICT_FLAGS: dict[str, tuple[str, ...]] = {
    "claude-code": ("--permission-mode", "--dangerously-skip-permissions"),
    "codex": (
        "--sandbox", "-s", "--ask-for-approval", "-a",
        "--dangerously-bypass-approvals-and-sandbox",
    ),
}


def detect_permission_conflict(backend: BackendConfig) -> str | None:
    """Return an error message if backend.command carries a permission-posture
    flag that conflicts with backend.permission_mode, else None.

    permission_mode is the single source of truth for the write/execute
    posture; letting a user ALSO set the posture via raw command flags means
    two sources silently disagreeing (e.g. permission_mode=plan while
    command has --dangerously-skip-permissions). For a safety control that
    is unacceptable, so this is surfaced as a hard error the user must
    resolve by removing one.
    """
    command = backend.command or []
    flags = _PERMISSION_CONFLICT_FLAGS.get(backend.name, ())
    for arg in command:
        head = arg.split("=", 1)[0]  # tolerate --flag=value form
        if head in flags:
            return (
                f"backend.command contains {head!r}, which sets the same "
                f"write/execute posture as backend.permission_mode "
                f"({backend.permission_mode!r}). These conflict -- remove one: "
                f"use permission_mode for the posture, or clear permission_mode "
                f"and drive it entirely through command."
            )
        # Codex's -c overrides of the posture config keys appear as their own
        # `key=value` arg (e.g. `-c approval_policy=never` is two tokens);
        # match the key directly rather than the `-c` token.
        if backend.name == "codex" and head in _CODEX_POSTURE_KEYS:
            return (
                f"backend.command overrides codex's {head!r} via -c, which "
                f"conflicts with backend.permission_mode "
                f"({backend.permission_mode!r}) -- remove one."
            )
    return None


_CODEX_POSTURE_KEYS = ("approval_policy", "sandbox_mode", "sandbox_permissions")


def detect_claude_code_approval_gap(
    backend: BackendConfig, interaction: InteractionConfig
) -> str | None:
    """Return an error message if claude-code's approval hook would be
    wired with nothing able to ever answer it, else None.

    Found via autonomous codebase review, 2026-08-08 (GitHub issue #235,
    finding A1). `ClaudeCodeAdapter._interactive_approval` is set purely
    from `permission_mode == "approve"` at construction -- independent of
    whether an approval gate exists. `set_interactive_approvals()` is a
    documented no-op for claude-code (its own module docstring: the hook
    is baked in at construction, not toggleable at runtime), so
    `scripts/run_convobox.py`'s `approval_gate` -- built only when
    `interaction.approval_phrase` is set -- is the ONLY thing that ever
    calls `resolve_pending_approval`. With the phrase unset, the hook still
    blocks every tool call for its full 120s timeout (with a misleading
    spoken "say your approval phrase" prompt substituting the literal
    `None`), and the stuck pending-approval state then silently
    auto-denies every subsequent tool call for the rest of the session --
    with no log line either time. `settings_tui.validate_config` already
    warns about this combination, but its warning text describes "denied
    with no voice prompt (the safe fail-closed default)", which is not
    what actually happens (a 120s hang with a broken prompt first, then
    silent denials) -- promoted here to a hard error instead, same
    fail-closed treatment `detect_permission_conflict` already gets for
    the analogous command-flag conflict above.
    """
    if backend.name != "claude-code":
        return None
    if backend.permission_mode != "approve":
        return None
    if interaction.approval_phrase is not None:
        return None
    return (
        "backend.permission_mode is \"approve\" for claude-code, but "
        "interaction.approval_phrase is unset -- the approval hook would "
        "be wired with nothing able to ever answer it: the first tool "
        "call hangs for its full timeout with a broken spoken prompt, "
        "then every later one is silently auto-denied for the rest of "
        "the session. Set interaction.approval_phrase, or use a "
        "different permission_mode (\"plan\"/\"permissive\")."
    )


class WebConfig(BaseModel):
    # Off by default -- the web UI (docs/WEB-UI-ARCHITECTURE.md) is opt-in,
    # same posture as everything else security/privacy-relevant in this
    # config (echo_cancellation, approval_phrase, etc.).
    enabled: bool = False
    # Loopback only by default: this server has no authentication (the
    # local-device trust model docs/WEB-UI-ARCHITECTURE.md's Security
    # section describes), so binding it to a non-loopback address exposes
    # an unauthenticated view of live transcripts/tool calls to anything
    # that can reach the port. 0.0.0.0 is allowed but only as an explicit,
    # deliberate choice -- see the validator below.
    bind_address: str = "127.0.0.1"
    port: int = 5173
    # Persisting transcripts/tool-call history to disk is a bigger privacy
    # commitment than just viewing a live session, so it needs its own
    # opt-in separate from `enabled` -- enabling the web UI alone must not
    # silently start writing history.
    history_tracking_enabled: bool = False
    history_dir: str = ".convobox-history"

    @field_validator("bind_address")
    @classmethod
    def _validate_bind_address(cls, v: str) -> str:
        # nosec B104 -- 0.0.0.0 is a deliberate, explicit opt-in; every other
        # non-loopback address is rejected below.
        if v in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):  # nosec B104
            return v
        if v.startswith("127."):  # rest of the IPv4 loopback block
            return v
        raise ValueError(
            f"web.bind_address {v!r} is a specific non-loopback address, "
            "which this server (no authentication) should never be bound "
            "to directly -- use 127.0.0.1 (localhost) for local-only "
            "access, or 0.0.0.0 if you deliberately want it reachable on "
            "every interface (e.g. from another device on your LAN)."
        )

    @field_validator("port")
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"web.port {v!r} must be between 1 and 65535")
        return v


class BackendProfileConfig(BaseModel):
    # Per-backend memory for the settings TUI. `url`/`model` matter for
    # opencode; `command` matters for claude-code and codex.
    url: str | None = None
    command: list[str] | None = None
    model: str | None = None


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class DisplayConfig(BaseModel):
    # Per-role bubble background color overrides for the web UI
    # (docs/WEB-UI-ARCHITECTURE.md). None (default) keeps
    # web/static/index.html's own built-in light/dark theme colors
    # untouched. When set, the color applies in BOTH light and dark mode
    # alike -- the frontend sets it as an inline CSS custom property on
    # the root element, which outranks the @media-scoped :root defaults
    # regardless of the system theme, rather than needing a separate
    # light/dark pair per role.
    user_color: str | None = None
    assistant_color: str | None = None
    # Display labels for the web UI's bubble "meta" line (e.g. "AI" ->
    # "Athena"). None (default) keeps index.html's current behavior of
    # showing the raw event_type ("transcript"/"response"). Only affects
    # transcript/response bubbles -- tool_call/tool_result/error/
    # approval_request keep showing their literal event type, since a
    # custom name there would obscure what kind of event it actually is.
    user_name: str | None = None
    assistant_name: str | None = None

    @field_validator("user_color", "assistant_color")
    @classmethod
    def _validate_hex_color(cls, v: str | None) -> str | None:
        if v is not None and not _HEX_COLOR_RE.match(v):
            raise ValueError(
                f"{v!r} is not a valid hex color -- use #RGB or #RRGGBB, "
                "e.g. #2e7dfb"
            )
        return v


class AppConfig(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    tts_profiles: dict[str, TTSProfileConfig] = Field(default_factory=dict)
    safeword: SafewordConfig = Field(default_factory=SafewordConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    backend_profiles: dict[str, BackendProfileConfig] = Field(default_factory=dict)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)


def resolve_config_path(path: str | Path | None = None) -> Path:
    """The same explicit-path / CONVOBOX_CONFIG / convobox.yaml fallback
    load_config() uses, exposed so callers that need to know WHICH file
    would be loaded (not just its parsed contents) don't have to
    duplicate the resolution order -- settings_tui.py's own
    default_config_path() and run_convobox.py's AEC-estimate sidecar path
    both need this."""
    return Path(path) if path else Path(os.environ.get("CONVOBOX_CONFIG", "convobox.yaml"))


def load_config(path: str | Path | None = None) -> AppConfig:
    candidate = resolve_config_path(path)
    if not candidate.exists():
        return AppConfig()
    with candidate.open() as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


def load_config_lenient(
    path: str | Path | None = None,
) -> tuple[AppConfig, dict[str, Any], list[str]]:
    """Like load_config, but never raises on a bad on-disk value: any
    top-level section that fails its own validation (e.g. an incompatible
    stt.compute_type/stt.device pairing) falls back to that section's
    defaults instead of failing the whole file, and the caller gets back
    which section(s) were rejected and why.

    Exists for settings_tui.py's own startup load -- the one tool meant to
    let an operator FIX a bad convobox.yaml previously couldn't open at
    all if the file already had one (live UAT, 2026-08-06: a leftover
    stt.compute_type: float16 with stt.device: cpu, from PR #210's own
    live-test, crashed both run_convobox.py -- correctly, that one should
    refuse to start a voice session on an unvalidated config -- AND
    settings_tui.py, which shouldn't, since recovering from exactly this
    is its entire purpose). run_convobox.py intentionally keeps using
    plain load_config()/model_validate() and still hard-fails on a bad
    config; only the settings editor gets the forgiving path in.

    Returns (config, raw, problems): config is always a fully valid
    AppConfig (bad sections reset to their defaults); raw is the
    as-parsed YAML dict, unchanged, so a caller can still show what the
    rejected value actually was; problems is a list of human-readable
    "section.field: message" strings (pydantic's own error locations/
    messages), empty when the whole file validated cleanly.
    """
    candidate = resolve_config_path(path)
    if not candidate.exists():
        return AppConfig(), {}, []
    with candidate.open() as f:
        raw = yaml.safe_load(f) or {}
    try:
        return AppConfig.model_validate(raw), raw, []
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        ]
        kwargs: dict[str, Any] = {}
        for name, model_field in AppConfig.model_fields.items():
            section_raw = raw.get(name)
            if section_raw is None:
                kwargs[name] = model_field.get_default(call_default_factory=True)
                continue
            try:
                # TypeAdapter handles both plain-BaseModel sections (stt,
                # tts, ...) and the dict[str, ...] profile maps
                # (tts_profiles/backend_profiles) through the same call --
                # no need to special-case the two shapes.
                kwargs[name] = TypeAdapter(model_field.annotation).validate_python(section_raw)
            except ValidationError:
                kwargs[name] = model_field.get_default(call_default_factory=True)
        return AppConfig(**kwargs), raw, problems


def aec_estimate_path(config_path: Path) -> Path:
    """A diagnostic sidecar next to the config file, not part of the
    config schema itself: run_convobox.py writes the AEC delay it
    actually auto-estimated (aec_delay_ms=None, the auto-tune case) here
    on every startup, so the Settings TUI can show "last auto-detected"
    for a value that only ever exists at runtime, without either process
    mutating convobox.yaml itself (that file should only ever reflect
    what the user deliberately set) or the two processes needing a live
    connection to each other."""
    return config_path.with_name(config_path.name + ".aec-estimate.json")


def write_aec_estimate(
    config_path: Path, delay_ms: int, output_latency_ms: float, input_latency_ms: float
) -> None:
    """Best-effort only -- a diagnostic write must never crash the voice
    loop over a permissions error or a read-only filesystem."""
    try:
        aec_estimate_path(config_path).write_text(
            json.dumps(
                {
                    "delay_ms": delay_ms,
                    "output_latency_ms": round(output_latency_ms, 1),
                    "input_latency_ms": round(input_latency_ms, 1),
                    "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        )
    except OSError:
        pass


def read_aec_estimate(config_path: Path) -> dict[str, Any] | None:
    """The counterpart read, for the Settings TUI -- also best-effort:
    a missing/corrupt sidecar (never written yet, or from a stale format)
    just means "nothing to show," never a crash."""
    try:
        path = aec_estimate_path(config_path)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text())
        return data
    except (OSError, json.JSONDecodeError):
        return None
