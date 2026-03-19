---
name: worklog-logging
description: >
  Lightweight work logger that captures what you accomplished in each Claude session.
  TRIGGER THIS SKILL when any of the following occur:
  (1) A session is ending or context is about to be compacted — capture what was done before it's lost.
  (2) The self-improve skill fires — piggyback on that trigger to also log work.
  (3) The user says "log this", "worklog", or wants to record what they've been doing.
  (4) The conversation has been long and productive — many tool calls, multiple tasks completed,
  or significant back-and-forth — and no worklog has been saved yet. Don't wait to be asked.
  (5) The user wraps up with signals like "thanks", "that's all", "goodbye", or switches to a
  completely different topic after sustained work.
  (6) The user says "/clear" or is about to start fresh.
  This skill ONLY handles logging — for standups, weekly summaries, monthly reviews, or any
  analysis of past work, use the worklog-analysis skill instead.
  Use this skill liberally. It's cheap to log and expensive to forget.
---

# Worklog Logging

Capture what was accomplished in this session. This skill is intentionally lightweight — it logs and gets out of the way.

## Platform detection

Before following the process below, figure out which environment you're running in. This determines which tools and paths are available:

- **CLI (Claude Code)**: You have `CLAUDE_SKILL_DIR` set, hooks handle auto-logging, `claude -p` is available, and you're typically in a git repo or project directory. If hooks are installed, you may not even need this skill — it fires automatically.
- **Cowork (Claude Desktop with VM)**: You have bash, Python, the Agent tool, and file system access, but no hooks. Skill scripts work if you run them explicitly. The working directory is under `/sessions/`.
- **Desktop chat (Claude Desktop without VM)**: You have Read/Write/Edit tools but no bash or Python. Use the text-only persistence method below.

The quickest check: try running a simple bash command. If bash works, you're in CLI or Cowork. If it doesn't, you're in Desktop chat. If `CLAUDE_SKILL_DIR` is set, you're likely in CLI.

## Storage

Files go to `~/Documents/AI/worklog/` for cross-device sync:

```
~/Documents/AI/worklog/
├── 2026-03-08-macbook-pro.md
├── 2026-03-08-mac-mini.md
├── 2026-03-07-macbook-pro.md
└── ...
```

**Naming**: `YYYY-MM-DD-{hostname}.md` — date-first for chronological sorting.

Fallback: `~/.claude/worklog/`

## Entry format

```markdown
### HH:MM — [Project/Context] `sess-XXXX`

**Summary:**
- [Specific accomplishment with enough detail for a performance review]
- [Another accomplishment — include numbers where possible]

**Tech:** [comma-separated technologies]

**Decisions:** [Optional — architectural or design decisions]

**Artifacts:** [Optional — PRs, deployments, docs created]

**Open:** [Optional — what's still pending]

---
```

**Session ID** (`sess-XXXX`): Generate a short 4-char alphanumeric ID for this session. Reuse the same ID if you've already logged earlier in this conversation. This distinguishes parallel sessions on the same machine.

## Process

### When bash/Python is available (CLI or Cowork)

1. **Gather context**: `hostname -s` for machine, `date` for time, infer project from cwd/git/conversation
2. **Draft entry**: Focus on outcomes, not process. Be specific enough for a performance review months later.
3. **Show user**:
   ```
   Worklog entry:
   [the entry]
   Save to worklog?
   ```
4. **On confirmation, persist**:
   Run the bundled Python script in this skill's `scripts/` directory:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/write_worklog.py" \
     --date "2026-03-08" --time "14:30" --machine "macbook-pro" \
     --session "sess-f3a1" --project "acme-api" \
     --summary '["Refactored auth middleware", "Fixed race condition"]' \
     --tech '["TypeScript", "Express"]' \
     --decisions "Chose PKCE over implicit" \
     --artifacts "PR #142" --open "Update API docs"
   ```

   If `CLAUDE_SKILL_DIR` is not set (e.g., in Cowork), try these fallback paths in order:
   - `~/.claude/skills/worklog-logging/scripts/write_worklog.py`
   - Look for `write_worklog.py` in `~/Documents/AI/worklog/scripts/`

   If the script can't be found, fall through to the text-only method below.

### When only Read/Write/Edit tools are available (Desktop chat)

1. **Gather context from conversation**: Infer project from what you've been working on, use current date/time from system context, use "desktop" as the machine name.
2. **Draft entry**: Same quality bar — specific, quantified, outcome-focused.
3. **Show user** and get confirmation.
4. **On confirmation, persist using Write/Edit tools**:
   - Target path: `~/Documents/AI/worklog/YYYY-MM-DD-desktop.md`
   - If the file exists, use Edit to append the new entry before the last line
   - If the file doesn't exist, use Write to create it with the header and entry
   - If `~/Documents/AI/worklog/` is not accessible, try `~/.claude/worklog/`

   The entry format is identical whether written by script or by hand — downstream analysis tools (worklog-analysis) don't care how it got there.

## What makes a good entry

- Specific: "Fixed auth token refresh race condition in /api/auth/refresh" not "Fixed a bug"
- Quantified: "Added 12 integration tests" not "Added tests"
- Outcome-focused: "Deployed v2.3 to prod" not "Worked on deployment"
- Brief: 2-4 bullets per session, not a novel

## Auto-trigger via hooks (CLI only)

This section only applies to Claude Code CLI where hooks are available. In Desktop/Cowork, skip this — the skill triggers from the description above instead.

This skill uses three hooks working together for reliable auto-logging:

1. **PostToolUse hook** (`scripts/post_tool_use_logger.py`, async) — fires after every tool call during the session. Captures tool name, files touched, commands run, bash command categories, and technologies to `/tmp/claude-worklog-{session_id}.jsonl`. Runs asynchronously so it never slows down the session.

2. **PreCompact hook** (`scripts/pre_compact_hook.py`) — fires before context compaction. Reads the activity log from `/tmp/` (NOT the transcript, which may already be compacted), generates rich summary bullets, and calls `write_worklog.py`. Cleans up the temp file after.

3. **SessionEnd hook** (`scripts/pre_compact_hook.py`) — fires when the session ends (Cmd+C, /exit, Ctrl+D). Uses the same script as PreCompact. Captures any work done after the last compaction. If PreCompact already processed the log, SessionEnd finds nothing and exits cleanly.

All hooks are registered in `~/.claude/settings.json` under `hooks.PostToolUse`, `hooks.PreCompact`, and `hooks.SessionEnd`.

If the hook's auto-generated entry is too sparse, you can always invoke `/worklog-logging` manually for a richer, context-aware entry.

## Integration with self-improve

When self-improve fires, it should also trigger this skill. Present both outputs (preferences learned + worklog entry) in a single confirmation to the user. One interruption, two outputs saved.
