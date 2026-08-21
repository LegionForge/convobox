# Advanced (Danger Zone) Settings Scope

This doc defines a first implementation target for an explicit "Advanced"
settings section, in both the Settings TUI and the web UI's Settings modal,
gating capabilities that are real but genuinely risky enough to deserve
friction and a warning rather than being one more plain field in the normal
list. Written as a design pass before code, same reasoning as
`docs/SETTINGS-TUI-SCOPE.md` and `docs/ARTIFACT-PANE-SCOPE.md`.

> For what `backend.permission_mode` actually does across the three
> backends, see [PERMISSION-MODEL.md](PERMISSION-MODEL.md) — the canonical
> reference. This document covers only how the setting should be
> *presented* in the two settings UIs, not what it means.

## Origin

Came up while scoping the artifact-pane chooser (`docs/ARTIFACT-PANE-
SCOPE.md`): JP asked about a full VSCode-style file explorer, which was
rejected for the default case (widens the no-auth loopback surface to
browse *any* file, not just tool-referenced ones). JP's follow-up: rather
than reject it outright, gate it — and generalize the pattern to other
already-risky-but-currently-ungated capabilities in this config. Scope,
per JP's own multi-select answer: **permission-mode bypass, broader
artifact/file access, and non-loopback web bind_address.**

## What's Actually New Here vs. What Already Exists

Checked `src/convobox/config.py` before writing this doc, since two of the
three turn out to be existing fields with no special treatment, not new
mechanisms:

1. **Permission-mode bypass — mostly already exists.**
   `backend.permission_mode` already has a `"permissive"` value ("the
   agent acts without asking. Opt-in, dangerous." — the field's own
   comment) and `detect_permission_conflict()` already hard-rejects trying
   to set the equivalent via a raw `backend.command` flag
   (`--dangerously-skip-permissions` etc.) instead. What's missing is
   presentation: today it's one of three plain choices in a picker, same
   visual weight as `plan`/`approve`. **New work: move it into the
   Advanced section with the warning treatment below, not a new field.**
2. **Non-loopback `web.bind_address` — mostly already exists.**
   `WebConfig._validate_bind_address` already allows `0.0.0.0` as a
   deliberate, explicit opt-in (rejects any OTHER specific non-loopback
   address outright). No confirmation/warning step exists beyond the
   validator's own error message when you get it wrong. **New work: move
   the choice into the Advanced section with the warning treatment,
   still validated the same way.**
3. **Broader artifact/file access — genuinely new.** No config field
   exists for this at all today. `GET /api/artifacts/{path}`
   unconditionally only serves paths a real `ARTIFACT` event named (see
   `ARTIFACT-PANE-SCOPE.md`'s security section) — there's no way, even in
   config, to widen that. This is real new scope, not a presentation
   change. See its own section below.

## Shared Mechanism: Marking A Field/Section As Dangerous

Both UIs already read the exact same `SECTION_SPECS`/`FieldSpec` data
(`scripts/settings_tui.py`) — the TUI and the web Settings modal are two
renderers over one schema (`docs/UAT-settings-tui.md`'s whole reason for
being). Marking something as "advanced/dangerous" needs to live in that
shared model, not be reimplemented per-renderer:

- Add a new `SectionSpec.danger: bool = False` (a whole "Advanced"
  section, not per-field — keeps the boundary simple and visually
  obvious: you either are or aren't in the danger zone).
- Add `FieldSpec.warning: str = ""` — a per-field consequence sentence,
  separate from the existing neutral `help_text`, e.g. for
  `permission_mode`: `"The agent can write files and run commands with
  NO approval step, voice or otherwise. Only use in a workspace you'd
  trust an unsupervised agent with."` Empty string means no extra
  warning beyond the section-level treatment.

### Rendering the warning

Per JP's explicit ask: hint text AND a caution icon, in red, explaining
the actual consequence — not just a generic "this is dangerous" label.

- **TUI:** the Advanced section's fields render their `warning` text in
  the existing help-panel area (already how `help_text` is shown), but in
  red (this project's TUI already uses ANSI color for status severity —
  same mechanism, new color mapping) with a `⚠` prefix. The section tab
  itself gets the same `⚠` marker so it's visible before you even enter
  it.
- **Web:** `_field_to_dict()` (`web/settings_api.py`) already builds each
  field's dict for the schema response — add `warning` to it. Frontend:
  a small red caution-icon + text block under the field (or a
  browser-native `title` tooltip on a caution icon next to the label,
  for the lower-chrome option) — actual pixel layout is a frontend
  detail to nail down while building, not this doc's job to over-specify;
  the requirement is that it's genuinely readable, not a mouseover you'd
  miss, matching the WCAG "never color alone" rule already used for the
  Quit button's armed state and the approval buttons elsewhere in
  `index.html`.

### Confirmation friction

A warning that's just text is easy to click past. Match the existing
Quit-button precedent (arms on first interaction, fires on a second) or
something similarly deliberate, TBD which fits the Settings save flow
better — see Open Questions.

## New Capability: Broader Artifact/File Access

Actual design for the one genuinely new field. Proposed:
`web.artifact_browse_enabled: bool = False`, Advanced-only, defaults off
(matches this project's own convention: every security/privacy-relevant
knob defaults to the safe posture — `echo_cancellation`, `web.enabled`,
`history_tracking_enabled`, `bind_address` all follow this same rule).

When `True`:
- A new endpoint (`GET /api/artifacts` with no path, or similar) lists
  files under `backend.working_dir`, subject to the SAME extension
  allowlist and the SAME working-dir-escape fence `GET /api/artifacts/
  {path}` already enforces — widening WHICH paths are servable
  (any file under working_dir, not just ones a tool call named), not
  the existing path-traversal fence itself, which stays exactly as
  strict.
- The frontend's artifact chooser (`ARTIFACT-PANE-SCOPE.md`) gains a
  "browse working directory" affordance alongside the tab strip, instead
  of (or in addition to) only listing tool-call-produced artifacts.
- Still bound by every existing constraint: `backend.working_dir` must be
  set to something other than ConvoBox's own tree (already enforced
  elsewhere), no-auth loopback trust model unchanged, same MIME
  allowlist.

When `False` (default): today's exact behavior, zero change.

## Initial Slice

1. `SectionSpec.danger`/`FieldSpec.warning` added to `scripts/
   settings_tui.py`'s model (backward compatible — both default to
   falsy/empty, every existing SectionSpec/FieldSpec construction
   unaffected).
