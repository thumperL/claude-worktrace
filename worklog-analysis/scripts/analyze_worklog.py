#!/usr/bin/env python3
"""
analyze_worklog.py — Read and analyze worklog entries for standups, summaries, reviews.

Storage: ~/Documents/AI/worklog/
Fallback: ~/.claude/worklog/

Usage:
    python analyze_worklog.py --standup
    python analyze_worklog.py --weekly
    python analyze_worklog.py --monthly
    python analyze_worklog.py --query "2026-03-08"
    python analyze_worklog.py --query "this-week"
    python analyze_worklog.py --query "2026-03"
    python analyze_worklog.py --session "sess-f3a1"
    python analyze_worklog.py --info
"""

import argparse
import re
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

ICLOUD_WORKLOG = Path.home() / "Documents" / "AI" / "worklog"
LOCAL_WORKLOG = Path.home() / ".claude" / "worklog"


def get_worklog_dir() -> Path:
    """Resolve worklog directory with validation and user-friendly error reporting."""
    # Primary: iCloud path
    if ICLOUD_WORKLOG.exists():
        return ICLOUD_WORKLOG

    # Fallback: local path
    if LOCAL_WORKLOG.exists():
        return LOCAL_WORKLOG

    # Neither exists — provide helpful diagnostics
    docs_ai = Path.home() / "Documents" / "AI"
    if docs_ai.exists():
        print(f"⚠️  ~/Documents/AI found but worklog directory is missing.", file=sys.stderr)
        print(f"   Expected: {ICLOUD_WORKLOG}", file=sys.stderr)
        print(f"   Please create it: mkdir -p ~/Documents/AI/worklog", file=sys.stderr)
    else:
        print(f"⚠️  ~/Documents/AI directory not found.", file=sys.stderr)
        print(f"   Expected: {docs_ai}", file=sys.stderr)
        print(f"   Please create it: mkdir -p ~/Documents/AI/worklog", file=sys.stderr)

    print(f"   Local fallback also missing: {LOCAL_WORKLOG}", file=sys.stderr)
    print(f"   No worklog data to analyze. Run worklog-logging first to create entries.", file=sys.stderr)
    sys.exit(1)


def parse_file_date(filepath: Path) -> str:
    parts = filepath.stem.split("-")
    return f"{parts[0]}-{parts[1]}-{parts[2]}" if len(parts) >= 3 else ""


def parse_file_machine(filepath: Path) -> str:
    parts = filepath.stem.split("-")
    return "-".join(parts[3:]) if len(parts) >= 4 else "unknown"


def find_files(days_back):
    worklog_dir = get_worklog_dir()
    files = []
    today = datetime.now().date()
    for i in range(days_back + 1):
        d = today - timedelta(days=i)
        files.extend(sorted(worklog_dir.glob(f"{d.strftime('%Y-%m-%d')}-*.md")))
    return files


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def parse_entries(content):
    """Parse a worklog file into structured entries."""
    entries = []
    current = None

    for line in content.splitlines():
        # Entry header: ### HH:MM — project `sess-xxxx`
        if line.startswith("### ") and " — " in line:
            if current:
                entries.append(current)
            time_part, rest = line[4:].split(" — ", 1)
            session = None
            project = rest.strip()
            sess_match = re.search(r'`(sess-\w+)`', rest)
            if sess_match:
                session = sess_match.group(1)
                project = rest[:sess_match.start()].strip()

            current = {
                "time": time_part.strip(),
                "project": project,
                "session": session,
                "summary": [],
                "tech": [],
                "decisions": None,
                "artifacts": None,
                "open": None,
                "_section": None,
            }
            continue

        if not current:
            continue

        if line.startswith("**Summary:**"):
            current["_section"] = "summary"
            continue
        elif line.startswith("**Tech:**"):
            techs = line.replace("**Tech:**", "").strip()
            current["tech"] = [t.strip() for t in techs.split(",") if t.strip()]
            current["_section"] = None
            continue
        elif line.startswith("**Decisions:**"):
            current["decisions"] = line.replace("**Decisions:**", "").strip()
            current["_section"] = None
            continue
        elif line.startswith("**Artifacts:**"):
            current["artifacts"] = line.replace("**Artifacts:**", "").strip()
            current["_section"] = None
            continue
        elif line.startswith("**Open:**"):
            current["open"] = line.replace("**Open:**", "").strip()
            current["_section"] = None
            continue
        elif line.strip() == "---":
            current["_section"] = None
            continue

        if current["_section"] == "summary" and line.strip().startswith("- "):
            current["summary"].append(line.strip()[2:])

    if current:
        entries.append(current)

    # Clean up internal field
    for e in entries:
        e.pop("_section", None)

    return entries


