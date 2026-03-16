---
name: worklog-analysis
description: >
  Analyze past work logged by the worklog-logging skill. Generates daily standups, weekly summaries,
  monthly reviews, and performance review material.
  TRIGGER THIS SKILL when the user says "standup", "what did I do", "weekly summary", "monthly review",
  "performance review", "resume update", "what have I been working on", or asks to analyze, summarize,
  or review past work over any timeframe.
  This skill reads worklog files — it does NOT log new entries. For logging, use worklog-logging.
---

# Worklog Analysis

Read past worklog entries and synthesize them for standups, reviews, and career documentation.

## Where worklogs live

```
~/Documents/AI/worklog/
├── 2026-03-08-macbook-pro.md
├── 2026-03-07-mac-mini.md
└── ...
```

**Naming**: `YYYY-MM-DD-{hostname}.md`

Fallback: `~/.claude/worklog/`

Use the bundled script for all queries, or read files directly.

## Analysis commands

### Daily standup

**Trigger:** "standup", "what did I do yesterday", "daily update"

```bash
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --standup
```

Output format:
```
🧍 Standup — March 8, 2026

**Yesterday:**
- [Items grouped by project, with session IDs for context]

**Today (carry-over):**
- [Open items from yesterday]

**Blockers:** None
```

When presenting a standup, keep it tight — this is what the user will paste into Slack or read in a meeting. No fluff.

### Weekly summary

**Trigger:** "weekly summary", "what did I do this week"

```bash
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --weekly
```

Output format:
```
📊 Week of March 2–8, 2026

**Projects:** [list]
**Sessions:** [count across machines]

**Key accomplishments:**
[Grouped by project, most significant first]

**Technologies:** [aggregated]
**Decisions:** [notable ones]
**Artifacts:** [PRs, deployments, docs]
```

### Monthly review

**Trigger:** "monthly review", "performance review", "what did I do last month"

```bash
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --monthly
```

Output format:
```
📈 Monthly Review — February 2026

**Overview:** [1-2 sentences — scope, impact, themes]

**By project:**
- [Project]: [Summary, outcomes, metrics]

**Skills & technologies:** [aggregated — for resume]

**Key achievements:** [Top 3-5 by impact]

**Patterns:** [Time allocation, recurring themes, growth areas]

**Resume-ready bullets:**
- [Impact-oriented achievement with quantification]
- [Another, framed for performance reviews]
```

The resume-ready bullets translate raw work into impact language. Quantify where possible: "Reduced API latency by 40% by implementing Redis caching layer" not "Improved performance".

### Custom queries

**Trigger:** "what did I do on [date]", "show me [timeframe]"

```bash
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --query "2026-03-05"
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --query "this-week"
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --query "2026-03"  # whole month
```

### Session tracing

**Trigger:** "what happened in session sess-f3a1", "trace that session"

When the user asks about a specific session, grep the worklog files for that session ID and reconstruct the full timeline of what happened in that session across all its log entries.

```bash
python "${CLAUDE_SKILL_DIR}/scripts/analyze_worklog.py" --session "sess-f3a1"
```

## Session grouping

Multi-segment sessions (same `sess-XXXX` across PreCompact + SessionEnd entries) are automatically grouped into single entries in standup, weekly, and monthly views. Summary bullets are deduplicated within grouped entries.

Individual segments are still visible via `--session sess-XXXX` trace, which shows the full timeline of all entries for that session.

## Analysis tips

When generating reviews (weekly, monthly):
- Read ALL relevant files before synthesizing — don't summarize from a subset
- Cross-reference across machines to build the complete picture
- Deduplicate items that appear in multiple entries (same work logged at different points)
- Highlight decisions and their rationale — these are gold for performance reviews
- Frame achievements by impact, not effort ("Shipped X that enabled Y" not "Spent 3 days on X")
