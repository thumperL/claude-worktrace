---
name: self-improve
description: >
  Self-improving interaction skill that learns from user corrections and steering patterns.
  TRIGGER THIS SKILL when any of the following occur during a session:
  (1) The user corrects Claude's approach 2 or more times (e.g., "no, do X instead", "that's not what I meant",
  "actually...", rephrasing the same request, asking to redo work, expressing dissatisfaction).
  (2) The conversation has been long and productive, and you sense context may be getting compacted soon —
  capture what worked before it's lost.
  (3) The user explicitly says "learn this", "remember this preference", "improve how you work with me",
  or similar self-improvement triggers.
  Also trigger when Claude notices repeated correction patterns, even before hitting
  the 2-correction threshold, if the pattern is clear. This skill makes Claude better at working
  with this specific user over time. Use it liberally — better to learn too often than miss patterns.
  Works in Claude Code CLI, Cowork, and Claude Desktop chat.
---

# Self-Improve: Adaptive Interaction Learning

Analyze how the user has been steering you, identify patterns, and — with confirmation — persist those learnings for all future sessions.

## Platform detection

Figure out which environment you're running in before choosing your analysis approach:

- **CLI (Claude Code)**: You have `CLAUDE_SKILL_DIR` set, the Agent tool for subagents, and bash/Python. Hooks may auto-detect steers between sessions. Use the full subagent analysis flow.
- **Cowork (Claude Desktop with VM)**: You have bash, Python, and the Agent tool. Use the full subagent analysis flow. The working directory is under `/sessions/`.
- **Desktop chat (Claude Desktop without VM)**: You have Read/Write/Edit tools but no bash, Python, or Agent tool. Analyze the conversation directly and use text-only persistence.

The quickest check: if you have the Agent tool, use the subagent path. If not, analyze directly. If bash works, scripts are available for persistence; otherwise, use text-only.

## Why this matters

Every correction is signal. Without this skill, the user re-trains Claude every session. This skill closes the loop by capturing preferences and making them permanent.

## When to activate

### Trigger 1: Steer count >= 2

A "steer" is ANY input where the user shapes HOW you work. The bar is intentionally low:

- **Explicit corrections**: "No", "Wrong", "I meant X", "Actually..."
- **Directional guidance**: "Use this for that", "Try it with X", "Go with Z approach"
- **Rephrased requests**: Same request, different words (first attempt missed)
- **Redo requests**: "Try again", "Do it differently", "Go back and..."
- **Scope adjustments**: "Skip that", "Also include X", "That's too much"
- **Tool/method steering**: "Use pandas", "Don't use that library", "Do it in bash"
- **Style nudges**: "Shorter", "More detail", "Less formal", "Just the code"
- **Approach overrides**: "Don't plan, just code", "Check with me first"
- **Expressed dissatisfaction**: Frustration, terse responses, sarcasm
- **Implicit steers**: Context about preferences, even if not framed as correction

Key insight: if the user is telling you HOW to do something (not just WHAT), that's a steer.

### Trigger 2: Long productive conversation nearing its end

If the conversation has involved many exchanges, sustained work, and significant tool use, treat it as a learning opportunity. Don't wait for the user to ask — proactively analyze what patterns emerged before the conversation wraps up or context gets compacted. Signs to watch for: the user switching topics, saying thanks, the conversation feeling "done", or you sensing that earlier messages might be getting far from your attention.

### Trigger 3: Explicit request

"Learn this", "remember this", "always do it this way", "improve yourself".

## The Analysis Process

### Step 1: Analyze the conversation

**If you have the Agent tool (CLI or Cowork):** Delegate to a subagent. This gives you fresh eyes on your own blind spots.

Use the Agent tool to spawn an analyzer:

```
Prompt for the subagent:
"Read the analyzer instructions at ${CLAUDE_SKILL_DIR}/agents/analyzer.md

Then analyze this conversation transcript for user steering patterns:

<transcript>
[Paste or summarize the key parts of the conversation — focus on user messages
that steered, corrected, or guided behavior. Include what Claude did before
each steer so the analyzer has context.]
</transcript>

Also check these existing preferences for conflicts:
[Include current contents of ~/.claude/CLAUDE.md if available]

Return your analysis as JSON following the format in the analyzer instructions."
```

If `CLAUDE_SKILL_DIR` is not set, you can still spawn a subagent — just include the analysis instructions inline in the prompt instead of pointing to a file.

