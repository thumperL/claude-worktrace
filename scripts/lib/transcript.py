#!/usr/bin/env python3
"""
transcript.py — Parse Claude Code session transcripts into a neutral model.

Turns a session JSONL file into tool invocations, approval signals, and
per-session aggregates. Shared by the ledger, the hooks and the skills.

WHAT THE TRANSCRIPT FORMAT ACTUALLY ENCODES

The JSONL does NOT record a permission decision per tool call. There is no
"approved"/"auto-allowed" field anywhere. What it does record:

  - `tool_use` blocks inside `assistant` message content, and matching
    `tool_result` blocks inside the following `user` message content.
  - Explicit REJECTION: the tool_result content is the sentinel string
    "The user doesn't want to proceed with this tool use..." with is_error true.
  - Explicit INTERRUPTION: a user message whose text is
    "[Request interrupted by user for tool use]", carrying `interruptedMessageId`.
  - Sidecar `{"type": "permission-mode", "permissionMode": "default"|"auto"|
    "plan"|"acceptEdits"|"bypassPermissions"}` records, appended in file order
    with no timestamp. They snapshot the mode in effect from that point on.
  - Timestamps on the assistant entry (tool issued) and the user entry
    (result returned).

So approval is INFERRED, never read. This module is explicit about that:
every invocation carries an `approval` value from APPROVAL_* below, plus an
`approval_reason` naming the evidence. Anything we cannot substantiate is
APPROVAL_UNKNOWN — the model never guesses "approved".

Python 3.9 compatible (CI pins 3.9). No 3.10+ syntax.
"""

import fnmatch
import json
import os
import re
from datetime import datetime


# --- Approval model -------------------------------------------------------

# The user was prompted and said no. Read directly from the transcript.
APPROVAL_REJECTED = "rejected"
# The user stopped the tool mid-flight. Read directly from the transcript.
APPROVAL_INTERRUPTED = "interrupted"
# No prompt was possible: permission mode bypassed prompting, or a settings
# allow-rule covered this exact call. Derived, but from hard evidence.
APPROVAL_AUTO = "auto_allowed"
# A prompt was possible and the call went through after a human-scale pause.
# HEURISTIC — this is the closest the format gets to "the user clicked yes".
APPROVAL_LIKELY_MANUAL = "likely_manual"
# A prompt was possible but we cannot tell whether one was shown.
APPROVAL_UNKNOWN = "unknown"

# Invocations in these states cost the user an interaction. Impact ranking
# for the safety classifier is built on this set.
PROMPTING_APPROVALS = (APPROVAL_LIKELY_MANUAL, APPROVAL_REJECTED)

# Latency at or above this (seconds) reads as a human in the loop rather than
# a tool that simply took a while. Deliberately conservative: below it we say
# unknown rather than claiming the call was auto-allowed.
MANUAL_LATENCY_SECONDS = 8.0

# Tools whose latency is dominated by the user thinking or by a long-running
# child process, so the latency heuristic tells us nothing.
LATENCY_BLIND_TOOLS = frozenset([
    "AskUserQuestion", "ExitPlanMode", "Agent", "Task", "SendMessage",
    "Monitor", "ScheduleWakeup", "WebSearch", "WebFetch",
])

# Bash signatures that routinely run longer than MANUAL_LATENCY_SECONDS on their
# own. Without this the heuristic reads every install, build, test and image
# pull as a human approval — and those are precisely the commands a retro would
# then nominate for an allow rule, so the false positives are not random noise,
# they point the recommendation the wrong way. Blind here means UNKNOWN, never
# auto_allowed: a slow command may still have been approved, we simply cannot
# tell, and the honest gap is the point of the model.
LATENCY_BLIND_BASH = frozenset([
    "npm install", "npm ci", "npm run", "npm test", "npm publish", "npm audit",
    "pnpm install", "pnpm run", "pnpm test",
    "yarn install", "yarn run", "yarn test",
    "bun install", "bun run", "bun test",
    "pip install", "pip3 install", "poetry install", "uv sync",
    "cargo build", "cargo test", "cargo publish", "go build", "go test",
    "make", "gradle", "mvn",
    "docker build", "docker run", "docker compose", "docker pull", "docker push",
    "git clone", "git fetch", "git pull", "git push",
    "gh api", "gh run", "gh pr checks", "gh release",
    "terraform plan", "terraform apply", "kubectl apply", "kubectl logs",
    "pytest", "jest", "vitest", "playwright", "cypress",
    "tsc", "eslint", "prettier", "webpack", "vite", "next", "rspack",
])

