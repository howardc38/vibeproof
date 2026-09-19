"""A red half that never reached the code is not a red half.

`verify` read the red run's exit code as the whole answer. A worktree carries
tracked files only, so a repo whose runner or imports are untracked dies at
import, exits non-zero, and hands that over as "red" -- measured, a mutation
that added a comment and changed nothing returned `ok: True`.

The tracer already knew. It was asked for the green half and for the
declaration and browser paths, and thrown away for the ordinary one.

The other half of the same reading: the per-symbol tracer wrote one shared
file that every process truncated on its way out, so a symbol entered in a
spawn child, a fork child or a subprocess read as never executed -- and so did
one the parent entered before a helper process exited after it.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import redgreen  # noqa: E402


def _git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)


class _Repo(unittest.TestCase):
    def repo(self, files, ignore=""):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(root)])
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        if ignore:
            (root / ".gitignore").write_text(ignore)
        for name, body in files.items():
            (root / name).write_text(body)
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
        return root


class AStartupFailureIsNotRed(_Repo):
    """The tree the red half runs in has no untracked files."""

    CMD = ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "t_it.py", "-q"]
    TEST = ("import unittest, app\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        self.assertEqual(app.thing(), 7)\n")

    def test_a_no_op_mutation_does_not_close_because_an_import_is_missing(self):
        root = self.repo({"app.py": "import localsettings\n\n\ndef thing():\n"
                                    "    return localsettings.VALUE\n",
                          "t_it.py": self.TEST},
                         ignore="localsettings.py\n__pycache__/\n")
        (root / "localsettings.py").write_text("VALUE = 7\n")   # untracked on purpose
        res = redgreen.verify(
            root, command=self.CMD, test_path="t_it.py", target_file="app.py",
            target_symbol="thing", parent_commit=None,
            mutation=("app.py", "    return localsettings.VALUE",
                      "    return localsettings.VALUE  # no-op comment, changes nothing"))
        self.assertFalse(res.ok)
        self.assertTrue(any("without entering thing" in n or
                            "without anything observing thing" in n
                            for n in res.notes), res.notes)

    def test_a_mutation_that_changes_behaviour_still_closes(self):
        """The green half: this route is how a finding whose repair is a test
        gets closed at all, and it has to keep working."""
        root = self.repo({"app.py": "def thing():\n    return 7\n", "t_it.py": self.TEST})
        res = redgreen.verify(
            root, command=self.CMD, test_path="t_it.py", target_file="app.py",
            target_symbol="thing", parent_commit=None,
            mutation=("app.py", "    return 7", "    return 8  # behaviour changed"))
        self.assertTrue(res.ok, res.notes)


class TheTracerSeesEveryProcess(_Repo):
    CMD = ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "t_mp.py", "-q"]
    APP = "def thing(n):\n    return n * 2\n"

    def _executed(self, test_body):
        root = self.repo({"app.py": self.APP, "t_mp.py": test_body})
        _rc, executed, calls, _out = redgreen._run_traced(
            root, self.CMD, "app.py", "thing", timeout=120)
        return executed, calls

    def test_a_spawn_child_counts(self):
        executed, calls = self._executed(
            "import multiprocessing as mp, unittest, app\n"
            "def run(q):\n    q.put(app.thing(3))\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        ctx = mp.get_context('spawn'); q = ctx.Queue()\n"
            "        p = ctx.Process(target=run, args=(q,)); p.start()\n"
            "        self.assertEqual(q.get(timeout=30), 6); p.join()\n")
        self.assertTrue(executed)
        self.assertEqual(calls, 1)

    def test_a_subprocess_counts(self):
        executed, _calls = self._executed(
            "import subprocess, sys, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        out = subprocess.run([sys.executable, '-c',\n"
            "                              'import app; print(app.thing(3))'],\n"
            "                             capture_output=True, text=True, cwd='.')\n"
            "        self.assertEqual(out.stdout.strip(), '6')\n")
        self.assertTrue(executed)

    def test_a_helper_that_exits_later_does_not_erase_the_parent(self):
        """The shape that forced a correct multiprocessing test to be rewritten:
        the parent entered the symbol, and a helper process reported last."""
        executed, _calls = self._executed(
            "import multiprocessing as mp, unittest, app\n"
            "def idle(q):\n    q.get()\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        ctx = mp.get_context('spawn'); q = ctx.Queue()\n"
            "        p = ctx.Process(target=idle, args=(q,)); p.start()\n"
            "        self.assertEqual(app.thing(3), 6)\n"
            "        q.put(None); p.join()\n")
        self.assertTrue(executed)

    def test_nothing_entering_it_is_still_false(self):
        """The control: the union must not turn every run green."""
        executed, calls = self._executed(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertTrue(True)\n")
        self.assertIs(executed, False)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
