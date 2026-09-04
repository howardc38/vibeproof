"""What a merge invalidates, and how many times you may answer it.  PL-5.

Two tasks in two worktrees. B merges to main, HEAD moves, and every
repo-scoped answer A, C and D already gave is about a tree that no longer
exists. The worker who earned those answers has exited -- exiting is how a task
finishes -- so nobody is left to notice.

`ledger.py`'s schema comment has listed `remerge` as an event kind since before
this file existed, and nothing has ever written one: declared at one end, absent
at the other, which is the shape `dead-wiring` looks for, sitting in the kernel.

What this is
------------
Two questions and a counter. Not a scheduler, not an exclusive lock, not a
dispatcher -- the notes are explicit that those are for a scale nobody has, and
one of them (a three-strike counter) was for a pathology that has never
happened. Building them would be answering a problem with machinery instead of
with a number.

  `stale_after(conn, head)`   which open tasks have repo-scoped answers older
                              than this HEAD, and how many claims each
  `record(conn, task, head)`  one round, appended, so the count is a fact

The bound is on rounds, not on outcomes. A task that re-merges three times is
not a task with a bad worker; it is two cuts that keep touching each other, and
saying so once is worth more than retrying forever. `blocked` is the same event
`ship` already writes when its own bounded loop runs out, for the same reason.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .ledger import insert

KIND = "remerge"

#: Two cuts that need each other re-checked three times are not a worker
#: problem, and a fourth round will not find that out. `ship` bounds its
#: re-derive the same way and says the same thing: at the limit this is a report
#: about the cut, not a risk for somebody to sign.
MAX_ROUNDS = 3


def _now():
    return datetime.now(timezone.utc).isoformat()


def rounds(conn, task_id) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM event WHERE task_id = ? AND kind = ?",
        (task_id, KIND)).fetchone()[0]


def stale_after(conn, digest: str, *, kinds_cfg=None, open_ids=None):
    """`[{task, claims, at}]` -- open tasks whose repo-scoped answers predate `digest`.

    The argument is a **worktree digest**, not a commit, and the first version of
    this got that wrong. `attempt.head_commit` holds two different things
    depending on the claim's staleness: `hashing.worktree_digest` for a
    repo-scoped kind and `runner.head_commit` for a subject-scoped one
    (`lifecycle.py:320`). Every other reader filters to repo-scoped and compares
    digests -- `state.py` and `composition.py` both do -- and this one did not,
    so it compared a digest to a commit hash and reported every repo-scoped
    claim in the repo as expired, forever. Demonstrated before it was fixed.

    Repo-scoped only, and now enforced rather than said in a docstring. A
    subject-scoped claim is keyed to the bytes of its own files, and a merge
    that did not touch them did not expire it.
    """
    from . import ledger as ledger_mod, state as state_mod
    ids = open_ids if open_ids is not None else ledger_mod.open_task_ids(conn)
    scoped = {k for k, v in (kinds_cfg or {}).items()
              if (v or {}).get("staleness") == state_mod.REPO_SCOPED}
    out = []
    for tid in ids:
        rows = conn.execute(
            "SELECT a.head_commit AS stamp, c.kind, COUNT(*) AS n FROM attempt a "
            "JOIN claim c ON c.id = a.claim_id "
            "WHERE c.task_id = ? AND a.exit_code = 0 "
            "GROUP BY a.head_commit, c.kind", (tid,)).fetchall()
        rows = [r for r in rows if not kinds_cfg or r["kind"] in scoped]
        stale = sum(r["n"] for r in rows if r["stamp"] and r["stamp"] != digest)
        if stale:
            out.append({"task": tid, "claims": stale,
                        "at": sorted({r["stamp"] for r in rows
                                      if r["stamp"] and r["stamp"] != digest})})
    return out


def record(conn, *, task_id, head: str, claims: int):
    """One re-check round, appended.  Returns `(round_number, blocked)`."""
    n = rounds(conn, task_id) + 1
    insert(conn, "event", task_id=task_id, claim_id=None, kind=KIND,
           actor="orchestrator",
           payload={"head": head, "claims_expired": claims, "round": n},
           created_at=_now())
    blocked = n >= MAX_ROUNDS
    if blocked:
        insert(conn, "event", task_id=task_id, claim_id=None, kind="blocked",
               actor="kernel",
               payload={"reason": "remerge_not_converging", "rounds": n},
               created_at=_now())
    return n, blocked


def report(conn, digest: str, *, kinds_cfg=None) -> str:
    stale = stale_after(conn, digest, kinds_cfg=kinds_cfg)
    if not stale:
        return (f"nothing open has repo-scoped answers older than "
                f"{digest[:16]}.\n"
                "Repo-scoped answers only -- a subject-scoped claim is keyed to "
                "its own bytes and a merge that missed them did not expire it.")
    lines = [f"{len(stale)} open task(s) answered against a tree that is no "
             f"longer what is here ({digest[:16]}):", ""]
    for s in stale:
        n = rounds(conn, s["task"])
        note = ""
        if n >= MAX_ROUNDS:
            note = f"  ← {n} rounds already; this is about the cut, not the worker"
        elif n:
            note = f"  ({n} round(s) so far)"
        lines.append(f"  {s['task']:<26} {s['claims']:>3} claim(s){note}")
    lines += ["",
              "Each needs `v4 check` re-run in its own worktree. Nobody is left "
              "in those tasks: a worker exits when it finishes, which is why "
              "this has to be asked from outside rather than remembered inside."]
    return "\n".join(lines)
