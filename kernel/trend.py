"""What the ledger says across tasks, rather than about one.  PL-7.

Twenty-five commands, and every one of them answers about a single task: what
does this task owe, is this task's scope wide enough, may this task ship. The
questions that decided anything in the last two reviews were not those. They
were arithmetic over the whole ledger -- how far into a task the first
engagement sentence lands, how many claims a task inherits from a file it barely
touched, how many terminal states were reached by a signature rather than by a
checker -- and every one of them was answered by SQL somebody typed by hand,
once, and did not keep.

A number nobody can re-run is an anecdote. So these live here: no agent, no
sampling, no judgement. The same ledger gives the same answer, and a claim about
this framework's cost becomes something you can check rather than something you
have to take.

Deliberately not a gate. Nothing here exits non-zero, nothing raises a claim,
nothing blocks a ship. The report exists so a proposal to add a mechanism can be
argued against a measurement -- which is the shape every "should we build this"
question in this project has been missing.
"""

from __future__ import annotations

import sys

from datetime import datetime

from . import risk


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def _at(ts):
    """A ledger timestamp as a datetime, or None if it is not one."""
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _gap_seconds(a, b):
    ta, tb = _at(a), _at(b)
    return (tb - ta).total_seconds() if ta and tb else None


def tasks(conn, since=None):
    """Every task, newest last.  `since` is an ISO date string."""
    sql = "SELECT id, created_at FROM task WHERE id != 'repo-review'"
    args = ()
    if since:
        sql += " AND created_at > ?"
        args = (since,)
    return _rows(conn, sql + " ORDER BY rowid", args)


def engagement_lag(conn, since=None):
    """How far into a task the first engagement sentence lands.

    The gate exists because sentences written after the code cannot have
    changed a decision. This is the number that says whether it worked: writes
    allowed before the first sentence, and the seconds between them.
    """
    out = []
    for t in tasks(conn, since):
        first_sentence = conn.execute(
            "SELECT MIN(created_at) FROM event WHERE task_id = ? AND kind IN "
            "('engagement', 'engagement_before')", (t["id"],)).fetchone()[0]
        first_write = conn.execute(
            "SELECT MIN(created_at) FROM event WHERE task_id = ? AND "
            "kind = 'hook_seen' AND json_extract(payload, '$.allowed') = 1",
            (t["id"],)).fetchone()[0]
        writes_before = conn.execute(
            "SELECT COUNT(*) FROM event WHERE task_id = ? AND kind = 'hook_seen' "
            "AND json_extract(payload, '$.allowed') = 1 AND created_at < "
            "COALESCE((SELECT MIN(created_at) FROM event WHERE task_id = ? AND "
            "kind IN ('engagement','engagement_before')), '9999')",
            (t["id"], t["id"])).fetchone()[0]
        # Whether anything was watching. Zero writes before the first sentence
        # is the best possible score, and it is the same output as a task the
        # hook never fired on -- so the report that exists to say whether the
        # engagement gate worked could not tell "engaged first" from "no gate
        # installed". `gate()` one function below already records `marks = 0`
        # for that state; this did not read it.
        marks = conn.execute(
            "SELECT COUNT(*) FROM event WHERE task_id = ? AND kind = 'hook_seen'",
            (t["id"],)).fetchone()[0]
        out.append({"task": t["id"], "writes_before_first_sentence": writes_before,
                    "marks": marks, "watched": bool(marks),
                    "seconds": _gap_seconds(first_sentence, first_write)})
    return out


def gate(conn, since=None):
    """Did the write gate fire, and could it have?

    `basis` separates "nothing was owed" from "nothing was visible yet" -- the
    distinction that let a write before `derive` retire the gate for a whole
    task. Counting refusals alone cannot see that; counting bases can.
    """
    out = []
    for t in tasks(conn, since):
        marks = _rows(conn,
            "SELECT json_extract(payload, '$.allowed') AS allowed, "
            "json_extract(payload, '$.basis') AS basis FROM event "
            "WHERE task_id = ? AND kind = 'hook_seen' ORDER BY id", (t["id"],))
        bases = {}
        for m in marks:
            bases[m["basis"] or "(pre-basis)"] = bases.get(m["basis"] or "(pre-basis)", 0) + 1
        out.append({"task": t["id"], "marks": len(marks),
                    "refused": sum(1 for m in marks if not m["allowed"]),
                    "bases": bases})
    return out


