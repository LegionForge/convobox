# ConvoBox Security & Privacy Model

## Philosophy: Privacy-First by Default

ConvoBox **never publishes sensitive data to git or public repos without explicit user opt-in**. This document explains what data ConvoBox collects, what's sensitive, and how to safely use history tracking in private repos.

---

## What Data ConvoBox Collects

### Always Logged (Terminal/Ephemeral)
- Transcripts of your spoken input (STT output)
- Backend responses and tool outputs
- Approval events (tool names, file paths, commands)
- System events (audio levels, VAD state, barge-in events)

### Optionally Persisted (Web UI History)
- Everything above, plus:
  - Timestamps and session IDs
  - Approval metadata (what you approved/denied and when)
  - Full tool input JSON (from Claude Code, Codex, OpenCode)
  - TTS voice synthesis cache (audio files)
  - STT model cache (Whisper models, ~1-2GB)

---

## What's Sensitive (Keep Out of Public Repos)

### Always Private by Default
✅ **Gitignored automatically:**

| Category | Examples | Why |
|----------|----------|-----|
| **Transcripts** | Your prompts to the AI agent | Could leak personal context, project details |
| **File paths** | `src/secrets.py`, `/home/user/...` | Reveals project structure, machine names |
| **Commands** | `rm -rf`, `curl https://...`, credentials in CLI args | Direct security risk; could execute destructively |
| **Approvals** | Which dangerous operations you approved | Audit trail of your security decisions |
| **Responses** | Backend output, tool results, errors | Often contains file contents, API responses |
| **Configs** | Device names, API endpoints, local paths | Machine-specific; reveals topology |
| **Models & Caches** | Whisper/Piper model files (~2GB), WAV recordings | Large binary files; may contain training data |
| **Logs** | Terminal output, web server logs | Aggregates all the above |

### Example Risky Data
```
# Tool approval event in history:
{
  "timestamp": "2026-07-23T14:30:00Z",
  "tool": "bash",
  "tool_input": {"command": "curl -H 'Authorization: Bearer <api-key>' ..."},
  "user_approval": "approve"
}

# Transcript in history:
{
  "timestamp": "2026-07-23T14:25:00Z",
  "transcript": "fix the CVE in /opt/internal/security-patch",
  "user": "jp"
}
```

Both are sensitive. Committing either to a public repo is a security incident.

---

## Default Behavior (Secure)

### Gitignore Protections
```gitignore
# Web UI and persistent history — contains user transcripts, approvals, commands
.convobox-history/        # SQLite history DB + JSONL exports
.convobox.db              # History database

# Caches (models, audio, transcripts)
.convobox-cache/
.models/

# Web/debug logs (may contain event data, tool inputs, responses)
.convobox-web.log
convobox-web-*.log

# Temporary/scratch
/_*
.scratch/
.private/
```

### Config Defaults
```yaml
# convobox.yaml (or convobox.example.yaml)
web:
  history_tracking_enabled: false  # OFF by default
  history_dir: .convobox-history   # Gitignored, local-only
  bind_address: 127.0.0.1          # Localhost only
```

**Result:** Running ConvoBox normally leaves no sensitive data in git. History is stored locally, not published.

---

## Opt-In: Tracking History in a Private Repo

If you want to keep an audit trail or share history with trusted team members, you can opt in **explicitly** and **safely**.

### Step 1: Create a Private Repository
```bash
# Option A: Private GitHub repo
gh repo create my-convobox-audit --private --local

# Option B: Private GitLab, Gitea, or local git server
git init --bare /secure/location/convobox-audit.git
```

**Requirement:** This repo must be **truly private** (access-controlled, not public).

### Step 2: Enable History Tracking
Edit `.gitignore` in your ConvoBox working directory:

```diff
# .gitignore
- .convobox-history/
+ # .convobox-history/  <- COMMENTED OUT: history will be tracked
- .convobox.db
+ # .convobox.db        <- COMMENTED OUT
```

Or create a separate `.gitignore.private` for a private worktree.

### Step 3: Enable in Config
```yaml
# convobox.yaml
web:
  history_tracking_enabled: true  # EXPLICIT OPT-IN
```

### Step 4: Acknowledge the Risk
```bash
# Add this comment to your commit message
# WARNING: This commit contains sensitive data (transcripts, approvals, commands).
# This repo is PRIVATE. Do not make it public.
```

### Step 5: Set Up Access Control
```bash
# Restrict the private repo to yourself + trusted team only
gh repo edit --visibility private my-convobox-audit
# OR
git config core.sharedRepository group  # For local/team servers
chmod 700 /path/to/repo
```

