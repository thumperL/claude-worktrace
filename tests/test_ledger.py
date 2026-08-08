#!/usr/bin/env python3
"""Unit tests for scripts/lib/ledger.py and scripts/lib/detectors.py.

Two properties carry this module and both are asserted directly rather than
inferred from a happy path:

  Idempotence. Transcripts grow while a session is live, so the same file is
  folded in repeatedly. Re-folding must replace that file's contribution, not
  add to it, or every long session inflates its own rows.

  The distinct-session gate. A row is proposable on sessions, never on raw
  count, so a hundred occurrences inside one session must stay below the gate.

Run: python3 tests/test_ledger.py
"""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from lib import detectors as D  # noqa: E402
from lib import ledger as L  # noqa: E402
from lib import transcript as T  # noqa: E402

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample_transcript.jsonl")

FAILURES = []


def check(name, actual, expected):
    if actual != expected:
        FAILURES.append("%s: expected %r, got %r" % (name, expected, actual))


def check_true(name, value):
    if not value:
        FAILURES.append("%s: expected truthy, got %r" % (name, value))


def event(kind, key, session_id, timestamp="2026-08-01T10:00:00.000Z",
          cost_calls=1, evidence="e"):
    return {"kind": kind, "key": key, "tool_name": "Bash",
            "session_id": session_id, "timestamp": timestamp,
            "cost_calls": cost_calls, "cost_seconds": 1.0, "evidence": evidence}


# --- Detectors --------------------------------------------------------------

def test_failed_retry_from_the_fixture():
    """`rg TODO src/` errors, is run again, and errors again."""
    session = T.parse_transcript(FIXTURE)
    events = D.detect_failed_retry(session)
    check("one retry event", len(events), 1)
    check("keyed on the signature", events[0]["key"], "Bash(rg)")
    check("cost is the distance to the retry", events[0]["cost_calls"], 1)
    check_true("error text is kept as evidence", "error:" in events[0]["evidence"])


def test_an_error_with_no_retry_is_not_a_row():
    """Giving up is a different finding, and not this one."""
    session = T.parse_transcript(FIXTURE)
    signatures = [e["key"] for e in D.detect_failed_retry(session)]
    check("rejected push is not a retry", "Bash(git push)" in signatures, False)


def test_rediscovery_is_once_per_key_per_session():
    session = T.parse_transcript(FIXTURE)
    events = D.detect_rediscovery(session)
    keys = [e["key"] for e in events]
    check("one row for the changelog read", len(keys), 1)
    check_true("keyed by tool and path", keys[0].startswith("Read("))


def test_rediscovery_excludes_files_that_were_edited():
    """Reading a file you then change is the work, not a lookup."""
    session = T.parse_transcript(FIXTURE)
    edited = [i.key_input for i in session.invocations
              if i.tool_name in T.EDIT_TOOLS]
    keys = [e["key"] for e in D.detect_rediscovery(session)]
    for path in edited:
        check("edited file excluded: %s" % path,
              any(path in key for key in keys), False)


def test_retry_distance_is_bounded():
    """The same command far later is different work, not a recovery.

    Asserting that an emitted distance is under the bound is vacuous, since
    raising the bound keeps it true. So this constructs the pair the bound
    exists to reject, and the adjacent pair it must still accept.
    """
    session = FakeSession().call("Bash", "npm test", error=True)
    for i in range(D.MAX_RETRY_DISTANCE + 2):
        session.call("Bash", "echo %d" % i)
    session.call("Bash", "npm test")
    check("too far apart to be a recovery", D.detect_failed_retry(session), [])

    near = FakeSession().call("Bash", "npm test", error=True).call("Bash", "npm test")
    check("adjacent still pairs", len(D.detect_failed_retry(near)), 1)


def test_the_distance_bounds_stay_small():
    """The tests above scale with these constants, so they verify the bound is
    enforced without noticing if it is raised to something meaningless. Both are
    proximity heuristics: "these two calls are related because they are near each
    other". Widen either to session scale and the detector starts pairing
    unrelated work, silently and with no test failing."""
    check_true("retry bound is a proximity heuristic", 2 <= D.MAX_RETRY_DISTANCE <= 25)
    check_true("followup bound is a proximity heuristic",
               2 <= D.MAX_FOLLOWUP_DISTANCE <= 25)


