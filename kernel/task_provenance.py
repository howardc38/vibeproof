"""Read-only task worktree and completion provenance from their ledger rows."""
import json
from pathlib import Path

from . import hashing, ledger

WORKTREE_KIND = "task_worktree"


def worktree_of(conn, task_id):
    """The working tree this task was opened in, or None for one opened before
    that was recorded."""
    row = conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = ? "
        "ORDER BY id LIMIT 1", (task_id, WORKTREE_KIND)).fetchone()
    return json.loads(row["payload"])["root"] if row else None



def completion_history(conn, task_id):
    """Successful historical observations, never reconstructed from this checkout."""
    ledger.require_task(conn, task_id)
    return [{"event_id": row["id"], "created_at": row["created_at"],
             "source_observation": json.loads(row["payload"]).get("source_observation")}
            for row in conn.execute(
                "SELECT id, created_at, payload FROM event "
                "WHERE task_id = ? AND kind = 'shipped' ORDER BY id", (task_id,))]



def completion_source(conn, root, task_id):
    assigned = worktree_of(conn, task_id)
    actual = str(Path(root).resolve())
    if assigned is not None and str(Path(assigned).resolve()) != actual:
        raise hashing.SourceUnreadable("completion refused: task belongs to a different worktree")
    observed = hashing.source_observation(root)
    observed.update(original_base_commit=ledger.require_task(conn, task_id)["base_commit"],
                    assigned_worktree=assigned)
    return observed
