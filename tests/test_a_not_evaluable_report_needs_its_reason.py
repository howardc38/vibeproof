"""`v4 review done --result not_evaluable` needed no reason at all.

    python3 -m unittest tests.test_a_not_evaluable_report_needs_its_reason -v

`kernel/review.py::record_lens_reviewed` has stored the result on the run-less
row since ef6798a -- and stopped there. `--result not_evaluable --findings 0`
with no `--note` and no `--evidence` exited 0 and left
`{"lens": X, "findings": 0, "result": "not_evaluable"}`: a report that says the
lens was not evaluated and not one word about why. Nothing downstream can argue
with that row or check its reason later, which is the same silence the
`--findings` refusal exists to prevent, one field over.

`kernel/maintenance.py::lens_result`, the run-bound writer, refused exactly
that from the day it was written ("a non-completed result needs its reason").
The run-less spelling is the one a sweep's reviewer actually uses, because a
sweep has no maintenance run to name. The rule now lives in one function,
`maintenance.check_lens_reason`, and both writers call it: a result other than
`completed` needs a non-blank note or a non-blank evidence reference, and the
run-less writer answers with `BadCoordinates`, which the CLI prints as REFUSED
and exits 2. Evidence is an alternative, not a weaker floor -- the run-bound
writer took only a note before, and an evidence reference is the same fact
with a pointer on it.

Against 77909ae this file is 8 failures, 1 error and 5 passes. The failures
are the five refusal cases -- four at the writer, one of them reported once
per non-completed result and once for the rows it had already left, and the
CLI's exit code; the error is the run-bound evidence alternative, which that
writer refused. The five passes are the controls: a note is a reason,
evidence is a reason, `completed` needs none, the CLI accepts a report that
carries one, and the run-bound writer still refuses a reasonless report.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, ledger, maintenance, review  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                ["git", "config", "user.name", "c"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    d = tmp / ".v4" / "lenses"
    d.mkdir()
    (d / "probe.json").write_text(json.dumps(
        {"name": "probe", "source": "test", "checks": ["one thing"],
         "anti_patterns": ["not another"]}, ensure_ascii=False))
    (tmp / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, capture_output=True)
    return tmp


def _rows(root):
    conn = ledger.connect(root)
    try:
        return [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'lens_reviewed' ORDER BY id")]
    finally:
        conn.close()


def _run(fn, args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(args)
    return code, out.getvalue(), err.getvalue()


def _done_args(root, **over):
    """Every flag `review done` reads, plus the ones the parser defines.

    Copied from the shape the CLI actually hands `cmd_review`, because a
    missing attribute here is an AttributeError inside the command rather than
    the refusal under test.
    """
    a = dict(repo=str(root), action="done", lens="probe", task=None,
             findings=0, result="not_evaluable", note=None, evidence=None,
             file=None, symbol=None, claim=None, test=None, command=None,
             parent=None, gone=None, now=None, why=None, target=None,
             name=None, withdraw=False, run=None)
    a.update(over)
    return SimpleNamespace(**a)


class TheRunLessWriterRefusesAReportWithNoReason(unittest.TestCase):
    """The writer, not the parser, is where the rule lives: `--result` is
    passed to it by callers that are not argv, and the last defect in this
    family was a rule that only the parser enforced."""

    def _write(self, root, **kw):
        conn = ledger.connect(root)
        try:
            return review.record_lens_reviewed(conn, root, slug="probe",
                                               findings=0, **kw)
        finally:
            conn.close()

    def test_a_not_evaluable_with_no_reason_is_refused(self):
        """The reproduced defect: this call exited 0 and left a row saying the
        lens was not evaluated, with nothing saying why."""
        root = _repo(self)
        with self.assertRaises(review.BadCoordinates) as caught:
            self._write(root, result="not_evaluable")
        self.assertIn("reason", str(caught.exception))
        self.assertEqual(_rows(root), [])

    def test_every_non_completed_result_needs_one(self):
        """`completed` is the one result that says a lens was reviewed; the
        other three all report why it was not, and none of them may be
        silent."""
        root = _repo(self)
        for result in ("not_applicable", "not_evaluable", "failed"):
            with self.subTest(result=result):
                with self.assertRaises(review.BadCoordinates):
                    self._write(root, result=result)
        self.assertEqual(_rows(root), [])

    def test_a_blank_note_is_not_a_reason(self):
        """`--note '   '` is a value the parser takes, and it says nothing.
        The rule is a *non-blank* note, so the check cannot be `if note`."""
        root = _repo(self)
        with self.assertRaises(review.BadCoordinates):
            self._write(root, result="not_evaluable", note="   ")
        self.assertEqual(_rows(root), [])

    def test_blank_evidence_entries_are_not_a_reason(self):
        """An empty `--evidence ''` is an argv shape `action="append"` makes
        easy, and a list of them is a list of nothing."""
        root = _repo(self)
        with self.assertRaises(review.BadCoordinates):
            self._write(root, result="not_evaluable", evidence=["", "  "])
        self.assertEqual(_rows(root), [])


class TheReportThatCarriesItsReasonIsKept(unittest.TestCase):
    """The controls. The rule is about the reason being absent, not about
    `not_evaluable` being unwritable -- a reviewer who says why must be able
    to say it, or the refusal teaches people to claim a clean review."""

    def _write(self, root, **kw):
        conn = ledger.connect(root)
        try:
            return review.record_lens_reviewed(conn, root, slug="probe",
                                               findings=0, **kw)
        finally:
            conn.close()

    def test_a_note_is_a_reason(self):
        root = _repo(self)
        self._write(root, result="not_evaluable",
                    note="no request/change context on a tree sweep")
        self.assertEqual(_rows(root), [{
            "lens": "probe", "findings": 0, "result": "not_evaluable",
            "note": "no request/change context on a tree sweep"}])

    def test_an_evidence_reference_alone_is_a_reason(self):
        root = _repo(self)
        self._write(root, result="failed",
                    evidence=["sweep of 2026-10-10, brief never printed"])
        self.assertEqual(_rows(root), [{
            "lens": "probe", "findings": 0, "result": "failed",
            "evidence": ["sweep of 2026-10-10, brief never printed"]}])

    def test_a_completed_report_still_needs_no_reason(self):
        """The one result that means "it was evaluated". Requiring a reason
        here would make the ordinary report the one with an extra step."""
        root = _repo(self)
        self._write(root, result="completed")
        self.assertEqual(_rows(root),
                         [{"lens": "probe", "findings": 0,
                           "result": "completed"}])


class TheCommandLineSaysRefused(unittest.TestCase):
    def test_a_reasonless_not_evaluable_exits_two_and_writes_nothing(self):
        root = _repo(self)
        code, said, err = _run(cli.cmd_review, _done_args(root))
        self.assertEqual(code, 2, said)
        self.assertIn("REFUSED", err)
        self.assertIn("reason", err)
        self.assertEqual(_rows(root), [])

    def test_the_two_other_ways_to_report_still_work(self):
        for flag, value in (("note", "the sweep reviews a tree, not a diff"),
                            ("evidence", ["sweep of 2026-10-10"])):
            with self.subTest(flag=flag):
                root = _repo(self)
                code, said, err = _run(cli.cmd_review,
                                       _done_args(root, **{flag: value}))
                self.assertEqual(code, 0, err)
                self.assertIn("not_evaluable", said)
                row = _rows(root)[0]
                self.assertEqual(row["result"], "not_evaluable")
                self.assertIn(flag, row)


class TheRunBoundWriterSharesTheRule(unittest.TestCase):
    """The run-bound `lens_result` refused a reasonless report already, and
    that is the answer that must not weaken -- so it is asserted here too.
    What changes is the alternative: it took only a note before, and the
    shared rule takes an evidence reference as the same fact."""

    def _briefed(self, root, run_id="r"):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        maintenance.start(root, run_id=run_id, selected=["probe"])
        review.record_lens_run(conn, slug="probe",
                               lens=review.lenses(root)["probe"],
                               root=root, run_id=run_id)
        return conn

    def test_a_reasonless_run_bound_report_is_still_refused(self):
        root = _repo(self)
        conn = self._briefed(root)
        with self.assertRaises(ValueError):
            review.record_lens_reviewed(conn, root, slug="probe", findings=0,
                                        run_id="r", result="not_evaluable")
        self.assertEqual(_rows(root), [])

    def test_an_evidence_reference_is_the_reason_for_a_run_bound_report(self):
        root = _repo(self)
        conn = self._briefed(root)
        review.record_lens_reviewed(
            conn, root, slug="probe", findings=0, run_id="r",
            result="not_evaluable", evidence=["the requested input is missing"])
        self.assertEqual(_rows(root)[0]["evidence"],
                         ["the requested input is missing"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
