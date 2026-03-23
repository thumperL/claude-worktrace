#!/usr/bin/env python3
"""
migrate-from-skills.py — Remove old .skill-based installation artifacts.

Safely removes ONLY claude-worktrace artifacts:
  - Hook entries in ~/.claude/settings.json that reference our scripts
  - Skill directories at ~/.claude/skills/{worklog-logging,self-improve,worklog-analysis}

Safe to run multiple times. Creates a backup of settings.json before modifying.

Usage:
    python3 scripts/migrate-from-skills.py           # Execute migration
    python3 scripts/migrate-from-skills.py --dry-run  # Preview without changes
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
SKILLS_DIR = CLAUDE_DIR / "skills"

# Exact path fragments from the old INSTALL.md hook commands.
# A hook is ours if and only if its command contains one of these.
OUR_HOOK_FINGERPRINTS = (
    "worklog-logging/scripts/pre_compact_hook.py",
    "worklog-logging/scripts/pre_clear_hook.sh",
    "worklog-logging/scripts/session_end_wrapper.sh",
)

# Old skill directories and a file that MUST exist to confirm it's ours.
# We check both directory name AND marker file to avoid removing
# an unrelated skill that happens to share a name.
OLD_SKILLS = {
    "worklog-logging": "SKILL.md",
    "self-improve": "SKILL.md",
    "worklog-analysis": "SKILL.md",
}

# Secondary markers — if SKILL.md exists but none of these do,
# warn instead of removing (likely a different skill with same name).
SECONDARY_MARKERS = {
    "worklog-logging": "scripts/write_worklog.py",
    "self-improve": "agents/analyzer.md",
    "worklog-analysis": "scripts/analyze_worklog.py",
}


def _is_our_hook(hook_entry):
    """Check if a hook entry's command references one of our scripts."""
    for hook in hook_entry.get("hooks", []):
        command = hook.get("command", "")
        if any(fp in command for fp in OUR_HOOK_FINGERPRINTS):
            return True
    return False


def clean_hooks_from_settings(dry_run=False):
    """Remove our hook entries from settings.json.

    Only touches hook entries whose command matches our fingerprints.
    Other hooks on the same event are preserved. Backs up before writing.

    Returns (removed_list, had_error).
    """
    if not SETTINGS_FILE.exists():
        print("  %s not found — skipping." % SETTINGS_FILE)
        return [], False

    with open(SETTINGS_FILE, "r") as f:
        try:
            settings = json.load(f)
        except json.JSONDecodeError:
            print("  ERROR: %s is not valid JSON — cannot inspect." % SETTINGS_FILE,
                  file=sys.stderr)
            return [], True

    hooks = settings.get("hooks")
    if not hooks or not isinstance(hooks, dict):
        return [], False

    removed = []
    events_to_delete = []

    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue

        kept = []
        for entry in entries:
            if _is_our_hook(entry):
                for h in entry.get("hooks", []):
                    removed.append("%s: %s" % (event_name, h.get("command", "?")))
            else:
                kept.append(entry)

        if len(kept) < len(entries):
            if kept:
                hooks[event_name] = kept
            else:
                events_to_delete.append(event_name)

    for event_name in events_to_delete:
        del hooks[event_name]

    if not hooks:
        del settings["hooks"]

    if removed and not dry_run:
        backup = SETTINGS_FILE.with_suffix(
            ".backup-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        try:
            shutil.copy2(SETTINGS_FILE, backup)
        except OSError as e:
            print("  ERROR: Could not create backup: %s" % e, file=sys.stderr)
            print("  Aborting settings.json modification for safety.", file=sys.stderr)
            return [], True
        print("  Backup: %s" % backup)

        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(settings, f, indent=2)
                f.write("\n")
        except OSError as e:
            print("  ERROR: Failed to write %s: %s" % (SETTINGS_FILE, e), file=sys.stderr)
            print("  Restore from backup: %s" % backup, file=sys.stderr)
            return [], True

    return removed, False


def clean_skill_directories(dry_run=False):
    """Remove old skill directories that are positively identified as ours.

    Checks both the directory name and marker files before removing.
    Returns list of removed directory paths.
    """
    if not SKILLS_DIR.exists():
        print("  %s not found — skipping." % SKILLS_DIR)
        return []

    removed = []

    for skill_name, primary_marker in OLD_SKILLS.items():
        skill_dir = SKILLS_DIR / skill_name
        if not skill_dir.is_dir():
            continue

        primary = skill_dir / primary_marker
        if not primary.exists():
            continue

        secondary = skill_dir / SECONDARY_MARKERS[skill_name]
        if not secondary.exists():
            print("  SKIP: %s/ has SKILL.md but missing %s — may not be ours" % (
                skill_dir, SECONDARY_MARKERS[skill_name]))
            continue

        removed.append(str(skill_dir))
        if not dry_run:
            try:
                shutil.rmtree(skill_dir)
            except OSError as e:
                print("  ERROR: Could not remove %s/: %s" % (skill_dir, e), file=sys.stderr)
                removed.pop()  # don't count as removed
                continue

    return removed


def main():
    parser = argparse.ArgumentParser(
        description="Remove old .skill-based claude-worktrace installation."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be removed without making changes"
    )
    args = parser.parse_args()

    prefix = "[DRY RUN] " if args.dry_run else ""
    found_anything = False
    errors = False

    # --- Hooks ---
    print("Checking %s for old hooks..." % SETTINGS_FILE)
    removed_hooks, hook_errors = clean_hooks_from_settings(args.dry_run)
    if hook_errors:
        errors = True
    if removed_hooks:
        found_anything = True
        for desc in removed_hooks:
            print("  %sRemove hook: %s" % (prefix, desc))
        if not args.dry_run:
            print("  Done.")
    else:
        print("  No old hooks found.")

    # --- Skill directories ---
    print("\nChecking %s/ for old skill directories..." % SKILLS_DIR)
    removed_dirs = clean_skill_directories(args.dry_run)
    if removed_dirs:
        found_anything = True
        for d in removed_dirs:
            print("  %sRemove: %s/" % (prefix, d))
        if not args.dry_run:
            print("  Done.")
    else:
        print("  No old skill directories found.")

    # --- Summary ---
    print()
    if errors:
        print("Migration completed with errors. Check messages above.")
        return 1
    elif not found_anything:
        print("Already clean — nothing to migrate.")
        return 0
    elif args.dry_run:
        print("Run without --dry-run to apply changes.")
        return 0
    else:
        print("Migration complete. Install the plugin if not already installed:")
        print("  claude plugins install claude-worktrace")
        return 0


if __name__ == "__main__":
    sys.exit(main())
