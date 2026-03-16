#!/usr/bin/env python3
"""
write_preferences.py — Persist learned preferences to ~/.claude/CLAUDE.md
and ~/Documents/AI/self-improve/preferences-log.md

Usage:
    python write_preferences.py \
        --preferences '[{"category": "Code Style", "preference": "Use 2-space indent", "context": "All JS/TS files", "evidence": "User said: use 2 spaces"}]' \
        --target global

    python write_preferences.py --show    # Display current preferences
    python write_preferences.py --remove "Use 2-space indent"  # Remove a preference
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

# iCloud primary, local fallback
ICLOUD_PREFS = Path.home() / "Documents" / "AI" / "self-improve"
LOCAL_PREFS = CLAUDE_DIR


def _get_prefs_log() -> Path:
    """Resolve preferences log path with validation and user-friendly error reporting."""
    # Primary: iCloud path (already created by user)
    if ICLOUD_PREFS.exists():
        return ICLOUD_PREFS / "preferences-log.md"

    # Check if parent (AI/) exists — try to create self-improve/ inside it
    icloud_parent = ICLOUD_PREFS.parent  # .../AI/
    if icloud_parent.exists():
        try:
            ICLOUD_PREFS.mkdir(parents=False, exist_ok=True)
            return ICLOUD_PREFS / "preferences-log.md"
        except OSError as e:
            print(f"⚠️  Could not create preferences directory at {ICLOUD_PREFS}: {e}", file=sys.stderr)
            print(f"   Falling back to {LOCAL_PREFS / 'self-improve-preferences.md'}", file=sys.stderr)

    # Check if ~/Documents/AI exists but self-improve/ subfolder is missing
    docs_ai = Path.home() / "Documents" / "AI"
    if docs_ai.exists() and not icloud_parent.exists():
        print(f"⚠️  ~/Documents/AI found but self-improve directory is missing.", file=sys.stderr)
        print(f"   Expected: {ICLOUD_PREFS}", file=sys.stderr)
        print(f"   Please create it: mkdir -p ~/Documents/AI/self-improve", file=sys.stderr)
        print(f"   Falling back to {LOCAL_PREFS / 'self-improve-preferences.md'}", file=sys.stderr)

    # Fallback: local path
    return LOCAL_PREFS / "self-improve-preferences.md"


PREFS_LOG = _get_prefs_log()

SECTION_HEADER = "## User Preferences (Auto-Learned)"
SECTION_MARKER_START = "<!-- self-improve:start -->"
SECTION_MARKER_END = "<!-- self-improve:end -->"


def ensure_claude_dir():
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)


def read_file(path: Path) -> str:
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
        # Exact match
        if new_lower == existing_lower:
            return True
        # Substring match (one contains the other)
        if new_lower in existing_lower or existing_lower in new_lower:
            return True
        # High word overlap
        new_words = set(new_lower.split())
        existing_words = set(existing_lower.split())
        if len(new_words) > 2 and len(existing_words) > 2:
            overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
            if overlap > 0.7:
                return True
    return False


def write_to_claude_md(preferences):
    """Append or update the auto-learned section in CLAUDE.md."""
    content = read_file(CLAUDE_MD)
    existing_prefs = extract_existing_preferences(content)

    new_prefs = []
    skipped = []
    for pref in preferences:
        pref_text = pref["preference"]
        if pref.get("context"):
            pref_text += f" (when: {pref['context']})"
        if is_duplicate(pref_text, existing_prefs):
            skipped.append(pref_text)
        else:
            new_prefs.append(pref_text)

    if not new_prefs:
        print(f"No new preferences to add. {len(skipped)} already exist.")
        return skipped

    # Build the new section content
    all_prefs = existing_prefs + new_prefs
    section_lines = [
        "",
        SECTION_HEADER,
        SECTION_MARKER_START,
        "<!-- Managed by self-improve skill. Safe to edit manually. -->",
    ]
    for p in all_prefs:
        section_lines.append(f"- {p}")
    section_lines.append(SECTION_MARKER_END)
    section_lines.append("")

    new_section = "\n".join(section_lines)

    # Replace existing section or append
    if SECTION_MARKER_START in content:
        # Replace the existing managed section
        lines = content.splitlines()
        new_lines = []
        skip = False
        for line in lines:
            if SECTION_MARKER_START in line or (SECTION_HEADER in line and SECTION_MARKER_START in content):
                # Find the header line before the marker
                skip = True
                continue
            if SECTION_MARKER_END in line:
                skip = False
                continue
            if SECTION_HEADER in line and skip is False and SECTION_MARKER_START in content:
                continue
            if not skip:
                new_lines.append(line)

        # Remove trailing blank lines before appending
        while new_lines and new_lines[-1].strip() == "":
            new_lines.pop()

        new_content = "\n".join(new_lines) + new_section
    else:
        # Append to the end
        if content and not content.endswith("\n"):
            content += "\n"
        new_content = content + new_section

    CLAUDE_MD.write_text(new_content, encoding="utf-8")
    print(f"Added {len(new_prefs)} preferences to {CLAUDE_MD}")
    if skipped:
        print(f"Skipped {len(skipped)} duplicates: {skipped}")
    return skipped


def write_to_prefs_log(preferences, session_context=""):
    """Append detailed preference log with evidence and timestamps."""
    content = read_file(PREFS_LOG)

    if not content:
        content = "# Learned Preferences Log\n\n"
        content += "This file logs all preferences learned by the self-improve skill.\n"
        content += "Sync this file across machines to carry your preferences everywhere.\n"
        content += "The active preferences are in `~/.claude/CLAUDE.md`.\n\n"
        content += "---\n\n"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"## {timestamp}"
    if session_context:
        entry += f" — {session_context}"
    entry += "\n\n"

    for pref in preferences:
        entry += f"- **{pref.get('category', 'General')}**: {pref['preference']}\n"
        if pref.get("context"):
            entry += f"  - Context: {pref['context']}\n"
        if pref.get("evidence"):
            entry += f"  - Evidence: \"{pref['evidence']}\"\n"
        confidence = pref.get("confidence", "medium")
        entry += f"  - Confidence: {confidence}\n"
    entry += "\n---\n\n"

    content += entry
    PREFS_LOG.write_text(content, encoding="utf-8")
    print(f"Logged {len(preferences)} preferences to {PREFS_LOG}")


def show_preferences():
    """Display current active preferences."""
    content = read_file(CLAUDE_MD)
    prefs = extract_existing_preferences(content)
    if not prefs:
        print("No auto-learned preferences found in ~/.claude/CLAUDE.md")
        return
    print(f"Active preferences ({len(prefs)}):\n")
    for i, p in enumerate(prefs, 1):
        print(f"  {i}. {p}")


def remove_preference(search_term: str):
    """Remove a preference matching the search term."""
    content = read_file(CLAUDE_MD)
    existing = extract_existing_preferences(content)
    search_lower = search_term.lower()

    to_remove = [p for p in existing if search_lower in p.lower()]
    if not to_remove:
        print(f"No preference found matching '{search_term}'")
        return

    remaining = [p for p in existing if p not in to_remove]

    # Rebuild the section
    section_lines = [
        "",
        SECTION_HEADER,
        SECTION_MARKER_START,
        "<!-- Managed by self-improve skill. Safe to edit manually. -->",
    ]
    for p in remaining:
        section_lines.append(f"- {p}")
    section_lines.append(SECTION_MARKER_END)
    section_lines.append("")
    new_section = "\n".join(section_lines)

    # Replace existing section
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
    print(f"Removed {len(to_remove)} preference(s): {to_remove}")


def main():
    parser = argparse.ArgumentParser(description="Manage auto-learned preferences")
    parser.add_argument("--preferences", type=str, help="JSON array of preferences to save")
    parser.add_argument("--target", choices=["global", "log-only"], default="global",
                        help="Where to write (global = CLAUDE.md + log, log-only = just the log)")
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
        print(f"Error parsing preferences JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(preferences, list):
        print("Preferences must be a JSON array", file=sys.stderr)
        sys.exit(1)

    if args.target == "global":
        write_to_claude_md(preferences)
    write_to_prefs_log(preferences, args.session_context)

    print("\nDone. Preferences will be active in all future Claude sessions on this machine.")
    print(f"To sync across machines, copy {PREFS_LOG} to ~/.claude/ on other machines.")


if __name__ == "__main__":
    main()
