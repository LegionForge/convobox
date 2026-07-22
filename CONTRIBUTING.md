# Contributing to ConvoBox

Thanks for considering a contribution. This is a small project with one
maintainer, so the process is lightweight — this file is the human-facing
front door; the details it points to are already written down elsewhere.

## Before you start

For anything beyond a small fix, open an issue first describing what
you'd like to change and why. It avoids duplicated work and lets us agree
on the approach before you invest time in an implementation — especially
relevant for anything touching the safeword path, approval handling,
barge-in gates, or VAD/STT front-end, which are safety-critical and get
extra scrutiny (see [AGENTS.md](AGENTS.md) rule 3).

[docs/ROADMAP.md](docs/ROADMAP.md) shows the current direction; a PR that
fits it is an easier review than one that doesn't.

## Setting up a dev environment

```bash
git clone https://github.com/LegionForge/convobox
cd convobox
uv sync --extra dev
```

[TESTING.md](TESTING.md) is the canonical reference for everything from
here: running the automated test suite, type checking, the real
end-to-end round trip (no mic needed), live mic/TTS testing, and what
CI does and doesn't cover. Read its "Setup" and "CI / LegionForge
dev-rig" sections before your first PR.

## Before opening a PR

Run what CI runs:

```bash
uv run pytest -q
uv run ruff check src/ scripts/ tests/
uv run mypy src/ scripts/
```

- Keep unrelated changes out of the same PR — one work-set at a time
  (see [AGENTS.md](AGENTS.md) rule 1 for why this matters here
  specifically).
- Update [CHANGELOG.md](CHANGELOG.md) for anything user-visible.
- Fill in the PR template's attribution section honestly — see
  [docs/AI-ATTRIBUTION.md](docs/AI-ATTRIBUTION.md) for the convention.
  This applies whether you wrote the change by hand or with an AI
  coding agent's help; the project has no preference between the two,
  just a preference for it being disclosed.

## Reporting bugs

Open a GitHub issue. Include your platform, backend (OpenCode/Claude
Code/Codex), and — if it's a live-voice issue — enough of the logged
transcript to reproduce. [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md) is
already-diagnosed-and-deferred problems; check there first.

## A note on AI-assisted contributions

Coding agents are welcome to work in this repo — see
[AGENTS.md](AGENTS.md) for the working agreements they follow here (and
the live incidents that produced each rule). If you're a human directing
an agent to make the change, you're still the one accountable for the
PR; review what it produced before opening it.
