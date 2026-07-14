"""Pure rendering for the live conversation TUI. No terminal I/O here --
render_conversation_frame() takes an explicit width/height and returns
plain text lines, same split as scripts/settings_tui.py's render_modal()
(pure, unit-tested) vs. its _draw_modal() (resolves the real terminal
size and writes to stdout). The eventual _draw wrapper and the
run_convobox.py wiring are a follow-up PR -- this one is just "what does
it look like," reviewable on its own.
"""

from __future__ import annotations

from convobox.tui.state import ConversationTuiState, TuiStatus

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_MAGENTA = "\x1b[35m"
_CYAN = "\x1b[36m"

_STATUS_LABEL: dict[TuiStatus, str] = {
    "listening": "LISTENING",
    "capturing": "CAPTURING",
    "transcribing": "TRANSCRIBING",
    "working": "WORKING",
    "speaking": "SPEAKING",
    "paused": "PAUSED",
}

_STATUS_COLOR: dict[TuiStatus, str] = {
    "listening": _CYAN,
    "capturing": _GREEN + _BOLD,
    "transcribing": _YELLOW,
    "working": _YELLOW,
    "speaking": _GREEN,
    "paused": _MAGENTA,
}

_SPEAKER_LABEL: dict[str, str] = {
    "user": "you",
    "assistant": "assistant",
    "system": "*",
}

_SPEAKER_COLOR: dict[str, str] = {
    "user": _CYAN,
    "assistant": _RESET,
    "system": _DIM,
}

# Minimum usable size -- below this the layout math (pane splits, wrapping)
# stops making sense, same floor settings_tui.py's render_modal() enforces.
_MIN_WIDTH = 60
_MIN_HEIGHT = 16


