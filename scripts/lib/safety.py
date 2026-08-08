#!/usr/bin/env python3
"""
safety.py — Decide whether a repeated action should stop asking.

Consumes the `signature` strings produced by lib/transcript.py ("Bash(git status)",
"Edit(*.ts)", "Read") and returns a verdict plus the evidence for it.

WHERE THIS MODEL COMES FROM

Derived from this user's own Claude Code permission rules, not from a generic
risk taxonomy. Two observations drove the shape:

  1. The allow list is NOT "read-only commands". Read it in order and it holds
     `npm install`, `git add`, `git checkout -b`, `git stash` and `mkdir` — all
     mutations. What they share is that a re-run or a `git checkout` undoes them
     and nothing left the machine. Meanwhile `git commit`, `git merge` and
     `git push` are absent despite being at least as frequent. So the dividing
     line already in use is not "does it write?" but "how expensive is undoing
     it, and how far did the effect travel?" Those are two independent
     questions, so this module tracks them as two axes and scores each
     separately, rather than flattening them into a single ordered severity.

  2. The deny list is not a more extreme version of the same axes. It mixes
     network egress (curl, wget), privilege (sudo, chmod 777), publication
     (npm publish), destruction (rm -rf, reset --hard, push --force) and secret
     reads (.env*, ./secrets/**, **/*.pem). A secret read is local and trivially
     undoable, so the axes rate it harmless; it is denied anyway. Hazards are
     therefore modelled as orthogonal flags that override the axes, rather than
     as a further position along them.

The governing rule stays asymmetric: a prompt costs a few seconds, a wrong
auto-allow costs a force-push or a leaked key. Unknown resolves to keep-asking.

Python 3.9 compatible.
"""

import re


# --- Axes -----------------------------------------------------------------

# How far the effect travels.
LOCAL = "local"
EXTERNAL = "external"

# What undoing it costs.
TRIVIAL = "trivial"      # re-run it, or `git checkout` it
COSTLY = "costly"        # recoverable, but it is a decision someone must make
NONE = "none"            # gone

# Orthogonal hazards. Any hazard overrides the axes.
EGRESS = "egress"            # reaches the network
SECRET = "secret"            # reads or writes credentials
PRIVILEGE = "privilege"      # escalates or weakens permissions
ARBITRARY = "arbitrary"      # runs code we cannot inspect from the signature

# --- Verdicts -------------------------------------------------------------

AUTO_ALLOW = "auto-allow"
CONDITIONAL = "conditional"
KEEP_ASKING = "keep-asking"
NEVER = "never"


# --- Tables ---------------------------------------------------------------
# Keyed by transcript signature body. Bash signatures are "<binary> <subcommand>"
# for multi-purpose CLIs and "<binary>" otherwise (see lib/transcript.py).

_LOCAL_TRIVIAL = frozenset([
    # Pure reads whose risk does not depend on the argument
    "ls", "wc", "which", "pwd", "echo", "basename", "dirname", "realpath",
    # Git reads
    "git log", "git status", "git diff", "git show", "git rev-parse",
    "git remote", "git branch", "git describe", "git blame",
    # GitHub reads
    "gh pr view", "gh pr list", "gh pr diff", "gh pr checks", "gh repo view",
    "gh issue view", "gh issue list", "gh run view", "gh run list",
    # Check-only tooling
    "tsc", "eslint", "prettier", "mypy", "ruff", "black", "gofmt",
    # Build, test and install: reversible, and confined to the working tree
    "npm test", "npm install", "npm ci",
    "pnpm test", "pnpm install",
    "bun test", "bun install",
    "yarn test", "yarn install",
    "pip install", "poetry install", "cargo build", "cargo test",
    # Reversible local mutations
    "git add", "git stash",
])

# The binary says nothing; the argument decides. `cat` is a pure read, and
# `cat ~/.aws/credentials` puts a key in the context window. A rule covers every
# future invocation rather than the ones already observed, so no sample of these
# — however clean — earns `Bash(cat:*)`. The signature has dropped the only token
# that mattered, and there is no narrowing available at this layer, so these stop
# at keep-asking and `proposed_rule()` returns nothing for them.
#
# Not a hardship in practice: Read, Grep and Glob do the same work, are already
# AUTO_ALLOW, and are what the skill's tool_histogram section tells the user to
# prefer anyway.
_ARG_DETERMINED = frozenset([
    "cat", "head", "tail", "grep", "rg", "find", "file", "stat", "less", "more",
    "cp", "mv", "ln", "touch", "mkdir", "tee", "sed", "awk",
])

