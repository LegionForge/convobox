# Design: a guided setup path for non-technical ConvoBox users

Written as a design pass before any code, same reasoning as
`docs/ARTIFACT-PANE-SCOPE.md`/`docs/SETTINGS-TUI-SCOPE.md`: JP asked for
"a good installer or install capability for non-technical users" —
before building anything, this doc surveys what's actually possible
given ConvoBox's real dependency shape, and recommends one approach
rather than leaving it as an open menu.

## Current state (docs/QUICKSTART.md, pyproject.toml)

Today's install path is `git clone` → `uv sync` → hand-edit
`convobox.yaml` → run a voice-picker TUI → run an audio-device-test
script → run `run_convobox.py`. Every step assumes comfort with a
terminal, git, and YAML. `scripts/bootstrap_windows.ps1` exists but is a
**developer environment smoke-test** (verifies Python/uv versions, runs
the test suite, reports PASS/FAIL per step) — not a non-technical
end-user onboarding flow. There is no existing precedent for the thing
being asked for here; this is a real gap, not a duplicate of something
already built.

## The real constraints, honestly assessed

Three things make ConvoBox specifically NOT a great fit for a
"double-click, fully self-contained, does everything" installer, no
matter which packaging tool is chosen:

1. **Native audio hardware access.** `sounddevice`/PortAudio needs real
   mic/speaker access, with real per-platform permission prompts
   (macOS TCC, Windows privacy settings) that no installer can grant on
   the user's behalf — the OS will always ask the human directly, at
   least once.
2. **Large, on-demand ML assets.** faster-whisper's Whisper model and
   the TTS voice (Kokoro or Piper) download on first use per
   `docs/QUICKSTART.md`'s own description, not bundled — could be
   pre-bundled into an installer, but that trades a much larger
   download for a "no first-run wait" experience; worth deciding
   deliberately, not by default.
3. **The backend CLI is external and cannot be bundled.** ConvoBox
   drives Claude Code, Codex, or OpenCode — three separate products
   with their own installation, licensing, and (for Claude Code/Codex)
   authentication. No ConvoBox installer can install or authenticate
   these for the user. **This means "one download, fully working" is
   not achievable by ConvoBox packaging alone, regardless of which
   approach below is chosen** — the best any installer can do is get
   ConvoBox itself running and clearly hand off to "now install/log
   into one of these three tools."

The `aec` extra is a fourth, narrower wrinkle: PyPI only ships a
prebuilt wheel for Windows; macOS/Linux need a source build requiring
`meson`/`ninja`/`swig` (`docs/KNOWN-ISSUES.md`'s own note, verified
working but not automatic). AEC is opt-in and the product degrades
gracefully without it (`audio.echo_cancellation` defaults off), so this
doesn't block a v1 installer — it just means AEC-source-build should
stay a documented advanced step, not something a non-technical
installer silently attempts.

## Options considered

1. **A full compiled bundle (PyInstaller or Briefcase)** — packages
   Python + every dependency into a single executable/app. Rejected as
   the FIRST step: doesn't remove the backend-CLI dependency (constraint
   3 above still applies regardless), means separate CI-built artifacts
   per platform, and the AEC extra's native source-build requirement
   would need prebuilt platform-specific binaries baked in per platform
   — real, ongoing CI/build maintenance for a payoff (skipping `uv
   sync`) that's smaller than it looks once constraint 3 is accounted
   for. Not ruled out forever — worth revisiting once a guided script
   proves the actual UX gap, as a thin wrapper (see "Recommendation").
2. **Docker** — good fit for headless server apps, a poor fit here
   specifically: ConvoBox needs real-time mic/speaker access, and
   audio-hardware passthrough into containers is genuinely painful on
   macOS and Windows (unlike Linux), which is exactly where most
   non-technical users are. Rejected.
