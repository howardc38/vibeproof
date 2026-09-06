"""A third way to close a finding, for the class the first two cannot reach.

    python3 -m unittest tests.test_a_red_half_for_a_covering_test -v

`review close` had two instruments and a finding of the form "nothing in the
suite enters this function" fitted neither.

  red-green      `redgreen.verify` copies the closing test into the parent tree
                 and runs it there. A test covering code that was always
                 correct passes at the parent, and is refused for pinning
                 nothing -- which is true, and is not the worker's fault.

  text closure   `bind_text_change` is checked against the finding's own file.
                 The repair for a coverage finding is a new file under
                 `tests/`, so `text_now is not in kernel/install.py`.

Both refusals are right on their own terms. Measured on five such findings in
one cut: all five refused by both, leaving a signature as the only exit -- the
outcome `review.resolve_symbol`'s docstring exists to prevent, and the one
SPEC 4.4 says the text closure was added to reduce.

So: `--mutation-file` with `--mutation-gone`. Break what the test covers in a
throwaway worktree and require the test to notice. That is red-green one level
up, and it is what the author of such a test does by hand anyway to know the
test works.

The green half is untouched -- same tracer, same symbol-executed requirement,
same source-assertion refusal. Only the red half is new.
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

from kernel import ledger, redgreen, review  # noqa: E402

#: A module with one function, and a test that covers it. Neither is broken, so
#: no parent commit exists where the test fails -- which is the whole case this
#: instrument is for.
MODULE = '''def gate(value):
    """Refuse anything that is not a positive number."""
    if not isinstance(value, int) or value <= 0:
        return False
    return True
'''

TEST = '''import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mod


class TheGateRefusesWhatItSaysItRefuses(unittest.TestCase):
    def test_a_positive_number_is_allowed(self):
        self.assertTrue(mod.gate(3))

    def test_zero_and_below_are_not(self):
        self.assertFalse(mod.gate(0))
        self.assertFalse(mod.gate(-1))

    def test_and_neither_is_a_string(self):
        self.assertFalse(mod.gate("3"))
'''


def _git_repo(case) -> Path:
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    return tmp


class _Repo(unittest.TestCase):
    """A repo where the code is correct and the test is new."""

    def setUp(self):
        self.root = _git_repo(self)
        (self.root / "mod.py").write_text(MODULE)
        self._commit("the code, correct from the start")
        (self.root / "t_mod.py").write_text(TEST)
        self._commit("the test somebody finally wrote")

    def _commit(self, message):
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root,
                       capture_output=True)

    def _verify(self, **kw):
        return redgreen.verify(
            self.root,
            command=[sys.executable, "-m", "unittest", "t_mod"],
            test_path="t_mod.py", target_file="mod.py", target_symbol="gate",
            **kw)


class TheParentRouteCannotReachThis(_Repo):
    """First, the thing that made a third instrument necessary."""

    def test_a_covering_test_passes_at_the_parent_and_is_refused(self):
        parent = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=self.root,
                                capture_output=True, text=True).stdout.strip()
        res = self._verify(parent_commit=parent)
        self.assertTrue(res.green_passed, res.notes)
        self.assertFalse(res.red_failed)
        self.assertFalse(res.ok)
        self.assertTrue(any("pins nothing" in n for n in res.notes), res.notes)


class BreakingWhatTheTestCoversIsTheOtherRedHalf(_Repo):
    """The instrument itself."""

    def test_a_mutation_the_test_notices_is_a_closure(self):
        res = self._verify(mutation=("mod.py", "value <= 0", "value < -99"))
        self.assertTrue(res.green_passed, res.notes)
        self.assertTrue(res.red_failed, res.notes)
        self.assertTrue(res.symbol_executed, res.notes)
        self.assertTrue(res.ok, res.notes)

    def test_a_mutation_the_test_sleeps_through_is_not(self):
        """The control, and the whole point: this is a claim about coverage, so
        a mutation the test does not notice has to come back as a failure."""
        (self.root / "mod.py").write_text(
            MODULE + "\n\ndef unused():\n    return 1\n")
        self._commit("something nothing covers")
        res = self._verify(mutation=("mod.py", "def unused():\n    return 1",
                                     "def unused():\n    return 2"))
        self.assertTrue(res.green_passed, res.notes)
        self.assertFalse(res.red_failed)
        self.assertFalse(res.ok)
        self.assertTrue(any("does not cover what it says" in n
                            for n in res.notes), res.notes)

    def test_the_live_tree_is_never_touched(self):
        """A worker is standing in it. The mutation happens in a worktree.

        Asked of git rather than by reading `mod.py` back. Reading the file is
        the narrower question -- it would miss a mutation applied to any other
        path -- and it is also the shape `test-shape` refuses, for the reason
        it gives: a test that reads source text passes whatever the code does.
        `git status --porcelain` answers about the whole tree.

        `-uno`, because running the test leaves a `__pycache__` here and that
        is not the mutation reaching anything. Tracked files are the right set
        anyway: `why_not_a_mutation` refuses an untracked target, so a mutation
        that escaped would land on one of these.
        """
        self._verify(mutation=("mod.py", "value <= 0", "value < -99"))
        dirty = subprocess.run(["git", "status", "--porcelain", "-uno"],
                               cwd=self.root, capture_output=True,
                               text=True).stdout.strip()
        self.assertEqual(dirty, "", "the mutation reached the live tree")

    def test_a_marker_that_matches_twice_is_refused(self):
        """Breaking two things and watching a test go red says nothing about
        which of them it noticed."""
        (self.root / "mod.py").write_text(
            MODULE + MODULE.replace("gate", "gate2"))
        self._commit("a second copy")
        res = self._verify(mutation=("mod.py", "if not isinstance(value, int)",
                                     "if False"))
        self.assertFalse(res.ok)
        self.assertTrue(any("exactly once" in n for n in res.notes), res.notes)

    def test_a_marker_that_matches_nothing_is_refused(self):
        """Otherwise the worktree is unbroken, the test passes there, and the
        worker is told their test does not cover the code -- which is false."""
        res = self._verify(mutation=("mod.py", "a line that is not in the file",
                                     ""))
        self.assertFalse(res.ok)
        self.assertTrue(any("0 time(s)" in n for n in res.notes), res.notes)

    def test_the_symbol_still_has_to_be_entered(self):
        """The green half is unchanged. A test that never calls the code cannot
        close a finding about it, whichever way it is made red."""
        (self.root / "t_mod.py").write_text(
            "import unittest\n\n\n"
            "class ReadsTheSourceAndNothingElse(unittest.TestCase):\n"
            "    def test_it_says_the_words(self):\n"
            "        self.assertIn('isinstance', open('mod.py').read())\n")
        self._commit("a test that reads instead of running")
        res = self._verify(mutation=("mod.py", "if not isinstance(value, int)",
                                     "if False"))
        self.assertFalse(res.ok, res.notes)

    def test_asking_for_both_halves_or_neither_is_refused(self):
        """They answer the same question, so offering both leaves no answer to
        which one counts."""
        with self.assertRaises(ValueError):
            self._verify(parent_commit="HEAD~1",
                         mutation=("mod.py", "value <= 0", "value < -99"))
        with self.assertRaises(ValueError):
            self._verify()


class WhatTheCommandWillAccept(unittest.TestCase):
    """`review.why_not_a_mutation` -- the refusals before anything runs."""

    def setUp(self):
        self.root = _git_repo(self)
        (self.root / "mod.py").write_text(MODULE)
        (self.root / "t_mod.py").write_text(TEST)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)

    def test_a_real_mutation_is_accepted(self):
        self.assertEqual(
            review.why_not_a_mutation(
                self.root, "mod.py",
                "if not isinstance(value, int) or value <= 0", "t_mod.py"),
            "")

    def test_breaking_the_closing_test_itself_is_refused(self):
        why = review.why_not_a_mutation(
            self.root, "t_mod.py", "self.assertFalse(mod.gate(0))", "t_mod.py")
        self.assertIn("closing test itself", why)

    def test_a_short_marker_is_refused(self):
        why = review.why_not_a_mutation(self.root, "mod.py", "return True",
                                        "t_mod.py")
        self.assertIn("floor is", why)

    def test_an_untracked_file_is_refused(self):
        """The red half runs in a worktree, which carries tracked files only."""
        (self.root / "scratch.py").write_text(MODULE)
        why = review.why_not_a_mutation(
            self.root, "scratch.py",
            "if not isinstance(value, int) or value <= 0", "t_mod.py")
        self.assertIn("not tracked", why)

    def test_naming_no_file_is_refused(self):
        self.assertIn("names the file",
                      review.why_not_a_mutation(self.root, "", "x" * 40, "t.py"))


class WhatTheLedgerRecords(unittest.TestCase):
    """`bind_closing_test` -- one event, and the two routes it can carry."""

    def setUp(self):
        self.root = _git_repo(self)
        (self.root / ".v4").mkdir()
        (self.root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.root / ".v4" / "claim_kinds.json").write_text("{}")
        (self.root / ".v4" / "checkers.json").write_text("{}")
        (self.root / "mod.py").write_text(MODULE)
        (self.root / "t_mod.py").write_text(TEST)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)

    def _payload(self):
        row = self.conn.execute(
            "SELECT payload FROM event WHERE kind = 'review_close' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["payload"]) if row else {}

    def test_a_mutation_route_is_recorded_whole(self):
        review.bind_closing_test(
            self.conn, claim_id="c1", test_path="t_mod.py",
            command="python3 -m unittest t_mod",
            mutation=("mod.py", "if not isinstance(value, int) or value <= 0",
                      "if False"),
            root=self.root)
        p = self._payload()
        self.assertEqual(p["mutation_file"], "mod.py")
        self.assertIn("isinstance", p["mutation_gone"])
        self.assertEqual(p["mutation_now"], "if False")
        self.assertEqual(p["parent_commit"], "")

    def test_the_parent_route_still_records_what_it_always_did(self):
        review.bind_closing_test(
            self.conn, claim_id="c2", test_path="t_mod.py",
            command="python3 -m unittest t_mod", parent_commit="abc123",
            root=self.root)
        p = self._payload()
        self.assertEqual(p["parent_commit"], "abc123")
        self.assertNotIn("mutation_file", p)

    def test_offering_both_or_neither_is_refused(self):
        for kw in ({"parent_commit": "abc123",
                    "mutation": ("mod.py", "x" * 40, "")},
                   {}):
            with self.subTest(kw=sorted(kw)):
                with self.assertRaises(review.BadCoordinates):
                    review.bind_closing_test(
                        self.conn, claim_id="c3", test_path="t_mod.py",
                        command="python3 -m unittest t_mod", root=self.root,
                        **kw)


if __name__ == "__main__":
    unittest.main()
