#!/usr/bin/env python3
"""
pre_compact_hook.py — AI-powered worklog generation.

Reads the session transcript and uses `claude -p` (print mode) to generate
a meaningful, narrative worklog entry. Falls back to smart transcript parsing
if the claude CLI is unavailable.

Handles PreCompact, SessionEnd, and Clear events.

Input (JSON via stdin):
  - session_id: unique session identifier
  - transcript_path: path to the session's JSONL transcript
  - hook_event_name: "PreCompact", "SessionEnd", or "Clear"
  - cwd: current working directory
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# State file tracks how many lines we've already processed per transcript,
# so we only summarize NEW content on each invocation.
STATE_DIR = Path(os.environ.get("TMPDIR", "/tmp"))
STATE_FILE = STATE_DIR / "worklog-hook-state.json"


def read_stdin():
    """Read hook JSON input from stdin."""
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return {}


def _load_state():
    """Load the processing state (lines already handled per transcript)."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    """Persist updated state."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def _transcript_key(path):
    """Stable key for a transcript path."""
    return hashlib.md5(str(path).encode()).hexdigest()


def parse_transcript(transcript_path, start_line=0):
    """Parse the transcript JSONL into user/assistant message pairs.

    Only processes lines starting from `start_line` (0-indexed) so that
    previously-logged content is not re-summarized.
    Returns (messages, total_lines_read).
    """
    messages = []
    line_num = 0
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                if line_num < start_line:
                    line_num += 1
                    continue
                line_num += 1

                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "user":
                    text = _extract_from_message_field(entry.get("message", ""))
                    if text and len(text.strip()) > 5:
                        messages.append(("user", text.strip()[:1000]))

                elif entry_type == "assistant":
                    text = _extract_from_message_field(entry.get("message", ""))
                    if text and len(text.strip()) > 10:
                        messages.append(("assistant", text.strip()[:800]))

    except (FileNotFoundError, PermissionError):
        pass

    return messages, line_num


def _safe_parse_stringified_dict(s):
    """Parse a stringified Python dict using json after converting Python syntax."""
    if not isinstance(s, str) or not s.startswith("{"):
        return None
    try:
        # Try JSON first
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Try converting Python dict syntax to JSON
    try:
        # Replace single quotes with double quotes (simple cases)
        converted = s.replace("'", '"')
        # Handle Python True/False/None
        converted = converted.replace(": True", ": true").replace(": False", ": false").replace(": None", ": null")
        return json.loads(converted)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _extract_from_message_field(raw_msg):
    """Extract text content from a message field."""
    if not raw_msg:
        return ""

    # If it's already a dict
    if isinstance(raw_msg, dict):
        return _text_from_content(raw_msg.get("content", ""))

    # If it's a stringified dict
    if isinstance(raw_msg, str) and raw_msg.startswith("{"):
        parsed = _safe_parse_stringified_dict(raw_msg)
        if parsed and isinstance(parsed, dict):
            return _text_from_content(parsed.get("content", ""))

    # Plain string
    if isinstance(raw_msg, str):
        return raw_msg

    return ""