def group_by_session(entries):
    """Merge entries with the same session ID into single grouped entries."""
    grouped = {}
    ungrouped = []
    for e in entries:
        sid = e.get("session")
        if not sid:
            ungrouped.append(e)
            continue
        if sid not in grouped:
            grouped[sid] = {
                "time": e["time"],
                "project": e["project"],
                "session": sid,
                "summary": list(e["summary"]),
                "tech": list(e.get("tech", [])),
                "decisions": e.get("decisions"),
                "artifacts": e.get("artifacts"),
                "open": e.get("open"),
            }
        else:
            g = grouped[sid]
            g["summary"].extend(e["summary"])
            g["tech"] = list(set(g["tech"]) | set(e.get("tech", [])))
            if e.get("decisions"):
                g["decisions"] = e["decisions"]
            if e.get("artifacts"):
                g["artifacts"] = e["artifacts"]
            if e.get("open"):
                g["open"] = e["open"]

    # Deduplicate summary bullets within grouped entries
    for g in grouped.values():
        seen = set()
        deduped = []
        for s in g["summary"]:
            key = s.lower().strip()
            if key not in seen:
                seen.add(key)
                deduped.append(s)
        g["summary"] = deduped

    return list(grouped.values()) + ungrouped


def generate_standup():
    files = find_files(days_back=2)
    if not files:
        print("No recent worklog entries found.")
        return

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    yesterday_items = []
    today_items = []
    open_items = []

    for f in files:
        file_date = parse_file_date(f)
        entries = group_by_session(parse_entries(read_file(f)))

        for e in entries:
            for s in e["summary"]:
                item = f"- {s} *({e['project']})*"
                if file_date == yesterday.strftime("%Y-%m-%d"):
                    yesterday_items.append(item)
                elif file_date == today.strftime("%Y-%m-%d"):
                    today_items.append(item)
            if e["open"]:
                open_items.append(e["open"])

    print(f"🧍 Standup — {today.strftime('%B %d, %Y')}\n")

    if yesterday_items:
        print("**Yesterday:**")
        for item in yesterday_items:
            print(f"  {item}")
        print()

    if today_items:
        print("**Today (so far):**")
        for item in today_items:
            print(f"  {item}")
        print()

    if open_items:
        print("**Carry-over:**")
        for item in open_items:
            print(f"  - {item}")
        print()

    if not yesterday_items and not today_items:
        print("No entries for yesterday or today.\n")

    print(f"(Source: {len(files)} files from {get_worklog_dir()})")


def generate_weekly():
    files = find_files(days_back=7)
    if not files:
        print("No worklog entries for the past week.")
        return

    today = datetime.now().date()
    week_start = today - timedelta(days=6)

    projects = {}
    all_tech = set()
    decisions = []
    artifacts = []
    sessions = set()

    for f in files:
        for e in group_by_session(parse_entries(read_file(f))):
            projects.setdefault(e["project"], []).extend(e["summary"])
            all_tech.update(e["tech"])
            if e["session"]:
                sessions.add(e["session"])
            if e["decisions"]:
                decisions.append(e["decisions"])
            if e["artifacts"]:
                artifacts.append(e["artifacts"])

    print(f"📊 Week of {week_start.strftime('%B %d')} — {today.strftime('%B %d, %Y')}\n")
    print(f"**Projects:** {', '.join(sorted(projects))}")
    print(f"**Sessions:** {len(sessions)} unique sessions\n")

    print("**Key accomplishments:**")
    for proj, items in sorted(projects.items()):
        print(f"\n  *{proj}:*")
        for item in items:
            print(f"    - {item}")
    print()

    if all_tech:
        print(f"**Technologies:** {', '.join(sorted(all_tech))}\n")
    if decisions:
        print("**Decisions:**")
        for d in decisions:
            print(f"  - {d}")
        print()
    if artifacts:
        print("**Artifacts:**")
        for a in artifacts:
            print(f"  - {a}")
        print()

    print(f"(Source: {len(files)} files)")