def test_a_long_run_of_retries_exhausts_the_followup_window():
    """The only case MAX_FOLLOWUP_DISTANCE actually gates.

    The scan breaks at the first candidate with a different signature, so the
    window is irrelevant unless the blocked signature repeats past it and pushes
    the first different call out of range.
    """
    session = FakeSession().call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
    for _ in range(D.MAX_FOLLOWUP_DISTANCE + 2):
        session.call("Read", "/x/.env")
    session.call("Bash", "cat /x/.env")
    check("the reroute is out of range", D.detect_blocked_followups(session), [])


# --- Blocked calls, and what happened next ---------------------------------

class FakeSession(object):
    """A session assembled call by call, so a scenario reads as its own story."""

    def __init__(self, session_id="s1"):
        self.session_id = session_id
        self.invocations = []
        self.prompt_boundaries = []

    def call(self, tool, key, blocked=None, error=False):
        inv = T.ToolInvocation("id%d" % len(self.invocations), tool,
                               _input_for(tool, key), "2026-08-01T10:00:00.000Z")
        inv.is_error = error
        if blocked:
            inv.approval = blocked
        self.invocations.append(inv)
        return self

    def user_says(self):
        self.prompt_boundaries.append(len(self.invocations))
        return self


def _input_for(tool, key):
    if tool == "Bash":
        return {"command": key}
    return {"file_path": key}


REDIRECT_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures",
                                 "redirected_after_block.jsonl")


def test_the_user_message_guard_works_through_the_REAL_parser():
    """The FakeSession tests below set prompt_boundaries by hand, so they check
    the logic while never exercising the data feeding it. That is how this guard
    shipped dead: boundaries were counted against session.invocations, which is
    empty until the parse finishes, so every boundary was 0 and nothing was ever
    disqualified. Only a parsed transcript catches that."""
    session = T.parse_transcript(REDIRECT_FIXTURE)
    check("the boundary lands after the blocked call", session.prompt_boundaries, [1])
    check("so the redirect is not an accusation",
          D.detect_blocked_followups(session), [])


def test_a_parsed_transcript_still_reports_a_genuine_circumvention():
    """The same fixture minus the user message must still be caught, or the
    guard above is just suppressing everything."""
    session = T.parse_transcript(REDIRECT_FIXTURE)
    session.prompt_boundaries = []
    check("caught when nobody redirected it",
          [e["kind"] for e in D.detect_blocked_followups(session)],
          [L.KIND_CIRCUMVENTION])


def test_a_shared_branch_name_is_not_a_shared_file():
    """`git checkout feature/foo` then `git branch --list feature/foo` share a
    slash-bearing token that is not a path. Accusing Claude of evading a block
    over a branch name is the worst output this detector can produce."""
    session = (FakeSession()
               .call("Bash", "git checkout feature/foo", blocked=T.APPROVAL_REJECTED)
               .call("Bash", "git branch --list feature/foo"))
    kinds = [e["kind"] for e in D.detect_blocked_followups(session)]
    check("correction at most, never circumvention", kinds, [L.KIND_CORRECTION])


def test_reaching_the_same_target_another_way_is_circumvention():
    session = (FakeSession()
               .call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
               .call("Bash", "cat /x/.env"))
    events = D.detect_blocked_followups(session)
    check("one event", len(events), 1)
    check("classified as circumvention", events[0]["kind"], L.KIND_CIRCUMVENTION)
    check_true("names the target", "/x/.env" in events[0]["key"])
    check_true("evidence carries both calls",
               "blocked:" in events[0]["evidence"] and "cat /x/.env" in events[0]["evidence"])


def test_the_safety_classifier_annotates_what_was_reached():
    session = (FakeSession()
               .call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
               .call("Bash", "cat /x/.env"))
    evidence = D.detect_blocked_followups(session)[0]["evidence"]
    check_true("hazard named in the evidence", "credential" in evidence)


