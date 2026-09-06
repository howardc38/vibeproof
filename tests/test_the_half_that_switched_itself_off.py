"""`checkers/test.py::main` -- three statements it made that were not true.

    python3 -m unittest tests.test_the_half_that_switched_itself_off -v

This claim has two halves. One asks whether the declared suite passes; the
other asks whether that suite executed any of the files this change touched.
The second half depended on a `git diff` that was allowed to fail in silence,
and on a timeout the file had already written a comment renouncing.

Every case here enters `main()` in this process. A subprocess would answer the
same questions and prove nothing about the symbol the findings name.

All of them fail against e46bd85.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _checker():
    """The checker as a module, so `main` runs where the tracer can see it."""
    spec = importlib.util.spec_from_file_location(
        "v4_test_checker_under_test", ROOT / "checkers" / "test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                           *args], cwd=root, capture_output=True, text=True)


def _repo(case, command="true", **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    _git(tmp, "init", "-q")
    (tmp / ".v4").mkdir()
    body = {"test_command": command, "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / "mod.py").write_text("def helper():\n    return 1\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "base")
    return tmp


def _run(case, root, diff_base):
    """`main()`, entered here, with everything it wrote to either stream."""
    subj = root / "subject.json"
    payload = {"repo_root": str(root),
               "subject_refs": [{"kind": "file", "path": "mod.py"}]}
    if diff_base is not None:
        payload["diff_base"] = diff_base
    subj.write_text(json.dumps(payload))
    mod = _checker()
    out, err = io.StringIO(), io.StringIO()
    argv = sys.argv[:]
    sys.argv = ["test.py", "--subject", str(subj)]
    try:
        with redirect_stdout(out), redirect_stderr(err):
            with case.assertRaises(SystemExit) as caught:
                mod.main()
    finally:
        sys.argv = argv
    code = caught.exception.code
    return (0 if code is None else code), out.getvalue(), err.getvalue()


class AnUnreadableDiffSaysSo(unittest.TestCase):
    """`git diff` against the base was run, and its result used only inside
    `if d.returncode == 0`. On a non-zero exit -- an unborn base, a ref from a
    branch this checkout does not have, a repository git declines to read --
    the changed list stayed empty, the tracer was never attached, and the claim
    was decided on the suite's exit code alone. Nothing said the second half
    had not been asked."""

    def test_a_base_that_does_not_resolve_is_reported(self):
        root = _repo(self)
        _code, _out, err = _run(self, root, "no-such-ref-4a1c9")
        self.assertIn("unanswered", err,
                      "the binding half was skipped without a word")
        self.assertIn("no-such-ref-4a1c9", err,
                      "the reader is not told which base failed to read")

    def test_and_it_carries_what_git_said(self):
        """Naming the base but not the reason leaves the reader running the
        command by hand to learn what this process already knew."""
        root = _repo(self)
        _code, _out, err = _run(self, root, "no-such-ref-4a1c9")
        self.assertIn("fatal", err.lower(),
                      "git's own message was captured and then dropped")

    def test_a_base_that_reads_is_silent(self):
        """The control. A message on every run is the same as none."""
        root = _repo(self)
        (root / "mod.py").write_text("def helper():\n    return 2\n")
        _code, _out, err = _run(self, root, "HEAD")
        self.assertNotIn("unanswered", err, err[:400])

    def test_and_a_readable_diff_still_reaches_the_trace(self):
        """The other control: reporting the failure must not cost the working
        path. With a base that reads and a changed Python file, the traced run
        happens -- `mod.py` is executed by the suite below, so the claim passes
        rather than reporting a file the suite never touched."""
        cmd = f"{sys.executable} -m unittest -q t_x"
        root = _repo(self, command=cmd)
        (root / "t_x.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from mod import helper\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertEqual(helper(), 1)\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "suite")
        (root / "mod.py").write_text("def helper():\n    return 1  # touched\n")
        code, out, err = _run(self, root, "HEAD")
        self.assertEqual(code, 0, (out + err)[:600])
        self.assertNotIn("never executed", out + err, (out + err)[:600])


class TheSuiteHasOneWall(unittest.TestCase):
    """The comment above `test_command` says the second timeout was removed
    and that there is "One owner: the registry" -- and then the traced run was
    given `test_timeout_sec or 1800` anyway. The registry's number is enforced
    by `runner`; this one was a second, invisible wall, and whichever was
    smaller won."""

    def _repo_with_a_slow_suite(self, **cfg):
        cmd = f"{sys.executable} -m unittest -q t_x"
        root = _repo(self, command=cmd, **cfg)
        (root / "t_x.py").write_text(
            "import sys, time, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from mod import helper\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_it(self):\n"
            "        time.sleep(3)\n"
            "        self.assertEqual(helper(), 1)\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "suite")
        (root / "mod.py").write_text("def helper():\n    return 1  # touched\n")
        return root

    def test_a_declared_second_wall_does_not_cut_the_trace(self):
        """`test_timeout_sec: 1` against a suite that takes three seconds. The
        checker used to hand that number to the traced run, which killed it and
        fell back to an untraced one -- so a key nothing declares, and which the
        registry already overrides, could switch off the binding half."""
        root = self._repo_with_a_slow_suite(test_timeout_sec=1)
        code, out, err = _run(self, root, "HEAD")
        self.assertNotIn("the execution trace did not run", out + err,
                         "a second wall cut the run the registry owns")
        self.assertEqual(code, 0, (out + err)[:600])

    def test_the_suite_itself_still_decides(self):
        """The control: ignoring the timeout must not also ignore the verdict.
        A failing suite is still a failing claim."""
        root = self._repo_with_a_slow_suite(test_timeout_sec=1)
        (root / "t_x.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.fail('no')\n")
        code, _out, _err = _run(self, root, "HEAD")
        self.assertEqual(code, 1)


class MainNeverReturns(unittest.TestCase):
    """It was annotated `-> int`, its last line was an unreachable `return 0`,
    and `__main__` called `sys.exit(main())`. Three statements about one
    function, two of them describing a value it has never produced."""

    def test_every_ending_is_an_exit(self):
        """Four endings, entered one at a time: no command declared, a
        passing suite, a failing suite, and an unreadable config. Each has to
        leave through `SystemExit` carrying the verdict, because the caller no
        longer wraps a returned code."""
        # A suite, not `true`. `true` exits 0 having said nothing, and this
        # checker refuses a silent command as proof of a passing suite -- so it
        # answers 1, correctly, and would have been the wrong input for a case
        # about a passing one. Predicted 0 here and measured 1 before reading
        # why; the expectation stayed and the input changed.
        passing = _repo(self, command=f"{sys.executable} -m unittest -q t_x")
        (passing / "t_x.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertTrue(True)\n")
        seen = {}
        for name, root, base in (
            ("unsupported", _repo(self, command=""), None),
            ("pass", passing, "HEAD"),
            ("fail", _repo(self, command="false"), "HEAD"),
        ):
            with self.subTest(name):
                seen[name], _out, _err = _run(self, root, base)
        self.assertEqual(seen["unsupported"], 4)
        self.assertEqual(seen["pass"], 0)
        self.assertEqual(seen["fail"], 1)

    def test_a_broken_config_exits_five_not_a_verdict(self):
        """The fourth ending. `5` is the kernel's word for "the checker could
        not run", and it must not arrive as `0` or `1`, which are verdicts."""
        root = _repo(self)
        (root / ".v4" / "config.json").write_text("{not json")
        code, _out, err = _run(self, root, None)
        self.assertEqual(code, 5, err[:300])


if __name__ == "__main__":
    unittest.main()
