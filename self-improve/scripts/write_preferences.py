#!/usr/bin/env python3
"""
write_preferences.py — Persist learned preferences with dual-write architecture.

Writes to BOTH Claude Code native paths (immediately active) and a standalone
portable store (iCloud-synced, tool-agnostic). All writes also go to the
audit trail (preferences-log.md).

Targets:
    global  — ~/.claude/CLAUDE.md + ~/Documents/AI/self-improve/GLOBAL_PREFERENCE.md + log
    project — ~/.claude/projects/{path}/memory/ + ~/Documents/AI/self-improve/projects/{name}/ + log
    log-only — preferences-log.md only (legacy, backward compat)

Usage:
    # Global preference (applied everywhere)
    python write_preferences.py \
        --preferences '[{"category": "Style", "preference": "Keep responses concise"}]' \
        --target global

    # Project preference (scoped to one project)
    python write_preferences.py \
        --preferences '[{"category": "Stack", "preference": "Use Hono not Express"}]' \
        --target project --project-name ipmates-v2 --project-cwd /Users/me/projects/ipmates-v2

    python write_preferences.py --show    # Display current preferences
    python write_preferences.py --remove "Use 2-space indent"  # Remove a preference
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


# --- Paths ---

CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

# Standalone store (iCloud-synced, portable)
STANDALONE_DIR = Path.home() / "Documents" / "AI" / "self-improve"
LOCAL_FALLBACK = CLAUDE_DIR

# Claude Code native memory base
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"


# --- Path resolution ---

def _get_standalone_dir():
    """Resolve standalone preferences directory, creating if parent exists."""
    if STANDALONE_DIR.exists():
        return STANDALONE_DIR
    icloud_parent = STANDALONE_DIR.parent  # ~/Documents/AI/
    if icloud_parent.exists():
        try:
            STANDALONE_DIR.mkdir(parents=False, exist_ok=True)
            return STANDALONE_DIR
        except OSError:
            pass
    return LOCAL_FALLBACK


def _get_prefs_log():
    """Resolve preferences log path."""
    base = _get_standalone_dir()
    return base / "preferences-log.md"


def _encode_claude_project_path(cwd):
    """Encode a cwd into Claude Code's project directory name.

    /Users/jane/projects/foo → -Users-jane-projects-foo
    """
    if not cwd:
        return None
    # Normalize: remove trailing slash, replace / with -
    clean = cwd.rstrip("/")
    encoded = clean.replace("/", "-")
    return encoded


def _sanitize_filename(text):
    """Create a safe filename from preference text."""
    # Take first 50 chars, lowercase, replace non-alnum with underscore
    clean = re.sub(r'[^a-z0-9]+', '_', text.lower().strip()[:50])
    clean = clean.strip('_')
    return clean or "preference"


# --- Constants ---

PREFS_LOG = _get_prefs_log()

SECTION_HEADER = "## User Preferences (Auto-Learned)"
SECTION_MARKER_START = "<!-- self-improve:start -->"
SECTION_MARKER_END = "<!-- self-improve:end -->"


# --- Utility ---

def ensure_claude_dir():
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)


def read_file(path):
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def extract_existing_preferences(content):
    """Extract preference lines from the managed section in CLAUDE.md."""
    prefs = []
    in_section = False
    for line in content.splitlines():
        if SECTION_MARKER_START in line:
            in_section = True
            continue
        if SECTION_MARKER_END in line:
            in_section = False
            continue
        if in_section and line.strip().startswith("- "):
            prefs.append(line.strip()[2:].strip())
    return prefs


def is_duplicate(new_pref, existing):
    """Check if a preference is already captured (fuzzy match)."""
    new_lower = new_pref.lower().strip()
    for existing_pref in existing:
        existing_lower = existing_pref.lower().strip()
        if new_lower == existing_lower:
            return True
        if new_lower in existing_lower or existing_lower in new_lower:
            return True
        new_words = set(new_lower.split())
        existing_words = set(existing_lower.split())
        if len(new_words) > 2 and len(existing_words) > 2:
            overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
            if overlap > 0.7:
                return True
    return False


# --- Writers: Claude Code native ---

def write_to_claude_md(preferences):
    """Append or update the auto-learned section in CLAUDE.md."""
    content = read_file(CLAUDE_MD)
    existing_prefs = extract_existing_preferences(content)

    new_prefs = []
    skipped = []
    for pref in preferences:
        pref_text = pref["preference"]
        if pref.get("context"):
            pref_text += " (when: %s)" % pref["context"]
        if is_duplicate(pref_text, existing_prefs):
            skipped.append(pref_text)
        else:
            new_prefs.append(pref_text)

    if not new_prefs:
        print("No new preferences to add. %d already exist." % len(skipped))
        return skipped

    all_prefs = existing_prefs + new_prefs
    section_lines = [
        "",
        SECTION_HEADER,
        SECTION_MARKER_START,
        "<!-- Managed by self-improve skill. Safe to edit manually. -->",
    ]
    for p in all_prefs:
        section_lines.append("- %s" % p)
    section_lines.append(SECTION_MARKER_END)
    section_lines.append("")

    new_section = "\n".join(section_lines)

    if SECTION_MARKER_START in content:
        # Use partition for safe single-occurrence splitting
        if SECTION_HEADER in content:
            before, _, _ = content.partition(SECTION_HEADER)
        else:
            before, _, _ = content.partition(SECTION_MARKER_START)
        _, _, after = content.rpartition(SECTION_MARKER_END)
        new_content = before.rstrip("\n") + new_section + after
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        new_content = content + new_section

    CLAUDE_MD.write_text(new_content, encoding="utf-8")
    print("Added %d preferences to %s" % (len(new_prefs), CLAUDE_MD))
    if skipped:
        print("Skipped %d duplicates: %s" % (len(skipped), skipped))
    return skipped


def _write_memory_dir(preferences, memory_dir, label=""):
    """Write preferences as individual memory files + MEMORY.md index.

    This is the shared format used by both Claude Code native memory
    and the standalone portable store. Same structure, same files.

    memory_dir/
    ├── MEMORY.md              ← index with links
    ├── feedback_some_pref.md  ← individual memory with frontmatter
    └── feedback_another.md
    """
    memory_dir.mkdir(parents=True, exist_ok=True)

    memory_index = memory_dir / "MEMORY.md"
    index_content = read_file(memory_index)
    if not index_content:
        index_content = "# Memory Index\n"

    written = 0
    for pref in preferences:
        name = pref.get("category", "General") + ": " + pref["preference"][:60]
        filename = "feedback_%s.md" % _sanitize_filename(pref["preference"])
        filepath = memory_dir / filename

        # Skip if file already exists with same preference (dedup)
        if filepath.exists():
            existing = read_file(filepath)
            if pref["preference"].lower() in existing.lower():
                continue

        description = pref["preference"]
        if pref.get("context"):
            description += " — %s" % pref["context"]

        body = "%s\n" % pref["preference"]
        if pref.get("evidence"):
            body += "\n**Why:** \"%s\"\n" % pref["evidence"]
        body += "\n**How to apply:** %s\n" % (pref.get("context") or "Apply when relevant.")

        file_content = "---\nname: %s\ndescription: %s\ntype: feedback\n---\n\n%s" % (
            name, description, body
        )
        filepath.write_text(file_content, encoding="utf-8")
        written += 1

        # Update index
        link = "- [%s](%s) — %s" % (filename, filename, pref["preference"][:80])
        if filename not in index_content:
            if "## Feedback" not in index_content:
                index_content += "\n## Feedback\n"
            index_content += link + "\n"

    memory_index.write_text(index_content, encoding="utf-8")
    if written:
        print("Wrote %d memory file(s) to %s%s" % (written, memory_dir, " (%s)" % label if label else ""))


# --- Writers: Claude Code native ---

def write_to_claude_memory(preferences, cwd):
    """Write project-scoped steers to Claude Code's native memory dir."""
    encoded = _encode_claude_project_path(cwd)
    if not encoded:
        return
    memory_dir = CLAUDE_PROJECTS_DIR / encoded / "memory"
    _write_memory_dir(preferences, memory_dir, label="claude-native")


