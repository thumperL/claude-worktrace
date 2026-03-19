# claude-worktrace

Auto-captures your Claude Code sessions — what you did, what decisions you made, and how you corrected Claude — so nothing is lost when context compacts or sessions end.

**Three skills, zero manual effort:**

- **worklog-logging** — Hooks into compaction, `/clear`, and session end. Sonnet reads your transcript and writes narrative summaries ("Fixed auth race condition" not "edited 3 files")
- **self-improve** — Detects when you steer Claude ("use Hono not Express", "keep it shorter") and persists those as preferences. Project steers stay scoped; global ones apply everywhere
- **worklog-analysis** — Generates standups, weekly/monthly summaries from your worklog

**How it works:** You work normally. On compaction/clear/exit, a hook reads the transcript, Sonnet analyzes it in one API call, and writes both a worklog entry and any detected preferences. Everything syncs to `~/Documents/AI/` via iCloud and into Claude's native memory so it's active next session.

## Platform Compatibility

Works in both Claude Code CLI and Claude Desktop:

| Feature | Claude Code (CLI) | Claude Desktop |
|---------|-------------------|----------------|
| worklog-logging | Automatic via hooks (PreCompact, SessionEnd, /clear) | Proactive — Claude offers to log at key moments |
| self-improve | Auto-detect steers via hooks + manual flow | Manual flow only — more proactive steer detection |
| worklog-analysis | On-demand | On-demand (unchanged) |
| Bash scripts | Required | Optional — text-only fallback available |
| AI summaries | Via `claude -p --model sonnet` subprocess | Via Claude's own capabilities inline |

**Trade-off:** CLI hooks are deterministic (always fire at the right moment). Desktop skill triggers are advisory (Claude proactively offers). Both produce the same worklog and preferences output format.

## Install

1. Download all three `.skill` files from the [latest release](../../releases/latest)
2. Open Claude Code **in the directory where you downloaded them** and paste:

```
I have three .skill files to install: worklog-logging.skill, self-improve.skill, and worklog-analysis.skill.

For each one:
1. Read the INSTALL.md inside the zip to understand the steps
2. Unzip into ~/.claude/skills/{skill-name}/
3. Create required directories (~/Documents/AI/worklog, ~/Documents/AI/self-improve)
4. Make shell scripts executable (chmod +x)
5. Register hooks in ~/.claude/settings.json (merge, don't overwrite)
6. Run the verification checks from INSTALL.md

Start with worklog-logging (it has the hooks), then self-improve, then worklog-analysis.
```

3. Claude will read each INSTALL.md and do everything. Verify it worked:

```bash
python3 ~/.claude/skills/worklog-logging/scripts/write_worklog.py --info
python3 ~/.claude/skills/self-improve/scripts/write_preferences.py --show
python3 ~/.claude/skills/worklog-analysis/scripts/analyze_worklog.py --info
```

## Requirements

- Claude Code CLI (`claude` in PATH)
- Python 3.9+ (macOS system Python works)
- `~/Documents/AI/` directory (iCloud sync recommended)

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

GitHub Action validates Python 3.9 compatibility, packages each skill into a `.skill` zip, and attaches them to a release.
