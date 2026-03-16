---
name: worklog-logging
description: >
  Lightweight work logger that captures what you accomplished in each Claude session.
  TRIGGER THIS SKILL when any of the following occur:
  (1) A session is ending or context is about to be compacted — capture what was done before it's lost.
  (2) The self-improve skill fires — piggyback on that trigger to also log work.
  (3) The user says "log this", "worklog", or wants to record what they've been doing.
  This skill ONLY handles logging — for standups, weekly summaries, monthly reviews, or any
  analysis of past work, use the worklog-analysis skill instead.
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
   Run the bundled Python script in this skill's `scripts/` directory:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/write_worklog.py" \
     --date "2026-03-08" --time "14:30" --machine "macbook-pro" \
     --session "sess-f3a1" --project "acme-api" \
     --summary '["Fixed auth token refresh race condition — stale tokens survived logout", "Researched PKCE vs implicit flow, chose PKCE for public client security"]' \
     --decisions "Chose PKCE over implicit flow" \
     --artifacts "PR #142" --open "Update API docs"
   ```
   > **Note:** `${CLAUDE_SKILL_DIR}` resolves to this skill's installation directory automatically.

## Auto-capture via AI-powered hooks

This skill uses **command hooks** that spawn a Claude subagent (`claude -p --model sonnet`) to analyze the session transcript and generate meaningful, narrative worklog entries automatically.

### How it works

```
Session starts
  │
  ├── (work happens — full transcript is recorded)
  │
  ├── Context compaction triggered
  │     └── PreCompact hook fires
  │           → Reads session transcript (JSONL)
  │           → Spawns claude -p --model sonnet to analyze
  │           → Sonnet generates narrative summary + detects steers
  │           → Calls write_worklog.py to persist worklog
  │           → Calls write_preferences.py to log steers (log-only)
  │           → Compaction proceeds
  │
  ├── (more work after compaction)
  │
  ├── User types /clear
  │     └── UserPromptSubmit hook fires
  │           → pre_clear_hook.sh checks if prompt is /clear
  │           → If yes: same pipeline (transcript → Sonnet → persist)
  │           → /clear executes after hook returns
  │
  └── Session ends (/exit, Ctrl+C, Ctrl+D)
        └── SessionEnd hook fires
              → Same process: read transcript → AI summary → persist
```

### Why a subagent, not mechanical analysis

The transcript contains everything — user requests, Claude's reasoning, decisions, corrections, errors. Only an AI can extract the semantic meaning ("debugged X because Y") from raw conversation data. Mechanical tool-activity counting produces useless entries like "edited 3 files, ran 5 commands."

### Fallback

If the `claude` CLI is not available, the hook falls back to smart transcript parsing — extracting user requests as work items. This is better than nothing but less rich than the AI summary.

## Automatic steer detection

The hook also detects user steering patterns (steers) from the same transcript in the same Sonnet API call. Detected steers are logged via self-improve's `write_preferences.py --target log-only` to `~/Documents/AI/self-improve/preferences-log.md`.

**Important:** Steers are NOT auto-applied to CLAUDE.md. Use the self-improve skill's manual flow to review and confirm detected steers before promoting them to active preferences.

## Integration with self-improve

When self-improve fires, it should also trigger this skill. Present both outputs (preferences learned + worklog entry) in a single confirmation to the user. One interruption, two outputs saved.