_LOCAL_COSTLY = frozenset([
    "git commit", "git merge", "git rebase", "git cherry-pick", "git tag",
    "git checkout", "git switch", "git restore", "git revert",
    "docker build", "docker run", "docker exec",
    "kubectl port-forward",
    # Script runners: the name after `run` is defined by the project, so the
    # same rule covers `npm run build` and `npm run deploy`. Conditional on
    # reading package.json first, never a global allow.
    "npm run", "pnpm run", "bun run", "yarn run", "make",
])

_LOCAL_NONE = frozenset([
    "git clean", "truncate", "shred",
])

_EXTERNAL_COSTLY = frozenset([
    "git push", "git fetch", "git pull", "git clone",
    # Every `gh` write verb, not just the memorable ones — an omission here
    # reads as "unrecognised" and lands on keep-asking, which is safe but
    # silent. Listing them means the report can say why.
    "gh pr comment", "gh pr close", "gh pr edit", "gh pr merge",
    "gh pr ready", "gh pr review", "gh pr reopen", "gh pr create",
    "gh issue comment", "gh issue close", "gh issue edit", "gh issue create",
    "gh release create", "gh repo create", "gh api",
])

_EXTERNAL_NONE = frozenset([
    "npm publish", "pnpm publish", "yarn publish", "cargo publish",
    "terraform apply", "terraform destroy", "kubectl apply", "kubectl delete",
    "wrangler deploy", "flyctl deploy", "fly deploy", "vercel deploy",
    "aws s3", "gcloud compute", "supabase db",
])

# Signatures that carry a hazard whatever else they do. Keyed on the BINARY,
# never on binary-plus-flag: signatures strip flags, so a `node -e` entry here
# would sit in the table looking like protection while matching nothing. An
# interpreter runs a program this layer cannot see whether the program arrives
# via `-e` or via a file, so the binary is the honest unit.
_HAZARDS = [
    (EGRESS, frozenset(["curl", "wget", "nc", "ssh", "scp", "rsync", "ftp"])),
    (PRIVILEGE, frozenset(["sudo", "su", "chmod", "chown", "launchctl"])),
    (ARBITRARY, frozenset(["node", "python", "python3", "deno", "npx",
                           "perl", "ruby", "osascript",
                           "eval", "sh", "bash", "zsh", "xargs"])),
]

# A redirect makes any verb a write. Checked against the command with quoted
# text removed, so `echo "x > y"` is a string containing an angle bracket rather
# than a write, and `2>&1` is a descriptor dup rather than a file.
_REDIRECTS = re.compile(r'(?:^|\s|\w)\d?>>?(?!&\s*[12])|\|\s*tee\b')

_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')


def _unquoted(text):
    return _QUOTED.sub(" ", text or "")


# Undoing these is not a question of cost; there is nothing to undo to.
# A flag decides these, and a flag can sit anywhere after the subcommand:
# `git push origin main --force` is the same command as `git push --force origin
# main`. Matching only the adjacent position reads the first as harmless.
_GIT = r'(?<![-\w])git(?![-\w])'


def _git_with(subcommand, flags):
    # git, that subcommand, and one of those flags, in any order after it.
    return (_GIT
            + r'(?=.*(?<![-\w])' + subcommand + r'(?![-\w]))'
            + r'(?=.*\s(?:' + flags + r')(?![-\w]))')


# Scoped to git rather than matched on the bare word, because `npm run push -f`
# and `make reset --hard` are ordinary script names that happen to share a word
# with a dangerous command.
_DESTRUCTIVE = re.compile("|".join([
    r'^(?:rm|rmdir)\b',
    _git_with("reset", r'--hard'),
    _git_with("push", r'--force|--force-with-lease|-f'),
    _git_with("branch", r'-D'),
    r'(?<![-\w])drop\s+(?:table|database)(?![-\w])',
]), re.IGNORECASE)