**If you don't have the Agent tool (Desktop chat):** Analyze the conversation directly. You'll have some blind spots about your own mistakes, so compensate by being extra conservative — only flag patterns you're confident about, and present findings with a note that you might have missed some. Focus on:
- What did the user correct you on? (These are the clearest signals.)
- Did the user rephrase the same request multiple times? (You likely misunderstood.)
- Did the user express preferences about style, format, or approach?
- Are there repeated patterns, not just one-offs?

### Step 2: Review the findings

Whether from a subagent or your own analysis, sanity-check:
- Do the identified patterns make sense?
- Are there any false positives (one-offs classified as patterns)?
- Are there conflicts with existing preferences?

### Step 3: Present to the user

Show EXACTLY what was learned, concisely:

```
Patterns detected from this session:

1. **[Category]**: [Preference summary]
   Evidence: "[Brief quote]"

2. **[Category]**: [Preference summary]
   Evidence: "[Brief quote]"

[If conflicts:] Conflict: Previously you preferred X, but this session suggests Y. Which should I keep?

Save these to your global preferences?
```

Keep it SHORT. The user wants to see what you learned and confirm quickly.

### Step 4: On confirmation, persist

**If bash/Python is available (CLI or Cowork):**

Write preferences using the bundled script:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/write_preferences.py" \
  --preferences '[{"category": "...", "preference": "...", "context": "...", "evidence": "..."}]' \
  --target global
```

If `CLAUDE_SKILL_DIR` is not set, try these fallback paths:
- `~/.claude/skills/self-improve/scripts/write_preferences.py`
- `~/Documents/AI/self-improve/scripts/write_preferences.py`

The script handles:
- Writing to `~/.claude/CLAUDE.md` (global, cross-project, active in all sessions)
- Writing to `~/Documents/AI/self-improve/preferences-log.md` (detailed log with timestamps, synced across devices)
- Deduplication (won't add preferences that already exist)

**If only Read/Write/Edit tools are available (Desktop chat):**

Write directly to `~/.claude/CLAUDE.md` using the Edit tool. The managed section uses markers so it won't collide with user-edited content:

```markdown
## User Preferences (Auto-Learned)
<!-- self-improve:start -->
<!-- Managed by self-improve skill. Safe to edit manually. -->
- [preference 1]
- [preference 2]
<!-- self-improve:end -->
```

If the markers already exist, read the file, find the section between `<!-- self-improve:start -->` and `<!-- self-improve:end -->`, and append new preferences inside it (checking for duplicates by reading existing lines first).

If the markers don't exist yet, append the whole block to the end of the file.

Also append a timestamped entry to `~/Documents/AI/self-improve/preferences-log.md` (or `~/.claude/self-improve-preferences.md` as fallback) with the category, preference, context, and evidence for each item.

### Step 5: Trigger worklog

After saving preferences, also trigger the **worklog-logging** skill to capture what was accomplished. Present both outputs together for a single confirmation.

### Step 6: Suggest compacting (CLI only)

If you're in Claude Code and context is high:

"Preferences and worklog saved. Context is getting high — should I compact now? Everything is persisted and will carry over."

In Desktop/Cowork, compaction is handled automatically by the platform, so skip this step.

## Handling conflicts

If a new preference contradicts an existing one:
- Show both to the user with the conflict clearly labeled
- On confirmation, replace the old preference
- Log the change in the preferences log with a note about the update

## What NOT to learn

- One-off task-specific requests (user said "use pandas for this" but normally prefers R)
- Project-specific preferences unless user says to generalize
- Anything explicitly marked as temporary

## Storage paths

- **Active preferences**: `~/.claude/CLAUDE.md` (auto-loaded by Claude in every session)
- **Detailed log**: `~/Documents/AI/self-improve/preferences-log.md` (synced across devices, includes evidence and timestamps)
- **Fallback log**: `~/.claude/self-improve-preferences.md`

## Automatic steer capture via hooks (CLI only)

In Claude Code CLI, hooks can auto-detect steers from transcripts between sessions. This happens without user intervention:
- Detected steers are logged to `~/Documents/AI/self-improve/preferences-log.md` for later review
- The manual flow (conversation-based, user-confirmed) remains the **only** path to CLAUDE.md
- Steers are logged with `--target log-only` — they are never auto-applied

In Desktop/Cowork, hooks are not available. The skill compensates by triggering more proactively during the conversation itself (see the description triggers above).

## Recovery

Users can always:
- Edit `~/.claude/CLAUDE.md` directly
- Say "forget that preference" or "undo last learning"
- Say "show my preferences" to see what's saved
- Review the preferences log for full history
