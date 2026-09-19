"""The anchor's three numbers describe one moment, or they describe none.

`write_chain_head` took four autocommit SELECTs. In WAL mode each one gets its
own snapshot, so an append committed by another worktree between the count and
the head left `last_event_id` describing one moment and `event_head_hash`
describing a later row. The anchor then failed its own verifier: `chain:
BROKEN` at ship, in a checkout that had done nothing wrong, and it stayed
broken until that checkout ran `v4 check` again.

The ledger is shared by every linked worktree by design, so two processes
appending while a third anchors is the ordinary case, not a contrived one.
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger  # noqa: E402


def _git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)


class AnAppendBetweenTheReadsDoesNotTearTheAnchor(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@t")
        _git(self.root, "config", "user.name", "t")
        (self.root / ".v4").mkdir()
        (self.root / ".v4/config.json").write_text(json.dumps(
            {"repo": "r", "test_command": "true", "policy": "allow_accepted_risk"}))
        (self.root / "a.txt").write_text("one\n")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
        conn = ledger.connect(self.root)
        for i in range(5):
            ledger.insert(conn, "event", task_id=None, claim_id=None, kind="hook_seen",
                          actor="kernel", payload={"i": i},
                          created_at=f"2026-01-0{i + 1}T00:00:00+00:00")
        conn.close()

    def _tearing_connection(self):
        """A connection that commits one append from elsewhere mid-read."""
        root = self.root

        class Tearing(sqlite3.Connection):
            fired = False

            def execute(self, sql, *a, **k):
                if "ORDER BY t.id DESC" in sql and "event" in sql and not Tearing.fired:
                    Tearing.fired = True
                    other = ledger.connect(root)
                    ledger.insert(other, "event", task_id=None, claim_id=None,
                                  kind="hook_seen", actor="kernel", payload={"i": 99},
                                  created_at="2026-02-01T00:00:00+00:00")
                    other.close()
                return super().execute(sql, *a, **k)

        conn = sqlite3.connect(ledger.ledger_path(root), factory=Tearing)
        conn.row_factory = sqlite3.Row
        return conn

    def test_the_anchor_it_writes_passes_its_own_verifier(self):
        conn = self._tearing_connection()
        ledger.write_chain_head(conn, self.root)
        conn.close()
        reader = ledger.connect_readonly(self.root)
        self.addCleanup(reader.close)
        ok, problems = ledger.audit_chain(reader, self.root)
        self.assertTrue(ok, "\n".join(problems))

    def test_and_nothing_it_writes_holds_a_ship(self):
        conn = self._tearing_connection()
        ledger.write_chain_head(conn, self.root)
        conn.close()
        reader = ledger.connect_readonly(self.root)
        self.addCleanup(reader.close)
        _ok, problems = ledger.audit_chain(reader, self.root)
        self.assertEqual(ledger.fatal(problems), [], problems)

    def test_the_count_and_the_head_name_the_same_row(self):
        """The control on the fix itself: the anchor is only meaningful if its
        three numbers come from one row, so read them back and compare."""
        conn = self._tearing_connection()
        ledger.write_chain_head(conn, self.root)
        conn.close()
        anchor = json.loads(ledger.chain_head_path(self.root).read_text())
        reader = ledger.connect_readonly(self.root)
        self.addCleanup(reader.close)
        row = reader.execute("SELECT row_hash FROM event WHERE id = ?",
                             (anchor["last_event_id"],)).fetchone()
        self.assertEqual(row["row_hash"], anchor["event_head_hash"])

    def test_an_empty_chain_still_anchors(self):
        """No rows is not a torn read; it is the genesis the verifier expects."""
        other = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(other)])
        _git(other, "init", "-q")
        (other / ".v4").mkdir()
        conn = ledger.connect(other)
        ledger.write_chain_head(conn, other)
        conn.close()
        anchor = json.loads(ledger.chain_head_path(other).read_text())
        self.assertEqual(anchor["events"], 0)
        self.assertEqual(anchor["event_head_hash"], ledger.GENESIS)
        self.assertEqual(anchor["head_hash"], ledger.GENESIS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
