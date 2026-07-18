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

## Save only writes fields you actually changed (fixed 2026-07-15)

Real incident: a plain `model_dump()` used to write EVERY field on every
save, including ones never touched -- so a single Settings TUI save
silently baked a stale `aec_delay_ms: 100` into `convobox.yaml`,
permanently disabling AEC delay auto-tuning (see the AEC delay item in
`docs/UAT-checklist.md`'s Echo section for the live symptom this caused).
Fixed via `exclude_defaults=True`; unit-tested (`tests/test_settings_tui.py`)
but a real save/reload/inspect pass closes the loop.

20. Open the settings TUI against a `convobox.yaml` you've never touched
    (or a fresh one). Without changing anything, save. Open the file and
    confirm it's empty or very close to it (no long list of every
    default value) -- an all-defaults save should write next to nothing.
21. Change exactly one field (e.g. `TTS > Voice`), save, and open the
    file. Confirm ONLY that field (plus its section header) appears --
    not every other field in that section, not `aec_delay_ms`.
22. Re-run `python scripts/run_convobox.py` against that saved file and
    confirm it loads with the same effective config as before (defaults
    for everything you didn't touch) -- the loaded behavior must be
    identical to a full-field dump, only the written FILE should differ.

## AEC delay auto-tune (fixed 2026-07-15)

`Audio > AEC delay ms` is now optional -- leave it `(unset)` (the
default) to auto-tune from real measured stream latencies on every
`run_convobox.py` startup, or set a fixed number to override. The help
panel shows a `Last auto-detected: ...ms` line read from a diagnostic
sidecar file (`<config>.aec-estimate.json`) that `run_convobox.py` writes
the first time it measures real latencies -- it never touches
`convobox.yaml` itself.

23. With `aec_delay_ms` unset and `echo_cancellation: true`, run a live
    session, say something, let it respond. Confirm the log shows
    `AEC delay auto-estimated: ...ms`, not `... explicit`.
24. Reopen the settings TUI, go to `Audio > AEC delay ms`. Confirm the
    help panel shows `Last auto-detected: <the same number>ms` with a
    real-looking timestamp -- not the placeholder text.
25. Before ever running a live session against a fresh config, check the
    same field's help panel and confirm it shows the
    `none yet -- run a live session...` placeholder instead of crashing
    or showing stale data from a different config file.
26. Type a fixed number into the field and save. Confirm the next
    `run_convobox.py` run logs `... explicit` and does NOT log the
    auto-estimated value as what it's using (it still logs the
    measured-vs-configured comparison for reference, per the existing
    "consider updating aec_delay_ms or removing it" line).

