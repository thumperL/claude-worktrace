# learning loop — design

**Date:** 2026-08-09
**Status:** Implemented

The single design document for how this plugin learns from sessions. It replaces
two earlier specs (a setup-tuning retro, and a standalone friction ledger), both
of which proposed a fourth skill. There is no fourth skill. What follows is the
shape that survived.

## The problem

Every Claude Code session writes a JSONL transcript and then forgets it. Three
skills already turn that into something durable:

| Skill | Question |
| --- | --- |
| `worklog-logging` | What did I do? |
| `worklog-analysis` | What did I do over time? |
| `self-improve` | How should Claude work with me? |

The third one has gone quiet, and the reason is structural rather than a bug. Its
gate is session-local: three corrections in one session, or a context checkpoint.
A preference expressed once per session across five sessions never fires. Its
evidence is the conversation in context, so it cannot span sessions or cite
anything. And nothing ever checks whether a saved preference changed Claude's
behaviour.

This design fixes those three things, and adds one detection kind that needs the
same machinery.

## What the transcript actually encodes

This drove most of the design, so it is worth stating precisely. Investigated
against real transcripts under `~/.claude/projects/*/`.

| Entry | Carries |
| --- | --- |
| `assistant` | `message.content[]` with `tool_use` blocks (id, name, input) |
| `user` | `message.content[]` with `tool_result` blocks (tool_use_id, content, is_error) |
| `permission-mode` | `permissionMode` only, no timestamp and no uuid |
| `system` | `subtype` in stop_hook_summary, turn_duration, local_command and others |

**There is no permission-decision field.** Nothing says a call was approved or
auto-allowed. What is available:

1. **Rejection, recorded.** The `tool_result` content is the sentinel
   `"The user doesn't want to proceed with this tool use…"` with `is_error: true`.
2. **Interruption, recorded.** A user entry with `"[Request interrupted by user
   for tool use]"` and an `interruptedMessageId` pointing at the assistant
   message that issued the call.
3. **Permission mode, recorded positionally.** `permission-mode` entries are
   appended in file order with no timestamp, so the mode for a call is the last
   such entry before it. Observed: `default`, `auto`, `plan`, `acceptEdits`,
   `bypassPermissions`. In `auto` and `bypassPermissions` no prompt is possible.
4. **Timing, recorded.** Assistant entry timestamp against the `tool_result`
   timestamp.

So approval is inferable only from timing. Measured across 25 real transcripts,
median tool latency is 0.1 to 1.2s with a thin tail above 10s, and the tail is
contaminated by `AskUserQuestion`, `ExitPlanMode`, `Agent` and network tools
whose latency is user think time or a child process.

`ToolInvocation.approval` is therefore one of `rejected`, `interrupted`,
`auto_allowed`, `likely_manual` or `unknown`, each carrying the evidence behind
it. The model never emits "approved", because the format cannot support the
claim.

**The consequence that shaped everything else.** 681 of 696 sampled calls ran in
`auto` mode. Anything built on approvals reads empty here, which is why this
design is not built on them. `failed_retry`, `rediscovery` and `circumvention`
are all just as visible when nothing can prompt.

## The ledger

One accretive store per project at
`~/.claude/friction-ledger/<encoded-cwd>.json`, using the same cwd encoding as
the existing memory directories.

```
id            stable hash of (kind, key)
kind          failed_retry | rediscovery | circumvention | correction | steer
key           the signature, path or target the events share
occurrences   [{session_id, timestamp, cost_calls, cost_seconds, source}]
evidence      raw excerpts, capped, retained for judging at query time
resolution    null
              | {target, text, destination, applied_at}
              | {declined_at, reason}
```

Three properties carry it.

**The gate is distinct sessions, never raw count.** Something re-derived twice in
one afternoon is one confused afternoon. The same thing in four separate sessions
is a fact the setup does not hold. A row is not proposable below two distinct
sessions, and the count of rows below the gate is reported rather than the rows
themselves.

**Folding is idempotent.** Transcripts grow while a session is live, so the same
path is folded repeatedly. Every occurrence records its source and re-folding a
path drops that path's previous contribution first. Without this, a long session
inflates its own rows and indexing could not sit on `SessionEnd` unconditionally.

**Derived, never authoritative.** The transcripts are the only source of truth.
Everything here is recomputable except `resolution`, which records a human
decision, and that single exception is why the file is readable JSON a human can
repair rather than an opaque cache. A schema mismatch discards the cache rather
than migrating it, since the cost is one re-index.

### Why `resolution` lives on the row

A fixed row is stamped, not deleted, and it keeps accruing. That makes one
question answerable that no report can answer: have there been occurrences since
`applied_at`?

