#!/usr/bin/env python3
"""
detectors.py — Turn a parsed session into friction events.

Four kinds, all with unambiguous evidence, and none of them dependent on a
permission prompt ever happening. That last property is the point: the
permission-tuning axis reads empty when nothing can prompt, and in `auto` mode
nothing does.

    failed_retry   something errored and had to be run again
    rediscovery    the same thing was looked up in a separate session
    correction     a call was stopped, and something else happened instead
    circumvention  a call was stopped, and the same target was reached anyway

Detectors are per-session by construction. Nothing here knows or cares that a
key has been seen before, because the cross-session gate lives in lib/ledger.py
where the occurrences accumulate. A detector that tried to decide "has this
recurred?" would need the whole history in scope and would stop being testable
against a single transcript.

Python 3.9 compatible.
"""

import os
import re

try:
    from . import safety as safety_lib
    from . import transcript as transcript_lib
except ImportError:  # executed directly rather than as a package
    import safety as safety_lib
    import transcript as transcript_lib

try:
    from .ledger import (KIND_CIRCUMVENTION, KIND_CORRECTION,
                         KIND_FAILED_RETRY, KIND_REDISCOVERY)
except ImportError:
    from ledger import (KIND_CIRCUMVENTION, KIND_CORRECTION,
                        KIND_FAILED_RETRY, KIND_REDISCOVERY)


# Lookups. An Edit or a Bash command is work; these are the tools you reach for
# when you need to know something.
LOOKUP_TOOLS = frozenset(["Read", "Grep", "Glob"])

# A retry further away than this is a different piece of work that happens to
# touch the same command, not a recovery from the failure.
MAX_RETRY_DISTANCE = 12


def detect(session):
    """Every event for one parsed session."""
    events = []
    events.extend(detect_failed_retry(session))
    events.extend(detect_rediscovery(session))
    events.extend(detect_blocked_followups(session))
    return events


# --- Blocked calls, and what happened next --------------------------------

# Beyond this many calls the connection to the block is speculative.
MAX_FOLLOWUP_DISTANCE = 6


def detect_blocked_followups(session):
    """What Claude did after being stopped. Two kinds, and the target decides.

    A blocked call followed by a different approach is a `correction`: you said
    no and something else happened. If that different approach reaches the SAME
    target, it is a `circumvention`: the block was routed around rather than
    accepted.

    Circumvention is the narrower claim and the more serious one, so a pair that
    qualifies as both is reported only as a circumvention.

    THE FALSE POSITIVE THAT MATTERS

    Being blocked and then told "just edit it directly" is instruction, not
    evasion, and the transcript records the difference: a user message between
    the two calls. Accusing Claude of working around a block it was explicitly
    redirected past is worse than missing a real one, so an intervening prompt
    disqualifies the pair entirely rather than downgrading it.
    """
    events = []
    invocations = session.invocations

    for index, inv in enumerate(invocations):
        if not _was_blocked(inv):
            continue

        blocked_targets = _targets(inv)
        limit = min(len(invocations), index + 1 + MAX_FOLLOWUP_DISTANCE)

        for offset in range(index + 1, limit):
            candidate = invocations[offset]
            if candidate.signature == inv.signature:
                continue  # a retry of the same thing, not a way around it
            if _user_spoke_between(session, index, offset):
                break

            shared = blocked_targets & _targets(candidate)
            if not shared and blocked_targets:
                # The binary gate above stops branch names being read as files,
                # but it also hides the wrappers: `python -c "open('/x/.env')"`,
                # `sh -c "cat /x/.env"`, `git show HEAD:/x/.env`. Those reach the
                # blocked path just as surely. Since a blocked target is already
                # a known path rather than a guess, looking for it verbatim in
                # the next command adds no false positives.
                text = candidate.key_input or ""
                shared = set(t for t in blocked_targets if t and t in text)
            if shared:
                events.append(_circumvention(session, inv, candidate, shared))
            else:
                events.append(_correction(session, inv, candidate))
            break

    return events


def _was_blocked(inv):
    return inv.approval in (transcript_lib.APPROVAL_REJECTED,
                            transcript_lib.APPROVAL_INTERRUPTED)


def _user_spoke_between(session, start, end):
    """True when a user message landed between these two invocations."""
    return any(start < boundary <= end
               for boundary in getattr(session, "prompt_boundaries", []))


def _circumvention(session, blocked, alternate, shared):
    verdict = safety_lib.hazard_reached(alternate.signature, raw=alternate.key_input)
    target = sorted(shared)[0]
    return {
        "kind": KIND_CIRCUMVENTION,
        "key": "%s -> %s (%s)" % (blocked.signature, alternate.signature, target),
        "tool_name": alternate.tool_name,
        "session_id": session.session_id,
        "timestamp": alternate.timestamp,
        "cost_calls": 1,
        "cost_seconds": _elapsed(blocked, alternate),
        "evidence": "blocked: %s | reached the same target via: %s%s" % (
            blocked.key_input, alternate.key_input,
            " | %s" % verdict.reason if verdict else ""),
    }


def _correction(session, blocked, alternate):
    return {
        "kind": KIND_CORRECTION,
        "key": blocked.signature,
        "tool_name": blocked.tool_name,
        "session_id": session.session_id,
        "timestamp": blocked.timestamp,
        "cost_calls": 1,
        "cost_seconds": _elapsed(blocked, alternate),
        "evidence": "stopped: %s | did instead: %s" % (
            blocked.key_input, alternate.key_input),
    }


