"""Repairs in `kernel/cli.py`, entered through the commands themselves.

    python3 -m unittest tests.test_the_cli_says_what_happened -v

The CLI is the whole surface an operator has. Each of these was a place where
what it printed, or what it accepted, was not what the kernel had done.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest.mock  # noqa: E402
from kernel import cli, ledger, lifecycle, review, risk  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                ["git", "config", "user.name", "c"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"review-finding": {"checker": "review-finding", "question_template": "q",
                            "staleness": "subject"}}))
    (tmp / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, capture_output=True)
    return tmp


def _run(fn, args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(args)
    return code, out.getvalue(), err.getvalue()


class TheCriteriaLiveBesideTheRepo(unittest.TestCase):
    """`--acceptance` was resolved against the process cwd.

    `--repo` exists so a command can be run from somewhere else -- `bin/v4`
    passes `--repo $(git rev-parse --show-toplevel)` every time -- so the ruler
    a measurement round is frozen against was whichever file happened to sit
    beside the shell.
    """

    def test_a_relative_path_is_read_from_the_repo(self):
        root = _repo(self)
        args = SimpleNamespace(repo=str(root), acceptance=".v4/acceptance.json")
        self.assertEqual(cli._acceptance(args),
                         (root / ".v4/acceptance.json").resolve())

    def test_an_absolute_one_is_left_alone(self):
        root = _repo(self)
        other = Path(tempfile.mkdtemp()) / "elsewhere.json"
        self.addCleanup(shutil.rmtree, other.parent, ignore_errors=True)
        args = SimpleNamespace(repo=str(root), acceptance=str(other))
        self.assertEqual(cli._acceptance(args), other.resolve())

    def test_main_resolves_it_the_same_way(self):
        """Through `main`, which is the symbol the finding names: `v4 round
        open` from another directory has to find this repo's criteria."""
        root = _repo(self)
        (root / ".v4" / "acceptance.json").write_text(json.dumps(
            {"rule": "r", "criteria": "every deliverable states its oracle",
             "amendment_rule": "between rounds", "amendments": []}))
        old = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        try:
            code, out, err = _run(
                cli.main, ["--repo", str(root), "round", "open", "--label", "r1"])
        finally:
            os.chdir(old)
        self.assertEqual(code, 0, err)
        self.assertIn("r1", out + err)

    def test_and_the_shell_it_was_typed_in_does_not_decide(self):
        """The control: the old code returned a path under the cwd, so running
        the same command from two directories froze two different rulers."""
        root = _repo(self)
        args = SimpleNamespace(repo=str(root), acceptance=".v4/acceptance.json")
        here = cli._acceptance(args)
        old = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        try:
            self.assertEqual(cli._acceptance(args), here)
        finally:
            os.chdir(old)


