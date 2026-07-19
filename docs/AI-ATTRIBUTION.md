# AI Attribution

This repository tracks AI-assisted edits with a single convention so changes
remain attributable on GitHub without sprinkling ad hoc notes through every
source file.

## Canonical rule

Every AI-assisted change should be attributed in at least one durable place:

- the pull request body
- the changelog entry for a release-visible change
- a commit trailer
- a file-level note, when the file itself is a generated artifact or the
  provenance matters inside that file

The preferred default is PR-level attribution. Use file-level attribution only
when it adds real value and does not create needless noise.

## Standard attribution block

Use this shape for Codex, Claude, opencode, Kilo, Cline, or any other
coding agent / work surface:

```text
Attribution: <product>
Provider: <provider>
Model: <model>
Scope: <files, feature, or PR>
Date: <YYYY-MM-DD>
```

## Published-artifact provenance (docs, field notes, anything we may share)

For material intended for eventual publication — field notes, design
writeups, research — attribution is a first-class provenance stamp, not a
courtesy line. It carries who and what produced the work so a future reader
(human or model) can weigh each claim. Required fields:

```text
authors:
  - JP Cruz <jp@legionforge.org> (role)
  - <Tool / work surface> (<provider> <model-id>) — <role: investigation | implementation | writing | review>
org: https://legionforge.org
created: <YYYY-MM-DDTHH:MM:SS±HH:MM>   # date AND time with timezone offset
revised: <same format; update on every substantive edit>
```

Rules:
- **Name every AI tool that contributed**, with its provider and specific
  model id — Claude Code (Anthropic), Codex (OpenAI), opencode (whichever
  provider/model it actually ran), Kilo, Cline, etc. If two models
  contributed (e.g. one investigated, one implemented), list both with
  their roles.
- **The human author is always named** with `jp@legionforge.org` and the
  org URL `https://legionforge.org`.
- **Timestamps are date + time + timezone offset**, not just a date —
  provenance benefits from ordering, and these sessions cross midnight.
- This applies to **all projects, private and public.** We keep the
  provenance stamp regardless of whether we ever publish; publishing is a
  later choice, but the record must already exist.

Examples:

```text
Attribution: OpenAI Codex
Provider: OpenAI
Model: GPT-5
Scope: docs/AI-ATTRIBUTION.md and PR #123
Date: 2026-07-15
```

```text
Attribution: Claude Code
Provider: Anthropic
Model: <model>
Scope: src/convobox/adapters/claude_code.py
Date: 2026-07-15
```

```text
Attribution: opencode
Provider: OpenCode
Model: hy3-free
Scope: docs/UAT-checklist.md
Date: 2026-07-15
```

## Where to put it

- PR body: required for AI-assisted edits.
- Commit trailer: recommended when the repo or workflow preserves commit
  messages on GitHub.
- Changelog: use for user-visible or release-visible AI-assisted changes.
- File header/footer: use only when the file is itself a generated artifact or
  when the provenance is important to future readers.

## Commit trailer format

If you want a compact machine-readable form, this is the preferred trailer:

```text
AI-Attribution: OpenAI Codex; provider=OpenAI; model=GPT-5; scope=docs/AI-ATTRIBUTION.md
```

The same pattern should be used for Claude or opencode by swapping the product,
provider, and model values.

## Repo expectation

When Codex edits files in this repository, the corresponding PR or changelog
entry should state that OpenAI Codex made the change and include the model
name. The same is true for Claude Code and opencode edits, using their own
product, provider, and model identifiers.

## Enforcement

Pull requests that mark themselves as AI-assisted should carry the standard
attribution block in the PR body or an `AI-Attribution:` commit trailer.
The repository's GitHub Actions check enforces that rule.

## Commit template

For local commits, set Git to use [`.gitmessage.txt`](../.gitmessage.txt) as
the commit template:

```bash
git config commit.template .gitmessage.txt
```

That template includes both the readable attribution block and the compact
`AI-Attribution:` trailer so commit authors can carry the same metadata into
Git history before the PR is opened.
