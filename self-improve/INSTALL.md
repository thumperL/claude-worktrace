# Install: self-improve

Agent-readable installation instructions for Claude Code CLI.
When a user asks you to install this skill, follow these steps exactly.

## Prerequisites

Create the preferences storage directory if it doesn't exist:

```bash
mkdir -p ~/Documents/AI/self-improve
```

## Step 1: Install skill files

```bash
mkdir -p ~/.claude/skills/self-improve
unzip -o self-improve.skill -d ~/.claude/skills/self-improve/
```

## Step 2: Verify

1. Skill files exist:
   ```bash
   ls ~/.claude/skills/self-improve/SKILL.md
   ls ~/.claude/skills/self-improve/scripts/write_preferences.py
   ls ~/.claude/skills/self-improve/agents/analyzer.md
   ls ~/.claude/skills/self-improve/references/pattern_categories.md
   ```

2. Script runs:
   ```bash
   python3 ~/.claude/skills/self-improve/scripts/write_preferences.py --show
   ```

3. CLAUDE.md is writable (this is where preferences are stored):
   ```bash
   touch ~/.claude/CLAUDE.md && echo "CLAUDE.md writable: OK"
   ```

## No hooks needed (but benefits from worklog-logging hooks)

This skill triggers based on its description (steer detection, high context, explicit request). It does not need its own hooks.

However, if the **worklog-logging** skill is installed, its PreCompact, UserPromptSubmit (/clear), and SessionEnd hooks **automatically detect steers** from the transcript and log them to `~/Documents/AI/self-improve/preferences-log.md` via `write_preferences.py --target log-only`. This happens without user intervention.

The automatic steers are log-only — they are NOT applied to CLAUDE.md. The manual flow (conversation-based, user-confirmed) remains the only path to active preferences. Use "review detected steers" to see what was captured and promote items you want to keep.

## Claude Desktop Installation

If installing for Claude Desktop (not CLI), the setup is simpler — no hooks are involved.

### Step 1: Install skill files

```bash
mkdir -p ~/.claude/skills/self-improve
unzip -o self-improve.skill -d ~/.claude/skills/self-improve/
```

### Step 2: Create storage directory

```bash
mkdir -p ~/Documents/AI/self-improve
```

### Step 3: Verify

1. Skill files exist:
   ```bash
   ls ~/.claude/skills/self-improve/SKILL.md
   ls ~/.claude/skills/self-improve/scripts/write_preferences.py
   ls ~/.claude/skills/self-improve/agents/analyzer.md
   ```

2. CLAUDE.md is writable:
   ```bash
   touch ~/.claude/CLAUDE.md && echo "CLAUDE.md writable: OK"
   ```

### Notes

- **No hooks needed**: This skill never had its own hooks. It triggers based on conversation patterns.
- **Automatic steer capture unavailable**: In Desktop, the worklog-logging hooks that auto-detect steers are not available. The skill compensates by being more proactive (triggers at 2 steers instead of 3, analyzes at 60% context instead of 85%).
- **Bash optional**: If bash is not available, the skill writes preferences directly to `~/.claude/CLAUDE.md` using the Write/Edit tool. See SKILL.md Step 4 for the text-only format.

## How it persists across sessions

- **Active preferences** go to `~/.claude/CLAUDE.md` under a managed `<!-- self-improve:start/end -->` section
- Claude Code automatically reads `~/.claude/CLAUDE.md` at session start
- So preferences learned in one session are active in all future sessions
- **Detailed log** (with evidence and timestamps) goes to `~/Documents/AI/self-improve/preferences-log.md`

## Companion skills

- **worklog-logging** — captures work before compaction (has PreCompact + SessionEnd hooks)
- **worklog-analysis** — standup, weekly summary, monthly review

## Uninstall

```bash
rm -rf ~/.claude/skills/self-improve
```

To also remove learned preferences, edit `~/.claude/CLAUDE.md` and delete the section between `<!-- self-improve:start -->` and `<!-- self-improve:end -->`.
