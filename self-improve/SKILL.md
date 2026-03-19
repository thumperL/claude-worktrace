---
name: self-improve
description: >
  Self-improving interaction skill that learns from user corrections and steering patterns.
  TRIGGER THIS SKILL when any of the following occur during a session:
  (1) The user corrects Claude's approach 3 or more times (e.g., "no, do X instead", "that's not what I meant",
  "actually...", rephrasing the same request, asking to redo work, expressing dissatisfaction).
  (2) Context usage is high (85%+) and the conversation has been productive — capture what worked before compacting.
  (3) The user explicitly says "learn this", "remember this preference", "improve how you work with me",
  or similar self-improvement triggers.
  Also trigger when Claude notices repeated patterns of correction across a conversation, even before hitting
  the 3-correction threshold, if the pattern is clear. This skill is about making Claude better at working
  with this specific user over time. Use it liberally — it's better to learn too often than to miss patterns.
---

# Self-Improve: Adaptive Interaction Learning

Analyze how the user has been steering you, identify patterns, and — with confirmation — persist those learnings for all future sessions.

## Why this matters

Every correction is signal. Without this skill, the user re-trains Claude every session. This skill closes the loop by capturing preferences and making them permanent.

## When to activate

### Trigger 1: Steer count ≥ 3

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

### Trigger 2: High context (85%+)

Before compacting, analyze the full conversation. This is your last chance to extract learnings.

### Trigger 3: Explicit request

"Learn this", "remember this", "always do it this way", "improve yourself".

## The Analysis Process

### Step 1: Delegate to a subagent

**This is critical.** Do NOT analyze your own conversation directly — you have blind spots about your own mistakes. Instead, spawn a subagent to analyze the transcript with fresh eyes.

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

The subagent returns structured JSON with steers found, patterns identified, and any conflicts.

### Step 2: Review the subagent's findings

Read the JSON response. Sanity-check:
- Do the identified patterns make sense?
- Are there any false positives (one-offs classified as patterns)?
- Are there conflicts with existing preferences?

### Step 3: Present to the user

Show EXACTLY what was learned, concisely:

```
📋 Patterns detected from this session:

1. **[Category]**: [Preference summary]
   Evidence: "[Brief quote]"

2. **[Category]**: [Preference summary]
   Evidence: "[Brief quote]"

[If conflicts:] ⚠️ Conflict: Previously you preferred X, but this session suggests Y. Which should I keep?

Save these to your global preferences?
```

Keep it SHORT. The user wants to see what you learned and confirm quickly.

### Step 4: On confirmation, persist

Write preferences using the bundled script:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/write_preferences.py" \
  --preferences '[{"category": "...", "preference": "...", "context": "...", "evidence": "..."}]' \
  --target global
```

The script handles:
- Writing to `~/.claude/CLAUDE.md` (global, cross-project, active in all sessions)
- Writing to `~/Documents/AI/self-improve/preferences-log.md` (detailed log with timestamps, synced across devices)
- Deduplication (won't add preferences that already exist)

If the script isn't available (or bash is unavailable, e.g. in Claude Desktop), write directly using the Write/Edit tool:

1. Read `~/.claude/CLAUDE.md`
2. Find the `<!-- self-improve:start -->` / `<!-- self-improve:end -->` markers
3. Append new preferences inside the markers:
   ```
   - [Category] Preference text (learned YYYY-MM-DD)
   ```
4. If markers don't exist, create the section:
   ```markdown
   ## User Preferences (Auto-Learned)
   <!-- self-improve:start -->
   - [Category] Preference text (learned YYYY-MM-DD)
   <!-- self-improve:end -->
   ```
5. Also append to `~/Documents/AI/self-improve/preferences-log.md`:
   ```markdown
   ## YYYY-MM-DD HH:MM — [Category]
   - **Preference**: The preference text
   - **Evidence**: "brief user quote that triggered this"
   - **Scope**: global|project
   - **Source**: Desktop session (manual)
   ---
   ```

### Step 5: Trigger worklog

After saving preferences, also trigger the **worklog-logging** skill to capture what was accomplished. Present both outputs together for a single confirmation.

### Step 6: Suggest compacting

If context is high:

"Preferences and worklog saved. Context is getting high — should I compact now? Everything is persisted and will carry over."

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

## Automatic steer capture (hooks)

PreCompact and SessionEnd hooks (from the worklog-logging skill) automatically detect steering patterns from each transcript segment. This happens without user intervention:

- Detected steers are logged to `~/Documents/AI/self-improve/preferences-log.md` for later review
- The manual flow (conversation-based, user-confirmed) remains the **only** path to CLAUDE.md
- Steers are logged with `--target log-only` — they are never auto-applied

### Desktop mode

In Claude Desktop, hooks are not available, so automatic steer capture between sessions does not occur. To compensate, this skill should be **more proactive** about detecting steers during the conversation:

- **Lower threshold**: Trigger at 2 steers instead of 3 in Desktop — fewer automatic captures means more manual captures needed
- **Earlier context trigger**: Analyze for steers at 60%+ context instead of 85%+ — don't wait until it's almost too late
- **Inline presentation**: Present detected steers during the conversation when noticed, rather than waiting for a dedicated review step
- **No auto-detected steers**: The "review detected steers" feature only contains entries from CLI sessions that had hooks active. In Desktop-only usage, the preferences log will only contain manually-confirmed entries.

### Reviewing detected steers

Trigger: "review detected steers", "what have you learned", "show auto-detected preferences"

When triggered, read the preferences log, identify entries marked as "Auto-detected via [hook]", and present unconfirmed items for the user to promote to active preferences or dismiss.

## Recovery

Users can always:
- Edit `~/.claude/CLAUDE.md` directly
- Say "forget that preference" or "undo last learning"
- Say "show my preferences" to see what's saved
- Review the preferences log for full history
