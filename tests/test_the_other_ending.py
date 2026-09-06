"""A task has two endings and only one of them had an owner.

    python3 -m unittest tests.test_the_other_ending -v

`cmd_abandon` decided every refusal and wrote the terminal `abandoned` event
itself, on the entry surface, while its mirror `shipped` is written by
`lifecycle.ship`. Three findings from one sweep say that, and the third is what
the first two cost: a full run under `sys.monitoring` never records
`cmd_abandon`, and every test that needed an ended task wrote
`ledger.insert(kind="abandoned")` by hand -- the transition simulated rather
than made.

This file is deliberately free of source assertions, so it can close the
findings it is about; the one claim here about there being a single reader lives
in `test_two_modules_that_have_to_agree.py`.
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

from kernel import cli                                          # noqa: E402
from kernel import ledger as ledger_mod                         # noqa: E402
from kernel import lifecycle                                    # noqa: E402
from kernel.config import RepoConfig                            # noqa: E402

NOW = "2026-09-05T00:00:00+00:00"
WHY = "this task was overtaken by a rewrite and its scope no longer exists"


def _repo(case, *, with_task=True):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"]):
        subprocess.run(a, cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}),
        encoding="utf-8")
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"test": {"checker": "test", "question_template": "q",
                  "staleness": "repo"}}), encoding="utf-8")
    conn = ledger_mod.connect(tmp)
    if with_task:
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "task", id="t", request="r",
                              scope_globs=["**"], base_commit="",
                              created_at=NOW)
    return tmp, conn


class TheOwnerRefuses(unittest.TestCase):
    def test_a_task_that_is_not_there(self):
        tmp, conn = _repo(self, with_task=False)
        with self.assertRaises(lifecycle.CannotAbandon) as caught:
            lifecycle.abandon(conn, RepoConfig(tmp), "t-nope", WHY)
        self.assertIn("no such task", str(caught.exception))

    def test_a_task_that_already_shipped(self):
        tmp, conn = _repo(self)
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="shipped", actor="kernel", payload={},
                              created_at=NOW)
        with self.assertRaises(lifecycle.CannotAbandon) as caught:
            lifecycle.abandon(conn, RepoConfig(tmp), "t", WHY)
        self.assertIn("already shipped", str(caught.exception))

    def test_a_task_that_was_already_abandoned(self):
        """Both kinds, not one. A guard that knows about `shipped` and not
        `abandoned` lets the same task end twice."""
        tmp, conn = _repo(self)
        lifecycle.abandon(conn, RepoConfig(tmp), "t", WHY)
        with self.assertRaises(lifecycle.CannotAbandon) as caught:
            lifecycle.abandon(conn, RepoConfig(tmp), "t", WHY)
        self.assertIn("already abandoned", str(caught.exception))

    def test_a_reason_shorter_than_the_floor(self):
        tmp, conn = _repo(self)
        with self.assertRaises(lifecycle.CannotAbandon) as caught:
            lifecycle.abandon(conn, RepoConfig(tmp), "t", "changed my mind")
        said = str(caught.exception)
        self.assertIn("not allowed to be silent", said)
        self.assertIn("floor is", said)


class TheOwnerWrites(unittest.TestCase):
    def test_the_terminal_event_lands(self):
        tmp, conn = _repo(self)
        lifecycle.abandon(conn, RepoConfig(tmp), "t", WHY)
        row = conn.execute(
            "SELECT payload FROM event WHERE task_id = 't' AND "
            "kind = 'abandoned'").fetchone()
        self.assertIsNotNone(row, "no terminal event was written")
        self.assertEqual(json.loads(row["payload"])["why"], WHY)

    def test_and_carries_what_the_task_never_settled(self):
        """The half that goes hollow if the fixture has no findings: a task
        with a failing claim has to name it in the row, because the next task
        starts from a new base and stops asking."""
        tmp, conn = _repo(self)
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "claim", id="c1", task_id="t", kind="test",
                              question="q", subject_refs=[], checker="test",
                              origin="derive", file="a.py", symbol=None,
                              variant=None, line=None, note=None, detector=None,
                              detector_sha=None, created_at=NOW)
        ledger_mod.append_attempt(
            conn, claim_id="c1", subject_digest="{}", checker_sha="s",
            config_sha="cf", head_commit="hc", worktree="w", argv="[]",
            exit_code=1, stdout="", stderr="", started_at=NOW, ended_at=NOW,
            duration_ms=1, facts_sha="")
        unsettled = lifecycle.abandon(conn, RepoConfig(tmp), "t", WHY)
        self.assertTrue(unsettled, "a FAILing claim was not carried")
        row = conn.execute(
            "SELECT payload FROM event WHERE task_id = 't' AND "
            "kind = 'abandoned'").fetchone()
        self.assertIn("c1", json.loads(row["payload"])["unsettled_fails"])


class TheCommandStillReachesIt(unittest.TestCase):
    """The other half of the finding: nothing had ever entered `cmd_abandon`,
    so the wiring between the command and the transition was unasserted."""

    def test_the_entry_itself_runs(self):
        """`cmd_abandon` in this process, not through a subprocess. The tracer
        behind a closure observes this one, so a command only ever reached in a
        child reads as never executed -- measured, and it is why this case
        exists beside the subprocess ones."""
        import argparse
        import io
        from contextlib import redirect_stdout

        tmp, _conn = _repo(self)
        args = argparse.Namespace(repo=str(tmp), task="t", why=WHY,
                                  acceptance=".v4/acceptance.json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.cmd_abandon(args)
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("abandoned", buf.getvalue())

    def test_and_the_entry_returns_two_on_a_refusal(self):
        import argparse
        import io
        from contextlib import redirect_stdout

        tmp, _conn = _repo(self)
        args = argparse.Namespace(repo=str(tmp), task="t", why="too short",
                                  acceptance=".v4/acceptance.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_abandon(args), 2)

    def test_the_cli_abandons_and_says_so(self):
        tmp, _conn = _repo(self)
        r = subprocess.run(
            [str(ROOT / "bin" / "v4"), "--repo", str(tmp), "abandon",
             "--task", "t", "--why", WHY],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertIn("abandoned", r.stdout)
        conn = ledger_mod.connect(tmp)
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM event WHERE task_id = 't' AND kind = 'abandoned'"
        ).fetchone())

    def test_and_a_refusal_reaches_the_exit_code(self):
        tmp, _conn = _repo(self)
        r = subprocess.run(
            [str(ROOT / "bin" / "v4"), "--repo", str(tmp), "abandon",
             "--task", "t", "--why", "too short"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 2)
        self.assertIn("REFUSED", r.stderr)

    def test_and_a_missing_task_is_not_a_refusal(self):
        """It is a different sentence, and the exit code is the same: nothing
        was refused, the thing was not there."""
        tmp, _conn = _repo(self, with_task=False)
        r = subprocess.run(
            [str(ROOT / "bin" / "v4"), "--repo", str(tmp), "abandon",
             "--task", "t-nope", "--why", WHY],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such task", r.stderr)
        self.assertNotIn("REFUSED", r.stderr)


class OneReaderForWhatADeriveWithdrew(unittest.TestCase):
    def test_it_reads_the_shape_the_producer_makes(self):
        lines = cli._retraction_lines(
            [("c1", "review-finding", ["a.py"], "the code it was about is gone"),
             ("c2", "fail-closed", ["b.py"], "the rule that raised it was narrowed")])
        self.assertEqual(len(lines), 2)
        self.assertIn("c1", lines[0])
        self.assertIn("narrowed", lines[1])

    def test_and_says_each_reason_rather_than_one_for_both(self):
        """The dead fallback both copies carried named only the first of the
        two, so the copy that ran and the copy that could not both said
        something the producer contradicts."""
        lines = cli._retraction_lines(
            [("c2", "fail-closed", ["b.py"], "the rule that raised it was narrowed")])
        self.assertNotIn("the code it was about is gone", lines[0])

    def test_the_command_that_prints_them_runs(self):
        """`cmd_derive` itself, in this process. The finding is filed there,
        and a test that only exercises the helper it now calls never enters the
        command that was repaired."""
        import argparse
        import io
        from contextlib import redirect_stdout

        tmp, _conn = _repo(self)
        (tmp / ".v4" / "detectors.json").write_text("{}", encoding="utf-8")
        args = argparse.Namespace(repo=str(tmp), task="t", phase="check",
                                  acceptance=".v4/acceptance.json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.cmd_derive(args)
        self.assertEqual(code, 0, buf.getvalue()[:400])
        self.assertIn("detector(s)", buf.getvalue())

    def test_nothing_withdrawn_prints_nothing(self):
        self.assertEqual(cli._retraction_lines([]), [])


if __name__ == "__main__":
    unittest.main()
