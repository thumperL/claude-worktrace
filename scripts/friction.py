#!/usr/bin/env python3
"""
friction.py — The friction ledger CLI.

    index   Fold new or changed transcripts into this project's ledger.
            Idempotent, incremental, and silent. Safe to run on every
            SessionEnd, which is where it is meant to run.

    top     Rows worth surfacing, ranked. This is what the skill reads.

    check   The nudge signal: did this fold push anything past the gate?
            Silent unless something did.

    verify  Applied fixes that did not land, i.e. rows that kept happening
            after they were stamped. Run before proposing anything new.

    show    Everything held about one row, including its raw evidence.

Only `index` and `check` write, and only to the derived ledger. Configuration files
are written by the skill, item by item, on the main thread, with the user
watching. Widening what Claude may do unsupervised is the one thing that must
not happen unsupervised.

Python 3.9 compatible.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import detectors as detectors_lib  # noqa: E402
from lib import ledger as ledger_lib  # noqa: E402
from lib import settings as settings_lib  # noqa: E402
from lib import transcript as transcript_lib  # noqa: E402


def newly_proposable(transcript_path, cwd, ledger_file=None, min_sessions=None):
    """Fold one transcript in, and report which rows that pushed past the gate.

    This is the nudge signal, and the hook and the CLI both read it from here so
    that the two cannot drift. It replaces the busy-session threshold the
    previous design used: "this session made 30 tool calls" is a proxy for having
    something to say, and "this recurred in a second session" is the thing
    itself. A first session can never trip it, which is the cold-start property
    working rather than a bug.
    """
    if min_sessions is None:              # read at call time, not bound at import
        min_sessions = ledger_lib.MIN_SESSIONS
    path = ledger_file or ledger_lib.ledger_path(cwd)
    ledger = ledger_lib.Ledger.load(path)
    before = ledger.proposable_ids(min_sessions)

    session = transcript_lib.parse_transcript(
        transcript_path, allow_rules=settings_lib.allow_rules(cwd=cwd))
    ledger.fold(transcript_path, detectors_lib.detect(session))
    ledger.save(path)

    crossed = ledger.proposable_ids(min_sessions) - before
    return [ledger.rows[rid] for rid in crossed], ledger


def _open_ledger(args):
    cwd = args.cwd or os.getcwd()
    path = args.ledger or ledger_lib.ledger_path(cwd)
    return cwd, path, ledger_lib.Ledger.load(path)


def cmd_index(args):
    cwd, path, ledger = _open_ledger(args)
    paths = ([args.transcript] if args.transcript
             else transcript_lib.find_transcripts(cwd=cwd, limit=args.limit))

    rules = settings_lib.allow_rules(cwd=cwd)
    folded = 0
    for transcript_path in paths:
        if not args.force and not ledger.needs_index(transcript_path):
            continue
        try:
            session = transcript_lib.parse_transcript(
                transcript_path, allow_rules=rules)
        except Exception as error:  # a bad transcript must not stall the rest
            print("[friction] skipped %s: %s" % (transcript_path, error),
                  file=sys.stderr)
            continue
        ledger.fold(transcript_path, detectors_lib.detect(session))
        folded += 1

    if folded:
        ledger.save(path)

    if args.json:
        payload = dict(ledger.stats())
        payload.update({"folded": folded, "ledger": path})
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif folded and not args.quiet:
        print("[friction] indexed %d transcript(s); %d row(s), %d proposable"
              % (folded, len(ledger.rows), len(ledger.ranked())))
    return 0


def cmd_top(args):
    cwd, path, ledger = _open_ledger(args)
    kinds = set(args.kind) if args.kind else None
    rows = ledger.ranked(min_sessions=args.min_sessions, kinds=kinds,
                         include_resolved=args.include_resolved)[:args.limit]

    payload = {
        "project": cwd,
        "ledger": path,
        "stats": ledger.stats(min_sessions=args.min_sessions),
        "rows": [row.summary() for row in rows],
        "caveat": (
            "Ranked by distinct sessions, then by calls burned. Ranking says "
            "what to look at and never what is safe -- permission verdicts come "
            "from lib/safety.py, and nothing here may widen one."
        ),
    }
    if args.json or not args.text:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    _print_rows(payload)
    return 0


def cmd_check(args):
    """Did anything newly cross the gate? Silent unless something did."""
    cwd = args.cwd or os.getcwd()
    transcript = args.transcript
    if not transcript:
        recent = transcript_lib.find_transcripts(cwd=cwd, limit=1)
        if not recent:
            return 0
        transcript = recent[0]

    crossed, _ = newly_proposable(transcript, cwd, ledger_file=args.ledger,
                                  min_sessions=args.min_sessions)
    if not crossed:
        return 0

    if args.json:
        print(json.dumps({"crossed": [row.summary() for row in crossed]},
                         indent=2, sort_keys=True))
    else:
        print(suggestion(crossed))
    return 0


def suggestion(crossed):
    """One line. Names what recurred, because that is the reason to look."""
    first = crossed[0]
    tail = ("" if len(crossed) == 1
            else ", and %d other thing(s)" % (len(crossed) - 1))
    return ('%s has now come up in %d separate sessions%s. Say "friction ledger" '
            'to see what is worth writing down.' % (first.key, first.sessions, tail))


def cmd_verify(args):
    """Which applied fixes did not land. Run this before proposing anything new.

    A row stamped as applied keeps accruing. If nothing has happened since, the
    fix worked. If things have, it did not, and that is usually more interesting
    than whatever is at the top of the ranking.
    """
    _, path, ledger = _open_ledger(args)
    failures = [{
        "id": row.id,
        "kind": row.kind,
        "key": row.key,
        "applied_at": row.resolution.get("applied_at"),
        "occurrences_since": count,
        "evidence": row.evidence[:1],
    } for row, count in ledger.unverified()]

    if args.json or not args.text:
        print(json.dumps({"ledger": path, "not_landing": failures},
                         indent=2, sort_keys=True))
        return 0

    if not failures:
        print("Every applied fix has stayed quiet since.")
        return 0
    for failure in failures:
        print("%s — applied %s. %d occurrence(s) since. Not landing."
              % (failure["key"], failure["applied_at"], failure["occurrences_since"]))
    return 0


def cmd_show(args):
    _, _, ledger = _open_ledger(args)
    row = ledger.get(args.id)
    if row is None:
        print(json.dumps({"error": "no row matching %r" % args.id}))
        return 1
    payload = row.summary()
    payload["occurrences"] = row.occurrences
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _print_rows(payload):
    stats = payload["stats"]
    print("Project: %s" % payload["project"])
    print("Rows: %d  |  proposable: %d  |  resolved: %d  |  transcripts: %d"
          % (stats["rows"], stats["proposable"], stats["resolved"],
             stats["transcripts_indexed"]))
    if stats["below_gate"]:
        print("Seen in fewer than %d sessions, so not proposed: %d"
              % (stats["min_sessions"], stats["below_gate"]))
    print()
    if not payload["rows"]:
        print("Nothing has recurred across sessions yet.")
        return
    print("%-16s %-14s %5s %6s  %s"
          % ("ID", "KIND", "SESS", "CALLS", "KEY"))
    for row in payload["rows"]:
        print("%-16s %-14s %5d %6d  %s"
              % (row["id"], row["kind"], row["sessions"], row["cost_calls"],
                 row["key"][:44]))
    print()
    print(payload["caveat"])


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="command")

    def common(target):
        target.add_argument("--cwd", help="Project directory (default: this one)")
        target.add_argument("--ledger", help="Override the ledger file path")
        target.add_argument("--json", action="store_true", help="Force JSON output")

    index = sub.add_parser("index", help="Fold new transcripts into the ledger")
    common(index)
    index.add_argument("--transcript", help="Index one transcript rather than the project")
    index.add_argument("--limit", type=int, default=None,
                       help="Only consider this many recent transcripts")
    index.add_argument("--force", action="store_true",
                       help="Re-fold transcripts even if unchanged")
    index.add_argument("--quiet", action="store_true", help="Print nothing on success")
    index.set_defaults(func=cmd_index)

    top = sub.add_parser("top", help="Ranked rows worth surfacing")
    common(top)
    top.add_argument("--limit", type=int, default=20, help="Max rows to return")
    top.add_argument("--min-sessions", type=int, default=ledger_lib.MIN_SESSIONS,
                     dest="min_sessions", help="Distinct-session gate")
    top.add_argument("--kind", action="append", help="Restrict to a kind (repeatable)")
    top.add_argument("--include-resolved", action="store_true", dest="include_resolved",
                     help="Include rows a fix has already been applied to")
    top.add_argument("--text", action="store_true", help="Human-readable table")
    top.set_defaults(func=cmd_top)

    check = sub.add_parser("check", help="Nudge signal: did anything cross the gate?")
    common(check)
    check.add_argument("--transcript", help="Check one transcript rather than the newest")
    check.add_argument("--min-sessions", type=int, default=ledger_lib.MIN_SESSIONS,
                       dest="min_sessions", help="Distinct-session gate")
    check.set_defaults(func=cmd_check)

    verify = sub.add_parser("verify", help="Applied fixes that did not land")
    common(verify)
    verify.add_argument("--text", action="store_true", help="Human-readable lines")
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show", help="Everything held about one row")
    common(show)
    show.add_argument("id", help="Row id, or a unique prefix of one")
    show.set_defaults(func=cmd_show)

    return parser


def main():
    args = build_parser().parse_args()
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
