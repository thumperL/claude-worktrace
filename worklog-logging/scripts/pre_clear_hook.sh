#!/bin/bash
# pre_clear_hook.sh — Intercept /clear to capture worklog before context is wiped.
#
# UserPromptSubmit fires for every prompt. We only care about /clear.
# Uses bash string matching (fast) instead of spawning python3 (slow).

INPUT=$(cat)

# Fast check: only spawn python if the input contains /clear
case "$INPUT" in
    *'"prompt"'*'/clear'*)
        echo "$INPUT" | python3 ~/.claude/skills/worklog-logging/scripts/pre_compact_hook.py
        ;;
esac
