"""Can these claims all hold at once?  SPEC.md 4.

Every other checker asks whether one claim is answered. This one asks whether
the set of them has a state where all of them are, and it exists because the
answer has twice been no -- both times for the same reason, and neither time
caught by a reader.

    committed .pyc      answering `test` rewrote it, which moved the working
                        tree, which staled `scope`; answering `scope` moved it
                        back. Nothing could ship, ever.

    signature records   accepting a risk wrote .v4/risks/<id>.json, which moved
                        the tree, which staled every other repo-scoped claim --
                        and signing is the only exit for several of them.

Both were mechanisms that were individually correct. Thirteen agents read the
design and found neither, because a collision does not live inside any one
rule; it lives between two. A reviewer reading a diff is structurally unable to
see it.

What it does: for every pair of claims, ask whether answering one necessarily
changes the key the other is judged by. If it does in both directions, the pair
is a livelock and the task can never ship.

What it does not: predict which files a checker writes. It reasons from what
they actually wrote, so a collision has to have happened once to be seen. That
is a real limit -- it catches the second occurrence, not the first.

This is a kernel command rather than a checker, and the reason is a limit the
spec now names: it audits the ledger, not the repo, and the registration gate
builds fixtures out of files. A fixture case gets a fresh empty ledger, so no
red case is expressible and every fixture would pass for a boring reason. A
checker that cannot be gated has no business being one -- unit tests cover it
instead, and `v4 audit --compositions` runs it.
"""

import json
from collections import defaultdict
from pathlib import Path


def repo_scoped_kinds(kinds_cfg):
    return {k for k, v in kinds_cfg.items() if v.get("staleness") == "repo"}


def collisions(conn, task_id, kinds_cfg):
    """Pairs where answering one moved the key the other was judged by.

    A repo-scoped claim is judged on working-tree content, so any claim whose
    answering writes a tracked or untracked file moves every repo-scoped
    claim's key. The evidence is in the ledger: two attempts on different
    claims recording different worktree stamps with no task work between them.
    """
    scoped = repo_scoped_kinds(kinds_cfg)
    rows = conn.execute(
        # `a.worktree` is selected, not only named below. The fallback beneath
        # reads `head_commit` when the column is absent from the row, and the
        # column was absent from *this query* -- so the repair that moved the
        # criterion from "did somebody commit" to "did the content move" was
        # reading the committed stamp again, and every pair looked identical.
        "SELECT a.id, a.claim_id, a.head_commit, a.worktree, a.ended_at, "
        "a.started_at, c.kind "
        "FROM attempt a JOIN claim c ON c.id = a.claim_id "
        "WHERE c.task_id = ? ORDER BY a.id", (task_id,),
    ).fetchall()

    latest, stamps = {}, defaultdict(list)
    for r in rows:
        if r["kind"] not in scoped:
            continue
        latest[r["claim_id"]] = r
        # `worktree`, which the docstring names, not `head_commit`. The column
        # exists, `attempt` carries it NOT NULL, and this function never read
        # it -- so the criterion being applied was "did somebody commit between
        # these two answers", which is the ordinary flow, while the one being
        # described was "did answering one move the content the other is judged
        # by". Distinct stamps, not one per attempt: `livelock` counted the
        # length of this list, so two attempts with the *same* stamp read as a
        # livelock.
        stamp = r["worktree"] if "worktree" in r.keys() else r["head_commit"]
        if stamp not in stamps[r["claim_id"]]:
            stamps[r["claim_id"]].append(stamp)

    found = []
    ids = sorted(latest)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ra, rb = latest[a], latest[b]
            sa = ra["worktree"] if "worktree" in ra.keys() else ra["head_commit"]
            sb = rb["worktree"] if "worktree" in rb.keys() else rb["head_commit"]
            if sa == sb:
                continue
            # "With no task work between them" -- the clause the docstring
            # states and the code never tested. A worker editing a file between
            # two repo-scoped checks moves the tree for the ordinary reason, and
            # reporting that as a pair that cannot both hold buried the signal:
            # measured, `v4 audit --compositions` reported t-003, t-004 and
            # t-005 each as "1 claim pair(s) cannot hold at once" and exited 1,
            # for run-check-edit-run-check.
            first, second = sorted((ra["id"], rb["id"]))
            wrote = conn.execute(
                "SELECT count(*) FROM event WHERE task_id = ? AND kind = 'hook_seen' "
                "AND json_extract(payload, '$.allowed') = 1 "
                "AND created_at > (SELECT ended_at FROM attempt WHERE id = ?) "
                "AND created_at < (SELECT started_at FROM attempt WHERE id = ?)",
                (task_id, first, second)).fetchone()[0]
            if wrote:
                continue
            # Different stamps on the two most recent answers means whichever
            # ran second saw a tree the first did not -- so the first is stale.
            earlier, later = (ra, rb) if ra["id"] < rb["id"] else (rb, ra)
            found.append({
                "stale": earlier["claim_id"], "stale_kind": earlier["kind"],
                "answered_after": later["claim_id"], "after_kind": later["kind"],
                "livelock": len(stamps[earlier["claim_id"]]) > 1
                            and len(stamps[later["claim_id"]]) > 1,
            })
    return found


def report(conn, repo_root, task_id):
    """(ok, lines).  `v4 audit --compositions` prints them."""
    kinds = json.loads((Path(repo_root) / ".v4" / "claim_kinds.json").read_text())
    scoped = repo_scoped_kinds(kinds)
    if len(scoped) < 2:
        return True, [f"only {len(scoped)} repo-scoped kind(s); nothing can collide"]

    found = collisions(conn, task_id, kinds)
    if not found:
        return True, [f"{len(scoped)} repo-scoped kind(s), no pair invalidated another"]

    livelocks = [c for c in found if c["livelock"]]
    lines = [f"{len(found)} claim pair(s) cannot hold at once"
             + (f", {len(livelocks)} of them a livelock" if livelocks else "")]
    for c in found:
        tag = "LIVELOCK" if c["livelock"] else "one-way"
        lines.append(f"  [{tag}] answering {c['after_kind']} ({c['answered_after']}) left "
                     f"{c['stale_kind']} ({c['stale']}) judged against a tree that no "
                     f"longer exists")
    if livelocks:
        lines += ["", "A livelock means the task can never ship: each answer expires the "
                      "other. Something answering one of these claims writes a file the "
                      "other is judged by."]
    return False, lines