def test_a_different_target_is_only_a_correction():
    session = (FakeSession()
               .call("Bash", "git push --force origin main", blocked=T.APPROVAL_REJECTED)
               .call("Bash", "git status"))
    events = D.detect_blocked_followups(session)
    check("one event", len(events), 1)
    check("correction, not circumvention", events[0]["kind"], L.KIND_CORRECTION)


def test_a_user_message_in_between_disqualifies_it():
    """Being told "just edit it directly" is instruction, not evasion. Accusing
    Claude of routing around a block it was redirected past is the worst
    false positive this detector can produce."""
    session = (FakeSession()
               .call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
               .user_says()
               .call("Bash", "cat /x/.env"))
    check("nothing reported", D.detect_blocked_followups(session), [])


def test_retrying_the_same_call_is_not_circumvention():
    session = (FakeSession()
               .call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
               .call("Read", "/x/.env")
               .call("Bash", "cat /x/.env"))
    events = D.detect_blocked_followups(session)
    check("the retry is skipped, the reroute is caught",
          [e["kind"] for e in events], [L.KIND_CIRCUMVENTION])


def test_only_the_first_followup_is_reported():
    """One block produces one finding, not one per subsequent call."""
    session = (FakeSession()
               .call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
               .call("Bash", "cat /x/.env")
               .call("Bash", "head /x/.env"))
    check("one event", len(D.detect_blocked_followups(session)), 1)


def test_a_distant_same_target_call_is_not_upgraded_to_circumvention():
    """Unrelated work first, then the same file much later. The immediate
    different approach is a correction, and the far-away read is just work."""
    session = FakeSession().call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
    for i in range(D.MAX_FOLLOWUP_DISTANCE + 2):
        session.call("Bash", "echo %d" % i)
    session.call("Bash", "cat /x/.env")
    kinds = [e["kind"] for e in D.detect_blocked_followups(session)]
    check("correction only", kinds, [L.KIND_CORRECTION])


def test_an_unblocked_call_produces_nothing():
    session = FakeSession().call("Read", "/x/.env").call("Bash", "cat /x/.env")
    check("no block, no finding", D.detect_blocked_followups(session), [])


def test_interruption_counts_as_blocked():
    session = (FakeSession()
               .call("Read", "/x/.env", blocked=T.APPROVAL_INTERRUPTED)
               .call("Bash", "cat /x/.env"))
    check("interrupt is a block too",
          [e["kind"] for e in D.detect_blocked_followups(session)],
          [L.KIND_CIRCUMVENTION])


# --- The gate ---------------------------------------------------------------

def test_one_session_never_reaches_the_gate():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(npm test)", "s1")
                               for _ in range(100)])
    row = list(ledger.rows.values())[0]
    check("100 occurrences", row.total, 100)
    check("but one session", row.sessions, 1)
    check("so nothing is proposable", len(ledger.ranked()), 0)
    check("and it is counted below the gate", ledger.below_gate(), 1)


def test_two_sessions_reach_the_gate():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(npm test)", "s1")])
    ledger.fold("/t/b.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(npm test)", "s2")])
    check("one row", len(ledger.rows), 1)
    check("two sessions", list(ledger.rows.values())[0].sessions, 2)
    check("proposable", len(ledger.ranked()), 1)


# --- Idempotence ------------------------------------------------------------

def test_refolding_a_transcript_replaces_it():
    ledger = L.Ledger()
    for _ in range(3):
        ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1"),
                                   event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    row = list(ledger.rows.values())[0]
    check("occurrences did not accumulate across re-folds", row.total, 2)


def test_refolding_does_not_disturb_other_transcripts():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    ledger.fold("/t/b.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s2")])
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    check("both sessions survive", list(ledger.rows.values())[0].sessions, 2)


def test_a_row_that_loses_all_evidence_is_pruned():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    ledger.fold("/t/a.jsonl", [])
    check("row gone", len(ledger.rows), 0)