# Paths whose contents are credentials. Matches the user's deny list, widened
# to the neighbouring locations it does not yet name.
#
# Anchored on a path boundary rather than on `^` alone, because this runs against
# whole commands now: in `cat .env` the interesting token starts after a space,
# and an anchor that only accepted `^` or `/` would read the command as clean.
# Endings are boundary-anchored for the same reason — `\.pem$` never fires on
# `scp key.pem host:` because the path is mid-command.
_B = r'(?:^|[\s/=:\'"])'
_E = r'(?:$|[\s\'";|&])'
_SECRET_PATH = re.compile("|".join([
    _B + r'\.env(?![A-Za-z0-9])',   # .env and .env.local, but not .environment
    _B + r'secrets?/',
    r'\.pem' + _E,
    r'\.key' + _E,
    r'\.p12' + _E,
    # Every common private key name, not just RSA. An omission here reads as
    # auto-allow, which is the wrong direction to be incomplete in.
    # Boundaries matter: without them `foo_id_rsa_notes` is a credential. And a
    # .pub is the half you are allowed to hand out.
    _B + r'id_(?:rsa|dsa|ecdsa|ed25519)(?!\.pub)' + _E,
    # Credential files the deny list never named, all of which hold live tokens.
    _B + r'\.(?:npmrc|netrc|pypirc|git-credentials|htpasswd)' + _E,
    _B + r'\.ssh/',
    _B + r'\.aws/',
    _B + r'\.gnupg/',
    r'credentials?\.(?:json|ya?ml)' + _E,
]), re.IGNORECASE)

# MCP tool name families. Read verbs are safe; write verbs reach a service.
_MCP_READ = re.compile(r'^(get|list|search|find|read|fetch|query|describe)_')
_MCP_WRITE = re.compile(r'^(send|create|update|delete|post|save|set|remove|archive)_')

_MIXED_RISK_CLIS = frozenset([
    "git", "gh", "docker", "kubectl", "aws", "gcloud", "npm", "pnpm", "yarn",
    "bun", "terraform", "wrangler", "flyctl", "supabase", "cargo", "make", "pip",
])

# SIGNATURE CONTRACT — imported from the module that builds signatures, never
# redefined here, so the grouping key and the safety verdict cannot drift apart.
# A grouping key coarser than the risk boundary silently merges a read with a
# publish, and the merged group then looks like a safe high-impact candidate.
#
# Known limitation: risk carried by a FLAG rather than a subcommand
# (`git branch -D`, `git reset --hard`, `git push --force`) is invisible here,
# because signatures strip flags to group variants together. Those land on
# keep-asking by not appearing in any safe table, which is the right outcome
# reached for the wrong reason. Do not add them to a safe table.
try:
    from .transcript import NEEDS_THIRD_TOKEN, operands
except ImportError:  # executed directly rather than as a package
    from transcript import NEEDS_THIRD_TOKEN, operands


# --- Classification -------------------------------------------------------

class Verdict(object):
    """A safety judgement plus everything needed to explain or test it."""

    def __init__(self, signature, verdict, reach=None, undo=None,
                 hazards=None, reason=""):
        self.signature = signature
        self.verdict = verdict
        self.reach = reach
        self.undo = undo
        self.hazards = tuple(hazards or ())
        self.reason = reason

    @property
    def auto_allowable(self):
        return self.verdict == AUTO_ALLOW

    def to_dict(self):
        return {
            "signature": self.signature,
            "verdict": self.verdict,
            "reach": self.reach,
            "undo": self.undo,
            "hazards": list(self.hazards),
            "reason": self.reason,
        }


def classify(signature, raw=None):
    """Verdict for a transcript signature. Unrecognised means keep-asking.

    `raw` is the command or path the signature was built from — the group's
    `examples` entry, or a settings rule's pattern. Signatures are grouping keys
    and are lossy by design: flags, arguments and redirects are stripped so that
    variants collapse into one row. Every one of those is somewhere risk hides,
    so the guards below run against `raw` when a caller has it and fall back to
    the signature body when it does not.

    Pass it whenever it exists. Without it `Edit(*.yml)` cannot be told apart
    from a workflow file, and `git push` cannot be told apart from `--force`.
    A group holds several commands; classify each and take the strictest.
    """
    tool, body = _split_signature(signature)
    subject = (raw or body or "").strip()

    # Checked before anything else. A credential read is local and trivially
    # undoable, so both axes rate it harmless; only the hazard flag catches it.
    # This ordering is the whole reason hazards exist as a separate concept.
    if subject and _SECRET_PATH.search(subject):
        return Verdict(signature, NEVER, LOCAL, TRIVIAL, [SECRET],
                       "reaches a credential path; the axes rate this harmless "
                       "and they are wrong, which is what hazards are for")

    if tool in ("Read", "Glob", "Grep"):
        return Verdict(signature, AUTO_ALLOW, LOCAL, TRIVIAL,
                       reason="read-only tool confined to the filesystem")

    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        return _classify_edit(signature, subject)

    if tool.startswith("mcp__"):
        return _classify_mcp(signature, tool)

    if tool != "Bash":
        return Verdict(signature, KEEP_ASKING,
                       reason="no rule covers the %s tool" % tool)

    return _classify_bash(signature, body, subject)