---

## What Happens If You Accidentally Commit Sensitive Data

If `.convobox-history/` gets committed despite the `.gitignore`:

### Immediate Actions
```bash
# Remove the history from git (doesn't delete local files)
git rm --cached .convobox-history/
git commit -m "Remove history from git (was accidentally committed)"
git push

# Rewrite history if already pushed (only if repo is private)
git filter-branch --tree-filter 'rm -rf .convobox-history' HEAD
git push --force-with-lease

# Or: assume the repo is now public-facing
# 1. Assume all data in history is compromised
# 2. Rotate any API keys, credentials mentioned in transcripts
# 3. Audit which tools were approved (could have been run maliciously if data leaked)
```

### Prevention
- Keep `.convobox-history/` in `.gitignore` (default, pre-configured)
- Enable git hooks: `git config core.hooksPath .githooks` (ConvoBox includes privacy scrub hooks)
- Before pushing: `git diff --cached | grep -E "approval|transcript|command"` to spot history

---

## Privacy Per Component

### Web UI History Database
- **Location:** `.convobox-history/` (working directory, gitignored)
- **Format:** SQLite3 database
- **What it stores:** All backend events with full metadata
- **Access:** Readable by any process running as the user who created it
- **Permissions:** File-mode 600 (owner-readable only) on Unix/macOS; NTFS ACLs on Windows
- **Encryption:** None (added to-do if repo goes remote; overkill for local)

### TTS/STT Caches
- **Location:** `.convobox-cache/`, `.models/` (gitignored)
- **What it stores:** 
  - Piper TTS voice models (~100-500MB depending on language)
  - Faster-Whisper STT models (~1-2GB, large)
  - Cached audio from testing
- **Risk:** Models may contain training data; cached audio is a direct PII risk
- **Recommendation:** Keep these out of git always; back them up separately if needed

### Terminal Logs
- **Default:** Logged to stderr/stdout only (ephemeral)
- **On-disk:** `.convobox-web.log`, `convobox-web-*.log` (gitignored)
- **Warning:** Web server logs contain full event data; do not commit

### Configuration Files
- **Tracked:** `convobox.example.yaml` (template only, no secrets)
- **Gitignored:** `convobox.yaml`, `convobox.yaml.backup-*`, `*.aec-estimate.json`
- **Why:** Device names, API endpoints, machine-specific paths

---

## Best Practices

### For Development
```bash
# Keep everything gitignored (default)
# History lives locally only
# Nothing sensitive in the repo
```

### For Team Collaboration
```bash
# Option 1: Use a shared private git repo (with encryption at rest if on cloud)
# - Set history_tracking_enabled: true
# - Keep the repo private
# - Audit access regularly

# Option 2: Share logs manually (safer)
# - Export history as JSON/CSV from web UI
# - Send encrypted or via secure channel
# - Don't commit to shared repos
```

### For Audit Trails (Compliance)
```bash
# If you need a durable audit log:
# 1. Enable history tracking in a private repo
# 2. Set up read-only backups
# 3. Configure access logging on the git server
# 4. Archive old histories regularly (don't let one file grow forever)
```

---

## Checklist Before Publishing to GitHub/Public

- [ ] `git status` shows `.convobox-history/` NOT in staging
- [ ] `.gitignore` includes `.convobox-history/` and `.convobox-cache/`
- [ ] No `convobox.yaml` (machine-specific config) in git
- [ ] No `*.log`, `*.wav`, `*.aec-estimate.json` files staged
- [ ] `git diff --cached` contains no "approval", "transcript", or "command" data
- [ ] If using `git add -A`: double-check before committing (run grep above first)

---

## Questions?

- **I accidentally committed history to GitHub. What do I do?**
  → Follow "What Happens If You Accidentally Commit" section above. Assume it's compromised; rotate secrets.

- **Can I safely track history in a private repo?**
  → Yes, if the repo is truly private (access-controlled) and you trust the hosting provider.

- **What if my machine is compromised?**
  → History is stored on-disk in plaintext. If an attacker has shell access, they can read it. Same as any terminal logs or config on your machine. No amount of git controls fix this; focus on machine security.

- **Can ConvoBox encrypt history?**
  → Future enhancement (to-do). For now: keep repos private, restrict access, use OS-level disk encryption.

---

**Last Updated:** 2026-07-23  
**Status:** MVP documentation; subject to change as privacy model evolves
