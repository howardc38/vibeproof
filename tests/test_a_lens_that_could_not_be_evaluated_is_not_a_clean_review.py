"""`v4 review done --result not_evaluable` was stored as a clean review.

    python3 -m unittest tests.test_a_lens_that_could_not_be_evaluated_is_not_a_clean_review -v

`kernel/review.py::record_lens_reviewed` stored `result`, `note` and
`evidence` only when `--run` named a maintenance run, and
`kernel/cli.py::cmd_review` passed them in every time. So the run-less form

    v4 review done --lens X --result not_evaluable --findings 0 --evidence '...'

exited 0 and left `{"lens": X, "findings": 0}` -- the same row a lens that ran
and found nothing writes, which is the distinction `--findings` exists to
make. Every reader then counted it as a clean review: `sweep.reviewed_since`
(and through it `v4 sweep --done`, `--history` and the sweep record),
`lifecycle._lenses_reviewed` (and through it `v4 ship`'s `reviewed by:` line),
and the ship payload.

Two things are asserted here. First, the writer keeps what it was handed and
refuses a result outside `maintenance.RESULTS`, so the row can say it was not
an evaluation. Second, the readers read the two apart without either one
becoming silence: `reviewed` is coverage, the new `inconclusive` key is the
report that a lens was not evaluated, and a row written before the field
existed still means completed.

Against e259258 this file is 10 failures, 1 error and 1 pass, not one clean
red. The error is `ARowFromBeforeTheFieldMeansCompleted`: it calls
`sweep.inconclusive_since`, which does not exist at the base. The pass is
`test_a_briefed_lens_that_never_reported_still_holds_the_cadence`, the
sweep-side control that silence is not `not_evaluable`. The old row does read
as `completed` on the branch, but at the base that test errors rather than
passing.
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

from kernel import cli, ledger, lifecycle, review, sweep  # noqa: E402


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
    for name in ("probe", "context-lens"):
        (d / f"{name}.json").write_text(json.dumps(
            {"name": name, "source": "test", "checks": ["one thing"],
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


class TheWriterKeepsTheResultOnARunLessRow(unittest.TestCase):
    def test_a_not_evaluable_report_keeps_its_result_note_and_evidence(self):
        """The reproduced defect: exit 0, and the row was only
        `{"lens": "near-miss", "findings": 0}`."""
        root = _repo(self)
        conn = ledger.connect(root)
        try:
            review.record_lens_reviewed(
                conn, root, slug="context-lens", findings=0,
                result="not_evaluable", note="no request/change context",
                evidence=["the sweep reviews a tree, not a diff"])
        finally:
            conn.close()
        self.assertEqual(_rows(root), [{
            "lens": "context-lens", "findings": 0, "result": "not_evaluable",
            "note": "no request/change context",
            "evidence": ["the sweep reviews a tree, not a diff"]}])

    def test_a_completed_report_still_names_its_result(self):
        """`completed` is written rather than implied, so a reader never has
        to treat "the field is absent" as the only way to say it."""
        root = _repo(self)
        conn = ledger.connect(root)
        try:
            review.record_lens_reviewed(conn, root, slug="probe", findings=0)
        finally:
            conn.close()
        self.assertEqual(_rows(root),
                         [{"lens": "probe", "findings": 0, "result": "completed"}])

    def test_a_result_outside_the_vocabulary_is_refused_at_the_writer(self):
        """The parser's `choices` is argv-shaped; a caller that is not argv
        gets the same rule here, and no row is written."""
        root = _repo(self)
        conn = ledger.connect(root)
        try:
            with self.assertRaises(ValueError):
                review.record_lens_reviewed(conn, root, slug="probe",
                                            findings=0, result="probably-fine")
        finally:
            conn.close()
        self.assertEqual(_rows(root), [])

    def test_the_command_line_keeps_the_result_it_was_given(self):
        root = _repo(self)
        args = SimpleNamespace(repo=str(root), action="done", lens="context-lens",
                               task=None, findings=0, result="not_evaluable",
                               note="the sweep has no request context",
                               evidence=["sweep of 2026-10-10"], file=None,
                               symbol=None, claim=None, test=None, command=None,
                               parent=None, gone=None, now=None, why=None,
                               target=None, name=None, withdraw=False, run=None)
        code, said, err = _run(cli.cmd_review, args)
        self.assertEqual(code, 0, err)
        self.assertIn("not_evaluable", said)
        self.assertNotIn("reviewed,", said)
        self.assertEqual(_rows(root)[0]["result"], "not_evaluable")


class TheSweepReadsTheTwoApart(unittest.TestCase):
    def _report(self, root, lens, result, findings=0):
        """Through the writer, with the reason it now requires.

        A non-completed result without a non-blank note or evidence is refused
        (`record_lens_reviewed` shares `maintenance.check_lens_reason` with the
        run-bound writer), so the not_evaluable rows here carry one. What each
        test asserts about the readers is unchanged: the note is not read.
        """
        conn = ledger.connect(root)
        try:
            review.record_lens_reviewed(
                conn, root, slug=lens, findings=findings, result=result,
                note="" if result == "completed" else
                "why this lens was not evaluated")
        finally:
            conn.close()

    def test_a_not_evaluable_lens_is_not_counted_as_coverage(self):
        root = _repo(self)
        self._report(root, "probe", "completed")
        self._report(root, "context-lens", "not_evaluable")
        conn = ledger.connect(root)
        try:
            self.assertEqual(sweep.reviewed_since(conn), {"probe": 0})
            self.assertEqual(sweep.inconclusive_since(conn),
                             {"context-lens": "not_evaluable"})
        finally:
            conn.close()

    def test_the_record_holds_both_and_still_completes(self):
        """A tree sweep always carries context-requiring lenses. Their
        `not_evaluable` is a report -- not silence, which would hold the
        cadence partial forever -- and not coverage either."""
        root = _repo(self)
        self._report(root, "probe", "completed")
        self._report(root, "context-lens", "not_evaluable")
        conn = ledger.connect(root)
        try:
            payload = sweep.record(conn, lenses=["probe", "context-lens"],
                                   findings=0)
        finally:
            conn.close()
        self.assertEqual(payload["reviewed"], {"probe": 0})
        self.assertEqual(payload["inconclusive"],
                         {"context-lens": "not_evaluable"})
        self.assertTrue(payload["complete"])

    def test_a_briefed_lens_that_never_reported_still_holds_the_cadence(self):
        """The control for the rule above: silence is not `not_evaluable`."""
        root = _repo(self)
        conn = ledger.connect(root)
        try:
            review.record_lens_run(conn, slug="probe",
                                   lens=review.lenses(root)["probe"])
            payload = sweep.record(conn, lenses=["probe"], findings=None)
        finally:
            conn.close()
        self.assertFalse(payload["complete"])

    def test_the_command_line_says_which_lenses_did_not_evaluate(self):
        root = _repo(self)
        self._report(root, "probe", "completed")
        self._report(root, "context-lens", "not_evaluable")
        args = SimpleNamespace(repo=str(root), done=True, history=False,
                               if_due=False, findings=None, note=None)
        code, said, err = _run(cli.cmd_sweep, args)
        # Both lenses reported, so the sweep is accounted for -- the
        # `not_evaluable` row is a report and not silence -- and the printed
        # line is what says it was not an evaluation.
        self.assertEqual(code, 0, err)
        self.assertIn("reported without evaluating: context-lens (not_evaluable)",
                      said)
        self.assertNotIn("briefed and never reported back: context-lens", said)

    def test_the_history_counts_the_two_apart(self):
        root = _repo(self)
        self._report(root, "probe", "completed")
        self._report(root, "context-lens", "not_evaluable")
        conn = ledger.connect(root)
        try:
            sweep.record(conn, lenses=["probe", "context-lens"], findings=0)
        finally:
            conn.close()
        args = SimpleNamespace(repo=str(root), done=False, history=True,
                               if_due=False, findings=None, note=None)
        code, said, err = _run(cli.cmd_sweep, args)
        self.assertEqual(code, 0, err)
        self.assertIn("1 reviewed, 1 not evaluated", said)


class TheShipDoesNotCallItReviewed(unittest.TestCase):
    def test_the_report_keeps_the_two_apart(self):
        root = _repo(self)
        conn = ledger.connect(root)
        try:
            ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                          base_commit="", created_at="2026")
            review.record_lens_reviewed(conn, root, slug="probe", findings=0,
                                        task_id="t")
            review.record_lens_reviewed(
                conn, root, slug="context-lens", findings=0, task_id="t",
                result="not_evaluable", note="no request/change context")
            self.assertEqual(lifecycle._lenses_reviewed(conn, "t"), {"probe": 0})
            self.assertEqual(lifecycle._lenses_inconclusive(conn, "t"),
                             {"context-lens": "not_evaluable"})
        finally:
            conn.close()

    def test_the_printed_ship_says_it_reported_without_evaluating(self):
        """`reviewed by: context-lens` is exactly what the row used to say.
        The headline for this state says nobody evaluated the task -- not
        that nobody reported."""
        root = _repo(self)
        rep = {"converged": True, "rounds": [1], "claims": [], "blocked": [],
               "chain_ok": True, "chain_problems": [], "facts_unconfirmed": [],
               "deferred": [], "detectors": {}, "detectors_not_run": [],
               "nothing_seen": [], "lenses_run": ["context-lens"],
               "lenses_reviewed": {},
               "lenses_inconclusive": {"context-lens": "not_evaluable"},
               "hook_seen": 1, "claim_origins": {}}
        real = lifecycle.ship
        lifecycle.ship = lambda conn, cfg, task: (True, rep)
        try:
            code, said, err = _run(cli.cmd_ship,
                                   SimpleNamespace(repo=str(root), task="t",
                                                   json=False))
        finally:
            lifecycle.ship = real
        self.assertEqual(code, 0, err)
        self.assertIn("reviewed by: no lens evaluated this task", said)
        self.assertIn("reported without evaluating: context-lens "
                      "(not_evaluable)", said)
        self.assertNotIn("reviewed by: context-lens", said)
        self.assertNotIn("briefed and never reported back", said)


class ARowFromBeforeTheFieldMeansCompleted(unittest.TestCase):
    """The ledger is append-only and every row written before `result`
    existed carries none. Reading the absence as anything but completed would
    retract the old record, which is the failure one column over."""

    def test_an_old_row_is_counted_as_coverage_and_not_as_inconclusive(self):
        root = _repo(self)
        conn = ledger.connect(root)
        try:
            ledger.insert(conn, "event", task_id=None, claim_id=None,
                          kind="lens_reviewed", actor="reviewer",
                          payload={"lens": "probe", "findings": 2},
                          created_at="2026-01-01T00:00:00+00:00")
            self.assertEqual(sweep.reviewed_since(conn), {"probe": 2})
            self.assertEqual(sweep.inconclusive_since(conn), {})
        finally:
            conn.close()


class FailedSweepsStayDue(unittest.TestCase):
    def test_failed_reports_do_not_advance_database_or_export_cadence(self):
        from kernel.config import RepoConfig
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        for lens in ("probe", "context-lens"):
            review.record_lens_reviewed(conn, root, slug=lens, findings=0,
                                        result="failed", note="reviewer crashed")
        args = SimpleNamespace(repo=str(root), done=True, history=False,
                               if_due=False, findings=0, note=None)
        code, said, err = _run(cli.cmd_sweep, args)
        self.assertEqual(code, 1, (said, err))
        payload = sweep.history(conn)[0][1]
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["inconclusive"],
                         {"probe": "failed", "context-lens": "failed"})
        self.assertIsNone(sweep.last(conn))
        self.assertTrue(sweep.due(conn, RepoConfig(root))[0])
        ledger.export_jsonl(conn, root / sweep.EXPORT, root)
        self.assertIsNone(sweep.last_exported(root))
        self.assertTrue(sweep.due_from_export(root, RepoConfig(root))[0])
        self.assertIn("PARTIAL", said)
        self.assertNotIn("next due", said)

    def test_historical_complete_flag_cannot_bless_failed_or_unknown_results(self):
        for result in ("failed", "unknown", "", None, 0, {}, []):
            with self.subTest(result=result):
                self.assertFalse(sweep.complete_payload({
                    "lenses": ["probe"], "reviewed": {}, "complete": True,
                    "inconclusive": {"probe": result}}))
        self.assertTrue(sweep.complete_payload({
            "lenses": ["probe"], "reviewed": {},
            "inconclusive": {"probe": "not_evaluable"}}))


class AReportSnapshotSurvivesAConcurrentRetry(unittest.TestCase):
    def _exercise(self, before, after, surface):
        from kernel.config import RepoConfig
        from unittest.mock import patch
        root = _repo(self)
        reader, writer = ledger.connect(root), ledger.connect(root)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        task = "t" if surface == "ship" else None
        if task:
            lifecycle.open_task(reader, RepoConfig(root), task_id=task,
                                request="Review x.py", scope_globs=["x.py"])
        # One unrelated lens also reports so the CLI has no silence to conflate.
        review.record_lens_reviewed(writer, root, slug="context-lens", findings=0,
                                    task_id=task, result="not_evaluable",
                                    note="no change context", evidence=["context.json"])
        def report(result, version):
            review.record_lens_reviewed(writer, root, slug="probe", findings=0,
                                        task_id=task, result=result,
                                        note=version, evidence=[version + ".log"])
        report(before, "first attempt")
        if task:
            ledger.write_chain_head(reader, root)
        selected, retried, errors = [], [], []

        def interleave(sql):
            # SQLite calls this before executing a statement. Commit the retry
            # immediately after the first real report SELECT has been consumed,
            # before any later query (including the former second projection).
            if selected and not retried:
                retried.append(sql)
                try:
                    report(after, "retry attempt")
                except Exception as exc:
                    errors.append(exc)
            if sql.startswith("SELECT") and "FROM event" in sql and "lens_reviewed" in sql:
                selected.append(sql)

        reader.set_trace_callback(interleave)
        try:
            if surface == "ship":
                ok, payload = lifecycle.ship(reader, RepoConfig(root), task)
                self.assertTrue(ok, payload)
                reviewed, inconclusive = payload["lenses_reviewed"], payload["lenses_inconclusive"]
                reports = payload.get("lenses_reports", {})
            elif surface == "sweep":
                payload = sweep.record(reader, lenses=["probe", "context-lens"], findings=0)
                reviewed, inconclusive = payload["reviewed"], payload["inconclusive"]
                reports = payload.get("reports", {})
            else:
                args = SimpleNamespace(repo=str(root), done=True, history=False,
                                       if_due=False, findings=0, note=None)
                # Only supply the real traced connection; all reads, writes and
                # CLI rendering execute normally, without a test-only transaction.
                with patch.object(ledger, "connect", return_value=reader):
                    code, said, err = _run(cli.cmd_sweep, args)
                reader.set_trace_callback(None)
                payload = sweep.history(reader)[0][1]
                reviewed, inconclusive = payload["reviewed"], payload["inconclusive"]
                reports = payload.get("reports", {})
                self.assertEqual(code, 0 if payload["complete"] else 1, err)
                self.assertEqual("probe (0 finding(s))" in said, "probe" in reviewed, said)
                self.assertEqual("probe (failed)" in said, inconclusive.get("probe") == "failed", said)
                self.assertIn("first attempt", said)
                self.assertNotIn("retry attempt", said)
        finally:
            reader.set_trace_callback(None)
        self.assertEqual(errors, [])
        self.assertTrue(retried, "the real concurrent writer must commit during the read")
        self.assertEqual(reviewed.get("probe"), 0 if before == "completed" else None)
        self.assertEqual(inconclusive.get("probe"), before if before == "failed" else None)
        self.assertEqual(set(reviewed) & set(inconclusive), set())
        self.assertIn("probe", set(reviewed) | set(inconclusive))
        self.assertEqual(reports["probe"]["result"], before)
        self.assertEqual(reports["probe"]["note"], "first attempt")
        self.assertEqual(reports["probe"]["evidence"], ["first attempt.log"])
        latest = json.loads(writer.execute(
            "SELECT payload FROM event WHERE kind = 'lens_reviewed' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0])
        self.assertEqual(latest["result"], after)
        if surface != "ship":
            self.assertEqual(sweep.history(reader)[0][1], payload)
            self.assertEqual(payload["complete"], before == "completed")

    def test_sweep_failed_to_completed_keeps_one_report(self):
        self._exercise("failed", "completed", "sweep")

    def test_sweep_completed_to_failed_keeps_one_report(self):
        self._exercise("completed", "failed", "sweep")

    def test_real_ship_failed_to_completed_keeps_one_report(self):
        self._exercise("failed", "completed", "ship")

    def test_real_ship_completed_to_failed_keeps_one_report(self):
        self._exercise("completed", "failed", "ship")

    def test_done_displays_the_persisted_failed_snapshot(self):
        self._exercise("failed", "completed", "done")

    def test_done_displays_the_persisted_completed_snapshot(self):
        self._exercise("completed", "failed", "done")


class SummariesKeepThePersistedReason(unittest.TestCase):
    def test_done_and_history_keep_failure_note_and_evidence(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        review.record_lens_reviewed(conn, root, slug="probe", findings=0,
                                    result="failed", note="artifact permission denied",
                                    evidence=["review-stderr.log", "unreadable-artifact.json"])
        args = SimpleNamespace(repo=str(root), done=True, history=False,
                               if_due=False, findings=0, note=None)
        code, said, err = _run(cli.cmd_sweep, args)
        self.assertEqual(code, 1, err)
        for token in ("artifact permission denied", "review-stderr.log", "unreadable-artifact.json"):
            self.assertIn(token, said)
        persisted = sweep.history(conn)[0][1]
        # A subsequent report must not change the historical explanation.
        review.record_lens_reviewed(conn, root, slug="probe", findings=0)
        args.done, args.history = False, True
        code, said, err = _run(cli.cmd_sweep, args)
        self.assertEqual(code, 0, err)
        for token in ("probe (failed)", "artifact permission denied", "review-stderr.log", "unreadable-artifact.json"):
            self.assertIn(token, said)
        self.assertEqual(sweep.history(conn)[0][1], persisted)

    def test_real_ship_prints_failure_reason_and_evidence_without_holding_ship(self):
        from kernel.config import RepoConfig
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        lifecycle.open_task(conn, RepoConfig(root), task_id="t",
                            request="Review x.py", scope_globs=["x.py"])
        review.record_lens_reviewed(conn, root, slug="probe", findings=0, task_id="t",
                                    result="failed", note="artifact permission denied",
                                    evidence=["review-stderr.log"])
        ledger.write_chain_head(conn, root)
        code, said, err = _run(cli.cmd_ship, SimpleNamespace(repo=str(root), task="t", json=False))
        self.assertEqual(code, 0, (said, err))
        for token in ("probe (failed)", "artifact permission denied", "review-stderr.log"):
            self.assertIn(token, said)
        self.assertIn("reviewed by: no lens evaluated this task", said)
        self.assertIn("SHIP", said)


if __name__ == "__main__":
    unittest.main(verbosity=2)