def _classify_bash(signature, body, subject):
    hazards = [name for name, members in _HAZARDS
               if body in members or body.split(" ")[0] in members]

    # Against `subject`, so that risk carried by a flag — `--force`, `--hard`,
    # `-D` — is visible here. The signature dropped it to group variants.
    if _DESTRUCTIVE.search(subject):
        return Verdict(signature, NEVER, EXTERNAL, NONE, hazards,
                       "destructive: there is no state to return to")

    # An output redirect makes any verb a write, whatever the verb was.
    if _REDIRECTS.search(_unquoted(subject)):
        return Verdict(signature, KEEP_ASKING, LOCAL, COSTLY, hazards,
                       "redirects output, so this writes regardless of the verb")

    if hazards:
        return Verdict(signature, NEVER if EGRESS in hazards or PRIVILEGE in hazards
                       else KEEP_ASKING,
                       EXTERNAL if EGRESS in hazards else LOCAL, COSTLY, hazards,
                       "carries hazard(s): %s" % ", ".join(hazards))

    if body.split(" ")[0] in _ARG_DETERMINED:
        return Verdict(signature, KEEP_ASKING, LOCAL, TRIVIAL, hazards,
                       "the argument decides this, and the signature dropped it; "
                       "no rule on `%s` alone can be scoped to a safe path"
                       % body.split(" ")[0])

    for table, reach, undo, verdict in (
        (_LOCAL_TRIVIAL, LOCAL, TRIVIAL, AUTO_ALLOW),
        (_LOCAL_COSTLY, LOCAL, COSTLY, CONDITIONAL),
        (_LOCAL_NONE, LOCAL, NONE, KEEP_ASKING),
        (_EXTERNAL_COSTLY, EXTERNAL, COSTLY, KEEP_ASKING),
        (_EXTERNAL_NONE, EXTERNAL, NONE, NEVER),
    ):
        if body in table:
            return Verdict(signature, verdict, reach, undo, hazards,
                           "%s reach, %s to undo" % (reach, undo))

    return Verdict(signature, KEEP_ASKING, reason="unrecognised command")


def _classify_edit(signature, subject):
    # Edit signatures are `Edit(*.yml)` — extension only, no path. Every check
    # here needs the path, so a caller that does not pass `raw` gets the
    # keep-asking fallback rather than a verdict these patterns could reach.
    if _SECRET_PATH.search(subject):
        return Verdict(signature, NEVER, LOCAL, COSTLY, [SECRET],
                       "writes to a credential path")
    if re.search(r'\.github/|\.gitlab-ci|\.circleci/|Jenkinsfile|Dockerfile|docker-compose', subject):
        return Verdict(signature, NEVER, EXTERNAL, COSTLY, [],
                       "CI and container definitions run on other people's machines")
    if re.search(r'\.(test|spec)\.|/tests?/|/fixtures?/', subject):
        return Verdict(signature, CONDITIONAL, LOCAL, TRIVIAL, [],
                       "test files revert cheaply, but scope the rule to this project")
    return Verdict(signature, KEEP_ASKING, LOCAL, COSTLY, [],
                   "source edits are the work itself, not a chore to automate")


def _classify_mcp(signature, tool):
    verb = tool.split("__")[-1]
    if _MCP_WRITE.match(verb):
        return Verdict(signature, KEEP_ASKING, EXTERNAL, COSTLY, [],
                       "MCP write verb: the effect lands in a service others see")
    if _MCP_READ.match(verb):
        return Verdict(signature, AUTO_ALLOW, EXTERNAL, TRIVIAL, [],
                       "MCP read verb: fetches, changes nothing")
    return Verdict(signature, KEEP_ASKING, reason="MCP verb %r is not clearly a read" % verb)


