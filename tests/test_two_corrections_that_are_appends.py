"""A deferral written about nothing, and a launcher that only ran from home.

    python3 -m unittest tests.test_two_corrections_that_are_appends -v

Both are cases where the record and the thing it describes had come apart, and
neither could be repaired by deleting anything: the ledger takes no deletes,
and the launcher is what every document tells a reader to type.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger, review  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


WHY = ("this record was written by a test that had not yet learned to build "
       "its own repo, and the claim it names has never existed")


class ADeferralAboutNothingIsCancelledByAnAppend(unittest.TestCase):
    """`v4 doctor` reported one on every run and nothing could answer it.

    `c1`, 2026-08-09, written while `defer` was being built: an event naming a
    claim that has never existed. The ledger takes no deletes, so the only
    thing a reader could do was recognise it again next time.
    `request_cover.withdraw` is the precedent -- the correction is itself an
    append -- and this is the same instrument one table over.
    """

    def _deferred(self, root, conn, claim_id="c1"):
        review.defer(conn, root, claim_id=claim_id, why=WHY, target="t-42",
                     actor="agent")

    def test_a_cancelled_deferral_stops_being_counted(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        self._deferred(root, conn)
        self.assertEqual([c for c, _t in review.deferred(conn)], ["c1"])
        review.withdraw_deferral(conn, root, claim_id="c1", why=WHY,
                                 actor="agent")
        self.assertEqual(review.deferred(conn), [])

    def test_both_rows_stay_in_the_ledger(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        self._deferred(root, conn)
        review.withdraw_deferral(conn, root, claim_id="c1", why=WHY,
                                 actor="agent")
        kinds = [r[0] for r in conn.execute(
            "SELECT kind FROM event WHERE claim_id = 'c1' ORDER BY id")]
        self.assertEqual(kinds, [review.DEFER_KIND, review.WITHDRAWN_KIND])

    def test_the_reconciler_stops_reporting_it(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        self._deferred(root, conn)
        (root / ".v4" / "deferred" / "c1.json").unlink()
        self.assertTrue([p for p in ledger.reconcile_deferrals(conn, root)
                         if "c1" in p])
        review.withdraw_deferral(conn, root, claim_id="c1", why=WHY,
                                 actor="agent")
        self.assertEqual([p for p in ledger.reconcile_deferrals(conn, root)
                          if "c1" in p], [])

    def test_a_deferral_about_a_real_claim_cannot_be_cancelled(self):
        """This cancels a record, not a decision."""
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        ledger.insert(conn, "claim", id="real1", task_id="t1",
                      kind="review-finding", question="q", subject_refs=[],
                      checker="review-finding", origin="review", file="a.py",
                      symbol="s", variant=None, line=None, note=None,
                      detector=None, detector_sha=None,
                      created_at="2026-08-19T00:00:00+00:00")
        self._deferred(root, conn, "real1")
        with self.assertRaises(review.CannotDefer) as caught:
            review.withdraw_deferral(conn, root, claim_id="real1", why=WHY,
                                     actor="agent")
        self.assertIn("Close the finding instead", str(caught.exception))

    def test_and_neither_can_one_that_does_not_exist(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        with self.assertRaises(review.CannotDefer):
            review.withdraw_deferral(conn, root, claim_id="never", why=WHY,
                                     actor="agent")

    def test_cancelling_needs_a_reason_of_its_own(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        self._deferred(root, conn)
        with self.assertRaises(review.CannotDefer):
            review.withdraw_deferral(conn, root, claim_id="c1", why="stale",
                                     actor="agent")


class TheLauncherRunsFromAnywhere(unittest.TestCase):
    """`bin/v4` was `exec python3 -m kernel.cli` and nothing else.

    So the command every document tells a reader to type worked only with the
    framework root as the working directory -- while
    `install.write_launcher` has always written an adopter launcher that sets
    `PYTHONPATH`, so the two disagreed about whether that was needed. Found by
    the first thing that ran the documented walkthrough from a throwaway repo:
    `No module named 'kernel'` on the first command.
    """

    def test_v4_answers_from_another_directory(self):
        elsewhere = _repo(self)
        r = subprocess.run([str(ROOT / "bin" / "v4"), "--repo", str(ROOT),
                            "explain", "--kind", "fail-closed"],
                           cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("checkers/fail_closed.py", r.stdout)

    def test_the_documented_walkthrough_runs_end_to_end(self):
        """`surface-proof`'s subject here: the five commands USING.md §1 names,
        through the launcher, against a repo that did not exist a moment ago."""
        probe = Path(tempfile.mkdtemp()) / "probe"
        self.addCleanup(shutil.rmtree, probe.parent, ignore_errors=True)
        r = subprocess.run(["bash", str(ROOT / "tools" / "surface_probe.sh")],
                           cwd=ROOT, capture_output=True, text=True,
                           env={**__import__("os").environ,
                                "V4_SURFACE_PROBE": str(probe)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("through the launcher", r.stdout)
        self.assertTrue((probe / ".v4" / "config.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
