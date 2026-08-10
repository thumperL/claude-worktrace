#!/usr/bin/env python3
"""
ledger.py — The friction ledger: rows that outlive the session that produced them.

A row is one thing this project has made you pay to learn more than once. Rows
accrue occurrences across sessions, and the gate for proposing a fix is DISTINCT
sessions rather than raw count: something re-derived twice in one afternoon is
one confused afternoon, and the same thing re-derived in four separate sessions
is a fact the setup does not hold.

DERIVED, NEVER AUTHORITATIVE

The transcripts under ~/.claude/projects are the only source of truth. This file
is a cache of what has already been read out of them, and it can be deleted at
any time and rebuilt with an identical result. One field breaks that rule:
`resolution`, which records a human decision and therefore cannot be recomputed.
That single exception is why this is readable JSON a human can repair rather than
an opaque cache, and why a file that fails to load is moved aside rather than
overwritten -- see `load()`.

IDEMPOTENCE

Transcripts grow while a session is live, so the same path gets folded in more
than once. Every occurrence records the file it came from, and re-folding a path
drops that path's previous occurrences first. Re-indexing is therefore safe to
run as often as you like, which is what lets it sit on SessionEnd unconditionally.

Python 3.9 compatible.
"""

import hashlib
import json
import os
import sys
from datetime import datetime


SCHEMA_VERSION = 1

# A row is not proposable below this. See the module docstring. Read inside the
# methods rather than bound as a default argument, since a default captures the
# value at import and would make this constant silently unchangeable at runtime.
MIN_SESSIONS = 2

KIND_FAILED_RETRY = "failed_retry"
KIND_REDISCOVERY = "rediscovery"
KIND_CORRECTION = "correction"
KIND_CIRCUMVENTION = "circumvention"

# Ordered by how far the evidence behind the kind can be trusted. The skill
# reports this, so a row never arrives without the basis for believing it.
KIND_CONFIDENCE = {
    KIND_FAILED_RETRY: "certain",
    KIND_REDISCOVERY: "certain",
    KIND_CIRCUMVENTION: "certain",
    KIND_CORRECTION: "certain",
}

MAX_EVIDENCE = 3
MAX_EVIDENCE_CHARS = 400


_INSTANT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


