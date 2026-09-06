"""`kernel/review.py::defer` and `::withdraw_deferral` -- which half lands first.

    python3 -m unittest tests.test_the_permanent_half_goes_last -v

Each writes twice with nothing between them: a row in an append-only ledger
that takes no deletes, and a git-tracked file under `.v4/deferred/`. One of
those is permanent and one can be done again, and both functions used to do the
permanent one first. `ledger.reconcile_deferrals` exists to report the state
that leaves -- a deferral recorded with no record on disk -- which is the shape
of a decision that reaches nobody.

The failure path is what these cases are about. The two successes are here as
controls, because reordering the writes must not cost the working path.

All of them fail against d5b538f.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as ledger_mod  # noqa: E402
from kernel import review  # noqa: E402

WHY = ("Real, and not now: the owner of this surface is being rewritten "
       "next week and the repair belongs in that cut.")
TARGET = "t-some-later-task"


class _Deferrals(unittest.TestCase):
    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        conn = sqlite3.connect(":memory:")
        # What `ledger.open` sets. `deferred` reads rows by column name, so a
        # connection without it fails on the way in and never reaches the
        # question these cases ask.
        conn.row_factory = sqlite3.Row
        conn.executescript(ledger_mod.SCHEMA)
        self.addCleanup(conn.close)
        return tmp, conn

    def _record(self, root, claim_id):
        return Path(root) / review.DEFER_DIR / f"{claim_id}.json"

    def _rows(self, conn, kind):
        return conn.execute("SELECT COUNT(*) FROM event WHERE kind = ?",
                            (kind,)).fetchone()[0]


class WhenTheLedgerWriteFails(_Deferrals):
    """`insert` is the permanent half now, so this is where the break is put.
    What must survive is a state somebody can run the command again from."""

    def test_defer_leaves_no_row_and_a_record_that_can_be_rewritten(self):
        root, conn = self._repo()
        with mock.patch.object(ledger_mod, "insert",
                               side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        self.assertEqual(self._rows(conn, review.DEFER_KIND), 0,
                         "a permanent row for a deferral that did not finish")
        self.assertTrue(self._record(root, "c1").is_file(),
                        "the retryable half did not happen either")

    def test_and_running_it_again_completes(self):
        """The point of the order. The first attempt left nothing that stops
        the second, and the second produces the state the caller asked for."""
        root, conn = self._repo()
        with mock.patch.object(ledger_mod, "insert",
                               side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        self.assertEqual(self._rows(conn, review.DEFER_KIND), 1)
        body = json.loads(self._record(root, "c1").read_text(encoding="utf-8"))
        self.assertEqual(body["target"], TARGET)

    def test_withdraw_leaves_no_row_either(self):
        root, conn = self._repo()
        review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        with mock.patch.object(ledger_mod, "insert",
                               side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                review.withdraw_deferral(conn, root, claim_id="c1", why=WHY)
        self.assertEqual(self._rows(conn, review.WITHDRAWN_KIND), 0,
                         "a row saying the deferral was withdrawn, and it was "
                         "not")
        self.assertEqual([c for c, _t in review.deferred(conn)], ["c1"],
                         "the deferral stopped counting without a row saying "
                         "so")
        # The assertion that separates the two orders. Both leave no row --
        # the break is in `insert` -- so a case asking only about rows passes
        # either way. What differs is whether the retryable half already
        # happened: it has to have, or the second run is the one that cannot
        # be retried. Measured: without it this case was green against the old
        # ordering.
        self.assertFalse(
            self._record(root, "c1").is_file(),
            "the retryable half was left behind the permanent one, so a "
            "second run is refused -- `deferred` no longer lists a claim "
            "whose row said it was withdrawn -- and the file has to go by "
            "hand")

    def test_and_withdrawing_again_completes(self):
        """The guard reads the ledger, not the disk, so the missing file does
        not block the retry -- and `is_file()` makes the unlink a no-op."""
        root, conn = self._repo()
        review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        with mock.patch.object(ledger_mod, "insert",
                               side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                review.withdraw_deferral(conn, root, claim_id="c1", why=WHY)
        review.withdraw_deferral(conn, root, claim_id="c1", why=WHY)
        self.assertEqual(self._rows(conn, review.WITHDRAWN_KIND), 1)
        self.assertEqual(review.deferred(conn), [])


class TheWorkingPathIsUnchanged(_Deferrals):
    """The controls. An ordering that never completes would pass every case
    above."""

    def test_a_deferral_writes_both_halves(self):
        root, conn = self._repo()
        review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        self.assertTrue(self._record(root, "c1").is_file())
        self.assertEqual([c for c, _t in review.deferred(conn)], ["c1"])

    def _tracked_deferral(self):
        """`reconcile_deferrals` also asks whether the record survives a clone,
        so the record has to be in an index for the other half of its verdict
        to be the one under test."""
        import subprocess

        root, conn = self._repo()
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                        "add", "-A"], cwd=root, capture_output=True)
        return root, conn

    def test_and_the_owner_of_the_mismatch_reports_none(self):
        """`reconcile_deferrals` is what says the two halves disagree, so it
        is what gets asked rather than a comparison written here."""
        root, conn = self._tracked_deferral()
        self.assertEqual(review.reconcile_deferrals(conn, root), [])

    def test_and_it_does_report_a_record_that_went_missing(self):
        """The control for the control: an empty list from a function that
        cannot see anything would pass the case above."""
        root, conn = self._tracked_deferral()
        self._record(root, "c1").unlink()
        self.assertNotEqual(review.reconcile_deferrals(conn, root), [])

    def test_the_state_a_failed_defer_leaves_is_a_loud_one(self):
        """Not just retryable -- visible. A record on disk with no row is what
        `reconcile_deferrals` reads in its other direction, so the half-done
        state is reported rather than sitting there."""
        import subprocess

        root, conn = self._repo()
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        with mock.patch.object(ledger_mod, "insert",
                               side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                        "add", "-A"], cwd=root, capture_output=True)
        self.assertNotEqual(
            review.reconcile_deferrals(conn, root), [],
            "a deferral record with no ledger row, and nothing says so")

    def test_a_withdrawal_removes_both_halves(self):
        root, conn = self._repo()
        review.defer(conn, root, claim_id="c1", why=WHY, target=TARGET)
        review.withdraw_deferral(conn, root, claim_id="c1", why=WHY)
        self.assertFalse(self._record(root, "c1").is_file())
        self.assertEqual(review.deferred(conn), [])


if __name__ == "__main__":
    unittest.main()