3. **A guided setup script** — keeps `uv`-managed dependencies exactly
   as they work today (no bundling, no new CI build matrix), but
   automates every manual QUICKSTART.md step: detect/install `uv` if
   missing, run `uv sync` (prompting for `--extra piper`/`--extra aec`
   only if the user says they want them, explaining the tradeoffs
   plainly), launch the existing `scripts/voice_picker_tui.py`, run
   `scripts/audio_devices.py`'s device test, ask which backend CLI the
   user has/wants and check for its presence on PATH (clear, specific
   install-instructions hand-off if missing, not a bundling attempt),
   then generate a working `convobox.yaml` from the answers instead of
   requiring hand-editing. This is real, scoped, buildable work that
   directly builds on scripts that already exist and are already
   tested, rather than a new packaging system.

## Recommendation

**Ship the guided setup script first (v1), not a compiled bundle.** It
directly closes the actual gap (nobody has to hand-edit YAML or
manually chain four separate scripts), reuses every already-built,
already-tested piece (`voice_picker_tui.py`, `audio_devices.py`,
`config.py`'s own schema), and doesn't take on new cross-platform build
infrastructure for a payoff (skipping `uv sync`, which is already one
command) that constraint 3 limits anyway. A thin native wrapper — a
`.app` on macOS or a `.exe` on Windows that just opens a terminal and
runs this same script — is a reasonable v2 once the script itself is
proven, giving non-technical users a double-click entry point without
requiring a full PyInstaller bundle of the ML dependencies.

## First slice (proposed, not built this pass)

A new `scripts/setup_wizard.py` (Python, so it runs the same way on
every platform `uv run` already supports — no separate PowerShell/bash
implementations to keep in sync, unlike `bootstrap_windows.ps1`'s
Windows-only scope):

1. Check for `uv` on PATH; if missing, print the official install
   command for the current OS (`curl -LsSf ...`/`irm ... | iex` per
   [astral.sh/uv docs](https://docs.astral.sh/uv/getting-started/installation/))
   and exit — do not silently self-install a package manager without
   explicit confirmation.
2. Run `uv sync`, then ask (interactive prompt, plain text, no TUI
   framework needed for this part) whether the user wants Piper voices
   (GPL-3.0 — say so) or AEC (needs build tools on macOS/Linux — say
   so, offer to check for `meson`/`ninja`/`swig` and print the `brew
   install` line if missing rather than attempting the source build
   unattended).
3. Ask which backend CLI they'll use (claude-code / codex / opencode);
   check `shutil.which()` for it; if absent, print that backend's real
   install/auth instructions (not attempt to install it) and continue
   anyway (some users may add it after setup).
4. Launch `scripts/voice_picker_tui.py` for voice selection (reuse, not
   reimplement).
5. Launch `scripts/audio_devices.py`'s device-listing/test flow (reuse).
6. Write a real `convobox.yaml` from the answers collected above (start
   from `convobox.example.yaml` as the template, same as QUICKSTART.md's
   manual step 4, just automated).
7. Run the existing `--text` smoke test
   (`run_convobox.py --text "..."`) automatically at the end, so setup
   ends with a real pass/fail signal instead of "hope it works."

Explicitly NOT in this first slice: bundling model downloads ahead of
time, a compiled installer, automating backend CLI installation/auth,
or a GUI (a second terminal-based wizard, matching the project's
existing `settings_tui.py`/`voice_picker_tui.py` style, not a new UI
paradigm).

## Open questions for JP

- Is a terminal-based wizard (matching the existing TUI tools' style)
  the right shape for "non-technical," or does "non-technical" mean
  JP wants a real GUI/native installer as the actual v1, accepting the
  extra build-system cost that implies?
- Should the wizard attempt the AEC source build automatically (with
  clear consent) once build tools are confirmed present, or always defer
  to a manual follow-up step even then?
- Priority relative to the Claude Skills work (this session's other
  overnight thread) and the open PRs (#255/#256/#257/#259/#261) awaiting
  review — not assumed here.
