#!/bin/bash
# pre_clear_hook.sh — Intercept /clear to capture worklog before context is wiped.
#
# UserPromptSubmit fires for every prompt. We only care about /clear.
# Uses bash string matching (fast) instead of spawning python3 (slow).

if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    echo "ERROR: CLAUDE_PLUGIN_ROOT is not set" >&2
    exit 1
fi

INPUT=$(cat)

# Match prompt value starting with /clear — avoids false positives from
# prompts that merely mention "/clear" mid-sentence.
# Handles both "prompt": "/clear" and "prompt":"/clear" (with/without space)
case "$INPUT" in
    *'"prompt": "/clear'*|*'"prompt":"/clear'*)
        echo "$INPUT" | python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/pre_compact_hook.py"
        ;;
esac