def _instant(value):
    """ISO-8601 UTC to a comparable datetime, tolerant of missing sub-seconds.

    Deliberately NOT transcript.parse_timestamp, which looks like a duplicate of
    this and is not. That one preserves the zone, so it returns an aware
    datetime for `...Z` and a naive one for a stamp without a zone, and
    comparing the two raises TypeError. This function is only ever used to order
    occurrences against each other, so it drops the zone and always returns
    naive UTC, which cannot raise whatever mixture the transcripts hold.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    text = text.split("+")[0]
    for fmt in _INSTANT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def row_id(kind, key):
    """Stable across runs and machines, so a row survives a rebuild."""
    digest = hashlib.sha1(("%s\0%s" % (kind, key)).encode("utf-8")).hexdigest()
    return "%s-%s" % (kind[:4], digest[:10])


class Row(object):
    """One recurring friction, plus what was decided about it."""

    def __init__(self, kind, key, tool_name=None):
        self.kind = kind
        self.key = key
        self.tool_name = tool_name
        self.id = row_id(kind, key)
        self.occurrences = []
        self.evidence = []
        self.resolution = None

    # --- accretion --------------------------------------------------------

    def add(self, occurrence, evidence=None):
        self.occurrences.append(occurrence)
        if evidence:
            self._remember(evidence)

    def _remember(self, text):
        """Raw excerpts, capped. Judging happens at query time, so this is kept."""
        text = text[:MAX_EVIDENCE_CHARS]
        if text in self.evidence:
            return
        self.evidence.insert(0, text)
        del self.evidence[MAX_EVIDENCE:]

    def drop_source(self, source):
        self.occurrences = [o for o in self.occurrences if o.get("source") != source]

    # --- derived ----------------------------------------------------------

    @property
    def sessions(self):
        return len(set(o.get("session_id") for o in self.occurrences
                       if o.get("session_id")))

    @property
    def total(self):
        return len(self.occurrences)

    @property
    def cost_calls(self):
        return sum(o.get("cost_calls") or 0 for o in self.occurrences)

    @property
    def cost_seconds(self):
        return sum(o.get("cost_seconds") or 0 for o in self.occurrences)

    @property
    def first_seen(self):
        stamps = sorted(o["timestamp"] for o in self.occurrences if o.get("timestamp"))
        return stamps[0] if stamps else None

    @property
    def last_seen(self):
        stamps = sorted(o["timestamp"] for o in self.occurrences if o.get("timestamp"))
        return stamps[-1] if stamps else None

    @property
    def confidence(self):
        return KIND_CONFIDENCE.get(self.kind, "weak")

    @property
    def proposable(self):
        return self.sessions >= MIN_SESSIONS and self.resolution is None

    def occurrences_since(self, timestamp):
        """What `verify` runs on: a fix that worked leaves this empty.

        Compared as instants, not as strings. Transcripts stamp
        `...T10:00:00.000Z` and the applied stamp is `...T10:00:00Z`, and `.`
        sorts before `Z`, so a string comparison reads a later occurrence as
        earlier and reports a fix as having landed when it did not.
        """
        cutoff = _instant(timestamp)
        if cutoff is None:
            return []
        out = []
        for occurrence in self.occurrences:
            when = _instant(occurrence.get("timestamp"))
            if when is not None and when > cutoff:
                out.append(occurrence)
        return out

    # --- serialisation ----------------------------------------------------

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key,
            "tool_name": self.tool_name,
            "occurrences": self.occurrences,
            "evidence": self.evidence,
            "resolution": self.resolution,
        }

    def summary(self):
        """What the skill reads. Derived fields resolved, raw occurrences dropped."""
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key,
            "tool_name": self.tool_name,
            "sessions": self.sessions,
            "total": self.total,
            "cost_calls": self.cost_calls,
            "cost_seconds": round(self.cost_seconds, 1),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data):
        row = cls(data.get("kind"), data.get("key"), data.get("tool_name"))
        row.id = data.get("id") or row.id
        row.occurrences = data.get("occurrences") or []
        row.evidence = data.get("evidence") or []
        row.resolution = data.get("resolution")
        return row


class Ledger(object):
    """Rows for one project, plus which transcripts have already been folded in."""

    def __init__(self, path=None):
        self.path = path
        self.rows = {}
        self.indexed = {}

    # --- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path):
        """Read the ledger, or return an empty one having preserved the old file.

        An unreadable ledger must never read as an EMPTY ledger and nothing
        else. The hook folds and saves on every session end, so a file that
        silently fails to parse is silently overwritten within one session, and
        `resolution` -- the applied stamps and the decline reasons, the only
        thing here that cannot be recomputed -- goes with it. Calling this file
        human-repairable means nothing if it is destroyed before anyone knows.

        So a load that fails for any reason other than "not there" moves the
        file aside first.
        """
        ledger = cls(path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (IOError, OSError):
            return ledger              # absent or unreadable: nothing to preserve
        except ValueError:
            _quarantine(path, "could not be parsed as JSON")
            return ledger
        if not isinstance(data, dict):
            _quarantine(path, "is not a JSON object")
            return ledger
        # A schema bump discards the cache rather than migrating it, since
        # everything except `resolution` is recomputable. The resolutions still
        # deserve preserving, so this goes through the same path.
        if data.get("version") != SCHEMA_VERSION:
            _quarantine(path, "has schema version %r, expected %d"
                        % (data.get("version"), SCHEMA_VERSION))
            return ledger
        ledger.indexed = data.get("indexed") or {}
        for payload in (data.get("rows") or {}).values():
            row = Row.from_dict(payload)
            ledger.rows[row.id] = row
        return ledger

    def save(self, path=None):
        path = path or self.path
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        payload = {
            "version": SCHEMA_VERSION,
            "indexed": self.indexed,
            "rows": dict((rid, row.to_dict()) for rid, row in self.rows.items()),
        }
        # Written on every session end, so a partial write must never be the
        # thing left on disk. The temp name carries the pid because two hooks
        # can run for the same project at once; a shared name means one clobbers
        # the other's partial file and os.replace can fail outright.
        #
        # This does not make concurrent writes safe, and is not trying to: the
        # last writer still wins and can drop the other's rows. Those are
        # derived and come back on the next index. `resolution` is the only
        # thing that would not, and it is written by the skill on the main
        # thread, never by two hooks at once.
        temporary = "%s.%d.tmp" % (path, os.getpid())
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(temporary, path)

    # --- folding ----------------------------------------------------------

    def needs_index(self, path):
        """True when this transcript has changed since it was last folded in."""
        try:
            stat = os.stat(path)
        except OSError:
            return False
        seen = self.indexed.get(path)
        if not seen:
            return True
        return seen.get("size") != stat.st_size or seen.get("mtime") != stat.st_mtime

    def fold(self, path, events):
        """Replace this transcript's contribution with `events`."""
        for row in self.rows.values():
            row.drop_source(path)

        for event in events:
            row = self._row_for(event)
            row.add({
                "session_id": event.get("session_id"),
                "timestamp": event.get("timestamp"),
                "cost_calls": event.get("cost_calls"),
                "cost_seconds": event.get("cost_seconds"),
                "source": path,
            }, evidence=event.get("evidence"))

        try:
            stat = os.stat(path)
            self.indexed[path] = {"size": stat.st_size, "mtime": stat.st_mtime}
        except OSError:
            pass

        self._prune()

    def _row_for(self, event):
        rid = row_id(event["kind"], event["key"])
        if rid not in self.rows:
            self.rows[rid] = Row(event["kind"], event["key"], event.get("tool_name"))
        return self.rows[rid]

    def _prune(self):
        """Drop rows that no transcript backs any more, unless a human touched them."""
        for rid in list(self.rows):
            row = self.rows[rid]
            if not row.occurrences and row.resolution is None:
                del self.rows[rid]

    # --- querying ---------------------------------------------------------

    def ranked(self, min_sessions=None, kinds=None, include_resolved=False):
        """Rows worth surfacing, most expensive first.

        Ranked by distinct sessions and then by calls burned. Frequency ranks
        what to LOOK at; it never says what is safe, and nothing here decides
        that. See lib/safety.py.
        """
        min_sessions = MIN_SESSIONS if min_sessions is None else min_sessions
        out = []
        for row in self.rows.values():
            if kinds and row.kind not in kinds:
                continue
            if row.resolution is not None and not include_resolved:
                continue
            if row.sessions < min_sessions:
                continue
            out.append(row)
        out.sort(key=lambda r: (-r.sessions, -r.cost_calls, -r.total, r.key))
        return out

    def proposable_ids(self, min_sessions=None):
        """Rows past the gate. The nudge fires on this set gaining a member."""
        min_sessions = MIN_SESSIONS if min_sessions is None else min_sessions
        return set(row.id for row in self.ranked(min_sessions=min_sessions))

    def below_gate(self, min_sessions=None):
        """Seen once. Reported as a count so the ranking is not padded with them."""
        min_sessions = MIN_SESSIONS if min_sessions is None else min_sessions
        return len([r for r in self.rows.values()
                    if r.resolution is None and r.sessions < min_sessions])

    def resolve(self, row_identifier, target, applied_at, text=None,
                destination=None):
        """Stamp a row as acted on. It keeps accruing, which is the point.

        `applied_at` is passed in rather than read from a clock, so this module
        stays free of one and a stamp can be asserted against a fixed value.
        """
        row = self.get(row_identifier)
        if row is None:
            return None
        row.resolution = {
            "target": target,
            "text": text,
            "destination": destination,
            "applied_at": applied_at,
        }
        return row

    def decline(self, row_identifier, reason, declined_at):
        """Record a no, with the reason. Re-proposing a declined row is the
        fastest way to make any of this unwelcome, so the reason is not
        optional."""
        row = self.get(row_identifier)
        if row is None:
            return None
        row.resolution = {"declined_at": declined_at, "reason": reason}
        return row

    def unverified(self):
        """Applied rows that have kept happening. The fix did not land."""
        out = []
        for row in self.rows.values():
            applied = (row.resolution or {}).get("applied_at")
            if not applied:
                continue
            since = row.occurrences_since(applied)
            if since:
                out.append((row, len(since)))
        out.sort(key=lambda pair: -pair[1])
        return out

    def get(self, row_identifier):
        if row_identifier in self.rows:
            return self.rows[row_identifier]
        matches = [r for r in self.rows.values()
                   if r.id.startswith(row_identifier)]
        return matches[0] if len(matches) == 1 else None

    def stats(self, min_sessions=None):
        min_sessions = MIN_SESSIONS if min_sessions is None else min_sessions
        return {
            "rows": len(self.rows),
            "min_sessions": min_sessions,
            "proposable": len(self.ranked(min_sessions=min_sessions)),
            "below_gate": self.below_gate(min_sessions),
            "resolved": len([r for r in self.rows.values() if r.resolution]),
            "transcripts_indexed": len(self.indexed),
        }


