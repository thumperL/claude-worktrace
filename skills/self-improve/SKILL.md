---
name: self-improve
description: >
  Self-improving interaction skill that learns from user corrections and steering patterns.
  TRIGGER THIS SKILL when any of the following occur during a session:
  (1) The user corrects Claude's approach 3 or more times (e.g., "no, do X instead", "that's not what I meant",
  "actually...", rephrasing the same request, asking to redo work, expressing dissatisfaction).
  (2) At periodic context checkpoints (~25%, ~50%, ~75%) — this is routine logging, not a signal to stop.
  Capture learnings silently and continue working on the current task without interruption.
  (3) The user explicitly says "learn this", "remember this preference", "improve how you work with me",
  or similar self-improvement triggers.
  Also trigger when Claude notices repeated patterns of correction across a conversation, even before hitting
  the 3-correction threshold, if the pattern is clear. This skill is about making Claude better at working
  with this specific user over time. Use it liberally — it's better to learn too often than to miss patterns.
  IMPORTANT: This skill is about capturing learnings, NOT about managing context. Never suggest ending
  the session, starting fresh, or doing a handoff. After logging, resume the task seamlessly.
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

A steer count of 3 is a **within-session** trigger, and on its own it misses the
commonest case: a preference you state once per session, every session. Five mild
steers across five sessions never reaches three in any one of them.

### Trigger 1b: Something recurred across sessions

The ledger accrues across sessions, so the pattern that never trips the count is
visible there. Ask it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/friction.py" top --cwd "$(pwd)" \
    --kind correction --kind circumvention --text
```

Rows appear only after **two distinct sessions**, so anything listed has already
happened more than once, with the calls attached. Two kinds arrive here:

| Kind | Means |
| --- | --- |
| `correction` | a call was stopped, and something else happened instead |
| `circumvention` | a call was stopped, and the same target was reached anyway |

`circumvention` is the sharper one and reads differently in a report. It is not
"you corrected me"; it is "I was blocked and went around it". Say that plainly,
quote both calls, and do not soften it.

**Before proposing anything, check what already failed:**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/friction.py" verify --cwd "$(pwd)" --text
```

A preference saved weeks ago whose rows keep arriving did not work. That is more
useful than a new suggestion, and re-proposing the same wording is the wrong
response: either the wording was too vague to act on, or the preference is not
one Claude can follow. Say which you think it is.

### Trigger 2: Periodic context checkpoints (~25%, ~50%, ~75%)

At each checkpoint, capture any learnings accumulated since the last checkpoint. This is routine periodic logging — NOT a signal that context is running out. After capturing, **resume the current task immediately without comment**. Do not suggest compacting, ending the session, or starting fresh.

### Trigger 3: Explicit request

"Learn this", "remember this", "always do it this way", "improve yourself".

## Checkpoint mode vs. interactive mode

**Checkpoint triggers (~25/50/75% context):**
1. Spawn the analyzer subagent to detect patterns
2. Auto-save worklog silently (worklog doesn't need confirmation)
3. **If no patterns worth noting** — skip entirely, resume task without interrupting the user
4. **If patterns were found**, present them with options:

```
📋 Checkpoint — patterns detected:

1. **[Category]**: [Preference summary]
   Evidence: "[Brief quote]"

2. **[Category]**: [Preference summary]
   Evidence: "[Brief quote]"

Options:
[1] Save these
[2] Skip, save nothing
[3] Let me edit/add my own
```

On [1]: persist via `write_preferences.py --target log-only` (logged, not auto-applied to CLAUDE.md)
On [2]: discard and continue
On [3]: accept user input, then persist

After the user responds, **immediately resume the current task**.

**Optional — ledger check at the same checkpoint.** Only when
`learning_loop_mode: checkpoint` is set in `.claude/claude-worktrace.local.md`
(it is unset by default, so this is normally a no-op): also run the cheap
recurrence check and, if something has now come up in a second session, mention
it in one line alongside the patterns above.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/friction.py" check --cwd "$(pwd)"
```

It prints nothing unless something recurred in a second session, so silence is
the normal outcome and needs no comment.

**All other triggers** (steer count ≥ 3, explicit request) use the full interactive flow below, which can write directly to CLAUDE.md on confirmation.

## The Analysis Process (interactive mode)

### Step 1: Delegate to a subagent

**This is critical.** Do NOT analyze your own conversation directly — you have blind spots about your own mistakes. Instead, spawn a subagent to analyze the transcript with fresh eyes.

Use the Agent tool to spawn an analyzer:

```
Prompt for the subagent:
"Read the analyzer instructions at ${CLAUDE_PLUGIN_ROOT}/agents/analyzer.md

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
python "${CLAUDE_PLUGIN_ROOT}/scripts/write_preferences.py" \
  --preferences '[{"category": "...", "preference": "...", "context": "...", "evidence": "...", "rows": ["circ-1a2b3c4d5e"]}]' \
  --target global --cwd "$(pwd)"
```

**Pass `rows` whenever the preference came from ledger rows.** It stamps them as
applied, which is the only reason `verify` can tell later whether the preference
changed anything. A preference saved without it is unfalsifiable, and
unfalsifiable preferences are what fill a `CLAUDE.md` with decoration.

If the user declines a row, record that too, so the next session does not raise
it again:

```bash
python3 -c "
import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from lib import ledger as L
from datetime import datetime
p = L.ledger_path('$(pwd)'); led = L.Ledger.load(p)
led.decline('<row-id>', '<their reason, in their words>',
            datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'))
led.save(p)"
```

The script handles:
- Writing to `~/.claude/CLAUDE.md` (global, cross-project, active in all sessions)
- Writing to `~/Documents/AI/self-improve/preferences-log.md` (detailed log with timestamps, synced across devices)
- Deduplication (won't add preferences that already exist)

If the script isn't available, write directly to `~/.claude/CLAUDE.md` under a `## User Preferences (Auto-Learned)` section.

### Step 5: Trigger worklog

After saving preferences, also trigger the **worklog-logging** skill to capture what was accomplished. Present both outputs together for a single confirmation.

### Step 6: Resume work

After logging, **immediately continue with the current task**. Do not suggest compacting, ending the session, starting fresh, or doing a handoff. The purpose of periodic logging is to capture learnings incrementally — it is not a stopping point.

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

Hooks in `hooks/hooks.json` automatically detect steering patterns on PreCompact/SessionEnd and log them to `~/Documents/AI/self-improve/preferences-log.md`. Steers are never auto-applied — the manual flow (conversation-based, user-confirmed) remains the only path to CLAUDE.md.

**Reviewing detected steers:** Say "review detected steers", "what have you learned", or "show auto-detected preferences" to review and promote unconfirmed items.

## Recovery

Users can always:
- Edit `~/.claude/CLAUDE.md` directly
- Say "forget that preference" or "undo last learning"
- Say "show my preferences" to see what's saved
- Review the preferences log for full history
