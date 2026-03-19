# Install: worklog-logging

Agent-readable installation instructions for Claude Code CLI.
When a user asks you to install this skill, follow these steps exactly.

## Prerequisites

Create the storage directories if they don't exist:

```bash
mkdir -p ~/Documents/AI/worklog
mkdir -p ~/Documents/AI/self-improve
```

**Note:** The hook writes worklog entries to `~/Documents/AI/worklog/` and detected steers to `~/Documents/AI/self-improve/preferences-log.md` (if the self-improve skill is installed).

**Requirements:**
- Python 3.9+ (macOS system Python works)
- Claude CLI (`claude` command available in PATH) — for AI-powered summaries via `claude -p --model sonnet`

## Step 1: Install skill files

Unzip the `.skill` file into the Claude Code skills directory:

```bash
mkdir -p ~/.claude/skills/worklog-logging
unzip -o worklog-logging.skill -d ~/.claude/skills/worklog-logging/
```

## Step 2: Make shell scripts executable

```bash
chmod +x ~/.claude/skills/worklog-logging/scripts/session_end_wrapper.sh
chmod +x ~/.claude/skills/worklog-logging/scripts/pre_clear_hook.sh
```

## Step 3: Register hooks

This skill uses **three command hooks** registered in `~/.claude/settings.json`:

1. **PreCompact** — fires before context compaction, analyzes transcript and writes worklog + detects steers
2. **UserPromptSubmit** — intercepts `/clear` commands to capture worklog before context is wiped
3. **SessionEnd** — fires when the session ends, captures any post-compaction work

### Read the current settings

```bash
cat ~/.claude/settings.json
```

### Merge the hook configuration

Add the following to the `hooks` key in `settings.json`. If `hooks` doesn't exist, create it. If hook arrays already exist, **append** — do not overwrite.

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/skills/worklog-logging/scripts/pre_clear_hook.sh"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/skills/worklog-logging/scripts/session_end_wrapper.sh"
          }
        ]
      }
    ]
  }
}
```

**Important:**
- If `settings.json` doesn't exist, create it with just the hooks config above
- If `settings.json` exists but has no `hooks` key, add the `hooks` key
- If hook arrays already exist, check if the worklog hooks are already there. If so, skip. If not, append.
- The same Python script (`pre_compact_hook.py`) handles PreCompact, SessionEnd, and /clear — it reads the transcript and generates a worklog entry + detects steers
- The SessionEnd hook uses a wrapper script (`session_end_wrapper.sh`) that captures stdin to a temp file synchronously, then backgrounds the Python script. This is necessary because the stdin pipe (containing hook JSON) closes when the session exits — without the wrapper, the backgrounded process can't read its input
- The UserPromptSubmit hook uses `pre_clear_hook.sh` which filters for `/clear` prompts only — all other prompts pass through with zero overhead
- Preserve ALL other existing settings — never overwrite or remove anything

## Step 4: Verify

Run these checks:

1. Skill files exist:
   ```bash
   ls ~/.claude/skills/worklog-logging/SKILL.md
   ls ~/.claude/skills/worklog-logging/scripts/write_worklog.py
   ls ~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py
   ls ~/.claude/skills/worklog-logging/scripts/pre_clear_hook.sh
   ls ~/.claude/skills/worklog-logging/scripts/session_end_wrapper.sh
   ```

2. All three hooks are registered:
   ```bash
   python3 -c "
   import json, os
   with open(os.path.expanduser('~/.claude/settings.json')) as f:
       d = json.load(f)
   hooks = d.get('hooks', {})
   pc = any('pre_compact_hook' in str(h) for h in hooks.get('PreCompact', []))
   ups = any('pre_clear_hook' in str(h) for h in hooks.get('UserPromptSubmit', []))
   se = any('session_end_wrapper' in str(h) for h in hooks.get('SessionEnd', []))
   print(f'PreCompact hook: {pc}')
   print(f'UserPromptSubmit hook: {ups}')
   print(f'SessionEnd hook: {se}')
   "
   ```

3. Storage directories exist:
   ```bash
   ls -d ~/Documents/AI/worklog
   ls -d ~/Documents/AI/self-improve
   ```

4. Python scripts compile on system Python (3.9 compatible):
   ```bash
   python3 -c "import py_compile; py_compile.compile(os.path.expanduser('~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py'), doraise=True); print('pre_compact_hook.py: OK')" 2>/dev/null || python3 -c "import py_compile, os; py_compile.compile(os.path.expanduser('~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py'), doraise=True); print('pre_compact_hook.py: OK')"
   python3 ~/.claude/skills/worklog-logging/scripts/write_worklog.py --info
   ```

5. Claude CLI is available (for AI-powered summaries):
   ```bash
   which claude && echo "Claude CLI available — Sonnet summaries enabled"
   ```

## How it works

```
Session starts
  │
  ├── (work happens — full transcript recorded in .jsonl)
  │
  ├── Context compaction triggered
  │     └── PreCompact hook fires
  │           → pre_compact_hook.py reads transcript JSONL (only new lines since last run)
  │           → Spawns `claude -p --model sonnet` to analyze
  │           → Sonnet generates narrative summary + detects user steers
  │           → Calls write_worklog.py to persist worklog entry
  │           → Calls write_preferences.py --target log-only to log steers
  │           → Compaction proceeds
  │
  ├── (more work after compaction)
  │
  ├── User types /clear
  │     └── UserPromptSubmit hook fires
  │           → pre_clear_hook.sh checks if prompt is /clear
  │           → If yes: same pipeline (transcript → Sonnet → persist)
  │           → If no: exits immediately (zero overhead)
  │           → /clear executes after hook returns
  │
  └── Session ends (/exit, Ctrl+C, Ctrl+D)
        └── SessionEnd hook fires
              → session_end_wrapper.sh captures stdin to temp file
              → Backgrounds pre_compact_hook.py with temp file as stdin
              → Hook returns immediately (not cancelled)
              → Background process: transcript → AI analysis → persist
