"""The source-assertion refusal, asked of what the command runs.

    python3 -m unittest tests.test_asked_of_what_runs -v

`redgreen.verify` refuses a closing test that reads source text instead of
running the code, and the reasoning holds: calling a symbol once and then
reading its source satisfies both halves of red-green and proves nothing.

It asked that of the whole file. Measured four times in one day:

  * `ebbdbc89` was refused a closure from `tests/test_kernel.py` -- 8,710 lines,
    114 classes -- for assertions in classes that had nothing to do with it,
    and had to be signed for
  * `0d039e43` was refused from `tests/test_what_guards_the_guards.py` the same
    way, and closed through a different file
  * `3f66e2f9` and `d52045d6` have their subject *inside* `test_kernel.py`, so
    no closure from there was possible at all

A closure runs what its `--command` names. So does the refusal now.

The narrowing is string containment against the names the file defines: a
command is a runner's syntax, and parsing it would be this module learning
three grammars. A command naming none of them gets the whole module, which is
what it runs.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import redgreen                                     # noqa: E402
from kernel.analysis import test_shape                          # noqa: E402

MODULE = '''import unittest
from pathlib import Path


class TheOneThatRunsTheCode(unittest.TestCase):
    def test_it_calls_the_thing(self):
        from mod import gate
        self.assertTrue(gate(3))


class TheOneThatReadsTheSource(unittest.TestCase):
    def test_it_reads_instead(self):
        self.assertIn("isinstance", Path("mod.py").read_text())


def a_helper_that_reads_too():
    return Path("mod.py").read_text()
'''


class TheUnitTheRefusalIsAskedOf(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(MODULE)

    def _names(self, picked):
        return [n.name for n in picked.body]

    def test_the_whole_file_carries_a_source_assertion(self):
        """The state that disqualified everything in it."""
        self.assertTrue(test_shape.source_assertions(self.tree, MODULE))

    def test_a_command_naming_one_class_is_asked_about_that_class(self):
        picked = redgreen.what_runs(
            self.tree, "python3 -m unittest t.TheOneThatRunsTheCode")
        self.assertEqual(self._names(picked), ["TheOneThatRunsTheCode"])
        self.assertEqual(test_shape.source_assertions(picked, MODULE), [])

    def test_and_the_class_that_does_read_source_is_still_refused(self):
        """The control: narrowing must not become a way past the rule."""
        picked = redgreen.what_runs(
            self.tree, "python3 -m unittest t.TheOneThatReadsTheSource")
        self.assertEqual(self._names(picked), ["TheOneThatReadsTheSource"])
        self.assertTrue(test_shape.source_assertions(picked, MODULE))

    def test_a_command_naming_the_module_alone_gets_the_module(self):
        """A whole-module run really does execute everything, so the honest
        answer is today's answer."""
        picked = redgreen.what_runs(self.tree, "python3 -m unittest t")
        self.assertIs(picked, self.tree)
        self.assertTrue(test_shape.source_assertions(picked, MODULE))

    def test_a_list_command_reads_the_same_way(self):
        """`review close --command` may be a list; the checker splits a string
        into one before calling."""
        picked = redgreen.what_runs(
            self.tree, ["python3", "-m", "unittest", "t.TheOneThatRunsTheCode"])
        self.assertEqual(self._names(picked), ["TheOneThatRunsTheCode"])

    def test_two_named_classes_are_both_asked_about(self):
        picked = redgreen.what_runs(
            self.tree,
            "python3 -m unittest t.TheOneThatRunsTheCode t.TheOneThatReadsTheSource")
        self.assertEqual(sorted(self._names(picked)),
                         ["TheOneThatReadsTheSource", "TheOneThatRunsTheCode"])
        self.assertTrue(test_shape.source_assertions(picked, MODULE))

    def test_a_module_level_function_can_be_named_too(self):
        """pytest selects functions, not only classes."""
        picked = redgreen.what_runs(
            self.tree, "pytest t.py::a_helper_that_reads_too")
        self.assertEqual(self._names(picked), ["a_helper_that_reads_too"])

    def test_it_only_ever_narrows(self):
        """A file with nothing to refuse is unaffected whatever the command."""
        clean = ast.parse(
            "import unittest\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertTrue(True)\n")
        for command in ("python3 -m unittest t", "python3 -m unittest t.T",
                        "pytest t.py"):
            with self.subTest(command=command):
                self.assertEqual(
                    test_shape.source_assertions(
                        redgreen.what_runs(clean, command), ""), [])


class TheRefusalStillReachesVerify(unittest.TestCase):
    """Through `verify`, because that is where the narrowing was needed."""

    def setUp(self):
        import shutil
        import subprocess
        import tempfile

        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.root, capture_output=True)
        (self.root / "mod.py").write_text(
            "def gate(value):\n"
            "    if not isinstance(value, int) or value <= 0:\n"
            "        return False\n"
            "    return True\n")
        (self.root / "t_mod.py").write_text(
            "import sys\nimport unittest\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "import mod\n\n\n" + MODULE.split("import unittest\n", 1)[1]
            .replace("from mod import gate", "from mod import gate"))
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)

    def _verify(self, selector):
        return redgreen.verify(
            self.root,
            command=[sys.executable, "-m", "unittest", selector],
            test_path="t_mod.py", target_file="mod.py", target_symbol="gate",
            # A mutation the running class actually notices: `if False` would
            # make `gate` return True for everything, which is what that class
            # asserts. This inverts the guard instead, so `gate(3)` is False.
            mutation=("mod.py", "if not isinstance(value, int) or value <= 0",
                      "if isinstance(value, int)"))

    def test_the_class_that_runs_the_code_can_close(self):
        res = self._verify("t_mod.TheOneThatRunsTheCode")
        self.assertTrue(res.ok, res.notes)

    def test_and_the_class_that_reads_source_still_cannot(self):
        res = self._verify("t_mod.TheOneThatReadsTheSource")
        self.assertFalse(res.ok)
        self.assertTrue(any("source text" in n for n in res.notes), res.notes)


if __name__ == "__main__":
    unittest.main()