# Modes in which no permission prompt can appear at all.
NON_PROMPTING_MODES = frozenset(["auto", "bypassPermissions", "acceptEdits"])
# ...except acceptEdits, which only silences file edits.
EDIT_TOOLS = frozenset(["Edit", "Write", "NotebookEdit", "MultiEdit"])

REJECTION_SENTINEL = "The user doesn't want to proceed with this tool use"
INTERRUPT_SENTINEL = "[Request interrupted by user"


# --- Model ----------------------------------------------------------------

class ToolInvocation(object):
    """One tool call, with whatever the transcript says about its approval."""

    def __init__(self, tool_use_id, tool_name, tool_input, timestamp,
                 uuid=None, permission_mode=None, is_sidechain=False,
                 skill=None, plugin=None, mcp_server=None):
        self.tool_use_id = tool_use_id
        self.tool_name = tool_name or "unknown"
        self.tool_input = tool_input if isinstance(tool_input, dict) else {}
        self.timestamp = timestamp
        self.uuid = uuid
        self.permission_mode = permission_mode
        self.is_sidechain = bool(is_sidechain)
        self.skill = skill
        self.plugin = plugin
        self.mcp_server = mcp_server

        self.result_timestamp = None
        self.result_text = ""
        self.is_error = False
        self.approval = APPROVAL_UNKNOWN
        self.approval_reason = "no result recorded"

    @property
    def key_input(self):
        """The one field that identifies what this call actually did."""
        return _key_input(self.tool_name, self.tool_input)

    @property
    def latency_seconds(self):
        """Seconds between the call being issued and its result. None if unknown."""
        start = parse_timestamp(self.timestamp)
        end = parse_timestamp(self.result_timestamp)
        if start is None or end is None:
            return None
        return (end - start).total_seconds()

    @property
    def signature(self):
        """Grouping key for 'the same kind of call, again'."""
        return _signature(self.tool_name, self.tool_input)

    @property
    def prompted(self):
        """True when this call plausibly cost the user an approval interaction."""
        return self.approval in PROMPTING_APPROVALS

    def to_dict(self):
        return {
            "tool_use_id": self.tool_use_id,
            "tool_name": self.tool_name,
            "key_input": self.key_input,
            "signature": self.signature,
            "timestamp": self.timestamp,
            "latency_seconds": self.latency_seconds,
            "permission_mode": self.permission_mode,
            "approval": self.approval,
            "approval_reason": self.approval_reason,
            "is_error": self.is_error,
            "is_sidechain": self.is_sidechain,
            "skill": self.skill,
            "plugin": self.plugin,
            "mcp_server": self.mcp_server,
        }


class ApprovalGroup(object):
    """Repeated invocations that share a signature — the retro's unit of impact."""

    def __init__(self, signature, tool_name):
        self.signature = signature
        self.tool_name = tool_name
        self.invocations = []

    def add(self, inv):
        self.invocations.append(inv)

    @property
    def total(self):
        return len(self.invocations)

    @property
    def prompted_count(self):
        return len([i for i in self.invocations if i.prompted])

    @property
    def rejected_count(self):
        return len([i for i in self.invocations if i.approval == APPROVAL_REJECTED])

    @property
    def examples(self):
        seen = []
        for inv in self.invocations:
            text = inv.key_input
            if text and text not in seen:
                seen.append(text)
            if len(seen) >= 3:
                break
        return seen

    def to_dict(self):
        return {
            "signature": self.signature,
            "tool_name": self.tool_name,
            "total": self.total,
            "prompted_count": self.prompted_count,
            "rejected_count": self.rejected_count,
            "examples": self.examples,
        }


