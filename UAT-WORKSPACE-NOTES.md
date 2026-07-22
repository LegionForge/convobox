# UAT workspace notes (this checkout only)

Not a `CLAUDE.md` variant on purpose — a file at that exact path
collides with the project's own tracked `CLAUDE.md` (which this clone
pulls in from `main` like everything else), causing sync-from-dev
merges to fail with "untracked working tree file would be overwritten"
every time dev's `CLAUDE.md` changes. Keeping workspace-specific notes
under a different name avoids that permanently instead of re-fixing it
each time.

## This checkout is a UAT workspace, not canonical

This directory (`convobox-UAT`) is a disposable clone for live testing —
`.venv` is a directory junction to `D:\LegionForge\ConvoBox\.venv` (shared
env, not a separate install), and `convobox.yaml` here carries live-tuning
backup files (`convobox.yaml.backup-*`) from UAT sessions. The canonical
clean tree is `D:\LegionForge\ConvoBox` on `main`. Don't assume this
tree's branch/commit state is authoritative — check `git log`/`git status`
before relying on it, and don't delete the `.yaml.backup-*` files or
`uat-acoustic-calibration/` without checking whether they're a UAT
session's live record.

For everything else (commands, architecture, repo orientation), see the
tracked `CLAUDE.md`/`AGENTS.md`/`README.md` — same as the dev checkout.