Applied to preferences, this is the headline capability:

```
"Prefer subagents for research" — saved 2026-07-18.
3 occurrences since. The preference is not landing.
```

Preferences pile up in `CLAUDE.md` and nothing distinguishes the ones that
changed behaviour from the ones that are decoration. Nothing else in this space
asks. Declines are stamped the same way with the reason given, which turns "do
not re-propose a rejected item" into a property of the data rather than an
instruction the skill must remember.

## Detection

| Kind | Detected from | Confidence | Feeds |
| --- | --- | --- | --- |
| `failed_retry` | an error, then the same signature again within a bounded window | Certain | worklog |
| `rediscovery` | the same lookup key in separate sessions | Certain | worklog |
| `circumvention` | a blocked call, then another route to the same target | Certain | self-improve |
| `correction` | rejection sentinel or interrupt, then a changed approach | Certain | self-improve |
| `steer` | prose steering, model-detected | Inferred | self-improve |

Detectors are per-session by construction. Nothing in them knows a key has been
seen before, because the cross-session comparison lives in the ledger. A detector
that asked "has this recurred?" would need the whole history in scope and would
stop being testable against a single transcript.

`failed_retry` fires once per error that was followed by a retry; an error with
nothing after it is a failure that was abandoned, which is a different finding.
`rediscovery` fires once per distinct key per session, never once per read, and
excludes files that were also edited, because reading a file you then change is
the work itself.

### Circumvention

The one kind that is genuinely new, and the reason the safety classifier
survives.

When a call is denied or rejected, the model sometimes reaches the same target by
another route: `Read(/x/.env)` refused, then `Bash(cat /x/.env)`; a blocked
`rm -rf` followed by `find . -delete`. That is not a permission to widen. It is
behaviour to correct, and it is the deterministic counterpart to a prose steer.
A steer says "stop doing that". A circumvention row proves it happened, with both
calls attached.

Detection is a blocked call followed within a bounded window by a call with a
**different signature** reaching the **same target**, where target means the path
or resource rather than the tool.

The output is a tightening or a behavioural rule, never an allowance. This is the
opposite direction from everything the two superseded designs proposed.

## The safety model

`scripts/lib/safety.py`, as code rather than prose, so the model is told to call
it instead of reasoning about a command itself.

It was derived from this machine's own permission rules, 60 allow entries and 15
deny, rather than from a generic risk taxonomy. Two observations about that file
shaped it:

- **The allow list is not "read-only commands".** It holds `npm install`,
  `git add`, `git stash` and `mkdir`, all mutations, while `git commit`,
  `git merge` and `git push` are absent despite being at least as frequent. The
  operative distinction is not read versus write but how far an effect reaches
  and what undoing it costs. Those are independent questions, so they are two
  axes rather than one scale.
- **The deny list is not a more extreme version of those axes.** It mixes egress,
  privilege, publication, destruction and credential reads. A secret read is
  local and trivially undoable, so both axes rate it harmless, yet it is denied.
  Hazards are therefore orthogonal flags that override the axes.

**A signature is a lossy grouping key, so guards written against it match
nothing.** Flags, arguments and redirects are stripped so variants group
together, and each is somewhere risk hides. `classify()` therefore takes the raw
command or path alongside the signature and runs the path, redirect and
destructive-flag checks against it. Commands whose risk lives entirely in an
argument, `cat` and `grep` and `cp`, are refused a binary-level verdict outright:
a rule quantifies over every future invocation while the evidence is a sample of
past ones, so no clean sample earns one.

In this design the tables answer "did this reach a hazard by another name" for
circumvention, rather than "what may we auto-allow". `proposed_rule()` and
`audit_allowlist()` have no caller, since nothing here proposes widening.

## Where judgement lives

**Deterministic on the write path, model on the read path.** The split is not
about difficulty. It is about who is watching when the answer is wrong.

Indexing runs on every session end, unattended, so it must be free, reliable and
rebuildable, and it stays code. Analysis runs when the user asks, with the user
present to catch a bad answer, and that is where a model call is affordable.

| Job | Judge |
| --- | --- |
| Parse, group, index, detect | code |
| Is this the same fact re-derived? | model |
| Is this row worth surfacing? | model |
| What exact preference line should be saved? | model |
| Did this circumvention reach a hazard? | code |
| Prose steer detection | model |

Prose steers must stay model-detected. "Use Hono not Express" has no structural
signal at all; it is an ordinary user message. `circumvention` and `correction`
are the certain, narrow subset and do not replace it.

## Safety: two checks, and only one is load-bearing