def _text_from_content(content):
    """Extract text from a content field (string or list of content blocks)."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)

    return ""


def detect_project(cwd):
    """Detect project name from cwd."""
    if not cwd:
        return "general"

    containers = {"projects", "repos", "workspace", "workspaces",
                  "code", "github", "gitlab"}
    parts = cwd.replace("\\", "/").split("/")

    # Look for project container pattern
    for i, p in enumerate(parts):
        if p.lower() in containers and i + 1 < len(parts):
            candidate = parts[i + 1]
            if candidate and len(candidate) > 1 and not candidate.startswith("."):
                return candidate

    # Fallback: skip system dirs and username
    skip = {"Users", "home", "tmp", "var", "Documents", "Desktop", "Downloads",
            ".claude", "src", "lib", "dist", "build", "AI"}
    past_user = False
    for p in parts:
        if not p or p.startswith("."):
            continue
        if p in ("Users", "home"):
            past_user = False
            continue
        if not past_user and p not in skip:
            past_user = True  # this is the username, skip
            continue
        if past_user and p not in skip and len(p) > 2 and p[0].isalpha():
            return p

    return "general"


def condense_transcript(messages, max_chars=8000):
    """Condense messages to fit within a reasonable prompt size."""
    condensed = []
    total = 0

    # Take messages from most recent, working backwards
    for role, text in reversed(messages):
        # Skip very short or noisy messages
        if len(text) < 10:
            continue
        # Skip system/hook noise
        if "WORKLOG CAPTURE" in text or "hook_progress" in text:
            continue

        entry = "%s: %s" % (role.upper(), text)
        if total + len(entry) > max_chars:
            break
        condensed.append(entry)
        total += len(entry)

    condensed.reverse()
    return "\n\n".join(condensed)


def summarize_with_claude(condensed, project):
    """Use claude CLI in print mode to generate a narrative summary."""
    prompt = (
        'You are analyzing a Claude Code session transcript to generate a worklog entry '
        'for the project "%s".\n\n'
        'Produce TWO outputs in a single JSON response:\n\n'
        '1. WORKLOG SUMMARY: Write 3-7 bullet points that tell the STORY of this session. Focus on:\n'
        '- What problems were identified, investigated, and solved — and WHY\n'
        '- What was researched and what conclusions were reached\n'
        '- Key decisions made and their reasoning\n'
        '- Corrections the user made and what they revealed\n'
        '- Blockers encountered and how they were resolved\n'
        '- What is still open or needs follow-up\n\n'
        'Write as if explaining to a colleague or for a future resume/performance review.\n'
        'Be specific — include the reasoning, root causes, and outcomes.\n\n'
        'GOOD: "Fixed NaN in annualized return — JS Math.pow fails with negative base + '
        'fractional exponent, added guard for losses exceeding capital"\n'
        'BAD: "Edited performance.ts"\n\n'
        '2. STEERS: Identify user corrections or steering patterns — places where\n'
        '   the user told Claude HOW to work (not just what to do). Look for:\n'
        '   - Explicit corrections ("no, do X instead", "actually...", "I meant...")\n'
        '   - Style/approach steering ("shorter", "just the code", "don\'t plan")\n'
        '   - Tool preferences ("use pandas", "don\'t use that library")\n'
        '   - Scope adjustments ("skip that", "that\'s too much")\n'
        '   Only include genuine reusable PATTERNS, not one-off task instructions.\n'
        '   If no steers detected, return empty array.\n\n'
        '   For each steer, classify its SCOPE:\n'
        '   - "global": applies to ALL projects (e.g., "keep responses shorter", "don\'t plan")\n'
        '   - "project": applies only to THIS project (e.g., "don\'t reference v1", "use Hono not Express")\n\n'
        'Output ONLY a valid JSON object (no markdown, no explanation):\n'
        '{"summary": ["bullet 1", "bullet 2"], "decisions": "key decisions or empty string", '
        '"open": "open items or empty string", '
        '"steers": [{"category": "...", "preference": "...", "context": "...", '
        '"evidence": "brief user quote", "confidence": "high|medium", '
        '"scope": "global|project"}]}\n\n'
        '--- SESSION TRANSCRIPT ---\n'
        '%s\n'
        '--- END TRANSCRIPT ---'
    ) % (project, condensed)

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "sonnet"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            # Find JSON object in output
            json_match = re.search(r'\{[\s\S]*\}', output)
            if json_match:
                parsed = json.loads(json_match.group())
                if "summary" in parsed and isinstance(parsed["summary"], list):
                    # Reject junk summaries where Haiku couldn't extract real work
                    junk_phrases = [
                        "no session transcript",
                        "no actionable session",
                        "awaiting actual session",
                        "awaiting complete session",
                        "no transcript provided",
                        "instructions without actual",
                        "instructions repeated",
                    ]
                    first_bullet = (parsed["summary"][0] or "").lower() if parsed["summary"] else ""
                    if any(phrase in first_bullet for phrase in junk_phrases):
                        return None
                    return parsed
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception:
        pass

    return None


def fallback_summary(messages):
    """Smart transcript-based summary when claude CLI is unavailable."""
    bullets = []

    # Extract user requests — these ARE the work items
    for role, text in messages:
        if role == "user" and len(text) > 20:
            # Skip system messages
            if any(skip in text for skip in ["WORKLOG", "hook_progress", "system-reminder"]):
                continue
            clean = text[:200].replace("\n", " ").strip()
            if len(clean) > 150:
                clean = clean[:147] + "..."
            bullets.append("Worked on: %s" % clean)
            if len(bullets) >= 7:
                break

    if not bullets:
        bullets = ["Session work captured (install claude CLI for richer AI-generated summaries)"]

    return {"summary": bullets, "decisions": "", "open": "", "steers": []}


def write_worklog(project, result, event, session_id=None):
    """Persist the worklog entry directly (no subprocess)."""
    try:
        # Import write_worklog.py from same directory
        script_dir = str(Path(__file__).parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from write_worklog import write_entry, get_hostname

        now = datetime.now()
        write_entry(
            date_str=now.strftime("%Y-%m-%d"),
            time_str=now.strftime("%H:%M"),
            machine=get_hostname(),
            session_id=session_id or "sess-unknown",
            project=project,
            summary=result["summary"],
            decisions=result.get("decisions"),
            open_items=result.get("open"),
        )
        print("[%s] Worklog entry saved for project: %s" % (event, project))
    except Exception as e:
        print("[%s] Error writing worklog: %s" % (event, e), file=sys.stderr)


def write_steers(steers, session_id, event, project="general", cwd=""):
    """Persist detected steers — dual-write to Claude native + standalone store."""
    if not steers:
        return
    write_script = Path(__file__).parent.parent.parent / "self-improve" / "scripts" / "write_preferences.py"
    if not write_script.exists():
        return

    session_ctx = "Auto-detected via %s hook (%s)" % (event, session_id or "unknown")
    cmd = [
        sys.executable, str(write_script),
        "--preferences", json.dumps(steers),
        "--target", "auto",
        "--project-name", project,
        "--project-cwd", cwd,
        "--session-context", session_ctx,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        pass


def main():
    hook_input = read_stdin()
    transcript_path = hook_input.get("transcript_path", "")
    cwd = hook_input.get("cwd", "")
    event = hook_input.get("hook_event_name", "PreCompact")
    raw_sid = hook_input.get("session_id", "")
    session_id = "sess-%s" % raw_sid[:4] if raw_sid else None

    if not transcript_path or not Path(transcript_path).exists():
        sys.exit(0)

    # Load state to find where we left off for this transcript
    state = _load_state()
    key = _transcript_key(transcript_path)
    start_line = state.get(key, 0)

    # Parse only NEW lines since last invocation
    messages, total_lines = parse_transcript(transcript_path, start_line)
    if len(messages) < 4:
        # Need at least 2 user+assistant pairs for a meaningful summary
        state[key] = total_lines
        _save_state(state)
        sys.exit(0)

    project = detect_project(cwd)
    condensed = condense_transcript(messages)

    if not condensed or len(condensed) < 50:
        state[key] = total_lines
        _save_state(state)
        sys.exit(0)

    # Try AI summary first, fall back to smart parsing
    result = summarize_with_claude(condensed, project)
    if not result:
        result = fallback_summary(messages)

    write_worklog(project, result, event, session_id=session_id)
    steers = result.get("steers", [])
    write_steers(steers, session_id, event, project=project, cwd=cwd)

    # Mark these lines as processed
    state[key] = total_lines
    _save_state(state)


if __name__ == "__main__":
    main()
