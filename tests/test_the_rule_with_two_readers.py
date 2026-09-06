"""The source-assertion rule, read by both the things that apply it.

    python3 -m unittest tests.test_the_rule_with_two_readers -v

`test-shape` refuses a test that asserts on source text, and two programs apply
that refusal: `checkers/test_shape.py` on every `v4 check`, and
`redgreen.verify` on every test offered as a closure. Only the first read
`.v4/test-shape_baseline.json` -- the list of findings this repo accepted with a
written reason.

So an accepted assertion left `v4 check` green and still refused every closure
offered out of its file. Measured once, in this repo, on `918d717f`'s own
evidence: `d52045d6` was repaired inside a class that has to `ast.parse`
`kernel/cli.py` to do its job at all; baseline entry `2b3f027807b54dd7` accepted
exactly that; `test-shape` printed "carrying 8 accepted finding(s)"; and the
closure was refused with "asserts on the source text of the code it is closing"
and had to be signed `unprovable` instead.

Two things have to hold, and both have a class here.

The id, because it is the whole of the lookup: `findings` and `finding_id` are
the one spelling both readers import, and if the narrowing `verify` does moved
the id, `verify` would be looking up something the checker never wrote and every
baseline entry in every adopter's repo would forgive nothing here.

And the filename, because the fix could have opened a hole while closing one.
The refusal used to call `source_assertions` directly and so applied to any file
at all; `findings` asks `subject_files.is_test`, which answers on the name
outside a declared test root. `TheFilenameDoesNotDecideThis` is the case that
says a closing test named `t_mod.py` is still a test.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import baseline, redgreen                           # noqa: E402
from kernel.analysis import subject_files, test_shape           # noqa: E402

#: A closing test that does both: calls the symbol in a way the mutation breaks,
#: and reads source text. Only the second thing is in question here -- a class
#: that merely reads source is refused for two reasons and proves neither.
MODULE = '''import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class TheOneThatDoesBoth(unittest.TestCase):
    def test_it_calls_and_reads(self):
        from mod import gate
        self.assertTrue(gate(3))
        self.assertIn("isinstance", Path("mod.py").read_text())
'''

TARGET = ("def gate(value):\n"
          "    if not isinstance(value, int) or value <= 0:\n"
          "        return False\n"
          "    return True\n")

#: Inverts the guard, so `gate(3)` returns False and the class above goes red.
MUTATION = ("mod.py", "if not isinstance(value, int) or value <= 0",
            "if isinstance(value, int)")


def one_id(rel, src=MODULE, **kw):
    """The id the checker files this fixture's one finding under.

    No `is_test` unless a caller passes one, because that is the checker's own
    call and the id has to be the checker's. Only `TheFilenameDoesNotDecideThis`
    needs the override, and it says why where it uses it.
    """
    found = test_shape.findings(rel, src, ast.parse(src), "source_assertion",
                                **kw)
    assert len(found) == 1, found
    return test_shape.finding_id(found[0])


class TheIdTheOtherReaderWrote(unittest.TestCase):
    """`verify` narrows the tree to what the command runs. The checker does not.
    If that changed the id, the lookup would be against nothing."""

    def setUp(self):
        self.tree = ast.parse(MODULE)

    def test_there_is_exactly_one_finding_to_forgive(self):
        found = test_shape.findings("test_mod.py", MODULE, self.tree,
                                    "source_assertion")
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][0], "test_mod.py")
        self.assertEqual(found[0][1], "test_it_calls_and_reads")

    def test_narrowing_does_not_move_the_id(self):
        whole = test_shape.findings("test_mod.py", MODULE, self.tree,
                                    "source_assertion")
        narrowed = test_shape.findings(
            "test_mod.py", MODULE,
            redgreen.what_runs(
                self.tree, "python3 -m unittest test_mod.TheOneThatDoesBoth"),
            "source_assertion")
        self.assertEqual([test_shape.finding_id(f) for f in whole],
                         [test_shape.finding_id(f) for f in narrowed])

    def test_the_id_does_not_come_from_the_line_number(self):
        """The reason `finding_id` is over `(file, symbol, why)`: a baseline
        made of line numbers forgives the wrong finding on the next commit."""
        self.assertEqual(one_id("test_mod.py"),
                         one_id("test_mod.py", "\n\n\n\n" + MODULE))

    def test_asking_subject_files_and_overriding_it_give_the_same_id(self):
        """The override changes whether a finding is produced, never which one.
        If it reached the id, `verify` and the checker would disagree on every
        file, which is the failure this whole change is about."""
        asked = test_shape.findings("test_mod.py", MODULE, self.tree,
                                    "source_assertion")
        told = test_shape.findings("test_mod.py", MODULE, self.tree,
                                   "source_assertion", is_test=True)
        self.assertEqual([test_shape.finding_id(f) for f in asked],
                         [test_shape.finding_id(f) for f in told])


class _AVerifiableClosure(unittest.TestCase):
    """A one-commit repo whose closing test both runs the code and reads it."""

    test_name = "test_mod.py"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.root, capture_output=True)
        (self.root / "mod.py").write_text(TARGET)
        (self.root / self.test_name).write_text(MODULE)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)
        self.fid = self._finding_id()

    def _finding_id(self):
        return one_id(self.test_name)

    def _write_baseline_text(self, text):
        d = self.root / ".v4"
        d.mkdir(exist_ok=True)
        (d / f"{test_shape.KIND}_baseline.json").write_text(text)

    def _accept(self, *ids):
        # Through `baseline.block`, the producer `load` was written against, so
        # this fixture cannot drift into a shape the reader does not accept.
        self._write_baseline_text(baseline.block(
            [{"id": i, "why": "the fixture accepted it"} for i in ids]))

    def _verify(self):
        selector = f"{self.test_name[:-3]}.TheOneThatDoesBoth"
        return redgreen.verify(
            self.root,
            command=[sys.executable, "-m", "unittest", selector],
            test_path=self.test_name, target_file="mod.py",
            target_symbol="gate", mutation=MUTATION)


class WhatVerifyDoesWithTheAcceptedList(_AVerifiableClosure):
    def test_with_no_baseline_at_all_it_is_refused(self):
        """The red half of this change: today's behaviour, and the state
        `918d717f` was reported in."""
        res = self._verify()
        self.assertFalse(res.ok)
        self.assertTrue(any("source text" in n for n in res.notes), res.notes)

    def test_an_accepted_finding_no_longer_blocks_the_closure(self):
        self._accept(self.fid)
        res = self._verify()
        self.assertTrue(res.ok, res.notes)

    def test_and_it_says_it_forgave_something(self):
        """A verdict reached while forgiving is not the same verdict as one
        reached with nothing to forgive."""
        self._accept(self.fid)
        res = self._verify()
        self.assertTrue(
            any("carrying 1 accepted finding" in n for n in res.notes),
            res.notes)

    def test_a_baseline_accepting_something_else_forgives_nothing(self):
        """The control: it is the id that forgives, not the file's existence."""
        self._accept("0" * 16)
        res = self._verify()
        self.assertFalse(res.ok)
        self.assertTrue(any("source text" in n for n in res.notes), res.notes)

    def test_an_unreadable_baseline_refuses_rather_than_forgives(self):
        """`.v4/**` is protected, which is expensive to write, not impossible.
        A file nobody can read must not be a cheaper way past the rule than
        answering the assertion."""
        self._write_baseline_text("{ not json")
        res = self._verify()
        self.assertFalse(res.ok)
        self.assertTrue(any("could not be read" in n for n in res.notes),
                        res.notes)
        self.assertTrue(any("refused" in n for n in res.notes), res.notes)

    def test_a_baseline_that_is_a_bare_list_of_ids_works(self):
        """What somebody writes by hand the first time. `load` accepts it, so
        this reader has to as well."""
        self._write_baseline_text(json.dumps([self.fid]))
        res = self._verify()
        self.assertTrue(res.ok, res.notes)


class TheFilenameDoesNotDecideThis(_AVerifiableClosure):
    """`subject_files.is_test` answers on the name outside a declared test root,
    and `t_mod.py` is not a test by that answer. The refusal here was
    unconditional before this change and has to stay unconditional: the file a
    closure names as its `--test` is a test by construction."""

    test_name = "t_mod.py"

    def _finding_id(self):
        """`subject_files` says no to this name, so nothing is found without the
        override -- which is the hole, stated as the way the id is obtained."""
        return one_id(self.test_name, is_test=True)

    def test_it_is_not_a_test_by_name(self):
        """The premise, checked rather than assumed -- if `subject_files` ever
        starts saying yes to this name, the case below stops testing anything
        and this one goes red to say so."""
        self.assertFalse(subject_files.is_test("t_mod.py", MODULE))

    def test_and_the_closure_is_refused_anyway(self):
        res = self._verify()
        self.assertFalse(res.ok)
        self.assertTrue(any("source text" in n for n in res.notes), res.notes)

    def test_and_its_own_id_still_forgives_it(self):
        self._accept(self.fid)
        res = self._verify()
        self.assertTrue(res.ok, res.notes)


if __name__ == "__main__":
    unittest.main()
