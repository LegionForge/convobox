# UAT [L3] Contrast Test — headset AEC on vs. off

**Goal.** Finding [L3] (author: jp-cruz, *recorded for assessment, not changed in
code*) warns that with a **headset** (mic hears no speaker sound) turning
`audio.echo_cancellation: true` on is harmful: AEC has nothing to cancel yet
still (a) artifacts the mic path and (b) its no-echo state makes the spoken-echo
filter **drop genuine barge-ins** as if they were self-echo. This test tries to
*confirm or refute* that claim on the same hardware, so [L3] can move from
"recommendation" to "validated" (or be corrected).

**Already done (the "after" half).** Session #81 with AEC **off**
(`convobox.yaml`) barge-in test, recorded as [L6]: 8/8 interrupts captured,
zero `NO ECHO DETECTED`, zero dropped barge-ins. So AEC-off + headset is a known
good baseline.

**The "before" half = force AEC on with the headset and repeat the same
barge-in exercise.**

---

## Preconditions (verified)

- `pip install -e ".[aec]"` requirement is satisfied: `convobox.audio.aec`
  imports OK in `.venv`, so AEC will *actually engage* when forced on.
- Single-instance lock: `run_convobox.py` binds `127.0.0.1:47613`. Two copies
  cannot run at once — the contrast is **sequential** by design.
- `uat-echo.log` is not hardcoded; it's produced by shell redirection, so each
  condition can write to its own log file and we can diff them.

## Files

- `convobox.uat-aec-on.yaml` — exact clone of `convobox.yaml` with
  `echo_cancellation: true` and `aec_delay_ms` omitted (auto-tune). This is the
  contrast config. Do **not** use it for normal operation.
- `scripts/uat-l3-contrast.ps1` — stop/start helpers + the exact launch lines.

## Procedure

1. **Stop the current AEC-off session** (session #81, pid 56828):
   `Ctrl+C` in its window, or `Stop-Process -Id 56828` from PowerShell.
   (This session is disposable — a fresh one starts cleanly, per operator.)
2. **Launch the AEC-on contrast** (logs to `uat-aec-on.log`):
   ```
   .venv\Scripts\python.exe scripts\run_convobox.py --config convobox.uat-aec-on.yaml > uat-aec-on.log 2>&1
   ```
   In the first ~2 responses, confirm the startup banner shows
   `echo-cancellation active` (canceller is non-None). If instead it errors
   importing AEC or refuses to start, stop and record the error.
3. **Run the same barge-in exercise** you did in session #81:
   - interrupt my spoken response mid-sentence (expect
     `barge-in: sustained speech during playback -- stopping audio`);
   - talk over a "backend still working" gap (while-busy barge-in);
   - short low-confidence utterances ("Don't okay?", "just barged in.").
   Aim for a similar count (~8 interrupts) and similar utterance mix.
4. **Stop the AEC-on session** (`Ctrl+C`).
5. **Diff the logs.** Compare `uat-aec-off.log` (baseline) vs `uat-aec-on.log`.

## What confirms [L3]

The claim is two-sided. Both would support it:

- **(a) Artifacts:** operator-perceived mic artifacts during the AEC-on run
  (subjective, but note it). Also watch the per-response
  `AEC stats for last response: attenuation=… of ~… measurable [NO ECHO
  DETECTED: …]` line — if `NO ECHO DETECTED` still dominates (mic hears no
  speaker) yet AEC is "active", that is exactly [L3]'s "nothing to cancel"
  premise.
- **(b) Dropped real barge-ins:** any of these in `uat-aec-on.log` that are
  NOT present (or far rarer) in the AEC-off baseline:
  - `dropped (overlap gate, echo-cancellation active): '<your words>'`
  - `dropped (spoken-echo filter, barge-in was our own echo): '<your words>'`
  where `<your words>` was a *genuine* interruption you spoke, not ConvoBox's
  own echoed voice. A genuine barge-in being dropped is the [L1] regression
  [L3] predicts.

## What would REFUTE [L3]

- AEC-on run captures all barge-ins identically to AEC-off, with no drops and
  no operator-perceived artifacts. Then [L3] overstates the risk for this
  headset and should be corrected (AEC-off stays a fine choice, but "OFF is
  required" would be wrong).

## How to record the result

Update `docs/UAT-checklist.md`:
- If [L3] confirmed: promote [L3] from "recorded for assessment" to
  "validated (L3 contrast, session #__)", and mark [L6]'s scope caveat
  ("NOT a validation that [L3] is correct") as resolved.
- If [L3] refuted/partial: add an [L7] correction noting the discrepancy and
  what actually happened.

> Finding numbers above reflect the final `docs/UAT-checklist.md` numbering:
> the AEC-off baseline landed as [L6] and the contrast result as [L7]/[L8]
> (main already had [L4] heartbeat coloring and [L5] event-stream findings).

Keep the two log files (`uat-aec-off.log`, `uat-aec-on.log`) as the evidence
artifacts; do not delete them until the finding is written up.
