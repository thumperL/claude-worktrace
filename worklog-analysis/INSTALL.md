# Install: worklog-analysis

Agent-readable installation instructions for Claude Code CLI.
When a user asks you to install this skill, follow these steps exactly.

## Prerequisites

The worklog storage directory should already exist (created by worklog-logging):

```bash
ls -d ~/Documents/AI/worklog
```

If not, create it:

```bash
mkdir -p ~/Documents/AI/worklog
```

## Step 1: Install skill files

```bash
mkdir -p ~/.claude/skills/worklog-analysis
unzip -o worklog-analysis.skill -d ~/.claude/skills/worklog-analysis/
```

## Step 2: Verify

1. Skill files exist:
   ```bash
   ls ~/.claude/skills/worklog-analysis/SKILL.md
   ls ~/.claude/skills/worklog-analysis/scripts/analyze_worklog.py
   ```

2. Script runs:
   ```bash
   python3 ~/.claude/skills/worklog-analysis/scripts/analyze_worklog.py --info
   ```

## No hooks needed

This skill is on-demand only — it reads existing worklog files when the user asks for standups, summaries, or reviews. No hooks required.

## Companion skills

- **worklog-logging** — creates the log entries this skill reads (install first)
- **self-improve** — learns from user corrections and persists preferences

## Uninstall

```bash
rm -rf ~/.claude/skills/worklog-analysis
```
