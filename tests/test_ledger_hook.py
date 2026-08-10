#!/usr/bin/env python3
"""Unit tests for hooks/scripts/ledger_hook.py.

Covers the nudge-budget ordering: a session gets one nudge, and a quiet
PreCompact must not spend it before SessionEnd has had a chance to trip the
threshold.

Run: python3 tests/test_ledger_hook.py
"""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "hooks", "scripts"))

import ledger_hook as H  # noqa: E402

FAILURES = []


def check(name, actual, expected):
    if actual != expected:
        FAILURES.append("%s: expected %r, got %r" % (name, expected, actual))


def check_true(name, value):
    if not value:
        FAILURES.append("%s: expected truthy, got %r" % (name, value))


def fresh_state():
    H.STATE_FILE = os.path.join(tempfile.mkdtemp(), "learning-loop-nudge.json")


def test_reading_does_not_consume_the_nudge():
    """The bug this covers: a quiet PreCompact silenced the SessionEnd nudge."""
    fresh_state()
    check("unseen session", H.already_suggested("s1"), False)
    check("still unseen after a read", H.already_suggested("s1"), False)
    check("and again", H.already_suggested("s1"), False)


def test_marking_is_what_consumes_it():
    fresh_state()
    H.mark_suggested("s1")
    check("marked session is seen", H.already_suggested("s1"), True)
    check("a different session is unaffected", H.already_suggested("s2"), False)


def test_a_first_session_never_trips_the_nudge():
    """Cold start is the design working: nothing has recurred yet."""
    directory = tempfile.mkdtemp()
    try:
        import friction
        fixture = os.path.join(REPO_ROOT, "tests", "fixtures", "sample_transcript.jsonl")
        ledger_file = os.path.join(directory, "ledger.json")
        crossed, ledger = friction.newly_proposable(fixture, directory,
                                                    ledger_file=ledger_file)
        check("nothing crossed", crossed, [])
        check("but it was indexed", len(ledger.rows) > 0, True)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_second_session_trips_it_once():
    """The same transcript under a second session id crosses the gate."""
    directory = tempfile.mkdtemp()
    try:
        import friction
        from lib import ledger as L
        ledger_file = os.path.join(directory, "ledger.json")
        led = L.Ledger(ledger_file)
        led.fold("/t/first.jsonl", [{
            "kind": L.KIND_FAILED_RETRY, "key": "Bash(rg)", "tool_name": "Bash",
            "session_id": "earlier", "timestamp": "2026-08-01T00:00:00Z",
            "cost_calls": 1, "cost_seconds": None, "evidence": "rg TODO src/"}])
        led.save()

        fixture = os.path.join(REPO_ROOT, "tests", "fixtures", "sample_transcript.jsonl")
        crossed, _ = friction.newly_proposable(fixture, directory,
                                               ledger_file=ledger_file)
        check("one row crossed", [r.key for r in crossed], ["Bash(rg)"])

        again, _ = friction.newly_proposable(fixture, directory,
                                             ledger_file=ledger_file)
        check("re-folding does not re-cross it", again, [])
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_the_legacy_mode_key_still_works():
    """Dropping it would not error. It would resolve to on-demand and the hook
    would go quiet, with nothing anywhere to explain why."""
    from lib import settings as S
    directory = tempfile.mkdtemp()
    try:
        claude_dir = os.path.join(directory, ".claude")
        os.makedirs(claude_dir)
        path = os.path.join(claude_dir, S.SETTINGS_FILENAME)

        with open(path, "w") as handle:
            handle.write("---\nsession_retro_mode: suggest\n---\n")
        check("legacy key honoured", S.loop_mode(cwd=directory), S.LOOP_MODE_SUGGEST)

        with open(path, "w") as handle:
            handle.write("---\nlearning_loop_mode: checkpoint\n---\n")
        check("current key honoured", S.loop_mode(cwd=directory), S.LOOP_MODE_CHECKPOINT)

        with open(path, "w") as handle:
            handle.write("---\nlearning_loop_mode: suggest\nsession_retro_mode: checkpoint\n---\n")
        check("current key wins over legacy", S.loop_mode(cwd=directory), S.LOOP_MODE_SUGGEST)

        with open(path, "w") as handle:
            handle.write("---\nlearning_loop_mode: nonsense\n---\n")
        check("unknown value falls back", S.loop_mode(cwd=directory), S.LOOP_MODE_ON_DEMAND)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_the_worklog_hook_sees_tool_level_friction():
    """A command that failed four times before working is invisible to the
    summariser, because nobody narrates their own retries."""
    import pre_compact_hook as W
    fixture = os.path.join(REPO_ROOT, "tests", "fixtures", "sample_transcript.jsonl")
    lines = W.detect_friction(fixture)
    check_true("found something", len(lines) > 0)
    check_true("names the retried command",
               any("Bash(rg)" in line and "again" in line for line in lines))


