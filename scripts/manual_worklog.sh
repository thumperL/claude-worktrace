#!/usr/bin/env bash
# manual_worklog.sh — Manual worklog entry point for Desktop or CLI.
#
# Usage:
#   manual_worklog.sh "Summary of what was done"
#   manual_worklog.sh --project myproject "Summary text"
#   manual_worklog.sh --project myproject "Bullet 1" "Bullet 2"

set -euo pipefail

# Resolve hook script — prefer CLAUDE_PLUGIN_ROOT, fall back to relative path
if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
    HOOK_SCRIPT="${CLAUDE_PLUGIN_ROOT}/hooks/scripts/pre_compact_hook.py"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    HOOK_SCRIPT="$SCRIPT_DIR/../hooks/scripts/pre_compact_hook.py"
fi

if [[ ! -f "$HOOK_SCRIPT" ]]; then
    echo "Error: pre_compact_hook.py not found at $HOOK_SCRIPT" >&2
    exit 1
fi

PROJECT=""
SUMMARIES=()

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)
            PROJECT="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: manual_worklog.sh [--project NAME] \"summary text\" [\"more bullets\"]"
            echo ""
            echo "Writes a worklog entry using the pre_compact_hook.py script."
            echo "If multiple summary arguments are given, each becomes a bullet point."
            echo ""
            echo "Options:"
            echo "  --project NAME    Project name (default: detected from cwd)"
            echo "  --help, -h        Show this help"
            exit 0
            ;;
        *)
            SUMMARIES+=("$1")
            shift
            ;;
    esac
done

if [[ ${#SUMMARIES[@]} -eq 0 ]]; then
    echo "Error: provide at least one summary argument." >&2
    echo "Usage: manual_worklog.sh [--project NAME] \"summary text\"" >&2
    exit 1
fi

# Build JSON array from summary arguments
if [[ ${#SUMMARIES[@]} -eq 1 ]]; then
    # Single argument — let pre_compact_hook.py handle parsing
    SUMMARY_JSON="${SUMMARIES[0]}"
else
    # Multiple arguments — build JSON array
    SUMMARY_JSON="["
    for i in "${!SUMMARIES[@]}"; do
        if [[ $i -gt 0 ]]; then
            SUMMARY_JSON+=","
        fi
        # Escape double quotes in the summary text
        escaped="${SUMMARIES[$i]//\"/\\\"}"
        SUMMARY_JSON+="\"$escaped\""
    done
    SUMMARY_JSON+="]"
fi

# Build command
CMD=(python3 "$HOOK_SCRIPT" --summary "$SUMMARY_JSON" --cwd "$(pwd)")

if [[ -n "$PROJECT" ]]; then
    CMD+=(--project "$PROJECT")
fi

exec "${CMD[@]}"
