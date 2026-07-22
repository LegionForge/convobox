# ConvoBox — Claude Code project instructions

Follow the working agreements in [AGENTS.md](AGENTS.md) — they bind every
agent in this repo and each rule cites the live incident that created it.

Repo orientation: README.md (architecture + status), docs/UAT-checklist.md
(live findings, [E*]/[L*]/[U*] numbering — check existing numbers before
adding), docs/KNOWN-ISSUES.md (diagnosed-and-deferred, incl. upstream
opencode limitations), docs/AI-ATTRIBUTION.md (attribution convention).

Once per clone: `git config core.hooksPath .githooks` (enables the
pre-push privacy scrub that scans outgoing COMMITS, not just the tree).

Verification bar: this project's standard is verify-against-the-real-thing
— run the actual pipeline (audio, STT, backend) rather than trusting
unit tests alone, and say plainly which claims are live-verified vs
schema-verified.
