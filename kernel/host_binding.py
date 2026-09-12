"""Explicit host/agent to V4 task bindings in the existing append-only ledger."""
import json
from datetime import datetime, timezone
from pathlib import Path
from . import ledger, lifecycle

EVENT = "host_binding"


def bind(conn, root, *, task_id, session, agent="", host="codex"):
    if host not in ("claude", "codex") or not session.strip():
        raise ValueError("host and a nonempty session are required")
    if task_id not in ledger.open_task_ids(conn):
        raise ValueError("only an open task can be bound")
    worktree = lifecycle.worktree_of(conn, task_id)
    if not worktree or Path(worktree).resolve() != Path(root).resolve():
        raise ValueError("bind from the worktree where this task was opened")
    payload = {"host": host, "session": session, "agent": agent,
               "worktree": str(Path(root).resolve())}
    ledger.insert(conn, "event", task_id=task_id, claim_id=None, kind=EVENT,
                  actor="agent", payload=payload,
                  created_at=datetime.now(timezone.utc).isoformat())
    return payload


def lookup(conn, *, session, agent="", host="codex"):
    for row in conn.execute("SELECT task_id,payload FROM event WHERE kind=? ORDER BY id DESC",
                            (EVENT,)):
        data = json.loads(row["payload"])
        if (data.get("host"), data.get("session"), data.get("agent", "")) == (host, session, agent):
            # An ended binding is not a reason to resurrect an older one.
            if row["task_id"] not in ledger.open_task_ids(conn):
                return None
            return {"task_id": row["task_id"], **data}
    return None
