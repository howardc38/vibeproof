"""Five gates whose output did not match what they do.

    python3 -m unittest tests.test_gates_that_said_one_thing -v

  c7c35d7  the by-name exemption covers the tamper anchor and the committed
           chain, so a hand edit of either never reaches the protected-path
           check. It stays -- a stronger mechanism owns both -- and this is
           where that handover is checked rather than asserted.
  5c2da251 a doctor row printed its strongest green from a registry read that
           had raised, which is the only way a traced suite could enter the
           check and enter neither function inside it.
  7a1cf8a6 the budget checker counts statements and told a worker to remove
           lines, which is the unit it was changed away from.
  9f39cfa0 `review add` handed the session that raised a finding the one
           command that session is told not to run, in the imperative and with
           no addressee.
  7765f84  a test edited a version-controlled file in the live working tree and
           restored it only in a `finally`.

Each has its own class and its own mutation.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli as cli_mod                               # noqa: E402
from kernel import config as config_mod                         # noqa: E402
from kernel import doctor                                       # noqa: E402
from kernel import hashing                                      # noqa: E402
from kernel import ledger as ledger_mod                         # noqa: E402
from kernel import review as review_mod                         # noqa: E402


def _repo(case) -> Path:
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    return tmp


class TheAnchorIsExemptAndSomethingElseHoldsIt(unittest.TestCase):
    """c7c35d7 -- the handover this exemption rests on, checked.

    `.v4/chain_head.json` and `.v4/ledger_export.jsonl` are exempt by name, so
    `checkers/scope.py` never reports a hand edit of either as touching a
    protected path. That is the shape the comment beside `.v4/checkers.json`
    gives for having removed *that* entry, and a review asked why these two
    stay. They stay because `audit_chain` owns the anchor and refuses harder
    than a `scope` row would -- and naming a mechanism without checking it is
    the half of the argument that costs nothing to make.
    """

    def test_both_are_exempt_and_both_are_protected(self):
        """The state the finding measured, and it is still the state."""
        cfg = config_mod.RepoConfig(ROOT)
        from kernel.analysis import subject_files
        for rel in (".v4/chain_head.json", ".v4/ledger_export.jsonl"):
            self.assertTrue(hashing.kernel_written(rel, ROOT), rel)
            self.assertTrue(subject_files.matches(rel, cfg.protected), rel)

    def test_a_forged_anchor_is_refused_by_the_audit(self):
        """The mechanism the exemption hands off to, doing the work."""
        root = _repo(self)
        conn = ledger_mod.connect(root)
        self.addCleanup(conn.close)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        for i in range(4):
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="scope_widen", actor="person",
                              payload={"path": f"p{i}.py", "why": "w" * 60},
                              created_at="2026")
        ledger_mod.write_chain_head(conn, root)
        ok, problems = ledger_mod.audit_chain(conn, root)
        self.assertTrue(ok, problems)

        anchor = root / ".v4" / "chain_head.json"
        forged = json.loads(anchor.read_text())
        forged["events"] = forged.get("events", 0) + 1
        anchor.write_text(json.dumps(forged))
        ok, problems = ledger_mod.audit_chain(conn, root)
        self.assertFalse(ok, "a hand-edited anchor has to be caught somewhere")
        self.assertTrue(problems)

    def test_and_checkers_json_is_not_exempt(self):
        """The control, and the precedent: that one was taken off the list for
        exactly the reason these two are questioned, and stays off."""
        self.assertFalse(hashing.kernel_written(".v4/checkers.json", ROOT))


class AnUnreadRegistryIsNotACleanOne(unittest.TestCase):
    """5c2da251 -- the row that printed its green from a read that raised."""

    def _rows(self, root):
        out = []
        doctor._check_reads_covers_the_paths_a_checker_names(root, out)
        return [c for c in out if c["what"] == "reads"]

    def test_a_registry_that_cannot_be_read_says_so(self):
        root = _repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "checkers.json").write_text("{ not json")
        rows = self._rows(root)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["status"], doctor.WARN)
        self.assertIn("could not read", rows[0]["detail"])

    def test_a_missing_registry_says_so_too(self):
        root = _repo(self)
        (root / ".v4").mkdir()
        rows = self._rows(root)
        self.assertEqual(rows[0]["status"], doctor.WARN)

    def test_a_registry_it_can_read_still_gets_the_green(self):
        """The control: this is not a row that has stopped answering."""
        rows = self._rows(ROOT)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["status"], doctor.OK, rows[0]["detail"])


class TheUnitAWorkerIsToldToMove(unittest.TestCase):
    """7a1cf8a6 -- statements counted, lines reported."""

    def _run(self, ceiling, comment=True):
        root = _repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "control_plane_budget.json").write_text(json.dumps(
            {"ceiling": ceiling, "why": "w" * 60, "measured_at": "2026-09-06"}))
        (root / "kernel").mkdir()
        (root / "kernel" / "m.py").write_text(
            ("# a comment, which is not a statement\n" if comment else "")
            + "x = 1\ny = 2\nz = 3\n")
        subj = root / "subject.json"
        subj.write_text(json.dumps({"repo_root": str(root), "subject_refs": [],
                                    "params": {}}))
        # The framework on the path, which is what `runner.child_env` gives a
        # checker in production -- every `checkers/*.py` imports `kernel`, and
        # the fixture repo has none of its own.
        import os

        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.run(
            [sys.executable, str(ROOT / "checkers/control_plane_budget.py"),
             "--subject", str(subj)],
            cwd=root, env=env, capture_output=True, text=True)

    def test_over_the_ceiling_names_statements(self):
        proc = self._run(ceiling=1)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("statement(s) over", proc.stdout)
        self.assertNotIn("line(s) over", proc.stdout)

    def test_and_so_does_the_room_it_reports(self):
        proc = self._run(ceiling=500)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("statement(s) of room", proc.stdout)
        self.assertNotIn("line(s) of room", proc.stdout)

    def test_main_itself_reports_in_statements(self):
        """The same assertion with `main` on the stack.

        The three above run the checker the way `runner` does -- a subprocess,
        which is the production shape and the only one in which the exit code
        and the PYTHONPATH a checker is given can be asked at all. It is also a
        shape no in-process tracer can see, and the finding this file closes is
        about `main`.
        """
        import contextlib
        import importlib.util
        import io as _io

        root = _repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "control_plane_budget.json").write_text(json.dumps(
            {"ceiling": 1, "why": "w" * 60, "measured_at": "2026-09-06"}))
        (root / "kernel").mkdir()
        (root / "kernel" / "m.py").write_text("x = 1\ny = 2\nz = 3\n")
        subj = root / "subject.json"
        subj.write_text(json.dumps({"repo_root": str(root), "subject_refs": [],
                                    "params": {}}))

        spec = importlib.util.spec_from_file_location(
            "budget_under_test", ROOT / "checkers/control_plane_budget.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        argv = sys.argv
        sys.argv = ["control_plane_budget.py", "--subject", str(subj)]
        self.addCleanup(setattr, sys, "argv", argv)
        said = _io.StringIO()
        with contextlib.redirect_stdout(said):
            code = mod.main()

        self.assertEqual(code, 1, said.getvalue())
        self.assertIn("statement(s) over", said.getvalue())
        self.assertNotIn("line(s) over", said.getvalue())

    def test_a_comment_moves_the_number_by_nothing(self):
        """Why the word matters: removing lines is what a worker does when told
        to, and it is not what this counts.

        The fixture module carries one comment line and three statements. The
        room reported is the same with or without the comment, which is the
        whole reason the sentence has to say `statement`.
        """
        with_comment = self._run(ceiling=500, comment=True).stdout
        without = self._run(ceiling=500, comment=False).stdout
        self.assertEqual(
            [l for l in with_comment.splitlines() if "of room" in l],
            [l for l in without.splitlines() if "of room" in l])


class AFindingIsClosedByADifferentSession(unittest.TestCase):
    """9f39cfa0 -- the command, and who it is for."""

    def _add(self, symbol="helper"):
        root = _repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding", "detector": None,
                                "staleness": "subject", "engagement": True,
                                "question_template": "is {file} closed"}}))
        (root / ".v4" / "checkers.json").write_text("{}")
        (root / "mod.py").write_text("def helper():\n    return 1\n")
        (root / "doc.md").write_text("a document with something wrong in it\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=root,
                       capture_output=True)
        args = SimpleNamespace(
            repo=str(root), action="add", claim=None, test=None, command=None,
            parent=None, gone=None, now=None, why=None, target=None, task=None,
            file="mod.py" if symbol else "doc.md", symbol=symbol,
            note="a finding somebody raised", lens=None, name=None,
            findings=None, withdraw=False, mutation_file=None,
            mutation_gone=None, mutation_now="")
        said = io.StringIO()
        with redirect_stdout(said):
            code = cli_mod.cmd_review(args)
        return code, said.getvalue()

    def test_the_line_says_whose_command_it_is(self):
        code, said = self._add()
        self.assertEqual(code, 0, said)
        self.assertIn("closed by the session that repairs it", said)
        self.assertIn("not this one", said)

    def test_the_same_for_a_finding_with_no_symbol(self):
        """The other branch: a document finding closes by text, and is handed
        to the same other session."""
        code, said = self._add(symbol=None)
        self.assertEqual(code, 0, said)
        self.assertIn("closed by the session that repairs it", said)
        self.assertIn("--gone", said)

    def test_and_the_command_is_still_there_to_paste(self):
        """Naming the addressee is not the same as withholding the command --
        the session that repairs it needs the exact line."""
        _code, said = self._add()
        self.assertIn("v4 review close --claim <id> --test <path>", said)


class ATestThatEditsTheTreeItStandsIn(unittest.TestCase):
    """7765f84 -- and what the repaired one does instead."""

    def test_the_case_itself_leaves_it_alone(self):
        """The same question with the case on the stack.

        The subprocess version below is the production shape -- a suite runs in
        its own process, and "after that process, had the file moved" can only
        be asked of one. It is also invisible to an in-process tracer, and the
        finding this closes names the method.
        """
        import tests.test_a_repo_can_widen_the_rule_it_is_judged_by as mod

        target = "kernel/analysis/fail_closed.json"
        before = (ROOT / target).read_bytes()
        case = mod.MovingTheRuleOutOfCodeDidNotMoveItOutOfTheKey(
            "test_this_repos_own_checker_answers_to_its_table")
        result = case.run()
        self.assertTrue(result.wasSuccessful(),
                        result.failures or result.errors)
        self.assertEqual((ROOT / target).read_bytes(), before,
                         "the case edited a version-controlled file")

    def test_the_suite_leaves_this_repos_rule_table_alone(self):
        """Run the case that used to edit it, and ask git afterwards.

        `-uno`: running a test leaves a `__pycache__`, which is not a tracked
        file moving. What this is about is whether the suite can hand somebody
        changes they did not write.
        """
        before = subprocess.run(
            ["git", "status", "--porcelain", "-uno",
             "kernel/analysis/fail_closed.json"],
            cwd=ROOT, capture_output=True, text=True).stdout
        proc = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_a_repo_can_widen_the_rule_it_is_judged_by"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        after = subprocess.run(
            ["git", "status", "--porcelain", "-uno",
             "kernel/analysis/fail_closed.json"],
            cwd=ROOT, capture_output=True, text=True).stdout
        self.assertEqual(after, before,
                         "the suite edited a version-controlled file")


if __name__ == "__main__":
    unittest.main()
