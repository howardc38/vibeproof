"""A task-scoped signature names the task it is for, and is filed where it lives.

`v4 risk accept` declared `--task` and never read it. An id copied out of a
stop-gate line -- which prints ids without saying whose task they are -- signed
whatever claim it named, from whatever checkout ran it. The record then lands
on the wrong branch and the ledger row cannot be taken back, which is the one
property a signature has.

Measured on an adopter running eight worktrees: four permanent `accepted_risk`
rows written from the wrong lane before anyone noticed.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import config, ledger, lifecycle, risk  # noqa: E402

WHY = "there is genuinely nothing this checker can read in this repository today"


def _git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)


class RiskAcceptSignsOnlyForTheTaskAndWorktreeItNames(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base)])
        self.main = base / "main"
        self.main.mkdir()
        _git(self.main, "init", "-q")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        (self.main / ".v4").mkdir()
        (self.main / ".v4/config.json").write_text(json.dumps(
            {"repo": "r", "test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 24}}))
        (self.main / "a.py").write_text("x = 1\n")
        _git(self.main, "add", "-A")
        _git(self.main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
        self.other = base / "wt-other"
        _git(self.main, "worktree", "add", "-q", "--detach", str(self.other))

        self.conn = ledger.connect(self.main)
        self.addCleanup(self.conn.close)
        for tid, tree in (("t-mine", self.main), ("t-other", self.other)):
            lifecycle.open_task(self.conn, config.RepoConfig(tree), task_id=tid,
                                request="r" * 40, scope_globs=["**"])
        ledger.insert(self.conn, "claim", id="c-other", task_id="t-other", kind="probe",
                      checker="probe", question="q", file="a.py", symbol="", variant="",
                      subject_refs=json.dumps([]), origin="derive", note="",
                      created_at="2026-01-01T00:00:00+00:00")
        self.conn.commit()

    def _sign(self, cfg_root, **kw):
        return risk.accept(self.conn, config.RepoConfig(cfg_root), claim_id="c-other",
                           kind="unprovable", why=WHY, require_tty=False, **kw)

    def test_a_claim_of_another_task_is_refused_and_says_whose_it_is(self):
        with self.assertRaises(risk.RefusedToSign) as caught:
            self._sign(self.main, task_id="t-mine")
        self.assertIn("t-other", str(caught.exception))
        self.assertEqual(self._rows(), [])

    def test_an_open_task_is_signed_from_its_own_worktree_only(self):
        """No `--task` at all was the old shape, and it signed from anywhere."""
        with self.assertRaises(risk.RefusedToSign) as caught:
            self._sign(self.main)
        self.assertIn(str(self.other), str(caught.exception))
        self.assertEqual(self._rows(), [])

    def test_the_worktree_it_was_opened_in_still_signs(self):
        """The green half: this route exists and has to keep working."""
        self._sign(self.other, task_id="t-other")
        self.assertEqual(self._rows(), [("c-other", "unprovable")])

    def test_an_ended_task_is_settled_from_anywhere(self):
        """A task that has ended has no worktree to be sent back to, and
        settling one from main is how a finished lane gets tidied up."""
        lifecycle.abandon(self.conn, config.RepoConfig(self.other), "t-other",
                          why="this lane is being dropped and the record says so plainly")
        self._sign(self.main, task_id="t-other")
        self.assertEqual(self._rows(), [("c-other", "unprovable")])

    def _rows(self):
        return [tuple(r) for r in self.conn.execute(
            "SELECT claim_id, kind FROM accepted_risk").fetchall()]


if __name__ == "__main__":
    unittest.main(verbosity=2)
