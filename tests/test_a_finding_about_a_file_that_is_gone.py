"""A finding whose subject was deleted, and the two guards around retracting it.

    python3 -m unittest tests.test_a_finding_about_a_file_that_is_gone -v

`_retract_orphans` says in its own words that "Renaming a file is not a risk
anybody should have to sign for", and it could not reach a single review
finding: its first guard is `row["detector"] not in healthy`, a review finding
has no detector, and `None not in healthy` is true forever. Measured on this
repo: 25 open findings about `dep_provenance.py`, `sweep_current.py`,
`dal_write.py` and six other modules removed at `a9ae5fb`, plus the facts table
under its pre-rename filename -- every one of them closable only by a signature
saying the code was gone.

The guard's stated purpose is to stop a *crashed* detector retracting the
claims it owns. A claim with no detector has none to crash, so the guard was
excluding exactly the claims its reasoning does not cover.

The danger in the repair is the other direction, and it is the one this file
spends most of its cases on: a mechanism that makes findings disappear is the
thing this whole system exists to refuse. So the file has to be gone, the
detector guard has to still hold for claims that have one, and a claim about
nothing in particular must not be swept up by an empty file list.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import derive as derive_mod                         # noqa: E402
from kernel import ledger as ledger_mod                         # noqa: E402

NOW = "2026-09-05T00:00:00+00:00"


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"]):
        subprocess.run(a, cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / "here.py").write_text("def f():\n    return 1\n")
    conn = ledger_mod.connect(tmp)
    with ledger_mod.writing(conn):
        ledger_mod.insert(conn, "task", id="t", request="r",
                          scope_globs=["**"], base_commit="", created_at=NOW)
    return tmp, conn


def _claim(conn, cid, *, files, detector=None, task="t"):
    with ledger_mod.writing(conn):
        ledger_mod.insert(
            conn, "claim", id=cid, task_id=task, kind="review-finding",
            question="q", subject_refs=[{"kind": "file", "path": f}
                                        for f in files],
            checker="review-finding", origin="review", file=files[0] if files
            else None, symbol=None, variant="a-lens", line=None, note="n",
            detector=detector, detector_sha=None, created_at=NOW)


def _run(conn, root, *, seen=frozenset(), healthy=frozenset()):
    with ledger_mod.writing(conn):
        return derive_mod._retract_orphans(conn, root, "t", set(seen),
                                           set(healthy), NOW)


class AFindingWhoseFileIsGone(unittest.TestCase):
    def test_a_detector_less_claim_about_a_deleted_file_retracts(self):
        root, conn = _repo(self)
        _claim(conn, "c1", files=["kernel/analysis/dep_provenance.py"])
        out = _run(conn, root)
        self.assertEqual([o[0] for o in out], ["c1"], out)
        self.assertIn("gone", out[0][3])

    def test_but_one_about_a_file_that_is_still_here_does_not(self):
        """The direction that matters. A mechanism that makes findings vanish
        is the thing this system exists to refuse, and 107 open findings in
        this repo are about files that are still on disk."""
        root, conn = _repo(self)
        _claim(conn, "c2", files=["here.py"])
        self.assertEqual(_run(conn, root), [])

    def test_a_claim_naming_both_a_gone_file_and_a_live_one_stays(self):
        """`gone` is every file, not any file."""
        root, conn = _repo(self)
        _claim(conn, "c3", files=["here.py", "kernel/analysis/dal_write.py"])
        self.assertEqual(_run(conn, root), [])

    def test_a_claim_with_no_subject_file_is_not_swept_up(self):
        """`bool(files)` already guarded this; a repo-scoped claim has no file
        and an empty list must not read as "all of them are gone"."""
        root, conn = _repo(self)
        _claim(conn, "c4", files=[])
        self.assertEqual(_run(conn, root), [])


class TheDetectorGuardStillHoldsForClaimsThatHaveOne(unittest.TestCase):
    def test_a_detector_that_did_not_complete_retracts_nothing(self):
        """The reason the guard exists: a detector that crashed emits nothing,
        and reading silence as "no longer applies" would let a broken detector
        retract every claim it owns and ship a clean task."""
        root, conn = _repo(self)
        _claim(conn, "c5", files=["kernel/analysis/dep_provenance.py"],
               detector="some_detector.py")
        self.assertEqual(_run(conn, root, healthy=()), [])

    def test_and_one_that_did_complete_still_retracts(self):
        root, conn = _repo(self)
        _claim(conn, "c6", files=["kernel/analysis/dep_provenance.py"],
               detector="some_detector.py")
        out = _run(conn, root, healthy={"some_detector.py"})
        self.assertEqual([o[0] for o in out], ["c6"])

    def test_the_settled_route_stays_shut_for_a_claim_with_no_detector(self):
        """`settled` means a detector was narrowed until it stopped raising
        something. A claim without one cannot be in that state, and letting it
        through there would retract a live finding whose file is on disk."""
        root, conn = _repo(self)
        _claim(conn, "c7", files=["here.py"])
        ledger_mod.append_attempt(
            conn, claim_id="c7", subject_digest="{}", checker_sha="s",
            config_sha="cf", head_commit="hc", worktree="w", argv="[]",
            exit_code=0, stdout="", stderr="", started_at=NOW, ended_at=NOW,
            duration_ms=1, facts_sha="")
        self.assertEqual(_run(conn, root), [],
                         "a passing attempt must not retract a live subject")


class TheContainerRunsIt(unittest.TestCase):
    """`derive` returns early for the review task -- rightly, it is a container
    and detectors have nothing to say about it -- and the early return skipped
    the retraction too, so the repair above could not be reached by any
    command."""

    def test_the_early_return_carries_the_retractions(self):
        """Run `derive` itself, not read it. The first spelling of this repair
        worked when the kernel function was called by hand and did nothing
        through any command, because the early return dropped the result -- so
        what has to be asserted is the command's own answer."""
        from kernel.config import RepoConfig

        root, conn = _repo(self)
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}), encoding="utf-8")
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding",
                                "question_template": "q",
                                "staleness": "subject"}}), encoding="utf-8")
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "task", id=ledger_mod.REVIEW_TASK,
                              request="r", scope_globs=["**"], base_commit="",
                              created_at=NOW, if_absent=True)
        _claim(conn, "orphan", files=["kernel/analysis/dep_provenance.py"],
               task=ledger_mod.REVIEW_TASK)
        out = derive_mod.derive(conn, RepoConfig(root),
                                task_id=ledger_mod.REVIEW_TASK,
                                scope_globs=["**"], subject_files=[],
                                phase="check")
        self.assertEqual([r[0] for r in out["retracted"]], ["orphan"], out)

    def test_and_a_second_run_retracts_nothing(self):
        """Idempotent, because the row is terminal after the first pass. A
        container that re-retracts on every derive would write a new event per
        run into an append-only table."""
        from kernel.config import RepoConfig

        root, conn = _repo(self)
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}), encoding="utf-8")
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding",
                                "question_template": "q",
                                "staleness": "subject"}}), encoding="utf-8")
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "task", id=ledger_mod.REVIEW_TASK,
                              request="r", scope_globs=["**"], base_commit="",
                              created_at=NOW, if_absent=True)
        _claim(conn, "orphan", files=["gone/nowhere.py"],
               task=ledger_mod.REVIEW_TASK)
        cfg = RepoConfig(root)
        kw = dict(task_id=ledger_mod.REVIEW_TASK, scope_globs=["**"],
                  subject_files=[], phase="check")
        self.assertEqual(len(derive_mod.derive(conn, cfg, **kw)["retracted"]), 1)
        self.assertEqual(derive_mod.derive(conn, cfg, **kw)["retracted"], [])


if __name__ == "__main__":
    unittest.main()