def test_a_resolved_row_survives_losing_its_evidence():
    """Otherwise the decline is forgotten and the row comes straight back."""
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    list(ledger.rows.values())[0].resolution = {"declined_at": "2026-08-01",
                                                "reason": "intentional"}
    ledger.fold("/t/a.jsonl", [])
    check("row kept", len(ledger.rows), 1)


# --- Resolution and verify --------------------------------------------------

def test_resolved_rows_leave_the_ranking():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    ledger.fold("/t/b.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s2")])
    check("proposable first", len(ledger.ranked()), 1)
    list(ledger.rows.values())[0].resolution = {"target": "claude_md",
                                                "applied_at": "2026-08-02T00:00:00Z"}
    check("gone once resolved", len(ledger.ranked()), 0)
    check("unless asked for", len(ledger.ranked(include_resolved=True)), 1)


def test_resolve_stamps_without_removing_the_row():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_CORRECTION, "Bash(x)", "s1")])
    row = list(ledger.rows.values())[0]
    stamped = ledger.resolve(row.id, "claude_md", "2026-08-02T00:00:00Z",
                             text="Prefer X")
    check("returns the row", stamped.id, row.id)
    check("still holds its occurrences", row.total, 1)
    check("stamped", row.resolution["applied_at"], "2026-08-02T00:00:00Z")


def test_decline_records_the_reason():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_CORRECTION, "Bash(x)", "s1")])
    row = list(ledger.rows.values())[0]
    ledger.decline(row.id, "deliberate, I want the prompt", "2026-08-02T00:00:00Z")
    check("reason kept", row.resolution["reason"], "deliberate, I want the prompt")
    check("no longer proposed", len(ledger.ranked(min_sessions=1)), 0)


def test_resolving_an_unknown_row_is_a_no_op():
    ledger = L.Ledger()
    check("resolve", ledger.resolve("nope", "claude_md", "2026-08-02T00:00:00Z"), None)
    check("decline", ledger.decline("nope", "why", "2026-08-02T00:00:00Z"), None)


def test_unverified_finds_fixes_that_did_not_land():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [
        event(L.KIND_CORRECTION, "Bash(landed)", "s1", timestamp="2026-08-01T00:00:00Z"),
        event(L.KIND_CORRECTION, "Bash(didnt)", "s1", timestamp="2026-08-01T00:00:00Z"),
    ])
    ledger.fold("/t/b.jsonl", [
        event(L.KIND_CORRECTION, "Bash(didnt)", "s2", timestamp="2026-08-05T00:00:00Z"),
    ])
    for row in ledger.rows.values():
        ledger.resolve(row.id, "claude_md", "2026-08-03T00:00:00Z")

    failures = ledger.unverified()
    check("one fix did not land", [r.key for r, _ in failures], ["Bash(didnt)"])
    check("with a count", failures[0][1], 1)


def test_a_declined_row_is_never_reported_as_unverified():
    """It was never applied, so there is nothing for it to have failed at."""
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_CORRECTION, "Bash(x)", "s1")])
    row = list(ledger.rows.values())[0]
    ledger.decline(row.id, "on purpose", "2026-08-01T00:00:00Z")
    ledger.fold("/t/b.jsonl", [event(L.KIND_CORRECTION, "Bash(x)", "s2",
                                     timestamp="2026-08-09T00:00:00Z")])
    check("not a failed fix", ledger.unverified(), [])


def test_occurrences_since_is_what_proves_a_fix():
    row = L.Row(L.KIND_FAILED_RETRY, "Bash(x)")
    row.add({"session_id": "s1", "timestamp": "2026-08-01T00:00:00Z"})
    row.add({"session_id": "s2", "timestamp": "2026-08-05T00:00:00Z"})
    check("one occurrence after the fix", len(row.occurrences_since("2026-08-03T00:00:00Z")), 1)
    check("none after the last", len(row.occurrences_since("2026-08-06T00:00:00Z")), 0)


# --- Persistence ------------------------------------------------------------