def test_friction_extraction_never_raises():
    """A worklog entry is worth more than a complete one."""
    import pre_compact_hook as W
    check("missing file", W.detect_friction("/nope/nope.jsonl"), [])
    check("empty path", W.detect_friction(""), [])


def test_the_friction_section_is_marked_as_a_separate_source():
    import pre_compact_hook as W
    check("nothing to add, nothing appended", W._friction_section([]), "")
    section = W._friction_section(["npm test failed twice"])
    check_true("flagged as derived", "tool calls" in section)
    check_true("warns against padding", "Do not pad" in section)


def test_main_folds_even_when_the_session_was_already_nudged():
    """Driven through main(), because testing the helpers separately cannot see
    the ordering. Reverting main() to check already_suggested() before folding
    passes every other test in this file: a PreCompact nudge would then stop
    SessionEnd recording the rest of the session, silently."""
    import friction
    import ledger_hook as H2
    from lib import ledger as L
    from lib import settings as S

    directory = tempfile.mkdtemp()
    try:
        fresh_state()
        claude_dir = os.path.join(directory, ".claude")
        os.makedirs(claude_dir)
        with open(os.path.join(claude_dir, S.SETTINGS_FILENAME), "w") as handle:
            handle.write("---\nlearning_loop_mode: suggest\n---\n")

        fixture = os.path.join(REPO_ROOT, "tests", "fixtures", "sample_transcript.jsonl")
        H2.mark_suggested("already-nudged")          # PreCompact already spent it

        original_stdin, folded = H2.read_stdin, {}
        H2.read_stdin = lambda: {"transcript_path": fixture, "cwd": directory,
                                 "session_id": "already-nudged"}
        original_signal = friction.newly_proposable

        def spy(transcript_path, cwd, **kwargs):
            folded["ran"] = True
            return original_signal(transcript_path, cwd, **kwargs)

        friction.newly_proposable = spy
        try:
            H2.main()
        finally:
            H2.read_stdin, friction.newly_proposable = original_stdin, original_signal

        check("the transcript was still folded", folded.get("ran"), True)
        ledger = L.Ledger.load(L.ledger_path(directory))
        check_true("and rows actually landed", len(ledger.rows) > 0)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_the_nudge_names_what_recurred():
    from lib import ledger as L
    row = L.Row(L.KIND_FAILED_RETRY, "Bash(npm test)")
    row.add({"session_id": "s1", "timestamp": "2026-08-01T00:00:00Z"})
    row.add({"session_id": "s2", "timestamp": "2026-08-02T00:00:00Z"})
    import friction
    text = friction.suggestion([row])
    check("names the thing", "Bash(npm test)" in text, True)
    check("gives the session count", "2 separate sessions" in text, True)


def test_empty_session_id_is_never_recorded():
    fresh_state()
    H.mark_suggested("")
    check("blank id stays unseen", H.already_suggested(""), False)


def test_unreadable_state_file_does_not_raise():
    H.STATE_FILE = "/definitely/not/writable/state.json"
    check("missing state reads as unseen", H.already_suggested("s1"), False)
    H.mark_suggested("s1")  # must not raise


def main():
    print("Python %s" % sys.version.split()[0])
    tests = [v for n, v in sorted(globals().items())
             if n.startswith("test_") and callable(v)]
    for test in tests:
        try:
            test()
        except Exception as e:
            FAILURES.append("%s raised %s: %s" % (test.__name__, type(e).__name__, e))
    print("Ran %d test groups" % len(tests))
    if FAILURES:
        print("FAILED (%d)" % len(FAILURES))
        for f in FAILURES:
            print("  %s" % f)
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
