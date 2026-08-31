---
title: Settings TUI's idle-resize repaint fixed with a SIGWINCH handler -- which surfaced two more bugs of its own before it shipped, both fixed and pty-verified
status: validated-live
date: 2026-08-31
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main + local settings-TUI fixes; scripts/settings_tui.py's draw()/_draw_modal()/run_tui(); Linux (pty.fork(), SIGWINCH)
evidence:
  - Two real pty.fork()-spawned runs of the actual scripts/settings_tui.py process (same methodology as the 2026-08-30 size-bug confirmation), raw bytes captured and inspected directly
  - tests/test_settings_tui.py, 162/162 green (156 before this session, +6 new: too-small fallback, modal-not-inflated, SIGWINCH install/restore, _modal_depth tracking x2)
  - Full repo test suite (tests/), 1559 passed / 9 skipped, no regressions
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; asked for the terminal-size known-issue to be continued after a session reboot)
    - Claude Code (Anthropic claude-sonnet-5) -- implemented the fix, found and fixed the two bugs it introduced, built and ran the pty verification, wrote this note
  org: https://legionforge.org
  created: 2026-08-31T02:00:00+00:00
  revised: 2026-08-31T02:00:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The resize fix that needed two fixes of its own

`docs/KNOWN-ISSUES.md`'s terminal-size entry described two compounding
bugs from 2026-08-30: `render()` forced an 80x24 floor regardless of the
real terminal (fixed same day), and there was no live-resize repaint --
resizing while idle left the stale layout on screen until the next
keypress. This note is about closing that second half, and the two real
bugs the fix itself introduced along the way -- worth keeping because
neither would have been obvious from reading the diff alone; both only
showed up under a real pty run.

## The fix: SIGWINCH, not a poll loop

`run_tui()`'s main loop blocks inside `read_key()`'s raw-mode
`sys.stdin.read(1)` between keypresses. Rather than restructure that into
a poll/select loop, installing a `SIGWINCH` handler that calls
`draw(state)` directly is enough: Python (PEP 475) automatically retries
a syscall interrupted by a signal once the handler returns, so
`read_key()`'s blocking read keeps waiting for a real key exactly as
before -- the handler just repaints in between. Not available on Windows
(no `SIGWINCH`), where behavior is unchanged from before this fix.

## Bug found while building it #1: bare `\n` corrupts a frame written mid-raw-mode

`draw()` joined rendered lines with `"\x1b[K\n"`, relying on the tty
driver's OPOST/ONLCR flags to translate that into `\r\n` for a real
terminal. That translation is only active in "cooked" mode -- `read_key()`
disables it via `tty.setraw(fd)` for the duration of its own blocking
read, restoring it in a `finally` once a key arrives. Before this fix,
`draw()` was never called from anywhere but the main loop, always
*between* raw-mode windows, so this never mattered. A `SIGWINCH` handler
is asynchronous by nature -- it can fire while `read_key()` is mid-block,
raw mode already active. Confirmed directly, not inferred: a real
`pty.fork()`-captured resize frame came back as one 2314-byte "line" with
zero `\r` bytes in it, because the bare `\n` moved the cursor down without
returning it to column 0. Fixed by having `draw()`/`_draw_modal()` write
`"\x1b[K\r\n"` explicitly instead of depending on OPOST/ONLCR at all.

## Bug found while building it #2: an idle resize would blow away an open modal

The naive handler just calls `draw(state)` unconditionally. But
`run_tui()`'s `SIGWINCH` handler is installed for the whole process, and
three other functions (`_edit_value_interactive`, `_confirm_modal`,
`_scrollable_test_picker_modal`) each run their *own* `read_key()`/
`_draw_modal()` loop while a modal is on screen -- also idle-blocked, also
reachable by the same signal. An unconditional `draw(state)` firing there
would repaint the MAIN browse screen on top of whatever modal the
operator is actually looking at, which is worse than the original bug
(stale-but-legible modal vs. the wrong screen entirely). Caught before it
shipped by reasoning through what else calls `read_key()` in this file,
not by a live report -- fixed with a module-level `_modal_depth` counter
(a counter, not a bool, so a Confirm Quit dialog opened from inside an
already-open field editor doesn't have the inner dialog's exit clear the
flag while the outer editor is still showing), set by a
`@_tracks_modal_depth` decorator on all three modal functions. The
handler now skips its repaint whenever `_modal_depth > 0`.

## Live verification: two real pty.fork() runs, not just render() called directly

Same methodology as the 2026-08-30 size-bug confirmation -- spawn the
actual `scripts/settings_tui.py` process under a real `pty.fork()`
pseudo-terminal, resize it via `TIOCSWINSZ` (the kernel delivers
`SIGWINCH` to the pty's foreground process group on a real resize, same
as a real terminal emulator), and read back raw bytes.

**Run 1 -- idle resize in the main browse loop, no keystroke sent:**

```
initial frame (1576 bytes): longest visible line = 71 (expected ~70)
post-resize frame (2614 bytes, no keypress sent): longest visible line = 101 (expected ~100)
RESULT: PASS
```

**Run 2 -- idle resize while an edit modal is open, then dismiss it:**

```
main screen frame: 1576 bytes
modal frame: 1600 bytes, contains 'Edit ': True
output during idle resize while modal open: 0 bytes (expected 0)
post-ESC main-screen frame: 3316 bytes, longest visible line = 111 (expected ~110)
RESULT: PASS
```

Zero bytes during the idle resize with a modal open confirms the guard
actually suppresses the repaint (not just that it compiles); the correct
111-column frame after Esc confirms the guard didn't leave the resize
unprocessed either -- the next real redraw picks up the new size exactly
as it always would have.

## What this is not

This is agent-run pty automation, not the operator's own live terminal
session -- the same distinction the 2026-08-30 arrow-key note draws
between a debug-log-confirmed fix and one the operator has personally
exercised. Real bytes from a real spawned process are strong evidence
(strong enough to have caught the OPOST bug above, which a unit test
calling `render()` directly never would have), but an actual live
session in the operator's own terminal is still worth doing before
calling this fully closed.

## What transfers

- A `SIGWINCH`-based idle-redraw handler for a raw-mode terminal app must
  not assume the terminal is in cooked (OPOST/ONLCR-translating) mode --
  it can fire while another part of the same process has deliberately
  put the terminal into raw mode for its own blocking read. Emit `\r\n`
  explicitly rather than relying on the driver to supply it. (validated-live)
- Any async repaint trigger (signal handler, timer, background thread)
  added to an app with more than one modal/loop level needs to know
  *which* loop currently owns the screen, not just repaint "the" screen
  -- otherwise it will clobber whatever's actually displayed the moment
  a second loop level exists. A depth counter, not a bool, if that second
  level can itself nest. (validated-live)
- `pty.fork()` + `TIOCSWINSZ` on the master fd is a reliable way to
  exercise a real terminal resize end-to-end, including the kernel's own
  `SIGWINCH` delivery to the foreground process group -- no need to send
  the signal manually. (validated-live)
