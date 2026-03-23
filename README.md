# claude-worktrace

Auto-captures your Claude Code sessions — what you did, what decisions you made, and how you corrected Claude — so nothing is lost when context compacts or sessions end.

**Three skills, zero manual effort:**

- **worklog-logging** — Hooks into compaction, `/clear`, and session end. Sonnet reads your transcript and writes narrative summaries ("Fixed auth race condition" not "edited 3 files")
- **self-improve** — Detects when you steer Claude ("use Hono not Express", "keep it shorter") and persists those as preferences. Project steers stay scoped; global ones apply everywhere
- **worklog-analysis** — Generates standups, weekly/monthly summaries from your worklog

**How it works:** You work normally. On compaction/clear/exit, a hook reads the transcript, Sonnet analyzes it in one API call, and writes both a worklog entry and any detected preferences. Everything syncs to `~/Documents/AI/` via iCloud and into Claude's native memory so it's active next session.

## Install

```bash
claude plugins add github:thumperL/claude-worktrace
```

That's it. The plugin registers hooks and skills automatically.

### Migrating from `.skill` zip install

If you previously installed via `.skill` files, use the bundled migration script to safely remove old artifacts before installing the plugin.

**Step 1: Preview what will be removed (dry run)**

```bash
python3 scripts/migrate-from-skills.py --dry-run
```

Review the output carefully. The script identifies:
- Hook entries in `~/.claude/settings.json` that reference `worklog-logging/scripts/`
- Skill directories at `~/.claude/skills/{worklog-logging,self-improve,worklog-analysis}`

Only claude-worktrace artifacts are targeted — other hooks and skills are left untouched.

**Step 2: Run the migration**

```bash
python3 scripts/migrate-from-skills.py
```

The script backs up `settings.json` before modifying (saved as `settings.backup-*.json`).

**Step 3: Install the plugin**

```bash
claude plugins install claude-worktrace
```

**Step 4: Verify in a new session**

Start a fresh Claude Code session and confirm:
- Skills load (try "standup" or "log this")
- No duplicate skills in the skill list (each should appear once as `claude-worktrace:*`)

## Requirements

- Claude Code CLI (`claude` in PATH)
- Python 3.9+ (macOS system Python works)
- `~/Documents/AI/` directory (iCloud sync recommended)

## Project Structure

```
claude-worktrace/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── worklog-logging/SKILL.md
│   ├── worklog-analysis/SKILL.md
│   └── self-improve/
│       ├── SKILL.md
│       └── references/pattern_categories.md
├── agents/
│   └── analyzer.md
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── pre_compact_hook.py
│       ├── pre_clear_hook.sh
│       └── session_end_wrapper.sh
├── scripts/
│   ├── write_worklog.py
│   ├── write_preferences.py
│   └── analyze_worklog.py
└── tests/test_python39_compat.py
```

## Storage

```
~/Documents/AI/
├── worklog/                    # Worklog entries (per-day, per-machine)
│   ├── 2026-03-17-macbook-pro.md
│   └── ...
└── self-improve/
    ├── MEMORY.md               # Global preferences index
    ├── feedback_*.md           # Individual global preferences
    ├── preferences-log.md      # Audit trail (all steers with timestamps)
    └── projects/
        └── {project-name}/
            ├── MEMORY.md       # Project preferences index
            └── feedback_*.md   # Individual project preferences
```

## Releasing

```bash
git tag -a v0.3.0 -m "description" && git push origin v0.3.0
```

GitHub Action validates Python 3.9 compatibility and creates a release.