class Session(object):
    """A parsed transcript: its invocations plus per-session aggregates."""

    def __init__(self, path):
        self.path = path
        self.session_id = None
        self.cwd = None
        self.git_branch = None
        self.version = None
        self.invocations = []
        self.user_prompts = []
        # How many invocations had been seen when each prompt arrived. File order
        # is the truth here, and it is the only way to ask whether the user spoke
        # between two calls -- which is what separates being told to do something
        # differently from working around a block.
        self.prompt_boundaries = []
        self.permission_modes = []
        self.first_timestamp = None
        self.last_timestamp = None
        self.parse_errors = 0

    # -- aggregates --

    @property
    def total_invocations(self):
        return len(self.invocations)

    @property
    def duration_seconds(self):
        start = parse_timestamp(self.first_timestamp)
        end = parse_timestamp(self.last_timestamp)
        if start is None or end is None:
            return None
        return (end - start).total_seconds()

    @property
    def tool_histogram(self):
        return _counter(inv.tool_name for inv in self.invocations)

    @property
    def approval_histogram(self):
        return _counter(inv.approval for inv in self.invocations)

    @property
    def permission_mode_histogram(self):
        return _counter(self.permission_modes)

    @property
    def mode_at_tool_use_histogram(self):
        """Permission mode in effect for each call — the mode that actually mattered."""
        return _counter(inv.permission_mode for inv in self.invocations)

    @property
    def prompt_capable_invocations(self):
        """Calls made in a mode that could have prompted.

        When this is near zero the session ran unattended, so a near-zero
        approval count says nothing about what is safe to auto-allow.
        """
        total = 0
        for inv in self.invocations:
            mode = inv.permission_mode
            if mode in ("auto", "bypassPermissions"):
                continue
            if mode == "acceptEdits" and inv.tool_name in EDIT_TOOLS:
                continue
            total += 1
        return total

    @property
    def prompted_estimate(self):
        """How many calls plausibly cost the user an approval. Lower bound."""
        return len([i for i in self.invocations if i.prompted])

    @property
    def error_count(self):
        return len([i for i in self.invocations if i.is_error])

    def approval_groups(self, prompted_only=True, min_total=1):
        """Invocations bucketed by signature, most impactful first."""
        groups = {}
        for inv in self.invocations:
            if prompted_only and not inv.prompted:
                continue
            key = inv.signature
            if key not in groups:
                groups[key] = ApprovalGroup(key, inv.tool_name)
            groups[key].add(inv)
        out = [g for g in groups.values() if g.total >= min_total]
        out.sort(key=lambda g: (-g.prompted_count, -g.total, g.signature))
        return out

    def repeated_failures(self, min_count=2):
        """Signatures that errored repeatedly — candidates for a documented gotcha."""
        buckets = {}
        for inv in self.invocations:
            if not inv.is_error:
                continue
            buckets.setdefault(inv.signature, []).append(inv)
        out = []
        for sig, invs in buckets.items():
            if len(invs) >= min_count:
                out.append({
                    "signature": sig,
                    "tool_name": invs[0].tool_name,
                    "count": len(invs),
                    "examples": [i.key_input for i in invs[:3]],
                })
        out.sort(key=lambda d: -d["count"])
        return out

    def stats(self):
        return {
            "session_id": self.session_id,
            "path": self.path,
            "cwd": self.cwd,
            "git_branch": self.git_branch,
            "version": self.version,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "duration_seconds": self.duration_seconds,
            "total_invocations": self.total_invocations,
            "prompted_estimate": self.prompted_estimate,
            "prompt_capable_invocations": self.prompt_capable_invocations,
            "error_count": self.error_count,
            "user_prompt_count": len(self.user_prompts),
            "tool_histogram": self.tool_histogram,
            "approval_histogram": self.approval_histogram,
            "mode_at_tool_use_histogram": self.mode_at_tool_use_histogram,
            "permission_mode_histogram": self.permission_mode_histogram,
            "parse_errors": self.parse_errors,
        }


# --- Parsing --------------------------------------------------------------

