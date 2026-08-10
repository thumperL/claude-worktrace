# claude-worktrace

Auto-captures your Claude Code sessions — what you did, what decisions you made, and how you corrected Claude — so nothing is lost when context compacts or sessions end.

**Three skills, zero manual effort:**

- **worklog-logging** — Hooks into compaction, `/clear`, and session end. Sonnet reads your transcript and writes narrative summaries ("Fixed auth race condition" not "edited 3 files")
- **self-improve** — Detects when you steer Claude ("use Hono not Express", "keep it shorter") and persists those as preferences. Project steers stay scoped; global ones apply everywhere
- **worklog-analysis** — Generates standups, weekly/monthly summaries from your worklog

**How it works:** You work normally. On compaction/clear/exit, a hook reads the transcript, Sonnet analyzes it in one API call, and writes both a worklog entry and any detected preferences. Everything syncs to `~/Documents/AI/` via iCloud and into Claude's native memory so it's active next session.

## Preferences that get checked

Most tools that learn your preferences never find out whether any of them worked. Saved preferences pile up in `CLAUDE.md` and nothing distinguishes the ones that changed Claude's behaviour from the ones that are decoration.

This one keeps the evidence. Every session end folds its transcript into a per-project ledger under `~/.claude/friction-ledger/`, and a saved preference is stamped with the date it was saved. If the behaviour it was meant to stop keeps showing up afterwards, you get told:

```
"Prefer subagents for research" — saved 2026-07-18.
3 occurrences since. The preference is not landing.
```

Two properties make that work. Evidence comes from the JSONL transcripts on disk rather than from Claude's recollection of the conversation, so it can span sessions and cite the actual calls. And nothing is proposed until it has recurred in **two separate sessions** — one confused afternoon is not a pattern. A fresh install therefore has nothing to say for a week or two, then compounds.

**Your worklog sees what you didn't narrate.** A command that failed four times before it worked never shows up in a session summary, because nobody types out their own retries. The tool calls are read separately and handed to the summariser, so "burned 40 calls recovering from a stale build command" can make the entry.

**Circumvention detection.** When a command is blocked, Claude sometimes reaches the same thing another way: `Read(.env)` refused, then `cat .env`. That is caught deterministically, with both calls attached, and surfaces as behaviour to correct rather than as a permission to widen. `scripts/lib/safety.py` decides whether the second route reached something the first was being kept away from.

Nothing is written to `CLAUDE.md` without showing you the exact diff, per item. See [docs/specs/2026-08-09-learning-loop-design.md](docs/specs/2026-08-09-learning-loop-design.md).

**Trigger modes** — set `learning_loop_mode` in `.claude/claude-worktrace.local.md` (project) or `~/.claude/claude-worktrace.local.md` (global):

| Mode | Behaviour |
| --- | --- |
| `on-demand` | Default. Nothing fires; installing changes no behaviour |
| `suggest` | Folds each session into the ledger, and nudges only when something recurs in a second session |
| `checkpoint` | Also checks at the self-improve context checkpoints |

## Install

```bash
claude plugins marketplace add https://github.com/thumperL/claude-worktrace
claude plugins install claude-worktrace
```

The first command registers the repo as a plugin source. The second installs the plugin, which automatically registers hooks and loads skills.

### Migrating from `.skill` zip install

If you previously installed via `.skill` files, use the bundled migration script to safely remove old artifacts before installing the plugin.

**Step 1: Preview what will be removed (dry run)**

```bash
python3 scripts/migrate-from-skills.py --dry-run
```

Review the output carefully. The script identifies:
- Hook entries in `~/.claude/settings.json` that reference `worklog-logging/scripts/`
- Skill directories named `{worklog-logging,self-improve,worklog-analysis}` under both
  `~/.claude/skills/` and `~/.claude/commands/` — earlier installs used `commands/`
- The matching `.skill` zip bundles sitting beside those directories

Only claude-worktrace artifacts are targeted — other hooks, skills and commands are left
untouched. A directory is removed only when its marker files match, and a `.skill` bundle
only when its zip contents match.

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
│       ├── ledger_hook.py
│       └── session_end_wrapper.sh
├── scripts/
│   ├── lib/
│   │   ├── transcript.py
│   │   ├── settings.py
│   │   ├── safety.py
│   │   ├── ledger.py
│   │   └── detectors.py
│   ├── friction.py
│   ├── write_worklog.py
│   ├── write_preferences.py
│   ├── analyze_worklog.py
│   └── migrate-from-skills.py
└── tests/
    ├── test_python39_compat.py
    ├── test_transcript_parser.py
    ├── test_safety.py
    ├── test_ledger.py
    └── test_ledger_hook.py
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

1. Bump version in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
2. Open PR to `main` and add the `release` label
3. On merge, auto-tag workflow creates the git tag from `plugin.json`
4. Release workflow validates and publishes the GitHub release