class TheMonitorFlagReachesTheKernel(unittest.TestCase):
    """`--as-monitor` was declared in the parser, passed on by `cmd_risk`, and
    driven by nothing.

    Measured 2026-08-27: deleting `as_monitor=args.as_monitor` from `cmd_risk`
    left the full 1455-test oracle with an identical failure set, and so did
    deleting the `add_argument` that declares the flag. Neither deletion breaks
    the command -- with the pass-through gone it falls through to the terminal
    check, refuses, and tells the monitor to run `--no-tty-check`, which records
    `signed_by: agent`. The whole point of the wiring is which of three values
    lands in a committed file, and nothing entered the command that decides it.

    So these go through `cli.main` with an argv, which is the only shape that
    holds both halves: the parser has to declare the flag and the command has to
    hand it on.
    """

    WHY = "A detector raised this and I did not write the code it judges."

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                    ["git", "config", "user.name", "c"]):
            subprocess.run(cmd, cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"lint": {"question_template": "q", "checker": "lint",
                      "staleness": "repo"}}))
        (tmp / ".v4" / "checkers.json").write_text("{}")
        (tmp / "x.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for cid, origin in (("c-derived", "derive"), ("c-filed", "review")):
            ledger.insert(conn, "claim", id=cid, task_id="t", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin=origin, created_at="2026")
        return tmp, conn

    def setUp(self):
        # Which terminal these assert about is declared rather than inherited:
        # the control below is a refusal that only happens without one, and
        # under `pty.spawn` the ambient answer is the other one. The same rule
        # `tests/test_kernel._Stdin` was written for.
        patch = unittest.mock.patch.object(
            sys, "stdin", SimpleNamespace(isatty=lambda: False))
        patch.start()
        self.addCleanup(patch.stop)

    def _sign(self, root, claim, *flags):
        return _run(cli.main, ["--repo", str(root), "risk", "accept",
                               "--claim", claim, "--kind", "unprovable",
                               "--why", self.WHY, *flags])

    def test_the_flag_signs_as_a_monitor_end_to_end(self):
        root, conn = self._repo()
        code, out, err = self._sign(root, "c-derived", "--as-monitor")
        self.assertEqual(code, 0, err)
        self.assertIn("record written to", out)
        record = json.loads((root / ".v4" / "risks" / "c-derived.json").read_text())
        self.assertEqual(record["signed_by"], risk.MONITOR)
        row = conn.execute("SELECT signed_by, was_tty FROM accepted_risk "
                           "WHERE claim_id = 'c-derived'").fetchone()
        self.assertEqual(row["signed_by"], risk.MONITOR)
        # And the column that used to be the whole answer cannot tell them
        # apart: this is `was_tty = 0`, exactly like `--no-tty-check`.
        self.assertEqual(row["was_tty"], 0)

    def test_without_it_the_same_command_is_refused_at_the_terminal_check(self):
        """The control, and the failure the missing wiring actually produced:
        not an error, but the monitor being sent to the route that records
        `agent`."""
        root, _conn = self._repo()
        code, _out, err = self._sign(root, "c-derived")
        self.assertEqual(code, 2)
        self.assertIn("stdin is not a terminal", err)
        self.assertIn("--no-tty-check", err)
        self.assertFalse((root / ".v4" / "risks" / "c-derived.json").exists())

    def test_and_a_hand_raised_claim_is_refused_through_the_command(self):
        """The refusal has to survive the trip through argparse too: a monitor
        pasting this at a finding gets a message, exit 2, and no file."""
        root, conn = self._repo()
        code, _out, err = self._sign(root, "c-filed", "--as-monitor")
        self.assertEqual(code, 2)
        self.assertIn("raised by hand", err)
        self.assertFalse((root / ".v4" / "risks" / "c-filed.json").exists())
        self.assertEqual(list(conn.execute(
            "SELECT 1 FROM accepted_risk WHERE claim_id = 'c-filed'")), [])

    def test_ship_and_status_say_which_route_signed(self):
        """The other half of the same finding. `signed_by` reached the record
        file and stopped there, so the two lines that count signatures --
        `v4 status` and the `signed   :` line in `v4 ship` -- put a monitor
        signature in the same number as a worker's `--no-tty-check`."""
        root, _conn = self._repo()
        self.assertEqual(self._sign(root, "c-derived", "--as-monitor")[0], 0)
        code, out, err = _run(cli.main, ["--repo", str(root), "status",
                                         "--task", "t"])
        self.assertIn("unprovable=1", out, err)
        self.assertIn(f"by {risk.MONITOR}=1", out)


class CloseRefusesWhatItCannotBind(unittest.TestCase):
    """`review close` had no branch of its own.

    Every flag it needs is optional in the parser, so `v4 review close` with
    nothing appended a `review_close` event with a NULL claim id and a payload
    of nulls into an append-only table and exited 0 -- a row nothing can use,
    reported as success. `.github/monitor/PROMPT.md` printed that exact form,
    so a monitor session pasting it believed a finding had been offered a test.
    """

    def _args(self, root, **kw):
        base = dict(repo=str(root), action="close", claim=None, test=None,
                    command=None, parent=None, gone=None, now=None, why=None,
                    target=None, task=None, file=None, symbol=None, note=None,
                    lens=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_close_with_no_flags_is_refused(self):
        root = _repo(self)
        code, _out, err = _run(cli.cmd_review, self._args(root))
        self.assertEqual(code, 2)
        self.assertIn("--claim", err)

    def test_and_nothing_was_written(self):
        root = _repo(self)
        _run(cli.cmd_review, self._args(root))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        rows = list(conn.execute(
            "SELECT 1 FROM event WHERE kind = 'review_close'"))
        self.assertEqual(rows, [], "a row nothing can use, reported as success")

    def test_an_unknown_action_is_refused_too(self):
        """It fell through to the same write: a typo in the action was a silent
        row as well."""
        root = _repo(self)
        code, _out, err = _run(cli.cmd_review, self._args(root, action="clsoe"))
        self.assertEqual(code, 2)
        self.assertIn("unknown action", err)


class DeriveSaysWhatItRetracted(unittest.TestCase):
    """`cmd_derive` printed `created` and `refused` and dropped `retracted`.

    Retraction is the only route to a terminal state that involves no checker
    and no signature, so a derive that retracted six looked exactly like one
    that retracted none.
    """

    def test_a_retracted_claim_is_named_on_the_way_out(self):
        """Through `cmd_derive` with a real retraction: a claim whose file is
        gone from the tree."""
        root = _repo(self)
        (root / "detectors").mkdir()
        (root / "detectors" / "always_probe.py").write_text(
            "import argparse, sys\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out'); p.parse_args()\n"
            "sys.exit(0)\n")
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="gone1", task_id="t",
                      kind="review-finding", question="q",
                      subject_refs=json.dumps(
                          [{"kind": "file", "path": "deleted.py"}]),
                      checker="review-finding", origin="derive",
                      created_at="2026", file="deleted.py", symbol="f",
                      detector="always_probe.py")
        conn.close()
        args = SimpleNamespace(repo=str(root), task="t", subject=None,
                               phase="open")
        code, out, _err = _run(cli.cmd_derive, args)
        self.assertIn(code, (0, 1))
        self.assertIn("gone1", out,
                      "retraction is the only terminal state with no checker "
                      "and no signature, and nothing said it happened")
        self.assertIn("retracted", out)


class TwoUnreadableThingsWereOneGuard(unittest.TestCase):
    """`changed = None` was the answer to both, and it is right for one.

    The block computes a hint -- "these claims are on files you did not change"
    -- from the task's base commit and a git diff. Both the ledger read and the
    diff read sat inside the same `try`, so a ledger this command cannot open
    reached the same `None` as a base git cannot resolve, and derive printed a
    clean page either way.

    They are not the same failure. A diff git cannot answer costs a hint. A
    ledger `cmd_derive` cannot read is the wrong answer to every claim printed
    below it, and the eleven signatures on this site each argued the first case
    while the second was what the rule had found.
    """

    def _task(self, root, base):
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit=base, created_at="2026")
        conn.close()
        return SimpleNamespace(repo=str(root), task="t", subject=None,
                               phase="open")

    def _head(self, root):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()

    def test_a_ledger_it_cannot_read_is_not_a_missing_hint(self):
        """The read that must propagate.

        Keyed on the calling frame, not on the SQL: `kernel/derive.py` and
        `kernel/scope.py` read `base_commit` with the same string, so a wrapper
        that matches the text fails inside `lifecycle.derive` before this block
        is reached -- and then passes against both versions of the code, which
        is how it was first written here.
        """
        root = _repo(self)
        args = self._task(root, self._head(root))
        real_connect = ledger.connect

        class _FailsOnlyInDerive:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def execute(self, sql, *a, **k):
                if ("base_commit FROM task" in sql
                        and sys._getframe(1).f_code.co_name == "cmd_derive"):
                    raise sqlite3.Error("ledger unreadable")
                return self._inner.execute(sql, *a, **k)

        cli.ledger.connect = lambda r: _FailsOnlyInDerive(real_connect(r))
        self.addCleanup(setattr, cli.ledger, "connect", real_connect)
        with self.assertRaises(sqlite3.Error):
            _run(cli.cmd_derive, args)

    def _inherited(self, root):
        """One claim on a file the diff does not name, so the hint can print.

        Without it the fixture derives nothing, `inherited` is empty, and
        asserting the hint is absent holds whatever `changed_since` does --
        which is how the first version of this test was written.
        """
        (root / "detectors").mkdir(exist_ok=True)
        (root / "detectors" / "always_probe.py").write_text(
            "import argparse, sys\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out'); p.parse_args()\n"
            "sys.exit(0)\n")
        conn = ledger.connect(root)
        ledger.insert(conn, "claim", id="c1", task_id="t",
                      kind="review-finding", question="q",
                      subject_refs=json.dumps(
                          [{"kind": "file", "path": "x.py"}]),
                      checker="review-finding", origin="derive",
                      created_at="2026", file="x.py", symbol="f",
                      detector="always_probe.py")
        conn.close()

    def test_a_diff_git_cannot_answer_costs_the_hint_and_nothing_else(self):
        """The read that may degrade, and the reason the guard stays at all.

        `changed_since` raises rather than returning an empty set -- a
        rewritten history, a shallow clone and a base this checkout does not
        have all look like a clean tree to a caller that cannot tell -- and
        this caller can afford it, because what it feeds is a hint.

        Asserted as a contrast, because the absence on its own proves nothing:
        the same fixture with the diff read working prints the hint, and
        blinded it does not, and derive completes either way.
        """
        from kernel.analysis import subject_files as sf

        root = _repo(self)
        args = self._task(root, self._head(root))
        self._inherited(root)
        code, seen, _err = _run(cli.cmd_derive, args)
        self.assertIn(code, (0, 1))
        self.assertIn("this task has not changed", seen,
                      "with a readable diff the hint must print, or the "
                      "absence below is about the fixture and not the code")

        def _blind(*a, **k):
            raise sf.DiffUnreadable("no such base")

        real = sf.changed_since
        sf.changed_since = _blind
        self.addCleanup(setattr, sf, "changed_since", real)
        code, blind, _err = _run(cli.cmd_derive, args)
        self.assertIn(code, (0, 1))
        self.assertNotIn("this task has not changed", blind,
                         "the hint is derived from the diff, so it must be "
                         "absent rather than guessed")



