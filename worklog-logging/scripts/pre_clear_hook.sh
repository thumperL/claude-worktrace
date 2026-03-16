#!/bin/bash
# pre_clear_hook.sh — Intercept /clear to capture worklog before context is wiped.
#
# UserPromptSubmit fires for every prompt. We only care about /clear.
# Reads stdin JSON, checks prompt field, and delegates to pre_compact_hook.py.

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null)

case "$PROMPT" in
    /clear|/clear\ *)
        echo "$INPUT" | python3 ~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py
        ;;
esac
