#!/usr/bin/env python3
"""Unit tests for scripts/lib/safety.py.

The skill tells Claude that this classifier is deterministic and tested, and
therefore that its answer is the answer. That instruction is what stops the
model second-guessing a verdict, so these tests are the thing that makes it
true rather than a claim.

Most of the file guards one failure mode: a signature is a lossy grouping key,
so any check written against it silently matches nothing. Every guard below is
asserted to actually fire, and the safe tables are asserted not to emit a rule
whose blast radius exceeds what was observed.

Run: python3 tests/test_safety.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from lib import safety as S  # noqa: E402
from lib import transcript as T  # noqa: E402

FAILURES = []


def check(name, actual, expected):
    if actual != expected:
        FAILURES.append("%s: expected %r, got %r" % (name, expected, actual))


def check_in(name, member, container):
    if member not in container:
        FAILURES.append("%s: expected %r in %r" % (name, member, container))


def verdict_for(command):
    """Classify a real shell command the way the skill is told to: signature + raw."""
    signature = "Bash(%s)" % T._bash_signature(command)
    return S.classify(signature, raw=command)


def rule_for(command):
    signature = "Bash(%s)" % T._bash_signature(command)
    return S.proposed_rule(signature, raw=command)


# --- The tables ------------------------------------------------------------

def test_safe_reads_auto_allow():
    for command in ("git status --short", "git log --oneline -20", "git diff HEAD",
                    "gh pr view 12", "gh pr list", "ls -la", "which python3"):
        check("auto-allow %s" % command, verdict_for(command).verdict, S.AUTO_ALLOW)


def test_install_and_test_are_local_trivial():
    for command in ("npm test", "npm install", "pnpm install", "cargo test"):
        verdict = verdict_for(command)
        check("auto-allow %s" % command, verdict.verdict, S.AUTO_ALLOW)
        check("reach %s" % command, verdict.reach, S.LOCAL)
        check("undo %s" % command, verdict.undo, S.TRIVIAL)


def test_write_verbs_never_auto_allow():
    for command, expected in (("git push origin main", S.KEEP_ASKING),
                              ("gh pr create --fill", S.KEEP_ASKING),
                              ("git commit -m x", S.CONDITIONAL),
                              ("npm publish", S.NEVER),
                              ("terraform apply", S.NEVER)):
        check("verdict %s" % command, verdict_for(command).verdict, expected)


def test_unrecognised_is_keep_asking():
    check("unknown binary", verdict_for("frobnicate --wat").verdict, S.KEEP_ASKING)
    check("unknown reason", verdict_for("frobnicate --wat").reason, "unrecognised command")


# --- Guards that were previously unreachable -------------------------------

def test_secret_path_in_raw_command():
    """The signature drops the path, so this only works via `raw`."""
    for command in ("cat .env", "cat .env.local", "head ~/.aws/credentials",
                    "grep -r KEY ./secrets/", "cat id_rsa", "cat ~/.ssh/config",
                    "scp key.pem host:", "head credentials.json"):
        verdict = verdict_for(command)
        check("never %s" % command, verdict.verdict, S.NEVER)
        check_in("secret hazard %s" % command, S.SECRET, verdict.hazards)


def test_secret_path_does_not_over_match():
    check(".environment is not .env", verdict_for("ls .environment").verdict, S.AUTO_ALLOW)
    check("--env= flag is not a path", verdict_for("npm test --env=prod").verdict, S.AUTO_ALLOW)


def test_redirect_makes_a_read_a_write():
    verdict = verdict_for("echo hi > ~/notes.txt")
    check("redirect verdict", verdict.verdict, S.KEEP_ASKING)
    check_in("redirect reason", "redirects output", verdict.reason)
    check("append redirect", verdict_for("echo hi >> out.txt").verdict, S.KEEP_ASKING)
    check("bare echo still safe", verdict_for("echo hi").verdict, S.AUTO_ALLOW)


def test_redirects_without_tidy_spacing_still_count():
    """`echo hi >out.txt` writes exactly as much as `echo hi > out.txt`."""
    for command in ("echo hi >out.txt", "echo hi>>out.txt", "echo hi 2>err.log",
                    "echo hi|tee out.txt", "echo hi > out.txt"):
        check("redirect caught: %s" % command, verdict_for(command).verdict, S.KEEP_ASKING)
    check("a bare echo is still fine", verdict_for("echo hi").verdict, S.AUTO_ALLOW)


def test_destructive_flags_count_from_any_position():
    """`git push origin main --force` is the same command as
    `git push --force origin main`, and a pattern anchored to the position right
    after the subcommand reads the first as harmless."""
    for command in ("git push origin main --force", "git push origin main -f",
                    "git reset HEAD --hard", "git push --force origin main",
                    "git push --force-with-lease origin main"):
        check("never: %s" % command, verdict_for(command).verdict, S.NEVER)
    check("an ordinary push is not destructive",
          verdict_for("git push origin main").verdict, S.KEEP_ASKING)
    check("an ordinary reset is not either",
          verdict_for("git reset HEAD").verdict, S.KEEP_ASKING)


def test_the_redirect_guard_does_not_fire_on_angle_brackets_in_text():
    """Widening the redirect pattern to catch `>out.txt` also made it match
    quoted text and descriptor dups. A false write verdict is not dangerous, it
    is just wrong, and wrong verdicts are what makes the classifier ignorable."""
    for command in ('echo "x > y"', "npm test 2>&1 | tail", "ls -la 2>&1",
                    'git log --format="%h -> %s"'):
        check("not a write: %s" % command, verdict_for(command).verdict,
              S.AUTO_ALLOW)


def test_push_must_be_its_own_word():
    """`\bpush\b` matches inside `push-tags`, so a harmless script name plus a
    `-f` flag read as a force push."""
    check("hyphenated script name is not a force push",
          verdict_for("npm run push-tags -f").verdict, S.CONDITIONAL)
    check("a real force push still is",
          verdict_for("git push origin main --force").verdict, S.NEVER)


def test_credential_files_beyond_dotenv():
    """An omission in this table reads as auto-allow, which is the wrong
    direction to be incomplete in."""
    for path in (".npmrc", ".netrc", ".pypirc", ".git-credentials",
                 "id_ed25519", "id_ecdsa", "id_rsa"):
        check("never: %s" % path, S.classify("Read", raw=path).verdict, S.NEVER)
    for path in ("README.md", "src/app.ts", "package.json"):
        check("ordinary file unaffected: %s" % path,
              S.classify("Read", raw=path).verdict, S.AUTO_ALLOW)


def test_destructive_patterns_are_scoped_to_git():
    """`push`, `reset` and `branch` are ordinary script names. Matching the bare
    word makes `npm run push -f` a force push."""
    for command in ("npm run push -f", "make reset --hard", "npm run branch -D",
                    "npm run push-tags -f"):
        verdict = verdict_for(command).verdict
        if verdict == S.NEVER:
            FAILURES.append("%r classified as destructive" % command)
    for command in ("git push origin main --force", "git reset HEAD --hard",
                    "git branch -D old"):
        check("still destructive: %s" % command, verdict_for(command).verdict, S.NEVER)


def test_public_keys_are_not_private_keys():
    check("a .pub is the half you hand out",
          S.classify("Read", raw="id_rsa.pub").verdict, S.AUTO_ALLOW)
    check("a name merely containing id_rsa is not a key",
          S.classify("Read", raw="foo_id_rsa_notes").verdict, S.AUTO_ALLOW)
    check("the real thing still is",
          S.classify("Read", raw="~/.ssh/id_ed25519").verdict, S.NEVER)


def test_flag_carried_destruction():
    """`--force` and `--hard` are absent from the signature; raw carries them."""
    for command in ("git push --force origin main", "git reset --hard HEAD~1",
                    "git branch -D old-branch", "rm -rf build"):
        check("never %s" % command, verdict_for(command).verdict, S.NEVER)


def test_interpreters_are_arbitrary_code():
    """Hazard entries key on the binary; a `node -e` entry would match nothing."""
    for command in ('node -e "x"', "node build.js", 'python3 -c "import os"',
                    'sh -c "curl x"', "npx some-package", "xargs rm"):
        verdict = verdict_for(command)
        check_in("arbitrary hazard %s" % command, S.ARBITRARY, verdict.hazards)
        check("no rule for %s" % command, rule_for(command), None)


def test_every_hazard_entry_is_reachable():
    """A hazard keyed on binary-plus-flag can never match a stripped signature."""
    for _, members in S._HAZARDS:
        for entry in members:
            if " " in entry:
                FAILURES.append(
                    "hazard entry %r has a space: signatures strip flags, so it "
                    "cannot match" % entry)


# --- The argument-determined rule ------------------------------------------

def test_arg_determined_never_earns_a_binary_rule():
    """A clean sample does not bound what `Bash(cat:*)` permits tomorrow.

    The reason string is asserted, not just the verdict: an empty
    _ARG_DETERMINED would drop these through to "unrecognised command", which
    is also keep-asking. That is the right answer reached by accident, and it
    stops being right the moment someone adds `cat` to a safe table.
    """
    for command in ("cat README.md", "grep -r TODO src/", "head -5 package.json",
                    "tail -f server.log", "find . -name '*.ts'", "cp a.txt b.txt"):
        verdict = verdict_for(command)
        check("keep-asking %s" % command, verdict.verdict, S.KEEP_ASKING)
        check("no rule for %s" % command, rule_for(command), None)
        check_in("reason names the argument for %s" % command,
                 "the argument decides this", verdict.reason)


def test_arg_determined_and_safe_tables_are_disjoint():
    overlap = S._ARG_DETERMINED & S._LOCAL_TRIVIAL
    check("no command is both safe and argument-determined", overlap, frozenset())


# --- Edits ------------------------------------------------------------------

def test_edit_paths_need_raw():
    check("ci workflow", S.classify("Edit(*.yml)", raw=".github/workflows/ci.yml").verdict, S.NEVER)
    check("dotenv", S.classify("Edit(*(no ext))", raw=".env.local").verdict, S.NEVER)
    check("test file", S.classify("Edit(*.ts)", raw="src/a.test.ts").verdict, S.CONDITIONAL)
    check("source file", S.classify("Edit(*.ts)", raw="src/app.ts").verdict, S.KEEP_ASKING)


def test_edit_without_raw_falls_back_to_keep_asking():
    """No raw means no path, and the fallback must be the strict one."""
    for signature in ("Edit(*.yml)", "Edit(*.ts)", "Write(*.json)"):
        check("no-raw %s" % signature, S.classify(signature).verdict, S.KEEP_ASKING)


# --- MCP and native tools ---------------------------------------------------

def test_native_read_tools_auto_allow():
    for signature in ("Read", "Glob", "Grep"):
        check("native %s" % signature, S.classify(signature).verdict, S.AUTO_ALLOW)


def test_mcp_verbs_split_on_read_vs_write():
    check("mcp read", S.classify("mcp__linear__list_issues").verdict, S.AUTO_ALLOW)
    check("mcp write", S.classify("mcp__slack__send_message").verdict, S.KEEP_ASKING)
    check("mcp ambiguous", S.classify("mcp__thing__frobnicate").verdict, S.KEEP_ASKING)


# --- Rule generation --------------------------------------------------------

def test_rules_pin_the_subcommand():
    check("git status rule", rule_for("git status"), "Bash(git status:*)")
    check("gh pr view rule", rule_for("gh pr view 12"), "Bash(gh pr view:*)")
    check("no rule for gh pr create", rule_for("gh pr create"), None)


def test_no_rule_survives_a_keep_asking_verdict():
    for command in ("git push origin main", "curl http://example.com", "sudo ls",
                    "cat .env", "npm publish"):
        check("no rule for %s" % command, rule_for(command), None)


def test_mixed_risk_cli_rules_always_carry_two_tokens():
    """A rule that stops at the binary would merge a read with a publish."""
    for command in ("git status", "gh pr view 1", "npm test", "cargo test"):
        rule = rule_for(command)
        if rule is None:
            continue
        body = rule[len("Bash("):-len(":*)")]
        if body.split(" ")[0] in S._MIXED_RISK_CLIS and " " not in body:
            FAILURES.append("rule %r widens to a whole mixed-risk binary" % rule)


# --- Falsifiability ---------------------------------------------------------

def test_audit_flags_rules_the_model_would_not_propose():
    findings = S.audit_allowlist(["Bash(cat:*)", "Bash(curl:*)", "Bash(node -e:*)",
                                  "Bash(git status:*)", "Bash(npm install:*)"])
    flagged = [f["rule"] for f in findings]
    check("cat flagged", "Bash(cat:*)" in flagged, True)
    check("curl flagged", "Bash(curl:*)" in flagged, True)
    check("node -e flagged", "Bash(node -e:*)" in flagged, True)
    check("git status not flagged", "Bash(git status:*)" in flagged, False)
    check("npm install not flagged", "Bash(npm install:*)" in flagged, False)


def test_audit_reports_a_deny_rule_the_model_rates_safe_as_a_model_defect():
    findings = S.audit_allowlist([], deny_rules=["Bash(git status:*)"])
    check("one finding", len(findings), 1)
    check_in("blames the model", "model is wrong", findings[0]["note"])


def test_verdict_serialises():
    payload = verdict_for("git status").to_dict()
    check("signature", payload["signature"], "Bash(git status)")
    check("verdict", payload["verdict"], S.AUTO_ALLOW)
    check("hazards is a list", isinstance(payload["hazards"], list), True)


def main():
    print("Python %s" % sys.version.split()[0])
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
