#!/usr/bin/env python3
"""Unit tests for scripts/lib/transcript.py against a synthetic fixture.

The fixture is invented — it mirrors the real Claude Code JSONL schema but
contains no real session data. It deliberately includes one malformed line so
the parser's tolerance is covered.

Run: python3 tests/test_transcript_parser.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from lib import transcript as T  # noqa: E402

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample_transcript.jsonl")

FAILURES = []


def check(name, actual, expected):
    if actual != expected:
        FAILURES.append("%s: expected %r, got %r" % (name, expected, actual))


def check_true(name, value):
    if not value:
        FAILURES.append("%s: expected truthy, got %r" % (name, value))


def parsed(allow_rules=None):
    return T.parse_transcript(FIXTURE, allow_rules=allow_rules)


def by_id(session):
    return dict((inv.tool_use_id, inv) for inv in session.invocations)


def test_session_metadata():
    session = parsed()
    check("session_id", session.session_id, "aaaaaaaa-1111-2222-3333-444444444444")
    check("cwd", session.cwd, "/Users/example/projects/widget")
    check("git_branch", session.git_branch, "feature/widget-tidy")
    check("version", session.version, "2.1.220")
    check("total_invocations", session.total_invocations, 9)
    check("parse_errors tolerates bad json", session.parse_errors, 1)
    check("duration_seconds", session.duration_seconds, 665.0)
    check("user_prompt_count", len(session.user_prompts), 1)


def test_permission_modes():
    session = parsed()
    check("permission_mode_histogram", session.permission_mode_histogram,
          {"default": 1, "auto": 1})
    check("mode_at_tool_use", session.mode_at_tool_use_histogram,
          {"default": 7, "auto": 2})
    check("prompt_capable_invocations", session.prompt_capable_invocations, 7)


def test_explicit_approval_signals():
    """Rejection and interruption are recorded in the transcript, not inferred."""
    invocations = by_id(parsed())
    check("rejection detected", invocations["toolu_0006"].approval, T.APPROVAL_REJECTED)
    check("interruption detected", invocations["toolu_0007"].approval,
          T.APPROVAL_INTERRUPTED)


def test_inferred_approval_signals():
    invocations = by_id(parsed())
    check("slow call in default mode", invocations["toolu_0001"].approval,
          T.APPROVAL_LIKELY_MANUAL)
    check("fast call stays unknown", invocations["toolu_0003"].approval,
          T.APPROVAL_UNKNOWN)
    check("non-prompting mode is auto", invocations["toolu_0008"].approval,
          T.APPROVAL_AUTO)
    check_true("every invocation carries a reason",
               all(inv.approval_reason for inv in parsed().invocations))


def test_latency_blind_tool_not_called_manual():
    """A three-minute AskUserQuestion is user think time, not an approval."""
    session = T.parse_transcript(FIXTURE, allow_rules=None)
    ask = [i for i in session.invocations if i.tool_name == "AskUserQuestion"][0]
    check("AskUserQuestion latency", ask.latency_seconds, 180.0)
    check("AskUserQuestion not likely_manual", ask.approval == T.APPROVAL_LIKELY_MANUAL,
          False)


def test_allow_rules_suppress_prompting():
    invocations = by_id(parsed(allow_rules=["Bash(git status*)"]))
    check("allow rule wins over latency", invocations["toolu_0001"].approval,
          T.APPROVAL_AUTO)
    check("allow rule matches with args", invocations["toolu_0002"].approval,
          T.APPROVAL_AUTO)
    check("unrelated command unaffected", invocations["toolu_0004"].approval,
          T.APPROVAL_LIKELY_MANUAL)


def test_matching_allow_rule():
    check("exact tool rule", T.matching_allow_rule("Read", "/x/y.ts", ["Read"]), "Read")
    check("glob rule", T.matching_allow_rule("Bash", "git log --oneline",
                                             ["Bash(git log *)"]), "Bash(git log *)")
    check("wrong tool", T.matching_allow_rule("Bash", "ls", ["Read(*)"]), None)
    check("no rules", T.matching_allow_rule("Bash", "ls", None), None)
    check("push is not covered by a log rule",
          T.matching_allow_rule("Bash", "git push", ["Bash(git log *)"]), None)


def test_signatures_group_variants():
    session = parsed()
    signatures = [inv.signature for inv in session.invocations]
    check("git status variants collapse", signatures.count("Bash(git status)"), 3)
    check("rg collapses", signatures.count("Bash(rg)"), 2)
    check("push kept separate", signatures.count("Bash(git push)"), 1)
    check("edit grouped by extension", signatures.count("Edit(*.ts)"), 1)


def test_bash_signature_shapes():
    def sig(command):
        return T._signature("Bash", {"command": command})

    check("subcommand cli", sig("git diff --stat"), "Bash(git diff)")
    check("plain binary", sig("ls -la /tmp"), "Bash(ls)")
    check("leading cd is skipped", sig("cd /tmp && npm test"), "Bash(npm test)")
    check("pipeline uses first stage", sig("grep -r foo . | head -5"), "Bash(grep)")
    check("env assignment stripped", sig("CI=1 npm run build"), "Bash(npm run)")
    check("absolute path binary", sig("/usr/bin/git status"), "Bash(git status)")
    check("empty command", sig(""), "Bash()")


def test_key_input_extraction():
    invocations = by_id(parsed())
    check("bash key input", invocations["toolu_0001"].key_input, "git status")
    check("read key input", invocations["toolu_0003"].key_input,
          "/Users/example/projects/widget/CHANGELOG.md")


def test_aggregates_and_groups():
    session = parsed()
    check("prompted_estimate", session.prompted_estimate, 5)
    check("error_count", session.error_count, 3)
    check("approval_histogram", session.approval_histogram, {
        T.APPROVAL_LIKELY_MANUAL: 4,
        T.APPROVAL_UNKNOWN: 1,
        T.APPROVAL_REJECTED: 1,
        T.APPROVAL_INTERRUPTED: 1,
        T.APPROVAL_AUTO: 2,
    })

    groups = session.approval_groups()
    check("group count", len(groups), 3)
    check("most impactful first", groups[0].prompted_count, 2)
    check_true("top group is a repeated command",
               groups[0].signature in ("Bash(git status)", "Bash(rg)"))

    failures = session.repeated_failures()
    check("repeated failure detected", len(failures), 1)
    check("repeated failure signature", failures[0]["signature"], "Bash(rg)")
    check("repeated failure count", failures[0]["count"], 2)


def test_multi_session_helpers():
    sessions = [parsed(), parsed()]
    totals = T.aggregate(sessions)
    check("aggregate sessions", totals["session_count"], 2)
    check("aggregate invocations", totals["total_invocations"], 18)
    check("aggregate prompted", totals["prompted_estimate"], 10)
    check("attended session not flagged unattended", totals["ran_unattended"], False)

    merged = T.merge_approval_groups(sessions)
    check("merged group totals double", merged[0].prompted_count, 4)


def test_unattended_detection():
    """A session that never prompts must not read as a well-tuned setup."""
    session = parsed()
    for inv in session.invocations:
        inv.permission_mode = "auto"
        T.classify_approval(inv)
    totals = T.aggregate([session])
    # Only the explicit rejection survives — a recorded rejection outranks mode.
    check("inferred approvals collapse under auto", totals["prompted_estimate"], 1)
    check("prompt-capable count is zero", totals["prompt_capable_invocations"], 0)
    check("ran_unattended flagged", totals["ran_unattended"], True)


def test_path_helpers():
    check("encode project dir", T.encode_project_dir("/Users/me/projects/foo"),
          "-Users-me-projects-foo")
    check("trailing slash", T.encode_project_dir("/Users/me/foo/"), "-Users-me-foo")
    check("empty cwd", T.encode_project_dir(""), None)
    check("missing projects dir", T.find_transcripts(
        cwd="/nope", projects_dir="/definitely/not/here"), [])


def test_timestamp_parsing():
    check_true("microseconds", T.parse_timestamp("2026-08-04T09:00:00.000Z") is not None)
    check_true("no microseconds", T.parse_timestamp("2026-08-04T09:00:00Z") is not None)
    check("garbage", T.parse_timestamp("not a time"), None)
    check("none", T.parse_timestamp(None), None)


def test_missing_file_is_empty_session():
    session = T.parse_transcript("/definitely/not/a/transcript.jsonl")
    check("missing file yields no invocations", session.total_invocations, 0)


def test_prefix_form_allow_rules():
    """`Bash(git log:*)` is what the permission prompt writes, so it must match.

    Treating the prefix form as a non-match reclassifies already-allowed calls
    as manual approvals, which inflates the one number a retro is built on.
    """
    rule = ["Bash(git log:*)"]
    check("prefix form matches bare command",
          T.matching_allow_rule("Bash", "git log", rule), "Bash(git log:*)")
    check("prefix form matches with arguments",
          T.matching_allow_rule("Bash", "git log --oneline -5", rule), "Bash(git log:*)")
    check("prefix form does not leak across subcommands",
          T.matching_allow_rule("Bash", "git logs-something", rule), None)
    check("prefix form does not cover a sibling subcommand",
          T.matching_allow_rule("Bash", "git push", rule), None)
    check("bare :* covers the tool",
          T.matching_allow_rule("Bash", "anything at all", ["Bash(:*)"]), "Bash(:*)")
    check("both syntaxes coexist in one rule set",
          T.matching_allow_rule("Bash", "ls -la", ["Bash(git log:*)", "Bash(ls *)"]),
          "Bash(ls *)")


def _timed_bash(command, seconds):
    inv = T.ToolInvocation(
        tool_use_id="t", tool_name="Bash", tool_input={"command": command},
        timestamp="2026-08-04T09:00:00.000Z", permission_mode="default")
    inv.result_timestamp = "2026-08-04T09:00:%02d.000Z" % seconds
    return inv


def test_slow_bash_is_latency_blind():
    """A build taking 45s is not evidence that a human approved it.

    These are exactly the commands a retro would then nominate for an allow
    rule, so the false positives point the recommendation the wrong way rather
    than merely adding noise.
    """
    for command in ("npm install", "docker build -t app .", "git clone https://x/y"):
        inv = _timed_bash(command, 45)
        T.classify_approval(inv)
        check("slow `%s` is not read as approval" % command.split()[0],
              inv.approval, T.APPROVAL_UNKNOWN)

    fast = _timed_bash("git status --short", 20)
    T.classify_approval(fast)
    check("a slow fast-command still reads as approval",
          fast.approval, T.APPROVAL_LIKELY_MANUAL)

    quick = _timed_bash("npm install", 1)
    T.classify_approval(quick)
    check("blind list never upgrades to auto_allowed", quick.approval, T.APPROVAL_UNKNOWN)


def main():
    print("Python %s" % sys.version.split()[0])
    if not os.path.exists(FIXTURE):
        print("FAIL: fixture missing at %s" % FIXTURE)
        return 1

    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        try:
            test()
        except Exception as e:
            FAILURES.append("%s raised %s: %s" % (test.__name__, type(e).__name__, e))

    print("Ran %d test groups" % len(tests))
    if FAILURES:
        print("FAILED (%d)" % len(FAILURES))
        for failure in FAILURES:
            print("  %s" % failure)
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
