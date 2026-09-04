"""Five behaviours the suite carried and never exercised.

    python3 -m unittest tests.test_unasserted_verdicts -v

Each one was found the same way: change the code so it stops doing what it
says, run the suite, and watch it stay green. Written together because they are
one shape -- a program whose verdict nothing asks for -- rather than because
they are one subject.

* `checkers/review_finding.py::_text_closure` -- `return 0` as its first
  statement, so every text closure passes unconditionally: 772 tests green.
* `kernel/redgreen.py::verify` -- `raise AssertionError` as its first
  statement, so no review finding can close at all: 772 tests green. Between
  them these are *both* routes by which a review finding closes.
* `kernel/sweep.py::raised_since` -- delete `AND origin = 'review'` from its
  SQL and the floor `reconcile` compares a sweep's self-report against starts
  counting detector-raised claims: green. The mechanism exists because a sweep
  reported 61 findings while the ledger held 1.
* `kernel/derive.py` -- the `python` count in `considered`. The one test on it
  asserted the *source text* of the writer, so mutating it to `1 + sum(...)` --
  making `_nothing_seen` unable to fire -- left everything green.
* `kernel/config.py::_load` -- the two errors it exists to raise. Grepping
  `tests/` for either message found nothing.
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

from kernel import config, derive, ledger, redgreen, sweep  # noqa: E402


def _repo(case, files=None):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / "detectors").mkdir()
    (tmp / "detectors" / "always_probe.py").write_text(
        "import argparse, sys\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--subject'); p.add_argument('--facts')\n"
        "p.add_argument('--out'); p.parse_args()\n"
        "print('V4-CLAIM: kind=probe')\n"
        "sys.exit(0)\n")
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({"probe": {
        "question_template": "q", "checker": "probe",
        "detector": "always_probe.py", "staleness": "repo"}}))
    (tmp / ".v4" / "checkers.json").write_text(json.dumps(
        {"probe": {"path": "checkers/probe.py", "kinds": ["probe"],
                   "reads": ["**/*.py"]}}))
    for rel, body in (files or {}).items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, check=True)
    return tmp


class AMalformedConfigIsNeverAnEmptyOne(unittest.TestCase):
    """`_load`'s own comment says so and nothing asked it."""

    def test_a_repo_with_no_config_says_which_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(tmp)
        self.assertIn("not found at", str(caught.exception))

    def test_a_config_that_is_not_json_is_an_error(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text("{ not json")
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(tmp)
        self.assertIn("is not valid JSON", str(caught.exception))

    def test_a_config_missing_a_required_field_is_an_error(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps({"policy": "x"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        with self.assertRaises(config.ConfigError):
            config.RepoConfig(tmp)


class TheSweepFloorCountsReviewFindings(unittest.TestCase):
    """`raised_since` is what `reconcile` holds a sweep's self-report to."""

    def _ledger(self):
        tmp = _repo(self)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        with ledger.writing(conn):
            ledger.insert(conn, "task", id="t", request="r",
                          scope_globs='["**"]', base_commit="x",
                          created_at="2026-01-01T00:00:00+00:00")
        return conn

    def _claim(self, conn, cid, origin, when="2026-06-01T00:00:00+00:00"):
        with ledger.writing(conn):
            ledger.insert(conn, "claim", id=cid, task_id="t",
                          kind="review-finding", question="q",
                          subject_refs="[]", checker="review-finding",
                          origin=origin, created_at=when)

    def test_a_detector_raised_claim_is_not_a_review_finding(self):
        """Without `AND origin = 'review'` the floor counts claims no reviewer
        filed, and a sweep that raised nothing reads as busy on somebody
        else's work."""
        conn = self._ledger()
        self._claim(conn, "c1", "review")
        self._claim(conn, "c2", "derive")
        self.assertEqual(sweep.raised_since(conn, None), 1)

    def test_the_window_starts_at_the_last_sweep(self):
        conn = self._ledger()
        self._claim(conn, "old", "review", "2026-01-02T00:00:00+00:00")
        self._claim(conn, "new", "review", "2026-07-01T00:00:00+00:00")
        from datetime import datetime, timezone
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(sweep.raised_since(conn, since), 1)


class WhatTheSubjectHeldIsCounted(unittest.TestCase):
    """`_nothing_seen` fires on `could_read is False`, so that flag is the whole
    mechanism -- and the test on it read the writer's source text.

    It used to fire on `considered["python"] == 0`, which was a proxy for "can
    anything here be parsed" and stopped being one the day a second language
    arrived. The probe detector's kind declares `reads: ["**/*.py"]`, so these
    two cases still separate -- by what the detector says it reads rather than
    by what the subject happens to be written in.
    """

    def test_a_repo_it_cannot_read_records_false(self):
        tmp = _repo(self, {"app.ts": "export const x = 1\n",
                           "README.md": "# hi\n"})
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        cfg = config.RepoConfig(tmp)
        with ledger.writing(conn):
            ledger.insert(conn, "task", id="t", request="r",
                          scope_globs='["**"]', base_commit="x",
                          created_at="2026")
        derive.derive(conn, cfg, task_id="t", scope_globs=["**"],
                      subject_files=["app.ts", "README.md"], phase="open")
        rows = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'detector_run'")]
        self.assertTrue(rows, "no detector ran, so nothing was counted")
        for r in rows:
            self.assertIs(r["could_read"], False, r)

    def test_a_repo_it_can_read_records_true(self):
        """The control: a flag hard-wired to False would pass the test above."""
        tmp = _repo(self, {"app.py": "x = 1\n"})
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        cfg = config.RepoConfig(tmp)
        with ledger.writing(conn):
            ledger.insert(conn, "task", id="t", request="r",
                          scope_globs='["**"]', base_commit="x",
                          created_at="2026")
        derive.derive(conn, cfg, task_id="t", scope_globs=["**"],
                      subject_files=["app.py"], phase="open")
        rows = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'detector_run'")]
        self.assertTrue(rows)
        for r in rows:
            self.assertIs(r["could_read"], True, r)


class BothRoutesByWhichAFindingClosesAreExercised(unittest.TestCase):
    """`redgreen.verify` and `_text_closure` -- neither was ever run.

    Between them they are every way a review finding reaches a terminal state
    other than a signature, and the suite could not tell either of them from a
    stub.
    """

    def _repo_with_history(self, closing_test):
        """A repo whose parent holds the defect and whose HEAD holds the fix.

        `unittest`, not `pytest`: this repo's own `test_command` is
        `python3 tests/run_without_silent_skips.py` and pytest is not a
        dependency, so a fixture that needs it would be a test about this
        laptop.
        """
        tmp = _repo(self, {"app.py": "def head(xs):\n    return xs[0]\n",
                           "t_app.py": "import unittest\n\n\n"
                                       "class T(unittest.TestCase):\n"
                                       "    def test_nothing(self):\n"
                                       "        self.assertTrue(True)\n"})
        parent = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                                capture_output=True, text=True).stdout.strip()
        (tmp / "app.py").write_text(
            "def head(xs):\n"
            "    if not xs:\n"
            "        return None\n"
            "    return xs[0]\n")
        (tmp / "t_app.py").write_text(closing_test)
        return tmp, parent

    #: Red at the parent (`head([])` raises IndexError), green at HEAD, and it
    #: enters the symbol -- all three, which is what `verify` asks for.
    REAL = ("import sys, unittest\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from app import head\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_empty_is_none(self):\n"
            "        self.assertIsNone(head([]))\n")

    #: Passes wherever it is run and never touches the symbol.
    VACUOUS = ("import unittest\n\n\n"
               "class T(unittest.TestCase):\n"
               "    def test_true(self):\n"
               "        self.assertTrue(True)\n")

    CMD = [sys.executable, "-m", "unittest", "-q", "{path}"]

    def _verify(self, closing_test):
        tmp, parent = self._repo_with_history(closing_test)
        return redgreen.verify(
            tmp, command=[c.replace("{path}", "t_app") for c in self.CMD],
            test_path="t_app.py", target_file="app.py", target_symbol="head",
            parent_commit=parent, timeout=120)

    def test_a_real_repair_closes(self):
        res = self._verify(self.REAL)
        self.assertTrue(res.red_failed, res.as_dict())
        self.assertTrue(res.green_passed, res.as_dict())
        self.assertTrue(res.symbol_executed, res.as_dict())
        self.assertTrue(res.ok, res.as_dict())

    def test_a_test_that_passes_everywhere_does_not_close(self):
        """Green at both ends proves the repair did nothing, which is the whole
        question `verify` exists to put."""
        res = self._verify(self.VACUOUS)
        self.assertFalse(res.red_failed, res.as_dict())
        self.assertFalse(res.ok, res.as_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
