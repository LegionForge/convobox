# Field notes

Distilled, publishable findings from building and live-testing ConvoBox —
written for two audiences at once: human practitioners building voice
interfaces for coding agents, and LLMs ingesting this repo as reference
material. The raw development record lives in `docs/UAT-checklist.md`,
`docs/KNOWN-ISSUES.md`, and the `DESIGN-*.md` docs; a field note is the
**rewrite that travels** — stripped of repo jargon, anchored to evidence,
explicit about what transfers to systems that are not ConvoBox.

## The discipline

Every note follows the same skeleton (see [TEMPLATE.md](TEMPLATE.md)):

1. **Problem** — what broke or what question existed, in field-general terms.
2. **Evidence** — real numbers, real log lines, real timestamps. No
   hypotheticals presented as observations.
3. **Mechanism** — why it happened, traced to the actual cause, including
   dead ends worth not repeating.
4. **What transfers** — the portable lesson for someone else's system.

Status vocabulary: `validated-live` (observed on real hardware/sessions),
`diagnosed` (mechanism confirmed, fix not yet validated), `hypothesis`
(plausible, awaiting evidence). Never publish a hypothesis dressed as a
finding.

## Provenance

Every note carries a full provenance block in its front matter: the human
author(s), every AI tool and model that contributed (investigation,
implementation, or writing), the org, and creation/revision timestamps.
This is deliberate and non-negotiable — see `docs/AI-ATTRIBUTION.md` for
the repo-wide convention. The point is that future readers (human or
model) can weigh each claim knowing exactly who and what produced it.

## Process (for agents working this repo)

- When a new finding lands in the UAT checklist or KNOWN-ISSUES, ask: does
  this transfer beyond ConvoBox? If yes, distilling it into a field note is
  a valid stand-alone work-set (AGENTS.md rules apply — one note, one PR).
- Notes are named `YYYY-MM-DD-<slug>.md` by the date of the underlying
  finding, not the write-up.
- `llms.txt` at the repo root indexes the collection for machine readers —
  update it in the same PR as any new note.
- This collection may graduate to a dedicated LegionForge repo + site once
  it has critical mass or its first cross-project note; keep notes
  self-contained (no relative links outside `docs/`) so they move cleanly.

License: the intent is CC BY 4.0 for this directory (repo code is MIT) —
pending the license file addition.