def parse_transcript(path, allow_rules=None, include_sidechain=True,
                     max_lines=200000):
    """Parse one transcript JSONL into a Session.

    allow_rules: settings permission strings such as "Bash(git log *)". When a
    rule covers a call, that call is classified auto-allowed regardless of mode.
    """
    session = Session(str(path))
    pending = {}
    order = []
    current_mode = None
    interrupted_messages = set()

    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except (IOError, OSError):
        return session

    with handle as f:
        for line_num, line in enumerate(f):
            if line_num >= max_lines:
                break
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                session.parse_errors += 1
                continue
            if not isinstance(entry, dict):
                session.parse_errors += 1
                continue

            etype = entry.get("type")

            if etype == "permission-mode":
                mode = entry.get("permissionMode")
                if mode:
                    current_mode = mode
                    session.permission_modes.append(mode)
                continue

            _absorb_session_metadata(session, entry)

            timestamp = entry.get("timestamp")
            if timestamp:
                if session.first_timestamp is None:
                    session.first_timestamp = timestamp
                session.last_timestamp = timestamp

            if entry.get("interruptedMessageId"):
                interrupted_messages.add(entry["interruptedMessageId"])

            if not include_sidechain and entry.get("isSidechain"):
                continue

            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")

            if etype == "user":
                # Tool results are also user entries; only real prompts count.
                text = _text_of(content, include_tool_results=False)
                if text and not text.startswith("[Request interrupted"):
                    session.user_prompts.append(text[:2000])
                    # Counted against `order`, the invocations seen SO FAR in
                    # this parse. session.invocations is not populated until the
                    # parse finishes, so counting against it records 0 every
                    # time and the boundary stops separating anything.
                    session.prompt_boundaries.append(len(order))

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")

                if btype == "tool_use":
                    inv = ToolInvocation(
                        tool_use_id=block.get("id"),
                        tool_name=block.get("name"),
                        tool_input=block.get("input"),
                        timestamp=timestamp,
                        uuid=entry.get("uuid"),
                        permission_mode=current_mode,
                        is_sidechain=entry.get("isSidechain"),
                        skill=entry.get("attributionSkill"),
                        plugin=entry.get("attributionPlugin"),
                        mcp_server=entry.get("attributionMcpServer"),
                    )
                    inv._message_id = message.get("id")
                    if inv.tool_use_id:
                        pending[inv.tool_use_id] = inv
                    order.append(inv)

                elif btype == "tool_result":
                    inv = pending.get(block.get("tool_use_id"))
                    if inv is None:
                        continue
                    inv.result_timestamp = timestamp
                    inv.result_text = _text_of(block.get("content"))[:4000]
                    inv.is_error = bool(block.get("is_error"))

    for inv in order:
        classify_approval(inv, allow_rules=allow_rules,
                          interrupted_messages=interrupted_messages)
    session.invocations = order
    return session


def classify_approval(inv, allow_rules=None, interrupted_messages=None):
    """Assign inv.approval and inv.approval_reason from transcript evidence."""
    interrupted_messages = interrupted_messages or set()

    if inv.is_error and REJECTION_SENTINEL in (inv.result_text or ""):
        inv.approval = APPROVAL_REJECTED
        inv.approval_reason = "transcript records an explicit user rejection"
        return inv

    message_id = getattr(inv, "_message_id", None)
    if message_id and message_id in interrupted_messages:
        inv.approval = APPROVAL_INTERRUPTED
        inv.approval_reason = "user interrupted the request carrying this tool use"
        return inv

    rule = matching_allow_rule(inv.tool_name, inv.key_input, allow_rules)
    if rule:
        inv.approval = APPROVAL_AUTO
        inv.approval_reason = "covered by existing allow rule %s" % rule
        return inv

    mode = inv.permission_mode
    if mode in ("auto", "bypassPermissions"):
        inv.approval = APPROVAL_AUTO
        inv.approval_reason = "permission mode %s cannot prompt" % mode
        return inv
    if mode == "acceptEdits" and inv.tool_name in EDIT_TOOLS:
        inv.approval = APPROVAL_AUTO
        inv.approval_reason = "acceptEdits mode cannot prompt for file edits"
        return inv

    if inv.tool_name in LATENCY_BLIND_TOOLS:
        inv.approval = APPROVAL_UNKNOWN
        inv.approval_reason = "%s latency reflects user or child-process time" % inv.tool_name
        return inv

    if inv.tool_name == "Bash":
        signature = _bash_signature(inv.key_input)
        if signature in LATENCY_BLIND_BASH:
            inv.approval = APPROVAL_UNKNOWN
            inv.approval_reason = (
                "`%s` runs long on its own, so its latency cannot distinguish a "
                "waiting human from a working command" % signature)
            return inv

    latency = inv.latency_seconds
    if latency is None:
        inv.approval = APPROVAL_UNKNOWN
        inv.approval_reason = "no result timestamp to compare against"
        return inv

    if latency >= MANUAL_LATENCY_SECONDS:
        inv.approval = APPROVAL_LIKELY_MANUAL
        inv.approval_reason = "%.0fs gap in %s mode reads as a human approval" % (
            latency, mode or "default")
        return inv

    inv.approval = APPROVAL_UNKNOWN
    inv.approval_reason = "returned in %.1fs — too fast to attribute either way" % latency
    return inv


