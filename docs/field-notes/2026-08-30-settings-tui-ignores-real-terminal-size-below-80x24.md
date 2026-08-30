---
title: Settings TUI ignores real terminal size below 80x24, and never repaints on resize alone -- diagnosed via direct render() call
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 121d771 (post-v0.4.0); scripts/settings_tui.py; openSUSE Tumbleweed
evidence:
  - Live operator report on Linux ("the settings tui isn't rendering right either, and it isn't autosizing for the terminal size")
  - Direct call to `render(state, width=60, height=20)` from a `uv run python -c ...` one-liner, output inspected directly
  - `tests/test_settings_tui.py` full run (156/156 passed) confirming no existing coverage of resize/small-terminal behavior
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; reported the symptom live on Linux, asked for this note)
    - Claude Code (Anthropic claude-sonnet-5) -- read the source, reproduced the root cause with a direct function call, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T06:14:00+00:00
  revised: 2026-08-30T06:14:00+00:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Settings TUI ignores real terminal size below 80x24

## Symptom

Reported live on Linux: the Settings TUI (`scripts/settings_tui.py`)
"isn't rendering right" and "isn't autosizing for the terminal size."

## Root cause 1: a hardcoded minimum floor, confirmed by direct call

`render(state, width, height)` opens with:

```python
width = max(width, 80)
height = max(height, 24)
```

Confirmed live by calling it directly against a real loaded config,
bypassing the interactive loop entirely:

```
$ uv run python -c "
import sys; sys.path.insert(0, 'scripts')
import settings_tui as st
from convobox.config import load_config_lenient, resolve_config_path
config, _raw, _ = load_config_lenient(resolve_config_path())
state = st.TuiState(path=resolve_config_path(), original=config, working=config.model_copy(deep=True))
lines = st.render(state, width=60, height=20)
print('rendered line count:', len(lines))
print('rendered line width:', len(lines[0]))
"
rendered line count: 22
rendered line width: 80
```

Requested 60x20; got 22 lines of exactly 80 characters each. On any real
terminal narrower than 80 columns or shorter than 24 rows -- a common
split-pane or tiled-window-manager size, not an edge case -- every line
this emits is wider than the actual terminal, so the terminal itself
wraps each logical row into two or more visual rows.

## Root cause 2: no live-resize repaint

`run_tui()`'s main loop:

```python
while running:
    draw(state)
    key = read_key()
    ...
```

`read_key()` blocks inside raw-mode `sys.stdin.read(1)` with no timeout
on the primary read (only a 0.05s `select()` for escape-sequence
continuation bytes). There is no `SIGWINCH` handler. Resizing the
terminal while the TUI is idle -- no key pressed -- leaves the stale
layout on screen until the next keystroke, which is when
`os.get_terminal_size()` is next read (inside `draw()`, at the top of
the loop's next iteration).

## Why the two compound into "not rendering right," not just "clipped"

`draw()`'s repaint scheme is `"\x1b[H"` (cursor-home) followed by one
`"\x1b[K"`-cleared write per logical line, which assumes each logical
line occupies exactly one visual terminal row. Once root cause 1 is
forcing 80-column lines onto a narrower real terminal, that assumption
breaks: the terminal wraps some lines into two visual rows, so
`"\x1b[H"` no longer lands the cursor at a boundary consistent with the
previous frame's actual rows. Combined with root cause 2 -- a session
that starts wide enough (80+) and is later resized smaller keeps
rendering as if nothing changed until the next key, then abruptly hits
the wrapping/misalignment above on the next repaint -- this produces a
garbled, overlapping appearance rather than a clean truncation.

## Confirmed not a coverage gap being missed by existing tests

`uv run pytest tests/test_settings_tui.py -v` -- **156/156 passed**, same
day. None of the existing tests exercise `render()` with a width or
height below the 80x24 floor, nor the resize-while-idle path -- this is
a genuine untested gap, not a regression in previously-covered behavior.

## Not yet built

- Clamping `render()`'s layout to the real terminal size, with an
  explicit "terminal too small" message instead of a forced 80x24 layout
  when the real terminal is smaller than some sane minimum.
- Either a `SIGWINCH` handler or a short poll timeout in `run_tui()`'s
  main loop so a resize repaints without waiting on a keypress.
- Not yet measured: the smallest terminal size the TUI can be made to
  render correctly at, once (1) is fixed -- likely still needs a real
  floor below which a "too small" message is the right answer rather
  than attempting a full layout.
