"""`COULD_NOT_RUN` was written for an exit code that could never arrive.

    python3 -m unittest tests.test_a_runner_that_is_not_there -v

The set names 126 and 127 -- the shell's "not executable" and "not found" --
and `_run_traced` passes a list to `subprocess.run`, so there is no shell to
produce them. A missing runner raised `FileNotFoundError` instead, which left
through `checkers/review_finding.py`'s `except Exception` as exit 5.

The branch that reads the set is the one that tells a worker what to do about
it: a worktree at the parent commit carries no `.venv`, no `node_modules`,
nothing untracked, so "could not run" there is inconclusive rather than red,
and the note says to point `--command` at a runner that works from a bare
checkout. That sentence could not be printed, because the condition it is
about arrived as an exception.

Measured on the reference adopter, 2026-08-27: `attempt #5333`, exit 5,
`[Errno 2] No such file or directory: '.venv/bin/pytest'`. What the worker did
next was point `--command` at an absolute path outside the worktree, which runs
the parent commit's code against HEAD's environment -- a workaround for advice
they were never shown.

All of it fails against the commit before this file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import redgreen  # noqa: E402

PASSES = '''\
from app import thing


def test_it():
    assert thing() == 1
'''


class WhatSubprocessDoesWithoutAShell(unittest.TestCase):
    """The mechanism the repair is about, stated so the reason survives it."""

    def test_a_list_and_a_missing_binary_raises_rather_than_exiting(self):
        with self.assertRaises(FileNotFoundError):
            subprocess.run(["./definitely-not-here/pytest", "-q"],
                           capture_output=True)

    def test_so_the_set_names_two_codes_a_shell_would_have_produced(self):
        self.assertIn(127, redgreen.COULD_NOT_RUN)
        self.assertIn(126, redgreen.COULD_NOT_RUN)


class TheRunnerAnswersInsteadOfRaising(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_missing_runner_is_127(self):
        rc, executed, calls, out = redgreen._run_traced(
            self.tmp, [".venv/bin/pytest", "-q"], "app.py", "thing")
        self.assertEqual(rc, 127)
        self.assertIsNone(executed, "nothing ran, so `executed` is not False")
        self.assertEqual(calls, 0)
        self.assertIn("No such file", out)

    def test_a_runner_that_is_there_and_not_executable_is_126(self):
        runner = self.tmp / "runner"
        runner.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(runner, 0o644)
        rc, _executed, _calls, _out = redgreen._run_traced(
            self.tmp, [str(runner)], "app.py", "thing")
        self.assertEqual(rc, 126)


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    run = lambda *a: subprocess.run(a, cwd=tmp, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tmp / "app.py").write_text("def thing():\n    return 1\n")
    (tmp / "t_it.py").write_text(PASSES)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    parent = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                            capture_output=True, text=True).stdout.strip()
    return tmp, parent


class WhatTheWorkerIsToldAtTheParent(unittest.TestCase):
    """The sentence that could not be printed, printed.

    This is the half that matters: the guard exists, is correct, and was
    unreachable.
    """

    def test_a_worktree_with_no_runner_is_inconclusive_and_says_why(self):
        root, parent = _repo(self)
        res = redgreen.verify(
            root, command=[".venv/bin/pytest", "-q", "t_it.py"],
            test_path="t_it.py", target_file="app.py", target_symbol="thing",
            parent_commit=parent, timeout=60)
        self.assertIs(res.red_failed, False,
                      "a runner that never started is not a red")
        joined = "\n".join(res.notes)
        self.assertIn("worktree has no untracked files", joined)
        self.assertIn("bare checkout", joined,
                      "the advice is the whole point of the branch")

    def test_and_head_says_it_could_not_run_rather_than_did_not_pass(self):
        """Two different facts. `does not pass at HEAD` about a test that never
        started sends the reader to read the test."""
        root, parent = _repo(self)
        res = redgreen.verify(
            root, command=[".venv/bin/pytest", "-q", "t_it.py"],
            test_path="t_it.py", target_file="app.py", target_symbol="thing",
            parent_commit=parent, timeout=60)
        joined = "\n".join(res.notes)
        self.assertIn("could not run at HEAD", joined)
        self.assertNotIn("does not pass at HEAD", joined)
        self.assertFalse(res.ok)

    def test_a_real_failure_still_says_it_does_not_pass(self):
        """The other half of the split, so the repair cannot swallow a red."""
        root, parent = _repo(self)
        (root / "t_it.py").write_text(
            "def test_it():\n    assert False\n")
        res = redgreen.verify(
            root, command=[sys.executable, "-c",
                           "import sys; sys.exit(1)"],
            test_path="t_it.py", target_file="app.py", target_symbol="thing",
            parent_commit=parent, timeout=60)
        joined = "\n".join(res.notes)
        self.assertIn("does not pass at HEAD", joined)
        self.assertNotIn("could not run at HEAD", joined)


if __name__ == "__main__":
    unittest.main()