def matching_allow_rule(tool_name, key_input, allow_rules):
    """Return the first allow rule covering this call, or None.

    Two syntaxes are in circulation and both must be honoured:

      Bash(git log *)    glob form   — fnmatch against the whole command
      Bash(git log:*)    prefix form — matches `git log` and anything below it

    The prefix form is what Claude Code writes when a rule is added through the
    permission prompt, so it dominates real settings files. fnmatch cannot
    express it (it would hunt for a literal colon), and treating it as a
    non-match silently reclassifies already-allowed calls as manual approvals,
    which is exactly the number a retro is built on.
    """
    if not allow_rules:
        return None
    for rule in allow_rules:
        if not isinstance(rule, str):
            continue
        rule = rule.strip()
        if rule == tool_name:
            return rule
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_-]*)\((.*)\)$', rule)
        if not match:
            continue
        rule_tool, pattern = match.group(1), match.group(2)
        if rule_tool != tool_name:
            continue
        target = key_input or ""
        if pattern in ("", "*", ":*"):
            return rule
        if pattern.endswith(":*"):
            prefix = pattern[:-2].strip()
            if prefix and (target == prefix or target.startswith(prefix + " ")):
                return rule
            continue
        if fnmatch.fnmatchcase(target, pattern):
            return rule
    return None


# --- Multi-session helpers ------------------------------------------------

def encode_project_dir(cwd):
    """Encode a cwd the way Claude Code names ~/.claude/projects entries."""
    if not cwd:
        return None
    return str(cwd).rstrip("/").replace("/", "-")


def find_transcripts(cwd=None, projects_dir=None, limit=None):
    """Transcript paths for a project (or all projects), newest first."""
    base = projects_dir or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    if not os.path.isdir(base):
        return []

    if cwd:
        candidates = [os.path.join(base, encode_project_dir(cwd))]
    else:
        candidates = [os.path.join(base, name) for name in os.listdir(base)]

    found = []
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(directory, name)
            try:
                found.append((os.path.getmtime(path), path))
            except OSError:
                continue

    found.sort(reverse=True)
    paths = [path for _, path in found]
    if limit:
        paths = paths[:limit]
    return paths


def aggregate(sessions):
    """Roll several parsed sessions into one set of totals."""
    totals = {
        "session_count": len(sessions),
        "total_invocations": 0,
        "prompted_estimate": 0,
        "prompt_capable_invocations": 0,
        "error_count": 0,
        "tool_histogram": {},
        "approval_histogram": {},
        "mode_at_tool_use_histogram": {},
    }
    for session in sessions:
        totals["total_invocations"] += session.total_invocations
        totals["prompted_estimate"] += session.prompted_estimate
        totals["prompt_capable_invocations"] += session.prompt_capable_invocations
        totals["error_count"] += session.error_count
        _merge_counts(totals["tool_histogram"], session.tool_histogram)
        _merge_counts(totals["approval_histogram"], session.approval_histogram)
        _merge_counts(totals["mode_at_tool_use_histogram"],
                      session.mode_at_tool_use_histogram)

    # Surfaced so callers never read a zero approval count as "nothing to fix"
    # when the real cause is that the session simply never prompted.
    totals["ran_unattended"] = (
        totals["total_invocations"] > 0
        and totals["prompt_capable_invocations"] * 10 < totals["total_invocations"]
    )
    return totals


def merge_approval_groups(sessions, min_total=1):
    """Approval groups across sessions, most impactful first."""
    groups = {}
    for session in sessions:
        for group in session.approval_groups(min_total=1):
            existing = groups.get(group.signature)
            if existing is None:
                existing = ApprovalGroup(group.signature, group.tool_name)
                groups[group.signature] = existing
            for inv in group.invocations:
                existing.add(inv)
    out = [g for g in groups.values() if g.total >= min_total]
    out.sort(key=lambda g: (-g.prompted_count, -g.total, g.signature))
    return out


# --- Internals ------------------------------------------------------------

# CLIs where the bare binary says nothing useful — the subcommand is the unit
# of risk, so group on "git status" rather than "git".
SUBCOMMAND_CLIS = frozenset([
    "git", "gh", "npm", "pnpm", "yarn", "bun", "docker", "kubectl", "cargo",
    "go", "poetry", "pip", "pip3", "brew", "make", "terraform", "aws", "gcloud",
    "wrangler", "flyctl", "supabase", "uv",
])

