"""When the after-gate is due, and whether now is a moment for it.  SPEC.md §10.1.

Measured on an eight-task X/Y run: running all nine lenses on every task cost
6.6x the token budget of not running them, and the arm that paid it shipped
three of eight briefs short. Running them once at the end of the same run,
against the same code, took the production-incident count from three to zero
for 1.96x -- the same result the per-task arm reached, for a third of the price.

So the after-gate becomes periodic. Which raises the only two questions a
program can answer about it: is it due, and is this a moment when a review
would be reading a settled tree rather than somebody's half-finished work.

Everything else -- reading the diff, judging it, raising findings -- is the
reviewer's, and no kernel here calls a model. `v4 sweep` prints the brief and
records that a sweep happened. It does not review.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ledger import insert
from . import state

KIND = "lens_sweep"

#: The same rows, in the file that travels.  `lifecycle.ship` writes it and it
#: is committed; `.git/v4/ledger.db` is neither, and a clone gets one of the two.
#:
#: The `chain` job in `.github/workflows/v4.yml` had already made this argument
#: and acted on it -- "the ledger lives in .git/v4/ and a clone does not carry
#: it, so this job walks the export `v4 ship` writes instead". The `sweep` job
#: beside it did not, so it asked `due()` of an empty database.
EXPORT = ".v4/ledger_export.jsonl"

#: What a repo that has said nothing gets.  Four days is the interval the X/Y
#: run's own numbers point at -- long enough that the sweep is not a per-task
#: cost, short enough that a finding is still about code somebody remembers.
DEFAULT = {"every_days": 4, "not_before_hour": None, "weekday": None}


def config(cfg) -> dict:
    out = dict(DEFAULT)
    out.update((cfg.config.get("lens_sweep") or {}))
    return out


def complete_payload(payload):
    """Coverage metadata, never an inference from a successful command exit."""
    if not isinstance(payload, dict) or payload.get("complete") is False:
        return False
    if payload.get("run_id") and payload.get("cadence_complete") is not True:
        return False  # Older selected-run records did not establish full cadence coverage.
    expected, reviewed = payload.get("lenses"), payload.get("reviewed")
    return (isinstance(expected, list) and bool(expected) and isinstance(reviewed, dict)
            and set(expected) <= set(reviewed)
            and all(isinstance(reviewed[x], int) and not isinstance(reviewed[x], bool) and reviewed[x] >= 0 for x in expected))


def last(conn):
    """Last complete recorded sweep. Partial/unknown legacy coverage is not fresh."""
    for row in conn.execute("SELECT created_at,payload FROM event WHERE kind=? ORDER BY id DESC", (KIND,)):
        try:
            if complete_payload(json.loads(row["payload"])):
                return datetime.fromisoformat(row["created_at"])
        except (ValueError, TypeError):
            continue
    return None


def last_attempted(conn):
    """Legacy evidence windows end at every attempt; cadence uses last complete."""
    row = conn.execute("SELECT created_at FROM event WHERE kind=? ORDER BY id DESC LIMIT 1", (KIND,)).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def last_exported(root):
    """When the last sweep the committed export carries happened, or None.

    The other end of `last`. Same rows, same `KIND`, read out of the file rather
    than out of the database -- so this is as fresh as the last `v4 ship` and
    never fresher, which is the honest ceiling on what a checkout can know.

    Raises rather than returning `None` when nothing is committed: "no export"
    and "an export recording no sweep" are different facts, and collapsing them
    is exactly the collapse this repair is about -- an empty database answering
    "last sweep never" for a repo that had swept twice.
    """
    path = Path(root) / EXPORT
    if not path.is_file():
        raise FileNotFoundError(
            f"{EXPORT} is not committed, so this checkout carries no record of "
            f"any sweep and cannot say whether one is due. `v4 ship` writes it.")
    from .ledger import segment_files
    newest = ""
    # `read_text().splitlines()`, the same way `ledger.verify_exported` walks
    # this file, and unguarded the same way: a line of this that does not parse,
    # or a timestamp that is not a timestamp, is a broken export and raising
    # here is how the job that reads it says so. `last` guards its parse because
    # a long-lived database holds rows older than the writer that made them; a
    # file `export_jsonl` wrote in one pass does not.
    #
    # The open file first, then the sealed segments newest first, and the walk
    # stops at the first file that holds a sweep: a row exported once is never
    # exported again, so the sweep the open file lacks sits in whichever sealed
    # segment was open when it was recorded.
    for f in [path] + list(reversed(segment_files(path))):
        for line in f.read_text(encoding="utf-8").splitlines():
            # A substring test before a 24 MB file's worth of `json.loads`. It
            # only skips lines that cannot be the row; every line it keeps is
            # still checked against `_table` and `kind`, so a note quoting the
            # word costs a parse and decides nothing.
            if KIND not in line:
                continue
            r = json.loads(line)
            if r.get("_table") == "event" and r.get("kind") == KIND:
                payload = r.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if complete_payload(payload):
                    newest = max(newest, r.get("created_at") or "")
        if newest:
            break
    return datetime.fromisoformat(newest) if newest else None


def open_work(conn, cfg, since=None):
    """Tasks holding a claim nobody has answered.  [(task, n)], [(task, why)]

    A review of a tree somebody is still writing reports the half-written state
    as a finding, and the worker who reads it has to work out which half of it
    was going to be true anyway. That is the noise that teaches people to stop
    reading a checklist.

    Only tasks touched since the last sweep. Every task ever was the first
    version, and this repo's own ledger holds demo tasks from days nobody is
    going back to -- each of which would have held every future sweep. The same
    trap `doctor` was reporting counts of before it started naming them.

    A task whose rows cannot be read is returned separately rather than skipped:
    unreadable is not the same as clean, and treating it as clean is how a
    sweep runs over work it could not see.
    """
    busy, unreadable = [], []
    # Tasks that have ended are not holding anything. A task ends two ways, and
    # `ledger.ENDED_TASKS_SQL` is where that is said -- this was the fourth place
    # asking the question, and it was written before `abandon` existed, so an
    # abandoned task went on holding every future sweep. The three that already
    # shared the definition (both hooks and `doctor`) were correct the same day.
    from .ledger import ENDED_TASKS_SQL
    sql = (f"SELECT id FROM task WHERE id NOT IN ({ENDED_TASKS_SQL}) "
           f"ORDER BY rowid")
    for task in conn.execute(sql).fetchall():
        tid = task["id"]
        if since is not None:
            # Attempts as well as events. `v4 check` -- the command a worker
            # runs most -- writes no event carrying a task id: attempts go to
            # the `attempt` table, `cost_observation` is its own table, and
            # `checker_out` carried `task_id = None`. So a task that had been
            # actively checked but not derived, widened or written to through
            # the hook was skipped entirely, never reached `state.task_report`,
            # and `due()` answered "nothing is open" while it held unanswered
            # claims. The guard against reviewing somebody's half-written tree
            # was blind to the one thing that says the tree is being worked on.
            row = conn.execute(
                "SELECT MAX(t) t FROM ("
                "  SELECT MAX(created_at) t FROM event WHERE task_id = ?1"
                "  UNION ALL"
                "  SELECT MAX(a.ended_at) FROM attempt a JOIN claim c"
                "    ON c.id = a.claim_id WHERE c.task_id = ?1)",
                (tid,)).fetchone()
            seen = (row["t"] if row else None) or ""
            if seen and seen < since.isoformat():
                continue
        try:
            # Only what blocks counts as busy. A task holding nothing but
            # `report` kinds is one somebody can ship, so a sweep that called
            # it busy would be waiting for work nobody owes.
            _rows, blocked, _reported = state.task_report(
                conn, cfg.root, tid, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
                checker_sha_of=cfg.checker_sha_on_disk,
                facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for,
                thresholds=cfg.thresholds)
        except Exception as exc:                                 # noqa: BLE001
            unreadable.append((tid, str(exc)[:80]))
            continue
        if blocked:
            busy.append((tid, len(blocked)))
    return busy, unreadable


def elapsed(cfg, prev, now):
    """(is the clock open, one sentence saying why) -- `due` minus the tree.

    Split out because the timestamp has two sources and the arithmetic must not.
    `last` reads the database a worker has; `last_exported` reads the file a
    clone has; both then ask this, so a schedule that says "Mondays only" cannot
    mean one thing on a laptop and another in CI.
    """
    c = config(cfg)
    if c.get("weekday") is not None and now.weekday() != int(c["weekday"]):
        return False, (f"the sweep is set for weekday {c['weekday']} and today "
                       f"is {now.weekday()}")
    if c.get("not_before_hour") is not None and now.hour < int(c["not_before_hour"]):
        return False, (f"the sweep is set for {int(c['not_before_hour']):02d}:00 "
                       f"or later and it is {now.hour:02d}:{now.minute:02d}")

    if prev is not None:
        gap = now - prev
        want = timedelta(days=float(c["every_days"]))
        if gap < want:
            left = want - gap
            return False, (f"the last sweep was {gap.days}d{gap.seconds // 3600}h "
                           f"ago and the interval is {c['every_days']}d -- "
                           f"{left.days}d{left.seconds // 3600}h to go")
    return True, f"last sweep {'never' if prev is None else f'{(now - prev).days}d ago'}"


def due_from_export(root, cfg, now=None):
    """Is the after-gate due, judged from the record a checkout carries.

    The scheduled job in `.github/workflows/v4.yml` runs in a fresh clone, and
    `ledger.connect` makes an empty database when it finds none -- so `due()`
    there read zero sweeps and zero tasks. Measured by cloning this repo at
    `dbd0a60` and running the job's own command in the clone: it printed
    `DUE -- last sweep never, and nothing is open`, while the export committed
    in that same clone carries two `lens_sweep` rows and `repo-review` was
    holding claims. Both halves of that sentence were false, the `|| true` on
    the line above swallowed anything that went wrong, and it would have printed
    those same words every Monday for as long as the cron existed.

    Only the clock half is answered. Whether somebody is standing in a
    half-written tree is a question about a working tree, and a checkout is not
    one; saying so is the point, because the failure being repaired is a
    sentence that claimed to have looked.
    """
    prev = last_exported(root)
    ok, why = elapsed(cfg, prev, now or datetime.now(timezone.utc))
    return ok, (f"{why}, from {EXPORT} -- a checkout carries no ledger, so this "
                f"is as fresh as the last ship. Whether a task is half-written "
                f"was not asked: that needs the ledger, and it belongs to "
                f"whoever runs `v4 sweep` where one exists.")


def due(conn, cfg, now=None):
    """(is it due, one sentence saying why).

    Deliberately a question, not a trigger. Nothing here starts a sweep: this
    is meant to be called from cron, a git hook, or by hand, as often as anyone
    likes, and to answer the same way every time until the answer changes.
    """
    now = now or datetime.now(timezone.utc)
    prev = last(conn)

    ok, why = elapsed(cfg, prev, now)
    if not ok:
        return False, why

    busy, unreadable = open_work(conn, cfg, since=prev)
    if busy:
        names = ", ".join(f"{t} ({n} open)" for t, n in busy[:4])
        return False, (f"{len(busy)} task(s) still hold an unanswered claim: "
                       f"{names}. A review of a tree somebody is still writing "
                       f"reports the half-written state as a finding.")
    if unreadable:
        names = ", ".join(t for t, _ in unreadable[:4])
        return False, (f"{len(unreadable)} task(s) could not be read, so whether "
                       f"they are open is unknown: {names} ({unreadable[0][1]}). "
                       f"Unreadable is not clean.")

    since = "never" if prev is None else f"{(now - prev).days}d ago"
    return True, f"last sweep {since}, and nothing is open"


def _lens_events(conn, kind, since):
    """`{lens: payload}` for one lens event kind, in the window a sweep owns.

    Two clauses, and both of them are what "this sweep" means:

    `created_at > since`, because a sweep reports on what happened since the
    last one. `task_id IS NULL`, because a sweep has no task and neither does
    a brief printed for it -- `v4 review lens --task T` records a reviewer
    reading *T's diff*, which is a different subject from the tree this
    after-gate reads ("this is the after-gate, so it reads code that exists,
    not a diff"). `lifecycle` keys the same two rows on `task_id = ?`; this is
    the same filter from the other side, and it is the only form available to
    a path that by design has no task.

    Measured on this repo's own ledger, at the sweep of 2026-08-27: eleven
    lenses were opened for it and all eleven reported, and `v4 sweep --done`
    printed `12 of 13 printed their brief since the last sweep`. The twelfth
    was `request-fidelity`, whose `lens_run` rows in that window were written
    on tasks `fw-rust`, `fw-reqfid` and `fw-tax` -- three workers reading three
    diffs, counted as coverage of a tree none of them was looking at.

    Last row wins per lens, the same reason `lifecycle._lenses_reviewed` gives:
    a reviewer that ran again after a repair is saying something newer, not
    something additional.
    """
    sql = "SELECT payload FROM event WHERE kind = ? AND task_id IS NULL"
    args = [kind]
    if since is not None:
        sql += " AND created_at > ?"
        args.append(since.isoformat())
    out = {}
    for r in conn.execute(sql + " ORDER BY id", args):
        try:
            p = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        if p.get("lens") and not p.get("run_id"):
            out[p["lens"]] = p
    return out


def ran_since(conn, since=None):
    """Which lenses actually printed their brief since a moment.

    `v4 review lens` records one `lens_run` per invocation, so this is the
    difference between a sweep that says it covered nine and one that did. The
    predecessor's measured failure was exactly this shape: two repos vendored a
    reviewer kit, both satisfied the contract by recording the command, and it
    executed zero times in either repo's history.

    A brief printed and nothing more. What it is not is a review, and the two
    readers on this side of the tree both took it for one -- see `record`.
    """
    from .review import LENS_RUN_KIND
    return set(_lens_events(conn, LENS_RUN_KIND, since))


def reviewed_since(conn, since=None):
    """`{lens: findings}` for the lenses a reviewer came back on.  The other
    half of `ran_since`, and the half this side of the tree did not have.

    `v4 review done --lens X --findings n` writes it, and `--findings 0` is the
    whole point of the row existing: "ran and found nothing" is a fact, and
    without somewhere to put it, it is stored as the same silence as "took the
    brief and walked away".

    Not gated, for the reason §10.2 gives about the ship side: a lens is
    judgement, and gating on "did a judgement happen" buys a checkbox. What it
    buys instead is that a sweep of thirteen lenses nobody reviewed stops
    looking like a sweep of thirteen.
    """
    from .review import LENS_REVIEWED_KIND
    return {name: p.get("findings")
            for name, p in _lens_events(conn, LENS_REVIEWED_KIND, since).items()}


def raised_since(conn, since=None):
    """Review findings that reached the ledger since a moment -- **all** of them,
    not only this sweep's.

    The other half of `ran_since`. `lenses` was cross-checked and `findings` was
    not, so the count went in as whatever the caller typed. Measured on one
    adopter: the record said 61, the ledger held 0, and neither number was
    written anywhere near the other.

    The name is the honest one because the number is a floor, not a total: a
    reviewer reading a diff between two sweeps files findings too, and they land
    in the same window. That direction of error is the one that matters -- this
    exists to catch "claimed many, raised few", and counting extra is what
    hides it. A sweep that did nothing can look busy on somebody else's work.

    Not fixed by giving each sweep an id and stamping it on every `review add`:
    that is a new concept every caller has to know it is inside, bought for a
    precision problem that has not misled anyone yet. When it does, the id is
    the fix. Until then the field says what it measures.

    So this window is deliberately wider than `_lens_events`, and the two are
    not drifting apart: the error that hides a finding count is under-reporting
    it, and the error that hides a coverage count is over-reporting it. Adding
    `AND task_id IS NULL` here would make a sweep look like it raised fewer
    than the ledger holds, which is the direction this exists to catch.
    """
    # Both halves from `review`, so the rows this counts and the rows
    # `raise_finding` writes cannot drift apart -- they did not have a shared
    # name, and nothing pinned the clause.
    from .review import KIND as REVIEW_KIND, ORIGIN as REVIEW_ORIGIN
    sql = (f"SELECT COUNT(*) FROM claim WHERE kind = '{REVIEW_KIND}' "
           f"AND origin = '{REVIEW_ORIGIN}'")
    args = ()
    if since is not None:
        sql += " AND created_at > ?"
        args = (since.isoformat(),)
    return conn.execute(sql, args).fetchone()[0]


def reconcile(conn, findings, note, floor):
    """`""` if the two counts agree or the note accounts for them, else why not.

    Both numbers were already stored -- `findings` is what the sweep says it
    raised, `raised_since_last_sweep` is what the ledger saw -- and nothing
    ever compared them. Measured on the reference adopter: a sweep reported 61
    and the ledger held 1, and that stayed true for three days because no
    command had to say anything about the gap.

    Not a refusal to differ. A sweep may legitimately raise five of what it
    found and put the rest somewhere a person reads -- that is a decision, and
    this asks for it in writing rather than forbidding it. The floor is the one
    `scope widen`, `abandon` and `review defer` all answer to: a reason short
    enough to skip reading is a reason nobody wrote.
    """
    if findings is None:
        return ""
    seen = raised_since(conn, last(conn))
    if seen == findings or len((note or "").strip()) >= floor:
        return ""
    return (f"this sweep says {findings} finding(s) and the ledger holds "
            f"{seen} since the last one. Both numbers are recorded either way; "
            f"the difference is not. Say where the other "
            f"{abs(findings - seen)} went, in --note, in at least {floor} "
            f"characters -- a document, an issue, a decision not to file them.")


def record(conn, *, lenses, findings=None, note=""):
    """A sweep happened, and what actually ran while it did.

    `lenses` is what the caller says it covered; `ran` is what the ledger saw.
    Storing both is the point -- a sweep that claims nine and shows two is a
    fact somebody should be able to read afterwards, and a sweep command that
    only ever stored the claim would be the same self-report this whole layer
    exists to replace.

    Except `ran` was never the second half of that pair. It comes from
    `lens_run`, which `v4 review lens` writes when the brief prints -- and
    printing a brief is free. So thirteen reviewers could take a brief, none of
    them come back, and the durable record of that sweep read `ran: 13`: the
    self-report this layer exists to replace, wearing the ledger's name.
    Nothing on this side of the tree read `lens_reviewed` at all, which is why
    the two rows had to be stored, not chosen between.

    `v4 ship` was the same defect and is repaired the same way (§10.2), with
    the one difference the two paths cannot share: ship keys its rows on a
    task, and a sweep has none, so `_lens_events` keys on the window and on
    the rows that name no task. What is deliberately *not* copied is a gate --
    neither side refuses, for the reason §10.2 measured.

    `findings` and `raised_since_last_sweep` are the same pair. The first was
    cross-checked and the second was not, so a sweep could report 61 findings
    while the ledger held none of them -- which is what happened, because
    `review add` could not file a finding that had no task.

    The second one is named for the window it measures and not for the sweep,
    because it counts every review finding in that window including ones this
    sweep did not raise. Calling it `raised` invited the reading that makes it
    useless: a sweep that found nothing looks like it worked if somebody else
    filed three findings in between.
    """
    since = last_attempted(conn)
    payload = {"lenses": list(lenses), "ran": sorted(ran_since(conn, since)),
               "reviewed": reviewed_since(conn, since), "findings": findings,
               "raised_since_last_sweep": raised_since(conn, since), "note": note}
    payload["complete"] = complete_payload(payload)
    insert(conn, "event", task_id=None, claim_id=None, kind=KIND, actor="worker",
           payload=payload, created_at=datetime.now(timezone.utc).isoformat())
    return payload



def history(conn, limit=10):
    rows = conn.execute(
        "SELECT created_at, payload FROM event WHERE kind = ? ORDER BY id DESC "
        "LIMIT ?", (KIND, limit)).fetchall()
    out = []
    for r in rows:
        try:
            out.append((r["created_at"], json.loads(r["payload"])))
        except (ValueError, TypeError):
            continue
    return out
