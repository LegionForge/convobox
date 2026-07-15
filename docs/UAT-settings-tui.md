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

## Audio device picker (added 2026-07-14, JP asked for "same logic as
`python scripts/audio_devices.py --setup`")

`input_device`/`output_device` are no longer plain free-text fields --
Space/Left/Right cycles through REAL, deduped, discovered devices (the
exact same `collect_devices`/`dedupe_devices` logic `--setup` uses), and
`[t]` plays a real short tone + records a real short sample with a level
meter, reusing `audio_devices.py`'s own functions directly. Unit-tested
against a fake device list (`tests/test_settings_tui.py`) and verified
once against real hardware on this machine (found the real connected
devices, including a Shokz OpenComm headset, and genuinely played a tone
through one), but the FEEL of it -- does cycling read cleanly on a real
terminal, does `[t]`'s ~2 second pause feel responsive enough -- hasn't
had a live UAT pass.

14. Go to the `Audio` tab, select `Input device`, press `Space` repeatedly.
    Confirm it cycles through your REAL connected microphones (not a
    static/empty list) and eventually wraps back to `(unset)` -- not the
    literal text `(system default)` showing up as a saved value.
15. Do the same for `Output device`.
16. Press `Enter` on `Output device` to open the edit modal; confirm
    `Left`/`Right`/`Space` all cycle the same way inside the modal, and
    `Enter` accepts whichever device is currently shown (including
    accepting back to unset if you cycled all the way around).
17. With the `Audio` tab selected, press `[t]`. Confirm you actually HEAR
    a short tone from the configured speaker, and the status line reports
    something like `speaker OK: played 0.6s tone on '...' | mic: [...]
    rms ... dBFS ... -- <verdict>`. Say something while it's recording (it
    listens for about 1.2s) and confirm the reported level looks sane
    (not stuck at `SILENT`).
18. Type a device name/index by hand (bypass cycling) into `Input device`
    or `Output device` and confirm free typing still works exactly as
    before -- this is still available for advanced users, not replaced by
    the picker.
19. Save, quit, and reopen the settings TUI (or re-run
    `python scripts/run_convobox.py`) and confirm the picked device
    actually took effect -- not just visually selected in the TUI.

