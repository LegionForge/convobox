# Settings TUI Scope

This doc defines the first implementation target for the ConvoBox settings
TUI. The goal is to make setup and day-to-day reconfiguration possible from a
single place without turning the TUI into a general-purpose package manager.

## Product Goal

One full-screen ASCII TUI that lets the user:

- inspect and edit the active `convobox.yaml` settings
- validate the current configuration before saving
- test the selected devices, engines, and backend
- install or remove local engine assets where ConvoBox owns the files
- get guided setup help for external CLIs that ConvoBox does not own

The TUI should be a control surface, not a wizard that hides what it is doing.
Every write must be explicit.

## What The TUI Owns

The TUI should directly manage things that live inside ConvoBox-owned storage:

- `audio.*` settings in `convobox.yaml`
- `stt.*` engine selection and model choice
- `tts.*` engine selection, voice selection, rate, and volume
- `interaction.*` settings
- `safeword.*` settings
- `vad.*` settings
- local model assets for engines that ConvoBox downloads or stores itself

For those owned assets, the TUI can:

- download
- delete
- refresh catalog metadata
- test-load or test-run the selected engine

## What The TUI Should Not Own

The TUI should not try to fully manage external tools that have their own
installation lifecycle:

- `opencode`
- `claude`
- `codex`

For those, the TUI should:

- detect whether the executable is available
- show the configured command or connection details
- run a health check or connection test
- offer setup guidance
- optionally copy or display install commands

It should not silently uninstall or mutate the user’s system package manager
state by default.

## Install/Uninstall Policy

### Local engine assets

Allowed and encouraged:

- download TTS voices or STT models
- remove those local assets
- keep the on-disk asset directory under ConvoBox control

### External CLIs

Allowed only as guided, explicit actions:

- “show install command”
- “run this command for me” only after confirmation
- “check availability”
- “validate connection”

Default behavior should be non-destructive. If uninstall support is added later,
it should be behind a separate destructive-confirmation flow and limited to the
specific install method the TUI knows how to reverse.

## Save And Revert Contract

The TUI should stage edits in memory until the user explicitly saves.

Recommended save flow:

1. User edits settings.
2. User chooses `Save`.
3. TUI writes a backup of the current config file.
4. TUI validates the staged config.
5. If validation passes, TUI atomically replaces `convobox.yaml`.
6. If validation fails, TUI restores the backup and shows the failure.

This applies to the config file itself and to ConvoBox-owned assets.
It cannot guarantee rollback for changes made through external package managers
or other tools outside the repo's control.

## Validation Levels

Use three validation layers where possible:

- Parse validation: YAML + schema validation.
- Dependency validation: required file or executable exists.
- Live validation: instantiate the selected engine/backend and run a small probe.

Live validation should be used before final save when it is cheap and safe to do
so. If a live probe fails, the TUI should preserve the previous config and tell
the user what failed.

## Initial Screens

Start with these screens:

- Overview / review
- Audio
- STT
- TTS
- Backend
- Interaction
- Safety
- Save / revert

The overview screen should summarize the current config and any unresolved
warnings. The save screen should show the exact diff or at least the exact
values that will be written.

## First Implementation Slice

The first slice should do the minimum useful thing:

- load an existing config or defaults
- edit and save `audio`, `stt`, `tts`, `backend`, `interaction`, `safeword`, `vad`
- validate the settings before save
- back up the previous config
- allow testing the selected TTS voice and backend connection
- allow local asset download/delete for TTS voices

Do not wait for a perfect plugin system before shipping this slice.
The point is to replace hand-editing YAML with a safe interactive path.

## Deferred For Later

These are useful, but not required for v1:

- full engine/plugin marketplace support
- automated installation of external CLIs
- uninstall flows for external CLIs
- cloud provider setup
- account auth flows for third-party services
- approval-word UI for destructive voice actions
- wake-word enrollment

## Open Questions

- Should config backups be stored beside the config file, or in a dedicated
  backup directory?
- Should live validation run automatically on every save, or only when the user
  requests it?
- Should TUI install helpers shell out to package managers directly, or only
  generate copy-paste commands?

## Decisions Made

- One config profile for now. Multiple profiles can wait until the product is
  actually orchestrating multiple agents at once.
- Save must stay explicit.
- Local assets can be managed directly; external CLIs should be detected and
  guided, not silently mutated.