# --- Writers: Standalone portable store ---

def write_to_standalone_global(preferences):
    """Write global steers to ~/Documents/AI/self-improve/ as memory files."""
    base = _get_standalone_dir()
    _write_memory_dir(preferences, base, label="standalone-global")


def write_to_standalone_project(preferences, project_name):
    """Write project steers to ~/Documents/AI/self-improve/projects/{name}/."""
    base = _get_standalone_dir()
    project_dir = base / "projects" / project_name
    _write_memory_dir(preferences, project_dir, label="standalone-project")


# --- Writer: Audit trail ---

def write_to_prefs_log(preferences, session_context=""):
    """Append detailed preference log with evidence and timestamps."""
    content = read_file(PREFS_LOG)

    if not content:
        content = "# Learned Preferences Log\n\n"
        content += "This file logs all preferences learned by the self-improve skill.\n"
        content += "Sync this file across machines to carry your preferences everywhere.\n\n"
        content += "---\n\n"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = "## %s" % timestamp
    if session_context:
        entry += " — %s" % session_context
    entry += "\n\n"

    for pref in preferences:
        scope = pref.get("scope", "global")
        entry += "- **%s** [%s]: %s\n" % (pref.get("category", "General"), scope, pref["preference"])
        if pref.get("context"):
            entry += "  - Context: %s\n" % pref["context"]
        if pref.get("evidence"):
            entry += "  - Evidence: \"%s\"\n" % pref["evidence"]
        confidence = pref.get("confidence", "medium")
        entry += "  - Confidence: %s\n" % confidence
    entry += "\n---\n\n"

    content += entry
    PREFS_LOG.write_text(content, encoding="utf-8")
    print("Logged %d preferences to %s" % (len(preferences), PREFS_LOG))