class TheReviewerSaysWhenItIsFinished(unittest.TestCase):
    """`v4 review done` -- the row `lens_run` could never be.

    A sweep already had this shape (`v4 sweep --done --findings N`); a per-task
    lens run did not, so the only row it left was written before it started.
    """

    def _args(self, root, **over):
        a = dict(repo=str(root), action="done", lens=None, task="t",
                 findings=None, file=None, symbol=None, note=None, claim=None,
                 test=None, command=None, parent=None, gone=None, now=None,
                 why=None, target=None, name=None, withdraw=False)
        a.update(over)
        return SimpleNamespace(**a)

    def _lens(self, root):
        d = root / ".v4" / "lenses"
        d.mkdir(parents=True, exist_ok=True)
        (d / "probe.json").write_text(json.dumps(
            {"name": "probe", "source": "test", "checks": ["one thing"],
             "anti_patterns": ["not another"]}, ensure_ascii=False))

    def _rows(self, root):
        conn = ledger.connect(root)
        try:
            return [json.loads(r["payload"]) for r in conn.execute(
                "SELECT payload FROM event WHERE kind = 'lens_reviewed' "
                "ORDER BY id")]
        finally:
            conn.close()

    def test_zero_findings_is_recorded_not_refused(self):
        root = _repo(self)
        self._lens(root)
        code, _out, _err = _run(cli.cmd_review,
                                self._args(root, lens="probe", findings=0))
        self.assertEqual(code, 0)
        self.assertEqual(self._rows(root), [{"lens": "probe", "findings": 0}])

    def test_omitting_findings_is_refused(self):
        """Defaulting it to 0 would make `I forgot` and `I found nothing` the
        same row, one layer down from the bug this command exists for."""
        root = _repo(self)
        self._lens(root)
        code, _out, err = _run(cli.cmd_review, self._args(root, lens="probe"))
        self.assertEqual(code, 2)
        self.assertIn("--findings is required", err)
        self.assertEqual(self._rows(root), [])

    def test_a_lens_that_does_not_exist_is_refused(self):
        root = _repo(self)
        self._lens(root)
        code, _out, err = _run(cli.cmd_review,
                               self._args(root, lens="nope", findings=1))
        self.assertEqual(code, 2)
        self.assertIn("no lens named", err)

    def test_a_second_pass_replaces_the_first(self):
        """A reviewer running again after a repair says something newer, not
        something additional. Summing would report one diff read twice as twice
        the findings."""
        root = _repo(self)
        self._lens(root)
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        conn.close()
        for n in (3, 0):
            _run(cli.cmd_review, self._args(root, lens="probe", findings=n))
        conn = ledger.connect(root)
        try:
            self.assertEqual(lifecycle._lenses_reviewed(conn, "t"),
                             {"probe": 0})
        finally:
            conn.close()


