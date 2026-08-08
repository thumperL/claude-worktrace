#!/usr/bin/env python3
"""
settings.py — Read claude-worktrace settings and Claude Code permission rules.

Plugin settings live in `.claude/claude-worktrace.local.md` as YAML frontmatter:

    ---
    learning_loop_mode: suggest
    ---

    Free-form notes below the frontmatter are ignored.

Resolution order: project file, then `~/.claude/claude-worktrace.local.md`,
then the caller's default. Only flat `key: value` pairs are supported — that
is all the pattern needs, and it keeps this dependency-free on Python 3.9.
"""

import json
import os


SETTINGS_FILENAME = "claude-worktrace.local.md"

LOOP_MODE_KEY = "learning_loop_mode"
# The name this setting shipped under. Read as a fallback so an existing config
# keeps working: dropping it would not error, it would silently resolve to
# on-demand and the hook would go quiet with nothing to explain why.
LOOP_MODE_KEY_LEGACY = "session_retro_mode"

LOOP_MODE_ON_DEMAND = "on-demand"
LOOP_MODE_SUGGEST = "suggest"
LOOP_MODE_CHECKPOINT = "checkpoint"
LOOP_MODES = (LOOP_MODE_ON_DEMAND, LOOP_MODE_SUGGEST, LOOP_MODE_CHECKPOINT)


def settings_paths(cwd=None):
    """Candidate settings files, most specific first."""
    paths = []
    if cwd:
        paths.append(os.path.join(cwd, ".claude", SETTINGS_FILENAME))
    paths.append(os.path.join(os.path.expanduser("~"), ".claude", SETTINGS_FILENAME))
    return paths


def read_setting(key, default=None, cwd=None):
    """First value found for `key` across the settings files."""
    for path in settings_paths(cwd):
        values = _read_frontmatter(path)
        if key in values:
            return values[key]
    return default


def loop_mode(cwd=None):
    """Trigger mode for the ledger hook. Unknown values fall back to on-demand."""
    value = read_setting(LOOP_MODE_KEY, None, cwd=cwd)
    if value is None:
        value = read_setting(LOOP_MODE_KEY_LEGACY, LOOP_MODE_ON_DEMAND, cwd=cwd)
    value = str(value).strip().lower()
    if value not in LOOP_MODES:
        return LOOP_MODE_ON_DEMAND
    return value


def allow_rules(cwd=None):
    """Permission allow rules from user, project and local Claude Code settings.

    Used to tell 'already auto-allowed' apart from 'the user approved this again'.
    """
    rules = []
    home = os.path.expanduser("~")
    candidates = [os.path.join(home, ".claude", "settings.json")]
    if cwd:
        candidates.append(os.path.join(cwd, ".claude", "settings.json"))
        candidates.append(os.path.join(cwd, ".claude", "settings.local.json"))

    for path in candidates:
        data = read_json(path)
        permissions = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(permissions, dict):
            continue
        for rule in permissions.get("allow") or []:
            if isinstance(rule, str) and rule not in rules:
                rules.append(rule)
    return rules


def read_json(path):
    """Parse a JSON file, returning {} rather than raising."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_frontmatter(path):
    """Flat key/value pairs from a markdown file's YAML frontmatter."""
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (IOError, OSError):
        return values

    if not lines or lines[0].strip() != "---":
        return values

    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