def _split_signature(signature):
    """'Bash(git status)' -> ('Bash', 'git status'); 'Read' -> ('Read', '')."""
    match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$', signature or "")
    if match:
        return match.group(1), match.group(2).strip()
    return (signature or "").strip(), ""


# --- Rule generation ------------------------------------------------------

def proposed_rule(signature, raw=None):
    """The permission rule text for a signature, or None if it must not exist.

    The subcommand pinning that keeps `git push` out of a `git status` rule is
    already done by the time a signature exists — lib/transcript.py builds
    `<binary> <subcommand>` for everything in _MIXED_RISK_CLIS, and this returns
    the whole body. There is deliberately no widening step here to undo it.
    """
    verdict = classify(signature, raw=raw)
    if verdict.verdict in (NEVER, KEEP_ASKING):
        return None
    tool, body = _split_signature(signature)
    if tool != "Bash":
        return signature if not body else "%s(%s)" % (tool, body)
    return "Bash(%s:*)" % body


# --- Falsifiability -------------------------------------------------------

def hazard_reached(signature, raw=None):
    """What a route reached, or None if it reached nothing notable.

    Asks the tables the inverted question. `classify` answers "may we permit
    this"; this answers "did taking this route arrive somewhere the blocked call
    was being kept away from". Same axes, same hazard flags, opposite purpose,
    and no new taxonomy to keep in step with the old one.

    A route is notable when it is denied outright or carries a hazard flag. A
    merely unrecognised command is not: keep-asking is the default for anything
    the tables do not name, and treating that as evidence of evasion would make
    every unfamiliar command look like one.
    """
    verdict = classify(signature, raw=raw)
    if verdict.verdict == NEVER or verdict.hazards:
        return verdict
    return None


def audit_allowlist(allow_rules, deny_rules=None):
    """Check an existing permission set against this model.

    The model is derived from the user's own rules, so it should agree with them.
    Every disagreement is either a bug here or a rule worth revisiting — which is
    the point: a taxonomy that cannot be checked against anything is not evidence.
    """
    findings = []
    for rule in allow_rules or []:
        signature = _rule_to_signature(rule)
        if signature is None:
            continue
        # The rule's own pattern is the closest thing to a raw command there is,
        # and it is where a rule like `Bash(cat .env*)` keeps its teeth.
        verdict = classify(signature, raw=_rule_pattern(rule))
        if verdict.verdict in (NEVER, KEEP_ASKING):
            findings.append({
                "rule": rule,
                "signature": signature,
                "model_says": verdict.verdict,
                "reason": verdict.reason,
                "hazards": list(verdict.hazards),
                "note": "allowed today, but the model would not propose it",
            })
    for rule in deny_rules or []:
        signature = _rule_to_signature(rule)
        if signature is None:
            continue
        verdict = classify(signature, raw=_rule_pattern(rule))
        if verdict.auto_allowable:
            findings.append({
                "rule": rule,
                "signature": signature,
                "model_says": verdict.verdict,
                "reason": verdict.reason,
                "note": "denied today, but the model rates it safe — model is wrong",
            })
    return findings


def _rule_pattern(rule):
    """'Bash(cat .env*)' -> 'cat .env*'. The rule text itself if it has no parens."""
    match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$', (rule or "").strip())
    return match.group(2).strip() if match else (rule or "").strip()


def _rule_to_signature(rule):
    """Turn a settings rule back into a transcript signature, both syntaxes."""
    if not isinstance(rule, str):
        return None
    match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$', rule.strip())
    if not match:
        return rule.strip() or None
    tool, pattern = match.group(1), match.group(2)
    # Both `git log *` and `git log:*` mean "git log, any arguments".
    body = re.sub(r'[:\s]\*$', '', pattern).strip()
    if tool != "Bash":
        return "%s(%s)" % (tool, body)
    tokens = operands(body.split())
    if not tokens:
        return None
    if tokens[0] in _MIXED_RISK_CLIS and len(tokens) > 1:
        two = "%s %s" % (tokens[0], tokens[1])
        # `gh pr` says nothing: view and create differ by risk and by nothing
        # else. Keep going until the token that actually decides it.
        if two in NEEDS_THIRD_TOKEN and len(tokens) > 2:
            return "Bash(%s %s)" % (two, tokens[2])
        return "Bash(%s)" % two
    return "Bash(%s)" % tokens[0]