```

If the `claude` CLI is not available, the hook falls back to extracting user requests from the transcript as summary bullets.

## Claude Desktop Installation

If installing for Claude Desktop (not CLI), hooks are not needed. The skill operates as a pure skill with proactive triggers.

### Step 1: Install skill files

```bash
mkdir -p ~/.claude/skills/worklog-logging
unzip -o worklog-logging.skill -d ~/.claude/skills/worklog-logging/
```

### Step 2: Create storage directories

```bash
mkdir -p ~/Documents/AI/worklog
mkdir -p ~/Documents/AI/self-improve
```

### Step 3: Skip hooks

No hooks are needed for Desktop. The skill triggers proactively via its SKILL.md instructions — Claude will offer to log work at key moments (high context, task completion, before /clear, session wind-down).

### Step 4: Verify

1. Skill files exist:
   ```bash
   ls ~/.claude/skills/worklog-logging/SKILL.md
   ls ~/.claude/skills/worklog-logging/scripts/write_worklog.py
   ls ~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py
   ls ~/.claude/skills/worklog-logging/scripts/manual_worklog.sh
   ```

2. Storage directory exists:
   ```bash
   ls -d ~/Documents/AI/worklog
   ```

### Notes

- **No Claude CLI required**: In Desktop mode, AI summaries use Claude's own capabilities inline — the `claude` command is not needed.
- **Bash optional**: If bash is not available in your Desktop setup, the skill falls back to writing worklog entries directly using the Write/Edit tool (text-only mode). See SKILL.md "Desktop mode" section.
- **Trade-off**: CLI hooks are deterministic (always fire at the right moment). Desktop skill triggers are advisory (Claude proactively offers but isn't guaranteed). Both produce the same output format.

## Companion skills

This skill works best with its companions. Install them too:
- **worklog-analysis** — standup, weekly summary, monthly review, session tracing
- **self-improve** — learns from user corrections and persists preferences; hooks auto-detect steers

## Uninstall

```bash
rm -rf ~/.claude/skills/worklog-logging
```

Then remove the PreCompact, UserPromptSubmit, and SessionEnd hook entries from `~/.claude/settings.json`.
