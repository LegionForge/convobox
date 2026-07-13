# Settings TUI UAT

This is the UAT checklist for `convobox-settings`.

## Goal

Verify the settings screen can be used without hand-editing YAML:

- navigate sections like firmware tabs
- edit values with Escape cancellation
- save with an explicit confirmation
- revert/quit with destructive confirmations
- keep the help panel and modal chrome readable on the target terminal

## Prep

- Have a working `convobox.yaml` in the repo root or set `CONVOBOX_CONFIG`.
- If you want to test TTS/STT/backend validation, make sure the corresponding
  voice, model, and backend are already installed and runnable.

## Tests

1. Launch `convobox-settings`.
2. Confirm `Left/Right` switch tabs and `Up/Down` move within the active tab.
3. Select `safeword.hard_stop_phrases`, press `Enter`, type a couple of
   characters, then press `Esc`.
4. Confirm you return to the main settings screen and the value was unchanged.
5. Open the same field again, enter a new comma-separated list, and confirm
   with `Enter`.
6. Trigger `S` to save.
7. Confirm the save dialog looks like the same BIOS-style screen, not a plain
   terminal prompt.
8. Press `Esc` on the save dialog and confirm you return to the editor with no
   write.
9. Trigger `R` to revert staged changes.
10. Confirm the revert dialog is visually stronger than save, and `Esc` returns
    to the editor.
11. Confirming revert should restore the working copy to the last saved config.
12. Trigger `Q` with unsaved changes and confirm the quit dialog is also
    destructive-styled.
13. Confirming quit should exit without leaving the terminal mangled.

## Pass Criteria

- No prompt traps the user in a hidden `input()` state.
- Escape always returns to the main editor from edit/confirm flows.
- Save remains explicit.
- Revert and quit are visually stronger than save.
- No YAML is written unless the save confirmation is accepted.

