---
title: Settings TUI swallowed a second, different arrow key pressed shortly after a first one -- root-caused to tty.setraw()'s TCSAFLUSH default plus a buffered-I/O-vs-select() desync, both fixed and pty-verified
status: validated-live-pty
date: 2026-08-31
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 + local settings-TUI fixes; scripts/settings_tui.py's read_key()/_handle_browse(); Linux (pty.openpty()/os.fork(), termios)
evidence:
  - Live pty harness (os.fork() + pty.openpty(), no operator round-trip needed) driving the real read_key() function with raw escape bytes written before the first call, isolating each hypothesis one at a time
  - tests/test_settings_tui.py, 162/162 green after the fix
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; found and precisely described the symptom -- "right, right" worked but "right, left" kept showing right -- while live-testing the arrow-key fix from the day before)
    - Claude Code (Anthropic claude-sonnet-5) -- reproduced the symptom in a pty harness, root-caused both underlying bugs, applied and verified the fix, wrote this note
  org: https://legionforge.org
  created: 2026-08-31T22:30:00+00:00
  revised: 2026-08-31T22:30:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# The second arrow key that never happened

Yesterday's fix ([`docs/field-notes/2026-08-30-settings-tui-arrow-keys-fixed-via-live-debug-instrumentation.md`](2026-08-30-settings-tui-arrow-keys-fixed-via-live-debug-instrumentation.md))
widened `read_key()`'s escape-sequence `select()` timeout to 1.0s and
fixed the "need to press an arrow key multiple times" symptom,
confirmed live. Today's live report was different and more precise:

> "double pressing seems to work, but I tested pressing one arrow key
> then another, the behavior seemed to be that the TUI would act on
> the first arrow pressed when I pressed the second (even different)
> arrow key. so if I hit right, right, the tui responded by selecting
> right. but if I pressed right, left, the tui responded by selecting
> right."

Right-then-right and right-then-left producing the *same visible
result* is the tell: it's not that the second press did the wrong
thing, it's that the second press did **nothing**, and right-then-right
just couldn't tell the difference (both outcomes look identical when
the second keystroke is silently dropped). Right-then-left could tell
the difference, and did.

## Reproducing it without a live round-trip

The 2026-08-30 investigation needed the operator's own terminal because
earlier `pty.fork()` attempts kept failing for unrelated environment
reasons (documented in that entry). This time a simpler harness worked
cleanly: `pty.openpty()` for a master/slave pair, `os.fork()` a child
that imports the real `settings_tui` module and calls the real
`read_key()` twice, and the parent writes both key sequences to the
master *before* the child ever calls `read_key()` the first time --
maximizing the chance that both are already sitting in the kernel's
input queue by the time the code runs, which is the condition a fast
double-press approximates.

```python
os.write(master, b"\x1b[C\x1b[D")  # Right, then Left, no gap
```

First run against the unmodified code: the harness **hung**. Adding a
print after every intermediate step isolated exactly where.

## Bug 1: `tty.setraw()`'s default silently discards queued input

`read_key()` calls `tty.setraw(fd)` at the top of *every* single call,
not just once when the TUI starts. Python's `tty.setraw()` signature is:

```python
def setraw(fd, when=TCSAFLUSH):
```

`TCSAFLUSH` means: apply the new terminal attributes, **and discard any
input that has been received but not yet read**. Every time
`read_key()` is called again -- including the very next call right
after handling a keypress -- this line throws away whatever's currently
sitting in the kernel's tty input queue.

Given the main loop's `draw()` + dispatch cost between `read_key()`
calls, a second keystroke typed quickly enough to land in that queue
before the *next* call's own `tty.setraw()` runs gets wiped before a
single byte of it is ever read. Isolated directly in the harness: with
only this bug's cause active, the Left sequence's bytes vanished at
that exact line, and the second `read_key()` call then hung forever
waiting on input that was never coming -- because it had already been
thrown away, not because it hadn't arrived yet.

Fix: `tty.setraw(fd, termios.TCSANOW)`. Same raw-mode switch, no
discard.

## Bug 2 (separate, coexisting): buffered `sys.stdin.read()` vs. raw-fd `select()`

Independently, `read_key()`'s leading-byte read used
`sys.stdin.read(1)` -- a *buffered* `TextIOWrapper` call. Even though
only 1 byte is requested, the buffered reader underneath can do a
single `os.read()` syscall that grabs everything currently pending (up
to its internal buffer size) and caches the rest in Python's own
userspace buffer, handing back just the first byte.

The very next line calls `select.select([sys.stdin], [], [], 1.0)` to
gate the rest of the escape sequence. `select()` only knows about the
*kernel* fd -- it has no visibility into Python's userspace buffer. If
the leading read already drained everything the kernel had, `select()`
sees nothing pending and can time out (return not-ready) even though
the actual bytes it's waiting for are sitting right there, one Python
attribute access away, just invisible to it.

Isolated directly: a minimal repro using `sys.stdin.read(1)` +
`select.select([sys.stdin], ...)` on a pty pre-loaded with a full
6-byte Right+Left sequence printed `ch='\x1b'` immediately followed by
`select timed out` -- confirming the desync happens exactly as
described, not just in theory.

Fix: read everything through `os.read(fd, 1)` instead of
`sys.stdin.read()`, so what's been consumed always matches exactly
what `select()` can see on the raw fd. Text fields can contain
non-ASCII typed input (unlike the ASCII-only CSI/SS3 escape bytes), so
a small `_read_utf8_char()` helper reads a leading byte's UTF-8-length
prefix and pulls the right number of continuation bytes off the same
raw fd -- still no `sys.stdin` buffering involved anywhere.

## Verification

Against the real, patched `read_key()`, via the same pty harness:

| Input | Result |
|---|---|
| `\x1b[C\x1b[D` (Right, Left) | `['RIGHT', 'LEFT']` -- correct, previously the reported bug |
| `\x1b[C\x1b[C\xc3\xa9` (Right, Right, "é") | `['RIGHT', 'RIGHT', 'é']` -- no regression on the case that already looked fine, and typed UTF-8 still decodes correctly |

`tests/test_settings_tui.py`: 162/162 green after the fix.

## A third, unrelated bug found while tracing the "nothing happened" symptom

While confirming that a swallowed key produces a silent no-op rather
than some visible error, `_handle_browse`'s key-normalization line
turned out to have its own defect:

```python
lowered = key.lower() if len(key) == 1 else key
```

`read_key()` returns `"ESC"` -- three characters -- for a real Escape
press, so this guard leaves it uncased, and
`if lowered in ("q", "esc")` can **never match it**. A real Escape
press in the browse screen has always been a complete no-op: no
quit-confirm, nothing. Unrelated to the two bugs above, but it's part
of why a swallowed keystroke reads as total silence rather than any
kind of feedback. Fixed by unconditionally lowercasing (`key.lower()`)
-- safe, since every multi-character token's lowered form (`"up"`,
`"enter"`, etc.) is still distinct from the single-character bindings
compared against it.

## What's still open

This entry's evidence is a pty harness, not the operator's own
terminal. Per this project's own established bar for input bugs (see
the 2026-08-30 entry, where a "plausible" fix twice needed a live
operator round-trip before it was actually confirmed), this fix should
still get a live retest -- specifically the exact right/right vs.
right/left contrast that surfaced it -- before being called fully
closed.