2. New `SectionSpec(key="advanced", label="Advanced", danger=True, ...)`
   containing `permission_mode` (moved, not duplicated — remove it from
   wherever it lives today) and `web.bind_address` (same), each with a
   real `warning` string.
3. TUI rendering: red + `⚠` for the section tab and field warnings.
4. Web rendering: `_field_to_dict()` passes `warning` through; frontend
   caution-icon/red-text treatment per the WCAG-safe pattern above.
5. Confirmation friction on saving a dangerous value (exact mechanism per
   Open Questions).
6. Live-verify both UIs: selecting `permission_mode: permissive` or
   `bind_address: 0.0.0.0` actually shows the warning and requires the
   confirmation step before it saves.
7. `web.artifact_browse_enabled` (the genuinely new field) as its own
   follow-up slice, not bundled into the same commit as steps 1-6 (this
   project's own change-scope discipline: one work-set at a time) — the
   presentation mechanism should ship and be verified on its own first,
   since the new capability depends on it existing.

## Deferred / Explicitly Out Of Scope

- Any OTHER currently-ungated risky setting not in JP's three (e.g.
  `stt.device: cuda` isn't a safety risk, just a compatibility one --
  doesn't belong here). This doc only scopes the three named items;
  expanding the danger-zone's membership later is a separate decision.
- A full VSCode-style file explorer/editor — still rejected, per
  `ARTIFACT-PANE-SCOPE.md`. `artifact_browse_enabled` is view-only
  listing+serving under the existing allowlist, not editing, not a
  general filesystem browser UI.
- Per-session or per-request opt-in (e.g. "allow just this once") — v1
  is a persistent `convobox.yaml` setting like everything else in
  Settings, not a runtime prompt.

## Open Questions (for JP, not decided here)

- Confirmation mechanism for saving a dangerous value: a typed
  confirmation (type the field name or "I understand"), a two-step
  arm/confirm like the Quit button, or just the visible warning +
  ordinary Save (relying on the warning being genuinely hard to miss,
  no extra friction)?
- Should entering the Advanced section itself require anything (e.g. a
  one-time "I understand these are dangerous" acknowledgment before the
  section's fields are even editable), or is per-field warning enough?
- `web.artifact_browse_enabled`'s new listing endpoint: return the full
  recursive tree under `working_dir`, or just the top level with
  drill-down (matters for how "file-explorer-like" this ends up feeling,
  which cuts against the earlier explicit rejection of a full explorer
  if not bounded carefully)?
- Exact wording for each field's `warning` text — draft above is a
  starting point, not final copy.
