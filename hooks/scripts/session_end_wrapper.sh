#!/bin/bash
# SessionEnd wrapper — captures stdin before backgrounding the hook.
#
# Problem: SessionEnd hooks get cancelled if they take too long.
# Solution: Read stdin (hook JSON) synchronously into a temp file,
#           then background the actual work so the hook returns immediately.
#
# The stdin pipe closes when the session exits, so we MUST read it
# before backgrounding.

TMP=$(mktemp /tmp/worklog-hook.XXXXXX)
cat > "$TMP"

# Run the hook in background, detached from the session process
(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/pre_compact_hook.py" < "$TMP"; rm -f "$TMP") &
disown