The judge reads transcript evidence: command strings, paths, error bodies. All of
it routinely contains text from repositories, fetched pages and tool output, so
all of it is attacker-influenceable. The outputs are standing instructions that
persist after the user stops watching.

**Incoming.** Narrow what the judge sees rather than trying to clean it.
Structured fields rather than free-text blobs, error bodies capped, framed
explicitly as untrusted. The judge is never asked whether something should be
permitted.

**Outgoing.** A per-item diff on every line that reaches `CLAUDE.md`, reviewed by
a human.

The two are deliberately asymmetric. Incoming reduces surface; it cannot
establish safety, because prompt injection is not reliably detectable by
inspection. They also fail differently, which is what makes stacking them worth
anything: incoming fails to recognise malicious text, outgoing fails only if a
human misreads a diff. Two model-based checks would share a failure mode.

Note that this outgoing check is weaker than the one an earlier design had, where
permission rule text could only come from `proposed_rule()` and model-generated
text structurally could not become a rule. Nothing proposes rules now, so the
boundary is a human reading prose. The per-item requirement on `CLAUDE.md`
therefore matters more here, not less.

**No subagent writes configuration.** The judging may run in a subagent, whose
context is full of untrusted evidence. If it could write, the injection path
would be complete. Subagents judge; proposals return to the main thread, and
every write happens there with the user present.

## Consent

Ceremony is set by what an output does once it exists, not by how hard it was to
produce.

| Output | What it does once written | Flow |
| --- | --- | --- |
| worklog entry | a record, changes no behaviour | silent |
| memory entry | recalled as context | batch confirm, diff shown |
| `CLAUDE.md` preference | loaded into every session | per item, diff shown |

`CLAUDE.md` is auto-loaded, so a line there is a standing instruction rather than
a reversible note, and model-authored prose derived from untrusted evidence is
exactly what must not arrive in a batch nod.

**Presentation.** Inform first, then ask. Findings land as text so the whole
picture is readable, and only then does a decision prompt appear. A question box
arriving before the user knows what was found is worse than a numbered list.

`AskUserQuestion` is the right vehicle where available, for two features. Its
`preview` renders the exact diff beside the choice rather than scrolled off
above it. And the note a user attaches to a selection becomes
`resolution.reason`, which is the field that stops a declined row being re-raised
badly.

Its caps shape the flow: four options per question, four questions per call, and
`preview` is single-select only. The four-option cap is treated as a feature,
since a prompt that cannot present twelve proposals enforces restraint better
than prose asking for it.

## Trigger

The hook folds the transcript into the ledger on PreCompact and SessionEnd, and
nudges only when a row crosses the distinct-session gate during that fold. The
fold is the real job; the nudge is a by-product, and the ledger only accrues if
every session end folds itself in.

The nudge signal is deliberately not "this session was busy". That was a proxy
for having something to say, and a row recurring in a second session is the thing
itself. A first session therefore never nudges, which is the cold-start property
working rather than a bug.

`learning_loop_mode` in `.claude/claude-worktrace.local.md` (project) or
`~/.claude/claude-worktrace.local.md` (global) gates it. The key shipped as
`session_retro_mode` and that name is still read as a fallback, because dropping
it would not error: it would resolve to on-demand and the hook would go quiet
with nothing to explain why.

| Mode | Behaviour |
| --- | --- |
| `on-demand` | Default. Nothing fires, so installing changes no behaviour |
| `suggest` | Folds and nudges on PreCompact and SessionEnd |
| `checkpoint` | The above, plus a check at the self-improve context checkpoints |

## Cold start

The two-session gate means a fresh install has nothing to say for a week or two.
Worklog produces value immediately; this produces value on a delay and then
compounds. Documentation should set that expectation rather than manufacture
day-one output, since the only way to manufacture it is lowering the gate, which
is the thing keeping false rows out.

## Prior art

`session-review` by Ben Friebe, in the internal `mr-yum/claude-plugins`
marketplace, addresses an adjacent problem: read a session, find repeated
approvals, propose permission rules and `CLAUDE.md` additions. It was installed
on this machine before any of this was written, so it is prior art and named here
as such.

The designs diverged on evidence and then on purpose. That plugin reads the
conversation in context and acknowledges it cannot reliably tell an approval from
an auto-allow; this reads the JSONL on disk, which makes approval a five-value
model with evidence attached and lets one query span sessions. Its safety model
is a four-tier prose table for the model to apply; this is an executable
classifier with two axes plus orthogonal hazards. And where that plugin proposes
widening what is permitted, this proposes tightening behaviour, having dropped
the permission axis entirely once the measurements showed it empty here.

