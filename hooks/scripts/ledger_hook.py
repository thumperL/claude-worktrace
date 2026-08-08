#!/usr/bin/env python3
"""
ledger_hook.py — Fold this session into the ledger, and nudge if it earned one.

Fires on PreCompact and SessionEnd. Indexes the transcript into the friction
ledger, which is the real job: the ledger only accrues if every session end folds
itself in. If that fold pushed a row past the distinct-session gate, it prints a
one-line nudge naming what recurred. The analysis itself is expensive and
interactive, so it only ever happens when the user asks for it.

The nudge signal is deliberately not "this session was busy". That was a proxy
for having something to say; a row recurring in a second session is the thing
itself, so a first session can never trip it.

Silent unless `learning_loop_mode` is `suggest` or `checkpoint` in
`.claude/claude-worktrace.local.md`. The default (`on-demand`) exits immediately,
so installing this plugin changes nothing until the user opts in.

Suggests at most once per session, tracked in a temp state file.

Input (JSON via stdin): session_id, transcript_path, hook_event_name, cwd.
"""

import hashlib
import json
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "scripts"))

STATE_FILE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "learning-loop-nudge.json")


def read_stdin():
    try:
        return json.loads(sys.stdin.read())
    except (ValueError, EOFError):
        return {}


def _state_key(session_id):
    return hashlib.md5(session_id.encode()).hexdigest()


def _read_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def already_suggested(session_id):
    """Has this session been nudged? Pure read — see mark_suggested.

    Reading and marking are separate because PreCompact can fire early, while
    the session is still quiet. Marking at that point would spend the session's
    one nudge on a check that decided not to nudge, and SessionEnd — by which
    time the thresholds are met — would then stay silent.
    """
    if not session_id:
        return False
    return bool(_read_state().get(_state_key(session_id)))


def mark_suggested(session_id):
    """Record that this session has now been nudged. Call only after printing."""
    if not session_id:
        return
    key = _state_key(session_id)
    state = _read_state()
    state[key] = True
    # Keep the file from growing without bound across months of sessions.
    if len(state) > 500:
        state = {key: True}
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def main():
    hook_input = read_stdin()
    transcript_path = hook_input.get("transcript_path", "")
    cwd = hook_input.get("cwd", "") or None

    if not transcript_path or not os.path.exists(transcript_path):
        return 0

    try:
        import friction
        from lib import settings as settings_lib
    except ImportError as e:
        print("[friction] Could not import lib: %s" % e, file=sys.stderr)
        return 0

    mode = settings_lib.loop_mode(cwd=cwd)
    if mode not in (settings_lib.LOOP_MODE_SUGGEST, settings_lib.LOOP_MODE_CHECKPOINT):
        return 0

    # Fold FIRST, unconditionally. Indexing is the point of this call, and the
    # nudge is the by-product: returning early on an already-nudged session
    # would mean a PreCompact nudge stopped SessionEnd from ever recording the
    # rest of the session.
    try:
        crossed, _ = friction.newly_proposable(transcript_path, cwd)
    except Exception as e:
        print("[friction] Indexing failed: %s" % e, file=sys.stderr)
        return 0

    session_id = hook_input.get("session_id", "")
    if not crossed or already_suggested(session_id):
        return 0

    # Only now has a nudge actually happened, so only now is it recorded.
    mark_suggested(session_id)

    print("[friction] %s" % friction.suggestion(crossed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