def _quarantine(path, reason):
    """Move an unusable ledger aside so its human decisions survive.

    An existing quarantine file is never replaced. The first one is the closest
    to the last good state, and a second failure overwriting it would defeat the
    whole point.

    Best effort, and deliberately so: this runs inside a session-end hook, and
    failing to preserve a file is not a reason to break the session.
    """
    kept = "%s.corrupt" % path
    try:
        # Never overwrite an earlier rescue, and never leave the bad file where
        # it is either: the next fold saves straight over it, so "left in place"
        # means "destroyed shortly afterwards". Take a new name instead.
        if os.path.exists(kept):
            nth = 1
            while os.path.exists("%s.%d" % (kept, nth)):
                nth += 1
            kept = "%s.%d" % (kept, nth)
        os.rename(path, kept)
        note = "moved to %s" % kept
        sys.stderr.write(
            "[friction-ledger] %s %s. Starting a new ledger; %s. "
            "Any applied or declined decisions are in that file.\n"
            % (path, reason, note))
    except OSError:
        pass


def ledger_path(cwd, base=None):
    """One ledger per project: the fix destination differs by scope.

    The encoding matches transcript.encode_project_dir by coincidence rather
    than by contract. That one has to track however Claude Code names its own
    project directories; this one names a directory we own. Sharing a helper
    would tie our filenames to someone else's format decision, so the two stay
    separate on purpose.
    """
    base = base or os.path.join(os.path.expanduser("~"), ".claude", "friction-ledger")
    encoded = str(cwd or "").rstrip("/").replace("/", "-") or "unknown"
    return os.path.join(base, "%s.json" % encoded)
