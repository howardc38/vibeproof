"""`doctor`'s unanswerable-kinds line, and the rows it should not carry.

The line answers "is a kind blocked on exit 4 with nobody able to close it",
and its remedy is to add what the checker reads or sign the kind off for the
repo. Neither is available for a kind the registry no longer has: nothing can
raise one, and the `--kind unprovable` cover it prints names a kind that is not
in `claim_kinds.json`.

Measured on this repo before the filter: `dependency`, `request-coverage` and
`secret-chain` on the line, all three removed from the registry, all three
reported as work somebody had to do. `secret` was there too and is real -- it is
still a kind, so it stays.

The pair below is the point. The first says the dead kind is gone; the second
says the live one beside it survived, because a filter that quietly emptied the
line would pass the first test while removing the check.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import doctor, ledger                                # noqa: E402


def _repo(case, kinds, claims):
    """A ledger holding one exit-4 attempt per `claims` entry (kind, task)."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    (tmp / ".v4").mkdir()
    # `ledger.connect` reaches `layout.repo_name`, which asks git where the
    # main worktree is. A bare directory answers with its basename; a directory
    # that looks like a repo without being one makes git exit 128.
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {k: {"checker": k, "question_template": "q"} for k in kinds}))
    conn = ledger.connect(tmp)
    with ledger.writing(conn):
        when = "2026-09-01T00:00:00+00:00"
        for i, (kind, task) in enumerate(claims):
            ledger.insert(conn, "task", id=task, request="r", scope_globs=["**"],
                          base_commit="", created_at=when)
            cid = f"c{i}"
            ledger.insert(conn, "claim", id=cid, task_id=task, kind=kind,
                          question="q", subject_refs=[], checker=kind,
                          origin="derive", file=None, symbol=None, variant=None,
                          line=None, note=None, detector=None, detector_sha=None,
                          created_at=when)
            ledger.append_attempt(
                conn, claim_id=cid, subject_digest="{}", checker_sha="s",
                config_sha="cf", head_commit="hc", worktree="w", argv="[]",
                exit_code=4, stdout="", stderr="", started_at=when,
                ended_at=when, duration_ms=1, facts_sha="",
                claim_digest=ledger.claim_digest(conn, cid))
    conn.close()
    return tmp


def _line(root):
    out = []
    doctor._check_kinds_this_repo_keeps_failing_to_answer(root, out)
    return next((c for c in out if c["what"] == "unanswerable kinds"), None)


class AKindTheRegistryDropped(unittest.TestCase):
    def test_it_is_not_reported(self):
        root = _repo(self, kinds=["secret"],
                     claims=[("secret-chain", "t-old")])
        line = _line(root)
        self.assertTrue(line is None or "secret-chain" not in line["detail"],
                        line and line["detail"])

    def test_and_a_kind_that_is_still_registered_is(self):
        """Otherwise the filter is an off switch wearing a fix's clothes."""
        root = _repo(self, kinds=["secret"], claims=[("secret", "t-live")])
        line = _line(root)
        self.assertIsNotNone(line)
        self.assertIn("secret", line["detail"])

    def test_the_live_one_survives_beside_the_dead_one(self):
        root = _repo(self, kinds=["secret"],
                     claims=[("secret", "t-live"), ("secret-chain", "t-old")])
        line = _line(root)
        self.assertIsNotNone(line)
        self.assertIn("secret", line["detail"])
        self.assertNotIn("secret-chain", line["detail"])


class ThisReposOwnLine(unittest.TestCase):
    def test_no_kind_it_names_is_missing_from_the_registry(self):
        """The report and the registry, read against each other."""
        line = _line(ROOT)
        if line is None:
            return
        known = set(json.loads(
            (ROOT / ".v4" / "claim_kinds.json").read_text()))
        named = {part.split(" blocked in ")[0].strip()
                 for part in line["detail"].split(";") if " blocked in " in part}
        self.assertTrue(named <= known, sorted(named - known))


if __name__ == "__main__":
    unittest.main()