def _fit(text: str, width: int) -> str:
    """Pad/truncate to `width` VISIBLE characters, ellipsis on real
    truncation. ANSI-aware (delegates to _visible_len/_clip_visible below)
    -- a naive len()-based fit would overcount every color escape
    sequence's byte length as visible text, truncating colored lines that
    actually fit (caught live: a colored "assistant:" label line got a
    spurious "..." mid-word even though the real rendered width was well
    within bounds)."""
    if width <= 0:
        return ""
    visible = _visible_len(text)
    if visible <= width:
        return text + " " * (width - visible)
    if width <= 3:
        return _clip_visible(text, width)
    return _clip_visible(text, width - 3) + "..."


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. Deliberately not textwrap.wrap(): that collapses
    blank lines (paragraph breaks), which would flatten multi-paragraph
    responses into a wall of text -- preserve them."""
    if width <= 0:
        return []
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def _elapsed_label(state: ConversationTuiState, now: float) -> str:
    elapsed = max(0, int(now - state.started))
    return f"{elapsed // 60:02d}:{elapsed % 60:02d}"


def render_conversation_frame(
    state: ConversationTuiState, width: int, height: int, now: float
) -> list[str]:
    """Render the full conversation TUI as plain lines, clamped to exactly
    `height` lines each `width`-wide (safe to write directly to a
    fixed-size terminal without extra clamping by the caller).

    `now` is passed explicitly (not time.monotonic() internally) for the
    same reason state.started is a plain float: deterministic, real-time-
    free unit tests.
    """
    width = max(width, _MIN_WIDTH)
    height = max(height, _MIN_HEIGHT)

    status_color = _STATUS_COLOR[state.status]
    status_label = _STATUS_LABEL[state.status]
    barge_flag = f"  {_RED}{_BOLD}[BARGE-IN]{_RESET}" if state.barge_in_active else ""
    header = (
        f"{_BOLD}ConvoBox{_RESET}  {status_color}{status_label}{_RESET}{barge_flag}"
    )
    elapsed = _elapsed_label(state, now)
    right = f"elapsed {elapsed}"
    pad = max(1, width - _visible_len(header) - len(right))
    lines: list[str] = [_clip_visible(header + " " * pad + right, width)]
    lines.append("-" * width)

    # Warning banner (phase 3) only takes space when actually set -- costs
    # nothing before approvals exist, and stays unmissable once they do
    # (loud color, its own bordered block, never folded into the
    # transcript scroll where it could roll off-screen unread).
    warning_lines: list[str] = []
    if state.warning is not None:
        warning_lines.append(f"{_RED}{_BOLD}{'!' * width}{_RESET}")
        for wrapped in _wrap(state.warning, width - 4):
            warning_lines.append(
                f"{_RED}{_BOLD}! {_RESET}{_fit(wrapped, width - 4)}{_RED}{_BOLD} !{_RESET}"
            )
        warning_lines.append(f"{_RED}{_BOLD}{'!' * width}{_RESET}")

    footer = f"{_DIM}Ctrl+C to exit{_RESET}"
    fixed_lines = len(lines) + len(warning_lines) + 1  # +1 footer
    body_height = max(4, height - fixed_lines)
    # Transcript gets the larger share (it's the primary "what's
    # happening" view); full-detail is secondary/reference.
    transcript_height = max(2, round(body_height * 0.6)) - 1  # -1 for its own header
    detail_height = max(2, body_height - transcript_height - 1) - 1  # -1 for its own header

    lines.append(f"{_BOLD}Transcript{_RESET}")
    transcript_lines = _render_transcript(state, width, transcript_height)
    lines.extend(transcript_lines)
    lines.append("-" * width)

    lines.append(f"{_BOLD}Full response{_RESET}")
    detail_lines = _wrap(state.full_detail, width) if state.full_detail else []
    if not detail_lines:
        detail_lines = [f"{_DIM}(nothing yet){_RESET}"]
    detail_lines = detail_lines[-detail_height:] if len(detail_lines) > detail_height else detail_lines
    detail_lines += [""] * max(0, detail_height - len(detail_lines))
    lines.extend(_fit(line, width) for line in detail_lines[:detail_height])

    lines.extend(warning_lines)
    lines.append(footer)

    # Pad/truncate to exactly `height` -- callers write this straight to
    # the terminal without their own bookkeeping.
    if len(lines) < height:
        lines.extend([""] * (height - len(lines)))
    return lines[:height]


def _render_transcript(state: ConversationTuiState, width: int, pane_height: int) -> list[str]:
    if not state.turns:
        return [f"{_DIM}(nothing heard yet){_RESET}"] + [""] * max(0, pane_height - 1)

    rendered: list[str] = []
    for turn in state.turns:
        color = _SPEAKER_COLOR[turn.speaker]
        label = _SPEAKER_LABEL[turn.speaker]
        prefix = f"{turn.timestamp}  {color}{label}:{_RESET} "
        prefix_len = _visible_len(prefix)
        for wrapped in _wrap(turn.text, max(10, width - prefix_len)):
            rendered.append(_fit(prefix + wrapped, width))
            prefix = " " * prefix_len  # continuation lines: indent, no repeated label

    tail = rendered[-pane_height:] if len(rendered) > pane_height else rendered
    tail += [""] * max(0, pane_height - len(tail))
    return tail[:pane_height]


def _visible_len(text: str) -> int:
    """Length ignoring ANSI escape sequences, so padding math accounts for
    real screen width, not raw string length (which would overcount by
    every color code's byte length)."""
    length = 0
    i = 0
    while i < len(text):
        if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = text.find("m", i)
            i = j + 1 if j != -1 else len(text)
            continue
        length += 1
        i += 1
    return length


def _clip_visible(text: str, width: int) -> str:
    """Truncate to `width` VISIBLE characters, preserving ANSI codes and
    always closing with a reset so color never bleeds into the next line."""
    if _visible_len(text) <= width:
        return text
    out = []
    length = 0
    i = 0
    while i < len(text) and length < width:
        if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = text.find("m", i)
            end = j + 1 if j != -1 else len(text)
            out.append(text[i:end])
            i = end
            continue
        out.append(text[i])
        length += 1
        i += 1
    out.append(_RESET)
    return "".join(out)