def test_round_trip_preserves_rows_and_resolutions():
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "nested", "ledger.json")
        ledger = L.Ledger(path)
        ledger.fold("/t/a.jsonl", [event(L.KIND_REDISCOVERY, "Read(/x/y.md)", "s1")])
        ledger.fold("/t/b.jsonl", [event(L.KIND_REDISCOVERY, "Read(/x/y.md)", "s2")])
        list(ledger.rows.values())[0].resolution = {"declined_at": "2026-08-01",
                                                    "reason": "on purpose"}
        ledger.save()

        reloaded = L.Ledger.load(path)
        check("rows survive", len(reloaded.rows), 1)
        row = list(reloaded.rows.values())[0]
        check("sessions survive", row.sessions, 2)
        check("evidence survives", len(row.evidence) > 0, True)
        check("the decline reason survives", row.resolution["reason"], "on purpose")
        check("a transcript that does not exist is not marked indexed",
              len(reloaded.indexed), 0)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_schema_bump_discards_the_cache_rather_than_migrating():
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "ledger.json")
        with open(path, "w") as handle:
            handle.write('{"version": 999, "rows": {"x": {"kind": "k", "key": "v"}}}')
        check("nothing loaded", len(L.Ledger.load(path).rows), 0)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_corrupt_ledger_reads_as_empty_rather_than_raising():
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "ledger.json")
        with open(path, "w") as handle:
            handle.write("{ this is not json")
        check("empty", len(L.Ledger.load(path).rows), 0)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_corrupt_ledger_is_preserved_before_it_is_replaced():
    """The hook folds and saves on every session end, so a file that silently
    fails to parse is silently overwritten within one session, taking every
    human decision with it. Reading as empty is fine; being destroyed is not."""
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "ledger.json")
        ledger = L.Ledger(path)
        ledger.fold("/t/a.jsonl", [event(L.KIND_CORRECTION, "Bash(x)", "s1")])
        ledger.decline(list(ledger.rows)[0], "deliberate", "2026-08-01T00:00:00Z")
        ledger.save()

        good = open(path).read()
        with open(path, "w") as handle:                # truncated mid-write
            handle.write(good[:len(good) - 3])

        reloaded = L.Ledger.load(path)
        reloaded.fold("/t/a.jsonl", [])
        reloaded.save()

        kept = path + ".corrupt"
        check("the old file was preserved", os.path.exists(kept), True)
        check_true("with its human decision intact", "deliberate" in open(kept).read())
        check("the live ledger started fresh", len(L.Ledger.load(path).rows), 0)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_second_corruption_takes_a_new_name_rather_than_being_left_to_die():
    """Not overwriting the first rescue is right. Leaving the second bad file
    where it is, is not: the next fold saves straight over it, so "left in
    place" means "destroyed a moment later"."""
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "ledger.json")
        for text in ("first, and closer to good", "second, holding newer decisions"):
            with open(path, "w") as handle:
                handle.write(text)
            L.Ledger.load(path)
        check("first rescue untouched",
              open(path + ".corrupt").read(), "first, and closer to good")
        check("second rescued too, under its own name",
              open(path + ".corrupt.1").read(), "second, holding newer decisions")
        check("and the bad file is no longer in the way", os.path.exists(path), False)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_occurrences_are_compared_as_instants_not_strings():
    """Transcripts stamp `...T10:00:00.000Z`; the applied stamp is
    `...T10:00:00Z`. `.` sorts before `Z`, so a string comparison reads a later
    occurrence as earlier and reports a failed fix as having landed."""
    row = L.Row(L.KIND_CORRECTION, "Bash(x)")
    row.add({"session_id": "s1", "timestamp": "2026-08-01T10:00:00.001Z"})
    check("sub-second occurrence counts as later",
          len(row.occurrences_since("2026-08-01T10:00:00Z")), 1)
    earlier = L.Row(L.KIND_CORRECTION, "Bash(x)")
    earlier.add({"session_id": "s1", "timestamp": "2026-07-01T00:00:00.000Z"})
    check("a genuinely earlier one is still excluded",
          earlier.occurrences_since("2026-08-01T10:00:00Z"), [])