def generate_monthly():
    files = find_files(days_back=31)
    if not files:
        print("No worklog entries for the past month.")
        return

    today = datetime.now().date()
    month_start = today - timedelta(days=30)

    projects = {}
    all_tech = set()
    decisions = []
    artifacts = []
    sessions = set()
    machines = set()

    for f in files:
        machines.add(parse_file_machine(f))
        for e in group_by_session(parse_entries(read_file(f))):
            projects.setdefault(e["project"], []).extend(e["summary"])
            all_tech.update(e["tech"])
            if e["session"]:
                sessions.add(e["session"])
            if e["decisions"]:
                decisions.append(e["decisions"])
            if e["artifacts"]:
                artifacts.append(e["artifacts"])

    print(f"📈 Monthly Review — {month_start.strftime('%B %d')} to {today.strftime('%B %d, %Y')}\n")
    print(f"**Overview:** {len(sessions)} sessions across {len(machines)} machine(s), "
          f"touching {len(projects)} project(s).\n")

    print("**By project:**")
    for proj, items in sorted(projects.items()):
        seen = set()
        deduped = [i for i in items if i.lower() not in seen and not seen.add(i.lower())]
        print(f"\n  *{proj}* ({len(deduped)} items):")
        for item in deduped[:5]:
            print(f"    - {item}")
        if len(deduped) > 5:
            print(f"    - ... and {len(deduped) - 5} more")
    print()

    if all_tech:
        print(f"**Skills & technologies:** {', '.join(sorted(all_tech))}\n")
    if decisions:
        print("**Key decisions:**")
        for d in decisions[:10]:
            print(f"  - {d}")
        print()
    if artifacts:
        print("**Artifacts:**")
        for a in artifacts:
            print(f"  - {a}")
        print()

    print("**Resume-ready bullets:**")
    for proj, items in sorted(projects.items()):
        if items:
            print(f"  - {max(items, key=len)} ({proj})")
    print()

    print(f"**Machines:** {', '.join(sorted(machines))}")
    print(f"(Source: {len(files)} files)")


def trace_session(session_id: str):
    """Find all entries for a specific session ID."""
    worklog_dir = get_worklog_dir()
    files = sorted(worklog_dir.glob("*.md"))

    found = []
    for f in files:
        file_date = parse_file_date(f)
        machine = parse_file_machine(f)
        for e in parse_entries(read_file(f)):
            if e.get("session") == session_id:
                found.append({"date": file_date, "machine": machine, **e})

    if not found:
        print(f"No entries found for session {session_id}")
        return

    print(f"🔍 Session trace: {session_id}\n")
    for e in found:
        print(f"### {e['date']} {e['time']} — {e['project']} ({e['machine']})")
        for s in e["summary"]:
            print(f"  - {s}")
        if e["tech"]:
            print(f"  Tech: {', '.join(e['tech'])}")
        if e["decisions"]:
            print(f"  Decision: {e['decisions']}")
        if e["open"]:
            print(f"  Open: {e['open']}")
        print()


def query_worklog(query: str):
    worklog_dir = get_worklog_dir()
    if query == "today":
        files = find_files(0)
    elif query == "yesterday":
        yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
        files = sorted(worklog_dir.glob(f"{yesterday}-*.md"))
    elif query == "this-week":
        files = find_files(7)
    elif query == "this-month":
        files = find_files(31)
    else:
        files = sorted(worklog_dir.glob(f"{query}*.md"))

    if not files:
        print(f"No entries for '{query}'")
        return

    for f in files:
        print(f"\n--- {f.name} ---\n")
        print(read_file(f))


def show_info():
    worklog_dir = get_worklog_dir()
    files = sorted(worklog_dir.glob("*.md"))
    machines = set()
    sessions = set()

    for f in files:
        machines.add(parse_file_machine(f))
        for e in parse_entries(read_file(f)):
            if e.get("session"):
                sessions.add(e["session"])

    print(f"Directory: {worklog_dir}")
    print(f"Files: {len(files)}")
    if files:
        print(f"Range: {files[0].name} — {files[-1].name}")
    print(f"Machines: {', '.join(sorted(machines)) if machines else 'none'}")
    print(f"Sessions: {len(sessions)} unique")


def main():
    parser = argparse.ArgumentParser(description="Analyze worklog entries")
    parser.add_argument("--standup", action="store_true")
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--monthly", action="store_true")
    parser.add_argument("--query", type=str)
    parser.add_argument("--session", type=str, help="Trace a specific session ID")
    parser.add_argument("--info", action="store_true")

    args = parser.parse_args()

    if args.info:
        show_info()
    elif args.standup:
        generate_standup()
    elif args.weekly:
        generate_weekly()
    elif args.monthly:
        generate_monthly()
    elif args.session:
        trace_session(args.session)
    elif args.query:
        query_worklog(args.query)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
