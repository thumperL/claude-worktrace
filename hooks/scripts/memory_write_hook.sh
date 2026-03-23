#!/bin/bash
# memory_write_hook.sh — Capture auto-memory writes to worklog + self-improve.
#
# PostToolUse hook for Write tool. Fires on every Write, but exits early
# unless the file path is in a /memory/ directory (Claude's auto-memory).
#
# - Always: writes a worklog entry with the memory description
# - If type=feedback: also routes to write_preferences.py for self-improve

if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    exit 0
fi

INPUT=$(cat)

# Extract file_path from tool_input — fast check before any parsing
FILE_PATH=$(echo "$INPUT" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//')

# Only proceed if this is a memory directory write
case "$FILE_PATH" in
    */memory/*.md) ;;
    *) exit 0 ;;
esac

# Extract all fields via Python to avoid shell injection from user content
eval "$(echo "$INPUT" | python3 -c "
import json, sys, shlex

try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)

content = data.get('tool_input', {}).get('content', '')
if not content:
    sys.exit(1)

# Parse frontmatter
mem_type = ''
mem_desc = ''
in_frontmatter = False
for line in content.split('\n'):
    if line.strip() == '---':
        if in_frontmatter:
            break
        in_frontmatter = True
        continue
    if in_frontmatter:
        if line.startswith('type:'):
            mem_type = line.split(':', 1)[1].strip()
        elif line.startswith('description:'):
            val = line.split(':', 1)[1].strip()
            if val and not val.startswith('>'):
                mem_desc = val
        elif mem_desc == '' and line.startswith('  '):
            mem_desc = line.strip()

if not mem_desc:
    sys.exit(1)

session_id = data.get('session_id', '')
cwd = data.get('cwd', '')

# Output shell-safe assignments
print('MEM_TYPE=%s' % shlex.quote(mem_type))
print('MEM_DESC=%s' % shlex.quote(mem_desc))
print('SESSION_ID=%s' % shlex.quote(session_id))
print('CWD=%s' % shlex.quote(cwd))
" 2>&1)" || exit 0

# Build session tag
SESS_TAG=""
if [ -n "$SESSION_ID" ]; then
    SESS_TAG="sess-${SESSION_ID:0:4}"
fi

# Detect project from cwd
PROJECT="general"
if [ -n "$CWD" ]; then
    PROJECT=$(basename "$CWD")
fi

# Build JSON summary safely via Python
SUMMARY=$(python3 -c "
import json, sys
print(json.dumps(['Memory saved (%s): %s' % (sys.argv[1], sys.argv[2])]))
" "${MEM_TYPE:-unknown}" "$MEM_DESC")

# Always: write worklog entry
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/write_worklog.py" \
    --summary "$SUMMARY" \
    --trigger "AutoMemory" \
    --session "${SESS_TAG:-sess-unknown}" \
    --project "$PROJECT"

# If feedback type: also route to self-improve preferences
if [ "$MEM_TYPE" = "feedback" ]; then
    PREFS=$(python3 -c "
import json, sys
print(json.dumps([{'category': 'auto-memory', 'preference': sys.argv[1]}]))
" "$MEM_DESC")
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/write_preferences.py" \
        --preferences "$PREFS" \
        --target auto \
        --project-name "$PROJECT" \
        --project-cwd "${CWD:-/tmp}" \
        --session-context "Auto-detected via AutoMemory hook (${SESS_TAG:-unknown})"
fi
