"""Repairs in `kernel/runner.py`, `kernel/sweep.py`, `kernel/detector_protocol.py`
and `kernel/ledger.py`.

    python3 -m unittest tests.test_the_floor_under_what_reaches_the_ledger -v

Everything here is about the last step before something becomes permanent: what
gets blanked, what gets attributed, what gets counted, and what a walk of the
chain can still see.

All of them fail against 0ad6b61.
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

from kernel import detector_protocol, ledger, review, runner, sweep  # noqa: E402


def _repo(case, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


def _conn(case, root):
    conn = ledger.connect(root)
    case.addCleanup(conn.close)
    return conn


class TheTableThatBlanksAndTheTableThatDetects(unittest.TestCase):
    """One carried five vendor prefixes and the other twelve families.

    `AKIA`, `shpat_`, `shpss_`, `sk_live_` and PEM blocks were in the table
    that detects and not in the one that blanks -- and blanking is the last
    thing between a checker printing a credential and an append-only table that
    is exported into a committed, scanned file.
    """

    def test_a_family_the_scanner_knows_is_blanked(self):
        # The word is in the value: `AKIA` plus sixteen still matches the
        # family, so this asserts exactly what it did, and a literal that says
        # what it is stays true wherever somebody copies it.
        fake = "AKIATESTNOTAREALKEY0"
        self.assertNotIn(fake, runner.redact(f"aws {fake} here"))

    def test_the_url_rule_still_keeps_the_host(self):
        # The password half has to look live: each assertion below is that redaction
        # blanks it, or that the engagement gate refuses a sentence carrying it.
        # pragma: allow-secret
        got = runner.redact("postgres://u:hunter2isnotreal@db.prod/app")
        self.assertIn("db.prod", got)
        self.assertNotIn("hunter2isnotreal", got)

    def test_and_the_repos_own_families_reach_it_too(self):
        """`checkers/secret_scan.py` unions `.v4/secret_patterns.json` in; this
        never saw it, so an adopter's own families were detected by the checker
        and written to the ledger in the clear."""
        root = _repo(self)
        (root / ".v4" / "secret_patterns.json").write_text(json.dumps(
            {"patterns": [{"id": "fixture-token", "label": "a fixture family",
                           "kind": "provider", "order": 900,
                           "regex": r"zzq_[A-Za-z0-9]{20}"}]}))
        secret = "zzq_a7Kq2mZp9Rt4Xw1Bs6Vd"
        self.assertNotIn(secret, runner.redact(f"tok {secret} end", root))
        self.assertIn(secret, runner.redact(f"tok {secret} end"),
                      "without a repo, the shipped families are the answer")


class ACheckerThatDidNotParseIsNotAFinding(unittest.TestCase):
    """`reported_nothing` tested for `Traceback (most recent call last):`.

    A `SyntaxError` in the file CPython was asked to run is reported by the
    parser, not the exception machinery: stderr is `File "x.py", line 2`, a
    caret, `SyntaxError: invalid syntax`, and no header at all. So the one
    crash a worker actually produces by editing a checker was recorded as FAIL
    -- a real finding -- instead of ERROR.
    """

    def _stderr_of(self, body):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = tmp / "broken.py"
        p.write_text(body)
        r = subprocess.run([sys.executable, str(p)], capture_output=True,
                           text=True)
        return r.returncode, r.stdout, r.stderr

    def test_a_file_that_does_not_parse_reads_as_a_crash(self):
        code, out, err = self._stderr_of("def f(:\n    pass\n")
        self.assertEqual((code, out.strip()), (1, ""))
        self.assertNotIn("Traceback (most recent call last):", err)
        self.assertTrue(runner.reported_nothing(runner.FAIL, out, err), err)

    def test_an_uncaught_exception_still_does(self):
        code, out, err = self._stderr_of("raise ValueError('x')\n")
        self.assertTrue(runner.reported_nothing(runner.FAIL, out, err))

    def test_and_a_checker_reporting_a_finding_on_stderr_does_not(self):
        self.assertFalse(runner.reported_nothing(
            runner.FAIL, "", "FAIL: two tests are failing"))


class WhoseCostWasThat(unittest.TestCase):
    """`checker_out` went in with `task_id = None` while the owner was in scope.

    Every structured checker output in the ledger was unattributable to a task
    by query -- reachable only one claim at a time -- and the row one insert up
    had already been fixed for the identical omission.
    """

    def test_both_rows_name_the_task_that_paid_for_them(self):
        root = _repo(self)
        conn = _conn(self, root)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c1", task_id="t1", kind="scope",
                      question="q", subject_refs=[], checker="scope",
                      origin="derive", file=None, symbol=None, variant=None,
                      line=None, note=None, detector=None, detector_sha=None,
                      created_at="2026-08-19T00:00:00+00:00")
        result = runner.CheckResult(
            exit_code=0, stdout="", stderr="", argv=["x"], duration_ms=5,
            subject_digest="d", checker_sha="s",
            out_payload={"findings": []})
        runner.record(conn, "c1", result, repo_root=root, config_sha="cfg",
                      worktree=root)
        ev = conn.execute("SELECT task_id FROM event WHERE kind = 'checker_out'").fetchone()
        cost = conn.execute("SELECT task_id, source FROM cost_observation").fetchone()
        self.assertEqual(ev["task_id"], "t1")
        self.assertEqual(cost["task_id"], "t1")
        self.assertEqual(cost["source"], runner.COST_OBSERVED)


class WhatASweepSaidAndWhatTheLedgerSaw(unittest.TestCase):
    """`record` stores both halves of two pairs and `--history` printed one.

    So the claimed-versus-observed gap -- the whole reason `reconcile` exists,
    after a sweep reported 61 findings against 1 in the ledger -- was readable
    only in the one second at `--done` time.
    """

    def test_history_carries_both_halves(self):
        root = _repo(self)
        conn = _conn(self, root)
        sweep.record(conn, lenses=["a", "b", "c"], findings=9, note="fixture")
        (_when, payload), = sweep.history(conn)
        self.assertEqual(payload["lenses"], ["a", "b", "c"])
        self.assertIn("ran", payload)
        # `ran` is not the observed half of the lens pair on its own: it comes
        # from `lens_run`, which says a brief printed, and printing one is free.
        self.assertIn("reviewed", payload)
        self.assertIn("raised_since_last_sweep", payload)

    def test_and_the_only_command_for_reading_them_prints_both(self):
        """`history` returned the whole payload all along; `--history` printed
        the lens count, the findings and the note, so the gap was readable only
        in the one second at `--done` time when the reconcile message fires."""
        import contextlib
        import io
        import types
        from kernel import cli
        root = _repo(self)
        conn = _conn(self, root)
        sweep.record(conn, lenses=["a", "b", "c"], findings=9, note="fixture")
        conn.commit()
        args = types.SimpleNamespace(
            repo=str(root), acceptance=".v4/acceptance.json", history=True,
            done=False, if_due=False, findings=None, note=None)
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = cli.cmd_sweep(args)
        self.assertEqual(code, 0)
        text = said.getvalue()
        # Three numbers where there were two, because the line it judges gained
        # a third state: `ran` said a brief printed and was being read as a
        # review, so `reviewed` is now stored and printed beside it. The
        # assertion's subject has not moved -- what the ledger observed is
        # printed beside what the sweep claimed, and here it observed nothing.
        self.assertIn("3 lens(es) available, 0 briefed, 0 reviewed", text)
        self.assertIn("9 finding(s) claimed and 0 in the ledger", text)


class WhichRowsTheFloorCounts(unittest.TestCase):
    """Deleting `AND origin = 'review'` left every test green.

    The count is the floor `reconcile` holds a sweep's self-reported number
    against, and letting detector-raised findings into it hides exactly the
    direction the mechanism exists for: claimed many, raised few.
    """

    def _claim(self, conn, cid, origin, kind=None):
        ledger.insert(conn, "claim", id=cid, task_id="t1",
                      kind=kind or review.KIND, question="q", subject_refs=[],
                      checker="review-finding", origin=origin, file="a.py",
                      symbol="s", variant=None, line=None, note=None,
                      detector=None, detector_sha=None,
                      created_at="2026-08-19T00:00:00+00:00")

    def test_a_detector_raised_finding_is_not_a_reviewers(self):
        root = _repo(self)
        conn = _conn(self, root)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        self._claim(conn, "c1", review.ORIGIN)
        self._claim(conn, "c2", "derive")
        self.assertEqual(sweep.raised_since(conn), 1)

    def test_the_two_sides_share_one_name_for_it(self):
        self.assertEqual(review.ORIGIN, "review")
        root = _repo(self)
        conn = _conn(self, root)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        self._claim(conn, "c3", review.ORIGIN, kind="scope")
        self.assertEqual(sweep.raised_since(conn), 0,
                         "the kind half has to hold too")


class ATaskSomebodyIsStillCheckingIsBusy(unittest.TestCase):
    """`v4 check` writes no event carrying a task id.

    Attempts go to the `attempt` table and `cost_observation` is its own, so a
    task actively being checked -- but not derived, widened, or written to
    through the hook -- was skipped by the freshness filter, never reached
    `state.task_report`, and `due()` answered "nothing is open" while it held
    unanswered claims.
    """

    def test_an_attempt_counts_as_the_task_being_touched(self):
        from datetime import datetime, timedelta, timezone
        from kernel import config as config_mod
        root = _repo(self)
        conn = _conn(self, root)
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at=old)
        ledger.insert(conn, "claim", id="c1", task_id="t1", kind="scope",
                      question="q", subject_refs=[], checker="scope",
                      origin="derive", file=None, symbol=None, variant=None,
                      line=None, note=None, detector=None, detector_sha=None,
                      created_at=old)
        # The task was derived three days ago and nothing has happened since,
        # which is what the freshness filter is for.
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t1", claim_id=None,
                          kind="round_opened", actor="kernel",
                          payload={"round": 1}, created_at=old)
        since = datetime.now(timezone.utc) - timedelta(days=1)
        cfg = config_mod.RepoConfig(root)
        busy, _unreadable = sweep.open_work(conn, cfg, since=since)
        self.assertEqual(busy, [], "nothing has touched it since")

        ledger.append_attempt(
            conn, claim_id="c1", subject_digest="d", checker_sha="s",
            config_sha="c", head_commit="h", worktree=str(root),
            facts_sha="", argv=["x"], exit_code=1, stdout="", stderr="",
            duration_ms=1, started_at=datetime.now(timezone.utc).isoformat(),
            ended_at=datetime.now(timezone.utc).isoformat())
        busy, _unreadable = sweep.open_work(conn, cfg, since=since)
        self.assertEqual([t for t, _n in busy], ["t1"],
                         "the command a worker runs most left no trace here")


class ADetectorThatHangsIsARowNotATraceback(unittest.TestCase):
    """`subprocess.run(..., timeout=)` raised `TimeoutExpired` out of `derive`.

    No `detector_run` row for it, no rows for any detector after it in the
    sorted glob, and a traceback instead of a report -- while the checker path
    had handled the same event as exit 8 with recorded stderr since it was
    written. The `--out` file was never read back either, in a protocol 14
    detectors write JSON to.
    """

    def _detector(self, body):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = tmp / "always_probe.py"
        p.write_text(body)
        return tmp, p

    def test_a_hang_comes_back_as_an_exit_code(self):
        root, det = self._detector("import time\ntime.sleep(30)\n")
        rc, _out, err, _payload = detector_protocol.run_detector(
            root, det, ["a.py"], timeout=2)
        self.assertEqual(rc, runner.TIMEOUT)
        self.assertTrue((err or "").strip(), "a timeout with no sentence is a "
                                             "number the reader cannot act on")

    def test_and_what_a_detector_writes_to_out_is_read_back(self):
        root, det = self._detector(
            "import argparse, json, pathlib\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out')\n"
            "a = p.parse_args()\n"
            "pathlib.Path(a.out).write_text(json.dumps({'looked_at': 7}))\n")
        rc, _out, _err, payload = detector_protocol.run_detector(
            root, det, ["a.py"], timeout=30)
        self.assertEqual(rc, 0)
        self.assertEqual(payload, {"looked_at": 7},
                         "14 detectors write JSON into a directory that was "
                         "torn down without anybody reading it")


class WhatAWalkOfTheChainCanSee(unittest.TestCase):
    """The walk read only the `attempt` table.

    Event rows are what decide permission -- `scope_widen` is the sole input to
    `current_scope` in both `kernel/scope.py` and `hooks/write_block.py` -- so
    an inserted widen left `v4 audit` reporting "chain: intact", with no
    committed artefact to reconcile against the way `.v4/risks/` gives a
    signature one.
    """

    def test_an_event_edited_in_place_is_reported(self):
        root = _repo(self)
        conn = _conn(self, root)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t1", claim_id=None,
                          kind="scope_widen", actor="worker",
                          payload={"paths": ["a.py"], "why": "fixture"},
                          created_at="2026-08-19T00:00:01+00:00")
        ok, problems = ledger.audit_chain(conn)
        self.assertTrue(ok, problems)
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute("DROP TRIGGER IF EXISTS no_update_event")
        conn.execute("PRAGMA writable_schema = OFF")
        conn.execute("UPDATE event SET payload = ? WHERE kind = 'scope_widen'",
                     (json.dumps({"paths": ["**"], "why": "fixture"}),))
        conn.commit()
        ok, problems = ledger.audit_chain(conn)
        self.assertFalse(ok)
        self.assertTrue([p for p in problems if "event" in p], problems)


class ARecordTheLedgerNeverMade(unittest.TestCase):
    """The orphan-file sweep was `risks_dir.glob("*.json")`, not recursive.

    A repo-scoped signature is written to `.v4/risks/repo/<kind>.json` -- the
    file whose whole purpose is to be what a human reads in a diff -- so the
    half of the check that catches a file with no row was missing exactly the
    layout the same function documents writing.
    """

    def test_a_repo_scoped_record_with_no_row_is_reported(self):
        root = _repo(self)
        conn = _conn(self, root)
        d = root / ".v4" / "risks" / "repo"
        d.mkdir(parents=True)
        (d / "unprovable.json").write_text(json.dumps(
            {"claim": "c9", "kind": "unprovable", "why": "hand written",
             "who": "nobody", "scope": "repo"}))
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue([p for p in problems if "unprovable" in p], problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