class ASweepPrintsWhoCameBackNotWhoTookABrief(unittest.TestCase):
    """`v4 sweep --done` -- the same defect `v4 ship` had, one command over.

    `ran` comes from `lens_run`, which `v4 review lens` writes when the brief
    prints, and printing a brief is free. No reader on the sweep side touched
    `lens_reviewed` at all, so thirteen reviewers taking a brief and none of
    them coming back was recorded, and printed, as thirteen lenses run.

    Measured on this repo's own ledger at the sweep of 2026-08-27: eleven
    lenses were opened and all eleven reported, and this command printed
    `12 of 13 printed their brief since the last sweep` -- the twelfth being
    `request-fidelity`, briefed on three tasks by three workers reading three
    diffs. Hence the third test: a sweep has no task, and a row that names one
    is about somebody else's subject.
    """

    def _args(self, root, **over):
        a = dict(repo=str(root), done=True, history=False, if_due=False,
                 findings=None, note=None)
        a.update(over)
        return SimpleNamespace(**a)

    def _lenses(self, root, *names):
        d = root / ".v4" / "lenses"
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / f"{n}.json").write_text(json.dumps(
                {"name": n, "source": "test", "checks": ["one thing"],
                 "anti_patterns": ["not another"]}, ensure_ascii=False))

    def _briefed(self, root, lens, task_id=None):
        """Through the writer, not around it. A hand-built row would let the
        payload key this reads drift from the one `v4 review lens` writes, and
        the whole finding is that a reader and a writer had different ideas
        about what a row means."""
        conn = ledger.connect(root)
        try:
            review.record_lens_run(
                conn, slug=lens, lens=review.lenses(root)[lens], task_id=task_id)
        finally:
            conn.close()

    def _reported(self, root, lens, findings, task_id=None):
        conn = ledger.connect(root)
        try:
            review.record_lens_reviewed(conn, root, slug=lens,
                                        findings=findings, task_id=task_id)
        finally:
            conn.close()

    def _recorded(self, root):
        conn = ledger.connect(root)
        try:
            row = conn.execute(
                "SELECT payload FROM event WHERE kind = 'lens_sweep' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            return json.loads(row["payload"])
        finally:
            conn.close()

    def test_a_brief_nobody_came_back_from_is_not_a_review(self):
        root = _repo(self)
        self._lenses(root, "probe", "other")
        self._briefed(root, "probe")
        code, said, _err = _run(cli.cmd_sweep, self._args(root))
        self.assertEqual(code, 0, said)
        self.assertIn("no lens reported on this sweep", said)
        self.assertIn("briefed and never reported back: probe", said)
        self.assertEqual(self._recorded(root)["reviewed"], {},
                         "the durable record has to hold it too -- the print "
                         "is gone the moment the terminal scrolls")

    def test_a_lens_that_reported_is_named_with_what_it_found(self):
        """And 0 is the answer that only exists because the row does."""
        root = _repo(self)
        self._lenses(root, "probe", "other")
        self._briefed(root, "probe")
        self._reported(root, "probe", 0)
        code, said, _err = _run(cli.cmd_sweep, self._args(root))
        self.assertEqual(code, 0, said)
        self.assertIn("1 of 2 reported: probe (0 finding(s))", said)
        self.assertNotIn("briefed and never reported back", said)
        self.assertIn("no brief printed since the last sweep: other", said)
        self.assertEqual(self._recorded(root)["reviewed"], {"probe": 0})

    def test_a_lens_read_against_a_task_is_not_this_sweeps_coverage(self):
        """A sweep reads the tree as it stands; `v4 review lens --task T`
        reads T's diff. Counting the second as the first is how eleven
        lenses were printed as twelve."""
        root = _repo(self)
        self._lenses(root, "probe")
        self._briefed(root, "probe", task_id="t-other")
        self._reported(root, "probe", 7, task_id="t-other")
        code, said, _err = _run(cli.cmd_sweep, self._args(root))
        self.assertEqual(code, 0, said)
        self.assertIn("no lens reported on this sweep", said)
        self.assertIn("no brief printed since the last sweep: probe", said)
        self.assertEqual(self._recorded(root)["ran"], [])

    def test_the_history_says_briefed_and_reviewed_apart(self):
        root = _repo(self)
        self._lenses(root, "probe", "other")
        self._briefed(root, "probe")
        self._briefed(root, "other")
        self._reported(root, "probe", 2)
        closed, why, _err = _run(cli.cmd_sweep, self._args(root))
        self.assertEqual(closed, 0, why)
        code, said, _err = _run(cli.cmd_sweep,
                                self._args(root, done=False, history=True))
        self.assertEqual(code, 0, said)
        self.assertIn("2 lens(es) available, 2 briefed, 1 reviewed", said)

    def test_a_sweep_recorded_before_this_existed_is_not_reported_as_zero(self):
        """The ledger is append-only and every row written before `reviewed`
        existed carries no such key. Printing those as `0 reviewed` would be
        this command inventing a measurement the row never took."""
        root = _repo(self)
        conn = ledger.connect(root)
        ledger.insert(conn, "event", task_id=None, claim_id=None,
                      kind="lens_sweep", actor="worker",
                      payload={"lenses": ["probe"], "ran": ["probe"],
                               "findings": 4, "note": ""}, created_at="2026")
        conn.close()
        code, said, _err = _run(cli.cmd_sweep,
                                self._args(root, done=False, history=True))
        self.assertEqual(code, 0, said)
        self.assertIn("1 lens(es)", said)
        self.assertNotIn("reviewed", said)


if __name__ == "__main__":
    unittest.main(verbosity=2)