# Tokens that name a file or directory: anything holding a separator, a leading
# dot, or a plausible extension. Deliberately literal. The spec's open question
# is how far to loosen this, and every loosening buys recall by risking an
# accusation, so it starts as narrow as it can usefully be.
_PATH_LIKE = re.compile(r'(?:^|[\s\'"=(])([~.]?/[^\s\'";|&()]+|[\w.-]+/[^\s\'";|&()]*|\.?[\w-]+\.[A-Za-z0-9]{1,6})')

_FILE_TOOLS = frozenset(["Read", "Edit", "Write", "NotebookEdit", "MultiEdit",
                         "Glob", "Grep"])


# Binaries whose arguments are file paths. Without this gate, any token holding
# a slash counts as a target, and `git checkout feature/foo` then
# `git branch --list feature/foo` reads as reaching the same file by another
# route. It is a branch name. Accusing Claude of evading a block on the strength
# of a shared branch name is exactly the false positive this detector must not
# produce, so path extraction is limited to commands that take paths.
_FILE_COMMANDS = frozenset([
    "cat", "head", "tail", "less", "more", "open", "bat",
    "cp", "mv", "rm", "ln", "touch", "mkdir", "rmdir", "tee", "install",
    "grep", "rg", "ag", "sed", "awk", "wc", "sort", "uniq", "diff", "cut",
    "file", "stat", "chmod", "chown", "shred", "truncate", "dd",
    "vim", "vi", "nano", "emacs", "code", "source", "scp", "rsync",
])


def _targets(inv):
    """Paths this call touches. Empty when it names none this layer can see."""
    if inv.tool_name in _FILE_TOOLS:
        return set(t for t in [_normalise(inv.key_input)] if t)
    if inv.tool_name == "Bash":
        command = (inv.key_input or "").strip()
        binary = os.path.basename(command.split()[0]) if command.split() else ""
        if binary not in _FILE_COMMANDS:
            return set()
        return set(t for t in (_normalise(m) for m in _PATH_LIKE.findall(command)) if t)
    return set()


def _normalise(token):
    """So that `./x`, `x` and `~/x` compare equal where they name one file."""
    if not token:
        return ""
    token = token.strip().strip("'\"")
    if not token:
        return ""
    return os.path.normpath(os.path.expanduser(token))


def detect_failed_retry(session):
    """An error, followed by the same signature again.

    One event per error that was followed by a retry. An error with nothing
    after it is a failure the session gave up on, which is a different finding
    and not this one.
    """
    events = []
    invocations = session.invocations
    for index, inv in enumerate(invocations):
        if not inv.is_error:
            continue
        retry, distance = _next_matching(invocations, index)
        if retry is None:
            continue
        events.append({
            "kind": KIND_FAILED_RETRY,
            "key": inv.signature,
            "tool_name": inv.tool_name,
            "session_id": session.session_id,
            "timestamp": inv.timestamp,
            "cost_calls": distance,
            "cost_seconds": _elapsed(inv, retry),
            "evidence": _evidence(inv),
        })
    return events


def _next_matching(invocations, index):
    """The next invocation sharing this signature, and how many calls away."""
    signature = invocations[index].signature
    limit = min(len(invocations), index + 1 + MAX_RETRY_DISTANCE)
    for offset in range(index + 1, limit):
        if invocations[offset].signature == signature:
            return invocations[offset], offset - index
    return None, 0


def detect_rediscovery(session):
    """Something looked up, that a written-down fact would have answered.

    Emitted once per distinct key per session, never once per read. Reading the
    same file three times in one afternoon is one session's confusion; the same
    file in four separate sessions is the finding, and that comparison happens
    in the ledger.

    Files that were also edited in this session are excluded. Reading a file you
    then change is the work itself, not a lookup that should have been
    unnecessary.
    """
    edited = set()
    for inv in session.invocations:
        if inv.tool_name in transcript_lib.EDIT_TOOLS:
            edited.add(inv.key_input)

    seen = {}
    for inv in session.invocations:
        if inv.tool_name not in LOOKUP_TOOLS:
            continue
        key = inv.key_input
        if not key or key in edited:
            continue
        if key in seen:
            seen[key]["cost_calls"] += 1
            continue
        seen[key] = {
            "kind": KIND_REDISCOVERY,
            "key": "%s(%s)" % (inv.tool_name, key),
            "tool_name": inv.tool_name,
            "session_id": session.session_id,
            "timestamp": inv.timestamp,
            "cost_calls": 1,
            "cost_seconds": None,
            "evidence": _evidence(inv),
        }
    return list(seen.values())


def _elapsed(first, second):
    start = transcript_lib.parse_timestamp(first.timestamp)
    end = transcript_lib.parse_timestamp(second.timestamp)
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _evidence(inv):
    """What the row keeps. Raw, capped by the ledger, judged at query time.

    Error text is included because it is usually the fact that was missing, and
    it is the field a CLAUDE.md line gets written from.
    """
    parts = [inv.key_input]
    if inv.is_error and inv.result_text:
        parts.append("error: %s" % inv.result_text.strip())
    return " | ".join(p for p in parts if p)