def test_a_wrapper_around_the_blocked_path_is_still_circumvention():
    """The binary gate stops branch names being read as files, and would also
    hide every interpreter and wrapper. A blocked target is a known path, so
    looking for it verbatim in the next command costs no false positives."""
    for alternate in ('python -c "open(\'/x/.env\').read()"',
                      'sh -c "cat /x/.env"',
                      "git show HEAD:/x/.env"):
        session = (FakeSession()
                   .call("Read", "/x/.env", blocked=T.APPROVAL_REJECTED)
                   .call("Bash", alternate))
        check("circumvention via %s" % alternate[:18],
              [e["kind"] for e in D.detect_blocked_followups(session)],
              [L.KIND_CIRCUMVENTION])


def test_a_second_corruption_does_not_clobber_the_first_rescue():
    """The first copy is closest to the last good state."""
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "ledger.json")
        kept = path + ".corrupt"
        with open(path, "w") as handle:
            handle.write("first, and closer to good")
        L.Ledger.load(path)
        with open(path, "w") as handle:
            handle.write("second, and worse")
        L.Ledger.load(path)
        check("the first rescue survived", open(kept).read(), "first, and closer to good")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_a_schema_mismatch_also_preserves_the_old_file():
    """Everything but `resolution` is recomputable, and that exception is the
    whole reason a version bump cannot just discard the file."""
    directory = tempfile.mkdtemp()
    try:
        path = os.path.join(directory, "ledger.json")
        with open(path, "w") as handle:
            handle.write('{"version": 999, "rows": {}}')
        L.Ledger.load(path)
        check("preserved", os.path.exists(path + ".corrupt"), True)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_needs_index_tracks_size_and_mtime():
    directory = tempfile.mkdtemp()
    try:
        transcript = os.path.join(directory, "t.jsonl")
        with open(transcript, "w") as handle:
            handle.write("{}\n")
        ledger = L.Ledger()
        check("unseen file needs indexing", ledger.needs_index(transcript), True)
        ledger.fold(transcript, [])
        check("seen file does not", ledger.needs_index(transcript), False)
        with open(transcript, "a") as handle:
            handle.write("{}\n")
        check("a grown transcript does again", ledger.needs_index(transcript), True)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# --- Ranking and identity ---------------------------------------------------

def test_ranking_is_sessions_then_calls():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [
        event(L.KIND_FAILED_RETRY, "Bash(cheap)", "s1", cost_calls=1),
        event(L.KIND_FAILED_RETRY, "Bash(dear)", "s1", cost_calls=50),
    ])
    ledger.fold("/t/b.jsonl", [
        event(L.KIND_FAILED_RETRY, "Bash(cheap)", "s2", cost_calls=1),
        event(L.KIND_FAILED_RETRY, "Bash(dear)", "s2", cost_calls=50),
    ])
    ledger.fold("/t/c.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(cheap)", "s3")])
    ranked = ledger.ranked()
    check("more sessions outranks more calls", ranked[0].key, "Bash(cheap)")
    check("then cost", ranked[1].key, "Bash(dear)")


def test_row_ids_are_stable_and_kind_scoped():
    check("stable", L.row_id("failed_retry", "Bash(x)"), L.row_id("failed_retry", "Bash(x)"))
    check("kind is part of identity",
          L.row_id("failed_retry", "Bash(x)") == L.row_id("rediscovery", "Bash(x)"), False)


def test_lookup_by_unique_prefix():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [event(L.KIND_FAILED_RETRY, "Bash(x)", "s1")])
    row = list(ledger.rows.values())[0]
    check("found by full id", ledger.get(row.id).id, row.id)
    check("found by prefix", ledger.get(row.id[:8]).id, row.id)
    check("unknown prefix returns nothing", ledger.get("zzzzzz"), None)


def test_evidence_is_capped():
    ledger = L.Ledger()
    ledger.fold("/t/a.jsonl", [
        event(L.KIND_FAILED_RETRY, "Bash(x)", "s1", evidence="unique-%d" % i)
        for i in range(10)])
    row = list(ledger.rows.values())[0]
    check("evidence capped", len(row.evidence), L.MAX_EVIDENCE)
    check("newest kept", row.evidence[0], "unique-9")


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