# --- Display / Remove ---

def show_preferences():
    """Display current active preferences."""
    content = read_file(CLAUDE_MD)
    prefs = extract_existing_preferences(content)
    if not prefs:
        print("No auto-learned preferences found in ~/.claude/CLAUDE.md")
        return
    print("Active preferences (%d):\n" % len(prefs))
    for i, p in enumerate(prefs, 1):
        print("  %d. %s" % (i, p))


def remove_preference(search_term):
    """Remove a preference matching the search term."""
    content = read_file(CLAUDE_MD)
    existing = extract_existing_preferences(content)
    search_lower = search_term.lower()

    to_remove = [p for p in existing if search_lower in p.lower()]
    if not to_remove:
        print("No preference found matching '%s'" % search_term)
        return

    remaining = [p for p in existing if p not in to_remove]

    section_lines = [
        "",
        SECTION_HEADER,
        SECTION_MARKER_START,
        "<!-- Managed by self-improve skill. Safe to edit manually. -->",
    ]
    for p in remaining:
        section_lines.append("- %s" % p)
    section_lines.append(SECTION_MARKER_END)
    section_lines.append("")
    new_section = "\n".join(section_lines)

    lines = content.splitlines()
    new_lines = []
    skip = False
    found_header = False
    for line in lines:
        if SECTION_HEADER in line:
            found_header = True
            continue
        if SECTION_MARKER_START in line:
            skip = True
            continue
        if SECTION_MARKER_END in line:
            skip = False
            continue
        if not skip and not (found_header and line.strip() == ""):
            new_lines.append(line)
            found_header = False

    while new_lines and new_lines[-1].strip() == "":
        new_lines.pop()

    new_content = "\n".join(new_lines) + new_section
    CLAUDE_MD.write_text(new_content, encoding="utf-8")
    print("Removed %d preference(s): %s" % (len(to_remove), to_remove))


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="Manage auto-learned preferences")
    parser.add_argument("--preferences", type=str, help="JSON array of preferences to save")
    parser.add_argument("--target", choices=["global", "project", "log-only", "auto"], default="global",
                        help="Scope: global, project, log-only, or auto (split by steer scope field)")
    parser.add_argument("--project-name", type=str, default="",
                        help="Project name (for --target project)")
    parser.add_argument("--project-cwd", type=str, default="",
                        help="Project cwd (for --target project, used to find Claude memory dir)")
    parser.add_argument("--session-context", type=str, default="",
                        help="Brief description of the session for the log entry")
    parser.add_argument("--show", action="store_true", help="Show current preferences")
    parser.add_argument("--remove", type=str, help="Remove preferences matching this term")

    args = parser.parse_args()
    ensure_claude_dir()

    if args.show:
        show_preferences()
        return

    if args.remove:
        remove_preference(args.remove)
        return

    if not args.preferences:
        parser.print_help()
        sys.exit(1)

    try:
        preferences = json.loads(args.preferences)
    except json.JSONDecodeError as e:
        print("Error parsing preferences JSON: %s" % e, file=sys.stderr)
        sys.exit(1)

    if not isinstance(preferences, list):
        print("Preferences must be a JSON array", file=sys.stderr)
        sys.exit(1)

    if args.target == "auto":
        # Split by scope field — one invocation handles both scopes
        global_prefs = [p for p in preferences if p.get("scope", "global") == "global"]
        project_prefs = [p for p in preferences if p.get("scope") == "project"]
        if global_prefs:
            write_to_claude_md(global_prefs)
            write_to_standalone_global(global_prefs)
        if project_prefs and args.project_cwd:
            write_to_claude_memory(project_prefs, args.project_cwd)
        if project_prefs and args.project_name:
            write_to_standalone_project(project_prefs, args.project_name)
        if project_prefs and not args.project_cwd and not args.project_name:
            print("Warning: %d project-scoped steers found but no project context provided. "
                  "Logged to audit trail only." % len(project_prefs), file=sys.stderr)
        write_to_prefs_log(preferences, args.session_context)

    elif args.target == "global":
        write_to_claude_md(preferences)
        write_to_standalone_global(preferences)
        write_to_prefs_log(preferences, args.session_context)

    elif args.target == "project":
        if args.project_cwd:
            write_to_claude_memory(preferences, args.project_cwd)
        if args.project_name:
            write_to_standalone_project(preferences, args.project_name)
        write_to_prefs_log(preferences, args.session_context)

    else:
        write_to_prefs_log(preferences, args.session_context)

    print("\nDone.")


if __name__ == "__main__":
    main()
