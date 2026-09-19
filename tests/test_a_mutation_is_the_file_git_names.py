"""A mutation names the file git names, and writes only the throwaway tree.

The rule "a red half breaks the code, not the test" was enforced once, where
the closure was bound, by comparing the argument string to the closing test's.
So `./t.py` was a different file to the guard and the same file to the
filesystem: the red half was the closing test breaking itself, and
`redgreen.verify` returned `ok=True` for it.

An absolute path was worse. `worktree / rel` is not a boundary -- an absolute
`rel` replaces the worktree -- so a bound closure rewrote the live checkout
while a checker was judging it.

Every case below was measured on 2101f23 before the repair.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import redgreen, review  # noqa: E402

CMD = ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "test_new.py", "-q"]
GONE = "return name[:8] + str(len(name))"
NOW = "return name[:8] + str(len(name) + 1)"


def _git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)


class _Repo(unittest.TestCase):
    """One commit: a function, and a test that pins what it returns."""

    def repo(self, extra=None):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(root)])
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        (root / "app.py").write_text(f"def notify(name):\n    {GONE}\n")
        (root / "test_new.py").write_text(
            "import unittest, app\n"
            "class T(unittest.TestCase):\n"
            "    def test_c(self):\n"
            "        self.assertEqual(app.notify('abcdefghij'), 'abcdefgh10')\n")
        for name, body in (extra or {}).items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
        return root


class EverySpellingOfTheClosingTestIsTheClosingTest(_Repo):
    def test_the_guard_reads_gits_name_not_the_argument(self):
        root = self.repo()
        marker = "self.assertEqual(app.notify('abcdefghij'), 'abcdefgh10')"
        for spelling in ("test_new.py", "./test_new.py", ".//test_new.py",
                         "./x/../test_new.py", "test_new.py "):
            problem = review.why_not_a_mutation(root, spelling, marker, "test_new.py")
            self.assertTrue(problem, f"{spelling!r} was accepted as a mutation target")

    def test_verify_refuses_it_even_when_the_binding_is_already_in_the_ledger(self):
        """The judge asks too.  A payload reaching `verify` came out of the
        ledger, and the ledger records what a worker offered."""
        root = self.repo()
        marker = "self.assertEqual(app.notify('abcdefghij'), 'abcdefgh10')"
        res = redgreen.verify(
            root, command=CMD, test_path="test_new.py", target_file="app.py",
            target_symbol="notify", parent_commit=None,
            mutation=("./test_new.py", marker, "self.assertEqual(1, 2)"))
        self.assertFalse(res.ok)
        self.assertTrue(any("spelled differently" in n for n in res.notes), res.notes)


class TheTestSideIsNotTheCode(_Repo):
    def test_a_helper_the_test_compares_against_is_refused(self):
        """Breaking what a test compares against makes it fail without the
        code behaving differently -- the same hollowness, one file over."""
        root = self.repo({"tests/__init__.py": "",
                          "tests/expectations.py": "EXPECTED_GREETING_VALUE = 'abcdefgh10'\n"})
        problem = review.why_not_a_mutation(
            root, "tests/expectations.py",
            "EXPECTED_GREETING_VALUE = 'abcdefgh10'", "test_new.py")
        self.assertIn("test side", problem)

    def test_a_foreign_language_test_file_read_as_data_is_not(self):
        """The control. A `.test.ts` file can be the input a Python scanner's
        test reads; refusing it would refuse a legitimate closure."""
        root = self.repo({"web/Widget.test.ts": "export const TOKEN = 'placeholder-value';\n"})
        problem = review.why_not_a_mutation(
            root, "web/Widget.test.ts",
            "export const TOKEN = 'placeholder-value';", "test_new.py")
        self.assertEqual(problem, "")

    def test_the_code_itself_still_closes(self):
        """The green half of this rule: a real mutation on product code is
        what the whole route is for, and it still works."""
        root = self.repo()
        self.assertEqual(review.why_not_a_mutation(root, "app.py", GONE, "test_new.py"), "")
        res = redgreen.verify(root, command=CMD, test_path="test_new.py",
                              target_file="app.py", target_symbol="notify",
                              parent_commit=None, mutation=("app.py", GONE, NOW))
        self.assertTrue(res.ok, res.notes)


class TheLiveTreeIsNeverWritten(_Repo):
    def test_an_absolute_path_does_not_reach_the_checkout_being_judged(self):
        root = self.repo()
        target = str(root / "app.py")
        self.assertTrue(review.why_not_a_mutation(root, target, GONE, "test_new.py"))
        redgreen.verify(root, command=CMD, test_path="test_new.py",
                        target_file="app.py", target_symbol="notify",
                        parent_commit=None, mutation=(target, GONE, NOW))
        dirty = _git(root, "status", "--porcelain", "-uno").stdout.strip()
        self.assertEqual(dirty, "", f"the live tree was written: {dirty}")

    def test_a_symlink_is_not_a_regular_file(self):
        root = self.repo()
        (root / "link.py").symlink_to("app.py")
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "link")
        problem = review.why_not_a_mutation(root, "link.py", GONE, "test_new.py")
        self.assertIn("not a regular file", problem)


class AClosingTestIsInsideTheRepo(_Repo):
    def test_a_path_that_leaves_the_repo_is_refused(self):
        """`(root / test_path).is_file()` claimed "in this repo" and did not
        check it; such a test hashes to a constant, so editing it never
        expired the answer it earned."""
        root = self.repo()
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(outside)])
        (outside / "test_out.py").write_text("def test_x():\n    assert True\n")
        self.assertTrue(review.why_not_a_test_file(root, str(outside / "test_out.py")))
        self.assertTrue(review.why_not_a_test_file(root, "../test_out.py"))
        self.assertEqual(review.why_not_a_test_file(root, "test_new.py"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
