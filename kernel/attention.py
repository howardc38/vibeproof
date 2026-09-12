"""Read current obligations in their owning worktrees, without creating state."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from . import config, hashing, ledger, lifecycle, review, risk, state


def scan(root, *, conn=None, task_id=None, kind=None):
    root = Path(root).resolve()
    own = conn is None
    try:
        conn = conn or ledger.connect_readonly(root)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"available": False, "items": [], "errors": [str(exc)]}
    items, errors, configs = [], [], {}
    try:
        deferred = dict(review.deferred(conn))
        tasks = conn.execute("SELECT id FROM task ORDER BY rowid").fetchall()
        for task in tasks:
            tid = task["id"]
            if task_id is not None and tid != task_id:
                continue
            claims = conn.execute("SELECT * FROM claim WHERE task_id=?", (tid,)).fetchall()
            if kind is not None:
                claims = [c for c in claims if c["kind"] == kind]
            if not claims:
                continue
            try:
                recorded = lifecycle.worktree_of(conn, tid)
                target = root if tid == ledger.REVIEW_TASK else Path(recorded).resolve() if recorded else None
                if target is None or not target.is_dir():
                    raise ValueError("the recorded task worktree is unavailable; no current verdict")
                if ledger.ledger_path(target) != ledger.ledger_path(root):
                    raise ValueError("task worktree belongs to a different Git common directory")
                if target not in configs:
                    configs[target] = config.RepoConfig(target)
                cfg = configs[target]
                rows, blocked, reported = lifecycle.report(conn, cfg, tid)
                states = {r["id"]: st for r, st in rows}
                blocked_ids = {r["id"] for r, _ in blocked}
                report_reasons = {r["id"]: why for r, _, why in reported}
                ended = conn.execute("SELECT 1 FROM event WHERE task_id=? AND kind IN ('shipped','abandoned') LIMIT 1", (tid,)).fetchone() is not None
                for row in claims:
                    st = states[row["id"]]
                    if st in state.TERMINAL:
                        continue
                    att = ledger.latest_attempt(conn, row["id"])
                    route, why = risk.route(conn, cfg, row, st)
                    action = ("check" if att is None else
                              "derive" if route == risk.REDERIVE else
                              "check" if st == state.STALE else
                              "review_fix" if st == state.OPEN and att["exit_code"] == 1 else
                              "investigate")
                    note = review.current_note(conn, row["id"], row["note"])
                    key = {"state": st, "attempt": att["id"] if att else None,
                           "subject": hashing.subject_digest(target, json.loads(row["subject_refs"]), ledger.attempt_reader(conn)),
                           "config": cfg.sha, "checker": cfg.checker_sha_on_disk(row["checker"]),
                           "facts": cfg.facts_sha_for(row["checker"]), "note": note}
                    items.append({"claim": row["id"], "task": tid, "kind": row["kind"],
                                  "state": st, "action": action, "why": why,
                                  "file": row["file"], "symbol": row["symbol"], "note": note,
                                  "worktree": str(target), "ended_task": ended,
                                  "blocking": row["id"] in blocked_ids,
                                  "report_reason": report_reasons.get(row["id"], ""),
                                  "deferred_to": deferred.get(row["id"]),
                                  "revision": hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
            except (OSError, ValueError, sqlite3.Error, config.ConfigError) as exc:
                errors.append({"task": tid, "reason": str(exc)})
        return {"available": True, "items": items, "errors": errors}
    finally:
        if own:
            conn.close()