Separately, and unrelated to overlap: that plugin's Tier 1 table recommends
`Bash(cat *)`, `Bash(grep *)`, `Bash(head *)`, `Bash(tail *)` and `Bash(find *)`
as safe to auto-allow. All are argument-determined, so the rule also permits
`cat .env`. That is a live defect in a shared plugin and worth reporting on its
own merits.

## Phasing

1. ~~**Circumvention detection**, with `safety.py` retargeted.~~ Done.
   `detect_blocked_followups()` emits `circumvention` when a blocked call is
   followed by a different signature reaching the same target, and `correction`
   otherwise. `safety.hazard_reached()` annotates what the alternate route got
   to. An intervening user message disqualifies the pair, since being redirected
   is instruction rather than evasion.
2. ~~**self-improve onto the ledger.**~~ Done. The skill queries the ledger for
   `correction` and `circumvention` rows past the gate, alongside its existing
   within-session count; `write_preferences.py` takes `rows` and stamps them
   applied. Prose steers keep the subagent.
3. ~~**`verify` on preferences.**~~ Done. `friction.py verify` reports applied
   rows that kept happening.
4. ~~**worklog content.**~~ Done. `detect_friction()` runs the two certain
   detectors and appends what they find to the summariser's prompt, marked as a
   separate source and explicitly not to be padded with. A command that failed
   four times before it worked is invisible to a prose summariser, because
   nobody narrates their own retries.
5. ~~**Plumbing unification.**~~ **Dropped, on inspection.** See below.

All five are settled: 1 to 4 implemented, 5 dropped on inspection.

## The plumbing unification that was not one

Earlier drafts of this design called for `pre_compact_hook.py` to move onto
`lib/transcript.py`, on the grounds that the repo carried two transcript parsers
of 508 and 813 lines. That framing was wrong, and reading both settles it.

They walk the same JSONL and extract different things.
`pre_compact_hook.parse_transcript` pulls **message text**, user and assistant
prose, for a narrative summariser. `lib/transcript.parse_transcript` builds
**tool invocations** with approval state, latency and signatures, for analysis.
Neither could produce the other's output without becoming the other.

Their incremental state differs for the same reason. The worklog hook resumes
from a line offset, because it must summarise only what is new since the last
checkpoint or it repeats itself. The ledger re-folds whole transcripts and
relies on idempotence, because a row's occurrence set has to be replaceable. A
shared mechanism would have to serve both, and the two requirements pull apart.

What is genuinely common is about fifteen lines: open the file, iterate, tolerate
a malformed line. Extracting that would couple two hooks with different resume
semantics to save less than it costs to read the indirection.

The duplication is real and it is the right amount. Recorded here because the
instinct to unify it will come back.

## Rejected alternatives

**A fourth skill for setup tuning.** Its two distinguishing axes were
permissions, which is empty here, and a configuration report, which is what
overlapped with prior art. Removing both leaves nothing to justify a skill.

**Analysing the in-context conversation.** Cheaper, but Claude's recollection of
what happened is exactly the unreliable input, and it cannot span sessions where
repeated patterns show up.

**Guessing approval when the evidence is ambiguous.** Every collapse of `unknown`
into `approved` inflates the numbers a change is then justified by.

**Making the ledger authoritative rather than derived.** Faster, and richer
state. It also makes corruption permanent, a schema change a migration, and the
tool trusts its own memory over the evidence on disk.

**One global ledger across all projects.** Simpler to store, but the fix
destination becomes ambiguous, and destination is most of the value in a row.

**Putting the model on the write path.** Pre-computed rows and cheaper queries,
paid for by charging for an analysis nobody asked for on every session end and
making the always-on path depend on a network call that currently cannot fail.

**Letting a judging subagent apply its own findings.** Removes a hop and
completes the injection path.

**Proposing deny rules automatically from circumvention rows.** The same mistake
as auto-allowing, in the other direction: a rule written from an inferred
intention is a rule the user did not choose. Circumvention surfaces as behaviour
to correct, and the user decides whether it becomes a rule.

**Folding these mechanisms in silently as plumbing.** Accretion plus `verify` is
the most interesting property the plugin has, and it belongs in the README as
something `self-improve` does rather than buried under it.

## Open questions

**Target matching for `circumvention`** needs a definition of "the same thing by
another route". Path equality is the conservative start and will miss the
interesting cases, such as `cat $F` where the variable was set earlier. Loosening
it risks calling ordinary work a circumvention, which is a serious false
positive: it accuses Claude of evading a block it never saw.

**What a `verify` failure should do.** Re-propose the preference, escalate its
wording, or simply report. Reporting is the conservative start.

**`cost_calls` assumes recovery inside one session.** Friction that ends a
session and resumes the next morning is under-counted, and that is probably the
expensive kind.
