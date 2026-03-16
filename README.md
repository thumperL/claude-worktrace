# Claude Skills

Three skills for Claude Code that work together to capture work, learn preferences, and generate reports.

## Skills

| Skill | Purpose | Hooks |
|-------|---------|-------|
| **worklog-logging** | Auto-captures session work via AI-powered hooks | PreCompact, UserPromptSubmit (/clear), SessionEnd |
| **self-improve** | Learns from user corrections and steering patterns | None (receives steers from worklog-logging hooks) |
| **worklog-analysis** | Standups, weekly/monthly summaries, session tracing | None (reads worklog files on demand) |

## How they connect

```
PreCompact / SessionEnd / /clear
  └── pre_compact_hook.py
        ├── Sonnet analyzes transcript
        ├── Writes worklog entry → ~/Documents/AI/worklog/
        └── Detects steers → ~/Documents/AI/self-improve/preferences-log.md (log-only)

worklog-analysis reads worklog files and groups multi-segment sessions
self-improve manual flow promotes logged steers to ~/.claude/CLAUDE.md
```

## Install

Download `.skill` files from the [latest release](../../releases/latest), then ask Claude:

> Install these skills: worklog-logging.skill, self-improve.skill, worklog-analysis.skill

Each `.skill` file contains an `INSTALL.md` with step-by-step instructions that Claude follows automatically.

### Quick install (manual)

```bash
# Prerequisites
mkdir -p ~/Documents/AI/worklog ~/Documents/AI/self-improve

# Unzip each skill
mkdir -p ~/.claude/skills/{worklog-logging,self-improve,worklog-analysis}
unzip worklog-logging-v*.skill -d ~/.claude/skills/worklog-logging/
unzip self-improve-v*.skill -d ~/.claude/skills/self-improve/
unzip worklog-analysis-v*.skill -d ~/.claude/skills/worklog-analysis/

# Make scripts executable
chmod +x ~/.claude/skills/worklog-logging/scripts/session_end_wrapper.sh
chmod +x ~/.claude/skills/worklog-logging/scripts/pre_clear_hook.sh
```

Then register hooks in `~/.claude/settings.json` — see `worklog-logging/INSTALL.md` for the JSON.

## Requirements

- Claude Code CLI (`claude` in PATH)
- Python 3.9+ (macOS system Python works)
- `~/Documents/AI/` directory (iCloud sync recommended)

## Storage

```
~/Documents/AI/
├── worklog/                          # Worklog entries (per-day, per-machine)
│   ├── 2026-03-17-macbook-pro.md
│   └── ...
└── self-improve/
    └── preferences-log.md            # Auto-detected steers (log-only)
```

## Releasing

Push a tag to create a release with packaged `.skill` artifacts:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The GitHub Action validates Python 3.9 compatibility, packages each skill directory into a `.skill` zip, and attaches them to a GitHub Release.