# Two-token prefixes where the second token is a noun, not a risk decision.
# `gh pr` covers both `view` and `create`; grouping them together would merge a
# read with a publish and present the pair as one high-impact allow candidate.
# lib/safety.py imports this, so the grouping key and the safety verdict always
# describe the same thing.
NEEDS_THIRD_TOKEN = frozenset([
    "gh pr", "gh issue", "gh repo", "gh run", "gh release", "gh secret",
    "git remote", "git branch", "git stash", "git submodule",
    "docker compose", "docker image", "docker volume",
    "kubectl config", "aws s3", "gcloud compute",
])

# Flags that swallow the next token as their value. Without this, `git -C /repo
# log` yields the operand list ["/repo", "log"] and the signature becomes
# `git /repo` — a per-repository key that never matches `git log`, so the same
# command run with -C fragments into one unrecognised group per directory.
VALUE_FLAGS = frozenset([
    "-C", "-c", "-f", "-o", "-n", "--git-dir", "--work-tree", "--file",
    "--config", "--output", "--namespace", "--context", "--profile",
])


def operands(tokens):
    """Positional arguments only: flags dropped, and their values with them."""
    words = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            # `--git-dir=/x` carries its value inline, so nothing to skip.
            if token in VALUE_FLAGS:
                skip = True
            continue
        words.append(token)
    return words


def _key_input(tool_name, tool_input):
    """The single most identifying input field for a tool call."""
    if not tool_input:
        return ""
    for field in ("command", "file_path", "pattern", "url", "query",
                  "notebook_path", "skill", "path", "prompt", "description"):
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""


def _signature(tool_name, tool_input):
    """Stable grouping key: same signature means 'this again'."""
    if tool_name == "Bash":
        return "Bash(%s)" % _bash_signature(_key_input(tool_name, tool_input))
    if tool_name in EDIT_TOOLS:
        path = _key_input(tool_name, tool_input)
        ext = os.path.splitext(path)[1] or "(no ext)"
        return "%s(*%s)" % (tool_name, ext)
    return tool_name


def _bash_signature(command):
    """Reduce a shell command to the verb the user is really approving."""
    if not command:
        return ""
    # Only the first pipeline stage matters, and a leading `cd x &&` is noise.
    segment = re.split(r'&&|\|\||[|;\n]', command.strip())[0].strip()
    if re.match(r'^cd\s', segment):
        parts = re.split(r'&&|\|\||[|;\n]', command.strip())
        for candidate in parts[1:]:
            candidate = candidate.strip()
            if candidate:
                segment = candidate
                break

    # Drop leading VAR=value assignments so `FOO=1 npm test` groups with `npm test`.
    tokens = [t for t in segment.split() if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', t)]
    if not tokens:
        return ""

    head = os.path.basename(tokens[0])
    if head not in SUBCOMMAND_CLIS:
        return head

    words = operands(tokens[1:])
    if not words:
        return head

    signature = "%s %s" % (head, words[0])
    # Two tokens is not always enough to decide risk: `gh pr view` and
    # `gh pr create` sit at opposite ends of the safety model and both reduce to
    # `gh pr`. Where the noun carries no risk information, keep the verb too.
    if signature in NEEDS_THIRD_TOKEN and len(words) > 1:
        return "%s %s" % (signature, words[1])
    return signature


def _absorb_session_metadata(session, entry):
    if session.session_id is None:
        session.session_id = entry.get("sessionId") or entry.get("session_id")
    if session.cwd is None and entry.get("cwd"):
        session.cwd = entry.get("cwd")
    if entry.get("gitBranch"):
        session.git_branch = entry.get("gitBranch")
    if session.version is None and entry.get("version"):
        session.version = entry.get("version")


def _text_of(content, include_tool_results=True):
    """Flatten a message content field to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        wanted = ("text", "tool_result") if include_tool_results else ("text",)
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in wanted:
                    value = block.get("text") or block.get("content")
                    if isinstance(value, str):
                        parts.append(value)
                    elif isinstance(value, list):
                        parts.append(_text_of(value, include_tool_results))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def parse_timestamp(value):
    """Parse an ISO-8601 transcript timestamp. None when unparseable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _counter(values):
    counts = {}
    for value in values:
        key = value or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _merge_counts(target, source):
    for key, value in source.items():
        target[key] = target.get(key, 0) + value