def _state_of(conn, claim_id):
    """`state.claim_state` for one claim, or `""` when it cannot be derived.

    Lazily, and tolerantly: `trend` is a report and a repo whose config has
    moved since must still be able to read its own history.
    """
    # Retraction first, and from the ledger alone. It is the one terminal state
    # reached with no attempt and no signature -- one event and nothing else --
    # so it needs no config, no checker on disk and no repo root, and asking
    # `claim_state` for it would make it depend on all three. A report about a
    # repo's history must not stop answering because that repo has moved on.
    if conn.execute("SELECT 1 FROM event WHERE claim_id = ? AND kind = 'retracted' "
                    "LIMIT 1", (claim_id,)).fetchone():
        return "RETRACTED"
    try:
        from . import config as config_mod, state as state_mod
        row = conn.execute("SELECT * FROM claim WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            return ""
        root = _repo_root_of(conn)
        cfg = config_mod.RepoConfig(root)
        return state_mod.claim_state(
            conn, root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
            checker_sha_of=cfg.checker_sha_on_disk,
            facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for)
    except Exception as exc:                                    # noqa: BLE001
        # `""` is this function's "cannot tell", and `trend` counts states --
        # so a claim whose state could not be derived left the count without
        # appearing anywhere, and a config that moved made rows quietly
        # vanish from a report about history.
        print(f"v4: trend could not derive the state of {claim_id} "
              f"({type(exc).__name__}: {exc}); it is not in the counts below",
              file=sys.stderr)
        return ""


def _repo_root_of(conn):
    """Where this ledger's repo is, from the connection's own file path."""
    from pathlib import Path as _P
    row = conn.execute("PRAGMA database_list").fetchone()
    # `.../<repo>/.git/v4/ledger.db`
    return _P(row[2]).resolve().parent.parent.parent


def how_claims_ended(conn, since=None):
    """Terminal by a green checker, or terminal by somebody's signature.

    Both are terminal and only one of them is evidence. The ratio is not a
    framework property: it is a function of which files a task touched, and
    saying so needs the per-task split rather than one number.
    """
    out = []
    for t in tasks(conn, since):
        rows = _rows(conn,
            "SELECT c.id, c.kind, (SELECT a.exit_code FROM attempt a "
            "WHERE a.claim_id = c.id ORDER BY a.id DESC LIMIT 1) AS last "
            "FROM claim c WHERE c.task_id = ?", (t["id"],))
        signed = {r["claim_id"] for r in _rows(conn,
            "SELECT r.claim_id FROM accepted_risk r JOIN claim c "
            "ON c.id = r.claim_id WHERE c.task_id = ?", (t["id"],))}
        by_kind = {}
        for r in rows:
            k = by_kind.setdefault(r["kind"],
                                   {"green": 0, "signed": 0, "open": 0,
                                    "retracted": 0, "lapsed": 0})
            # How the work ended, which is a historical question, corrected in
            # the two places the raw exit code plus a join answered it wrongly
            # and both fell into "open".
            #
            # A RETRACTED claim is terminal and is reached with *no attempt at
            # all*, so `last` was NULL and it counted as open forever. Only
            # `claim_state` knows that, so only that is asked of it.
            #
            # And a signature whose staleness key has moved stopped covering --
            # `claim_state` re-derives it -- while the `accepted_risk` join went
            # on calling it signed. A lapsed signature is an open claim that
            # somebody has already paid for once, so it is named rather than
            # folded into either.
            #
            # `last == 0` stays what "green" means: whether the checker has
            # since changed is a question about now, and this column is about
            # then. That is the one place `claim_state` would have answered a
            # different question.
            st = _state_of(conn, r["id"])
            if st == "RETRACTED":
                k["retracted"] += 1
            elif r["last"] == 0:
                k["green"] += 1
            elif r["id"] in signed:
                k["signed" if st == "RISK_ACCEPTED" else "lapsed"] += 1
            else:
                k["open"] += 1
        out.append({"task": t["id"], "claims": len(rows), "by_kind": by_kind})
    return out


def inherited(conn, since=None):
    """Claims a task got from a file rather than from its own change.

    A claim raised at the first derive is about code that already existed. One
    raised later is about code this task wrote. The first number is what a task
    inherits by touching a file; measured on one adopter, a one-line change to a
    hardcoded number brought eight of them.
    """
    out = []
    for t in tasks(conn, since):
        first_write = conn.execute(
            "SELECT MIN(created_at) FROM event WHERE task_id = ? AND "
            "kind = 'hook_seen'", (t["id"],)).fetchone()[0]
        rows = _rows(conn,
            "SELECT kind, created_at FROM claim WHERE task_id = ?", (t["id"],))
        if first_write is None:
            # The fallback was the string `"9999"`, which sorts above every ISO
            # timestamp, so every claim landed in `before_first_write` and the
            # task reported 100% inherited -- the maximum -- precisely when
            # nothing was observing. That is the state `ship` calls DEGRADED and
            # the state every adopter is in until `.claude/settings.json`
            # exists, and `cmd_trend` printed the number with no qualification.
            out.append({"task": t["id"], "before_first_write": None,
                        "after": None, "watched": False, "kinds": [],
                        "why": "the write hook never fired for this task, so "
                               "there is no first write to sort against"})
            continue
        pre = [r for r in rows if r["created_at"] < first_write]
        out.append({"task": t["id"], "before_first_write": len(pre),
                    "after": len(rows) - len(pre), "watched": True,
                    "kinds": sorted({r["kind"] for r in pre})})
    return out


def signatures(conn, since=None):
    """Who signed, and whether anything says a person read it.

    `signed_by` came from a run where a worker signed away the claim judging its
    own work. The count of `agent` signatures is not a fault by itself -- it is
    the number that says how much of a repo's terminal state rests on something
    no person has confirmed.

    `tty` alone was that count for a day after it stopped being able to be.
    Three routes reach a signature and only two values came out of `was_tty`:
    `--no-tty-check` and `--as-monitor` are both `was_tty = 0`, and a pty is
    `was_tty = 1` for a signature no person gave. So this report -- the one that
    answers *who signed* -- put a second session's signature in the same bucket
    as the worker signature it was built to be told apart from. Both buckets are
    here now: `tty` still says whether a terminal was attached, `by_signer` says
    which route it came through, and those stopped being the same question on
    2026-08-27.

    `unrecorded` is rows written before `accepted_risk.signed_by` existed. It is
    not derived from `was_tty`: that would be a second copy of `risk.accept`'s
    rule, applied to rows it never ran on. The ledger refuses UPDATE, so the
    bucket is permanent and shrinks only by new signatures being made.
    """
    rows = _rows(conn, "SELECT * FROM accepted_risk ORDER BY id")
    by_kind, tty = {}, {"tty": 0, "no_tty": 0}
    # Seeded, so a route nobody used reads as zero rather than as absent. A key
    # that is missing and a key that is 0 are the same to a reader skimming and
    # different to one asking "has anybody signed as a monitor here".
    by_signer = {risk.PERSON: 0, risk.AGENT: 0, risk.MONITOR: 0}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        tty["tty" if r["was_tty"] else "no_tty"] += 1
        who = r.get("signed_by") or risk.UNRECORDED
        by_signer[who] = by_signer.get(who, 0) + 1
    return {"total": len(rows), "by_kind": by_kind, "tty": tty,
            "by_signer": by_signer,
            "median_why_chars": _median([len(r["why"] or "") for r in rows])}


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def checker_cost(conn):
    """Median wall-clock per kind, from what actually ran.

    `timeout_sec` is a ceiling somebody wrote down. This is the measurement, and
    the gap between the two is how a repo finds out its timeouts are fiction.
    """
    out = {}
    for kind, in conn.execute("SELECT DISTINCT kind FROM claim ORDER BY kind"):
        ms = [r[0] for r in conn.execute(
            "SELECT a.duration_ms FROM attempt a JOIN claim c ON c.id = a.claim_id "
            "WHERE c.kind = ? ORDER BY a.duration_ms", (kind,))]
        if ms:
            out[kind] = {"runs": len(ms), "median_ms": ms[len(ms) // 2],
                         "max_ms": ms[-1]}
    return out


def report(conn, since=None):
    """Everything above, in one dict.  No judgement, no thresholds."""
    return {
        "since": since,
        "tasks": len(tasks(conn, since)),
        "engagement_lag": engagement_lag(conn, since),
        "gate": gate(conn, since),
        "how_claims_ended": how_claims_ended(conn, since),
        "inherited": inherited(conn, since),
        "signatures": signatures(conn, since),
        "checker_cost": checker_cost(conn),
    }
