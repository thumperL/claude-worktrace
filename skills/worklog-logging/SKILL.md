---
name: worklog-logging
description: >
  Lightweight work logger that captures what you accomplished in each Claude session.
  TRIGGER THIS SKILL when any of the following occur:
  (1) A session is ending or context is about to be compacted — capture what was done before it's lost.
  (2) The self-improve skill fires — piggyback on that trigger to also log work.
  (3) The user says "log this", "worklog", or wants to record what they've been doing.
  (4) The user says "thanks", "that's all", or similar session-ending phrases (Desktop/Cowork).
  (5) The conversation topic shifts significantly — log the previous topic before moving on (Desktop/Cowork).
  (6) A long conversation has covered substantial ground — proactively offer to log (Desktop/Cowork).
  This skill ONLY handles logging — for standups, weekly summaries, monthly reviews, or any
  analysis of past work, use the worklog-analysis skill instead.
  Works in CLI, Cowork, and Desktop chat.
  Use this skill liberally. It's cheap to log and expensive to forget.
---

# Worklog Logging

Capture what was accomplished in this session. This skill is intentionally lightweight — it logs and gets out of the way.

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
- [What problem was solved and WHY — enough detail for a resume or performance review]
- [What was researched, what was learned, what conclusions were reached]
- [Key decisions made and their reasoning]

**Decisions:** [Optional — architectural or design decisions]

**Artifacts:** [Optional — PRs, deployments, docs created]

**Open:** [Optional — what's still pending]

---
```

**Session ID** (`sess-XXXX`): Derived from Claude's `session_id` (first 4 chars of the UUID), ensuring consistency across all entries in a session — PreCompact and SessionEnd hooks produce matching IDs. This distinguishes parallel sessions on the same machine.

## What makes a good entry

Write as if explaining to a colleague or updating a resume months from now.

**GOOD bullets — tell the story:**
- Fixed NaN in annualized return calculation — JS Math.pow fails with negative base + fractional exponent, added guard for total loss exceeding invested capital
- Debugged worklog hooks not firing — root cause was Python 3.10 type syntax (dict | None) crashing on macOS system Python 3.9.6
- Completed security audit of 98-file branch — reviewed branding APIs, file upload handlers, confirmed proper auth/RBAC checks and file validation
- Researched IPv6 CIDR validation approaches, settled on ipaddr library for subnet handling

**BAD bullets — mechanical noise:**
- Edited performance.ts
- Ran 4 shell commands
- Used TypeScript
- Modified 3 files

Focus on the WHAT and WHY, never the HOW (tools used, files touched, tech stack). Those details are in git history if anyone needs them.

## Process

1. **Gather context**: `hostname -s` for machine, `date` for time, infer project from cwd/git/conversation
2. **Draft entry**: Focus on outcomes, decisions, problems solved. Be specific enough for a performance review months later.
3. **Show user**:
   ```
   Worklog entry:
   [the entry]
   Save to worklog?
   ```
4. **On confirmation, persist**:
   Run the bundled Python script:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/write_worklog.py" \
     --date "2026-03-08" --time "14:30" --machine "macbook-pro" \
     --session "sess-f3a1" --project "acme-api" \
     --summary '["Fixed auth token refresh race condition — stale tokens survived logout", "Researched PKCE vs implicit flow, chose PKCE for public client security"]' \
     --decisions "Chose PKCE over implicit flow" \
     --artifacts "PR #142" --open "Update API docs"
   ```

## Text-only fallback (Desktop / Cowork)

When only Read/Write/Edit tools are available (no Bash, no Agent), persist the worklog entry directly:

1. **Gather context**: Infer project from conversation, use current date/time, generate a session ID from context.
2. **Draft entry**: Same format as above — focus on outcomes, decisions, problems solved.
3. **Show user and confirm** (same as CLI flow).
4. **On confirmation, write directly**:
   - Read the current worklog file at `~/Documents/AI/worklog/YYYY-MM-DD-{hostname}.md`
   - If it doesn't exist, create it with a `# Worklog — YYYY-MM-DD` header
   - Append the entry using the Write or Edit tool
   - If `~/Documents/AI/worklog/` is not writable, fall back to `~/.claude/worklog/`

This ensures worklog capture works in Desktop chat where Bash is unavailable.

## Auto-capture via hooks

Hooks in `hooks/hooks.json` fire on PreCompact, `/clear`, and SessionEnd. Each reads the transcript, uses `claude -p --model sonnet` to generate a narrative summary, and persists it via `write_worklog.py`. Falls back to smart transcript parsing if the `claude` CLI is unavailable.

The same hook also detects user steering patterns and logs them via `write_preferences.py --target log-only` to `~/Documents/AI/self-improve/preferences-log.md`. Steers are NOT auto-applied to CLAUDE.md — use the self-improve skill to review and promote them.

## Integration with self-improve

When self-improve fires, it should also trigger this skill. Present both outputs (preferences learned + worklog entry) in a single confirmation to the user. One interruption, two outputs saved.
