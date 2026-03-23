#!/usr/bin/env python3
"""
write_worklog.py — Persist worklog entries to dated, per-machine markdown files.

Storage: ~/Documents/AI/worklog/
Fallback: ~/.claude/worklog/
File naming: YYYY-MM-DD-{hostname}.md

Usage:
    python write_worklog.py \
        --date "2026-03-08" --time "14:30" \
        --machine "macbook-pro" --session "sess-f3a1" \
        --project "acme-api" \
        --summary '["Refactored auth", "Fixed race condition"]' \
        --tech '["TypeScript", "Express"]' \
        --decisions "Chose PKCE over implicit" \
        --artifacts "PR #142" --open "Update API docs"
"""

import argparse
import json
import random
import socket
import string
import sys
from datetime import datetime
from pathlib import Path

ICLOUD_WORKLOG = Path.home() / "Documents" / "AI" / "worklog"
LOCAL_WORKLOG = Path.home() / ".claude" / "worklog"


def get_worklog_dir() -> Path:
    """Resolve worklog directory with validation and user-friendly error reporting."""
    # Primary: iCloud path (already created by user)
    if ICLOUD_WORKLOG.exists():
        return ICLOUD_WORKLOG

    # Check if iCloud parent exists (AI/ folder) — try to create worklog/ inside it
    icloud_parent = ICLOUD_WORKLOG.parent  # .../AI/
    if icloud_parent.exists():
        try:
            ICLOUD_WORKLOG.mkdir(parents=False, exist_ok=True)
            return ICLOUD_WORKLOG
        except OSError as e:
            print(f"⚠️  Could not create worklog directory at {ICLOUD_WORKLOG}: {e}", file=sys.stderr)
            print(f"   Falling back to {LOCAL_WORKLOG}", file=sys.stderr)

    # Check if ~/Documents/AI exists but worklog/ subfolder is missing
    icloud_root = Path.home() / "Documents" / "AI"
    if icloud_root.exists() and not icloud_parent.exists():
        print(f"⚠️  ~/Documents/AI found but worklog directory is missing.", file=sys.stderr)
        print(f"   Expected: {ICLOUD_WORKLOG}", file=sys.stderr)
        print(f"   Please create it: mkdir -p ~/Documents/AI/worklog", file=sys.stderr)
        print(f"   Falling back to {LOCAL_WORKLOG}", file=sys.stderr)

    # Fallback: local directory
    try:
        LOCAL_WORKLOG.mkdir(parents=True, exist_ok=True)
        return LOCAL_WORKLOG
    except OSError as e:
        print(f"❌ Could not create any worklog directory: {e}", file=sys.stderr)
        print(f"   Tried: {ICLOUD_WORKLOG}", file=sys.stderr)
        print(f"   Tried: {LOCAL_WORKLOG}", file=sys.stderr)
        sys.exit(1)


def get_hostname() -> str:
    hostname = socket.gethostname().lower().replace(".local", "")
    return "".join(c if c.isalnum() or c == "-" else "-" for c in hostname)


def generate_session_id() -> str:
    chars = string.ascii_lowercase + string.digits
    return f"sess-{''.join(random.choices(chars, k=4))}"


def write_entry(date_str, time_str, machine, session_id, project, summary,
                tech=None, decisions=None, artifacts=None, open_items=None,
                trigger=None):
    worklog_dir = get_worklog_dir()
    filepath = worklog_dir / f"{date_str}-{machine}.md"
    try:
        content = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
    except UnicodeDecodeError:
        print(f"Warning: {filepath} has encoding issues, reading with replacement", file=sys.stderr)
        content = filepath.read_text(encoding="utf-8", errors="replace") if filepath.exists() else ""

    # Dedup: skip if this session_id already has an entry in today's file
    # Skip dedup for sess-unknown since multiple unrelated entries can share it
    if content and session_id and session_id != "sess-unknown" and f"`{session_id}`" in content:
        print(f"Skipping duplicate entry for {session_id}", file=sys.stderr)
        return filepath

    if not content:
        content = f"# Worklog — {date_str} ({machine})\n\n"

    if trigger:
        entry = f"### {time_str} — {project} `{session_id}` ({trigger})\n\n"
    else:
        entry = f"### {time_str} — {project} `{session_id}`\n\n"
    entry += "**Summary:**\n"
    for item in summary:
        entry += f"- {item}\n"
    if tech:
        entry += f"\n**Tech:** {', '.join(tech)}\n"
    if decisions:
        entry += f"\n**Decisions:** {decisions}\n"
    if artifacts:
        entry += f"\n**Artifacts:** {artifacts}\n"
    if open_items:
        entry += f"\n**Open:** {open_items}\n"
    entry += "\n---\n\n"

    content += entry
    filepath.write_text(content, encoding="utf-8")
    print(f"Entry saved to {filepath}")
    print(f"Session: {session_id}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Write a worklog entry")
    parser.add_argument("--date", type=str)
    parser.add_argument("--time", type=str)
    parser.add_argument("--machine", type=str)
    parser.add_argument("--session", type=str)
    parser.add_argument("--project", type=str)
    parser.add_argument("--summary", type=str)
    parser.add_argument("--tech", type=str)
    parser.add_argument("--decisions", type=str)
    parser.add_argument("--artifacts", type=str)
    parser.add_argument("--open", type=str)
    parser.add_argument("--trigger", type=str)
    parser.add_argument("--info", action="store_true")

    args = parser.parse_args()

    if args.info:
        d = get_worklog_dir()
        files = sorted(d.glob("*.md"))
        print(f"Directory: {d}")
        print(f"Files: {len(files)}")
        if files:
            print(f"Range: {files[0].name} — {files[-1].name}")
        return

    if not args.summary:
        print("Error: --summary is required when writing an entry.", file=sys.stderr)
        sys.exit(1)

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    time_str = args.time or datetime.now().strftime("%H:%M")
    machine = args.machine or get_hostname()
    session_id = args.session or generate_session_id()

    try:
        summary = json.loads(args.summary)
    except json.JSONDecodeError as e:
        print(f"Error parsing summary: {e}", file=sys.stderr)
        sys.exit(1)

    tech = None
    if args.tech:
        try:
            tech = json.loads(args.tech)
        except json.JSONDecodeError:
            tech = [t.strip() for t in args.tech.split(",")]

    write_entry(date_str, time_str, machine, session_id, args.project or "general",
                summary, tech, args.decisions, args.artifacts, args.open,
                trigger=args.trigger)


if __name__ == "__main__":
    main()
