"""A caller the checker called fine, and a branch nothing ever ran.

    python3 -m unittest tests.test_the_caller_that_would_raise -v

`signature_change` answers "did this caller thread the newly required
parameter". It compared a total argument count against a required-parameter
count, so a keyword-only requirement was satisfied by any other keyword -- and
the call it passed raises `TypeError` when run. Beside it, the branch that reads
the commonest TypeScript declaration form executed zero times in a full suite.

`dead_wiring` builds the file set it judges from its own copy of a rule the
analysis layer owns, without the policy that layer carries.

All of them fail against 060923b.
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

from kernel.analysis import signature_change as sig  # noqa: E402


def _repo(case, files, *, config=None):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        config or {"test_command": "true", "policy": "allow_accepted_risk"}))
    for name, body in files.items():
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                    "add", "-A"], cwd=tmp, capture_output=True)
    return tmp


class AKeywordOnlyRequirementIsCheckedByName(unittest.TestCase):
    """`given = len(node.args) + len(node.keywords)` is a total, and `now` is a
    count of required parameters. So any keyword satisfied a new keyword-only
    one -- and `f(1, verbose=True)` against `def f(a, *, token, verbose=False)`
    raises `TypeError: missing 1 required keyword-only argument: 'token'`."""

    BEFORE = "def f(a, *, verbose=False):\n    return a\n"
    AFTER = "def f(a, *, token, verbose=False):\n    return a\n"

    def _scan(self, caller):
        root = _repo(self, {"mod.py": self.AFTER, "caller.py": caller})
        return sig.scan(root, lambda rel: self.BEFORE, ["mod.py"])

    def test_a_caller_that_would_raise_is_reported(self):
        got = self._scan("from mod import f\n\nf(1, verbose=True)\n")
        self.assertTrue(got, "a call that raises at runtime was reported clean")

    def test_and_the_call_really_does_raise(self):
        """The measurement the case above rests on, not an assumption about
        Python's rules."""
        root = _repo(self, {"mod.py": self.AFTER,
                            "caller.py": "from mod import f\nf(1, verbose=True)\n"})
        r = subprocess.run([sys.executable, "caller.py"], cwd=root,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("keyword-only argument", r.stderr)

    def test_a_caller_that_threaded_it_is_not_reported(self):
        """The control. Reporting every caller would pass the case above and
        make the checker useless."""
        got = self._scan("from mod import f\n\nf(1, token='x', verbose=True)\n")
        self.assertEqual(got, [], got)

    def test_and_the_positional_case_still_works(self):
        """The other control: the repair must not trade one blind spot for
        another. A caller left at the old positional arity was already
        reported and has to stay reported."""
        root = _repo(self, {"mod.py": "def g(a, b):\n    return a\n",
                            "caller.py": "from mod import g\n\ng(1)\n"})
        got = sig.scan(root, lambda rel: "def g(a):\n    return a\n", ["mod.py"])
        self.assertTrue(got, got)


class TheArrowFormIsRead(unittest.TestCase):
    """`_ts_arrow_params` finds the `(` of `= (a) =>` and the two forms beside
    it, and executed zero times in a full suite run: all 25 `signature_change`
    fixtures declare the changed function as `export function send(...)` and use
    the arrow form only in the caller file. So the arity of the commonest
    TypeScript declaration form was covered by nothing.

    It takes a masked source and the index just past the name, and returns the
    index of the opening paren -- I read its contract off a wrong guess first
    (a name and a count) and the cases below are what it actually answers.
    """

    def _params(self, src, name):
        """The parameter text the checker would read, through the real path."""
        i = src.index(name) + len(name)
        at = sig._ts_arrow_params(src, i)
        if at is None:
            return None
        return src[at:]

    def test_the_plain_arrow(self):
        self.assertTrue(
            self._params("export const f = (a, b) => a + b;", "f").startswith(
                "(a, b)"))

    def test_the_async_arrow(self):
        self.assertTrue(
            self._params("export const f = async (a) => a;", "f").startswith(
                "(a)"))

    def test_the_function_expression(self):
        self.assertTrue(
            self._params("export const f = function (a, b) { return a; }",
                         "f").startswith("(a, b)"))

    def test_a_generic_annotation_does_not_break_the_balance(self):
        """The case its own docstring says it was written for: the `=` inside
        `Cmp<A = B>` is not the assignment, and stopping there reads the rest
        as a parameter list that is not one."""
        got = self._params("export const f: Cmp<A = B> = (a) => a;", "f")
        self.assertTrue(got.startswith("(a)"),
                        f"the annotation's `=` was taken for the assignment: "
                        f"{got!r}")

    def test_and_a_statement_that_declares_nothing_answers_none(self):
        """The control. A reader that answered an index for anything would
        pass all four cases above."""
        self.assertIsNone(self._params("export const f = 3;\n", "f"))

    def test_and_the_arity_it_feeds_is_the_one_a_caller_owes(self):
        """The number the checker actually compares, from the text this
        function locates."""
        got = self._params("export const f = (a, b?, ...rest) => a;", "f")
        inner = got[1:got.index(")")]
        self.assertEqual(sig.ts_required_params(inner), 1)


class TheFileSetComesFromTheLayerThatOwnsIt(unittest.TestCase):
    """`dead_wiring.tracked_files` was a fifth hand-rolled copy of
    `subject_files.tracked`: same `git ls-files`, no `keep()`, no subject, and
    a `root.rglob("*")` fallback -- the `.venv` walk the analysis module was
    written to remove. It feeds the adopter file set `check()` judges."""

    def _tracked(self, root, subject=None):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "v4_dead_wiring_under_test", ROOT / "checkers" / "dead_wiring.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [str(Path(p).relative_to(root)) for p in
                mod.tracked_files(root, subject)]

    def test_a_path_the_repo_excludes_from_derivation_is_not_read(self):
        root = _repo(self, {"src/a.py": "x = 1\n",
                            "tests/fixtures/broken.py": "def (:\n"})
        # `derive_exclude` lives under `params`, which is where the
        # subject a checker is handed carries it -- `exclusions`
        # reads `subject["params"]["derive_exclude"]`.
        got = self._tracked(
            root, {"params": {"derive_exclude": ["tests/fixtures/**"]}})
        self.assertIn("src/a.py", got)
        self.assertNotIn("tests/fixtures/broken.py", got,
                         "a file `derive_exclude` names was read anyway")

    def test_and_a_path_it_does_not_exclude_is_read(self):
        """The control. Returning nothing would pass the case above."""
        root = _repo(self, {"src/a.py": "x = 1\n",
                            "tests/fixtures/broken.py": "def (:\n"})
        got = self._tracked(root, {})
        self.assertIn("tests/fixtures/broken.py", got)

    def test_and_an_unreadable_git_is_not_a_walk_of_the_tree(self):
        """`rglob` on a non-zero exit is the `.venv` walk the analysis layer
        exists to remove -- `secret_chain` walked one and reported a finding
        inside `jwt/jwks_client.py`. An unreadable answer is empty, not
        everything."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self._tracked(tmp), [])


if __name__ == "__main__":
    unittest.main()
