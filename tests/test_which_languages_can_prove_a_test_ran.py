"""`_run_traced` answers "did this symbol run" for three languages, not one.

    python3 -m unittest tests.test_which_languages_can_prove_a_test_ran -v

The mechanism was `sys.settrace` through a `sitecustomize` on `PYTHONPATH`, so
it answered for Python and left `None` -- nothing observed -- for everything
else. `None` is the honest answer for a command that cannot be instrumented,
and it stays honest here: what changed is that two more commands *can* be
instrumented, so `None` now means what it says rather than "not Python".

Both additions are the language's own toolchain and neither rewrites the
repo's test command:

    Go     `GOFLAGS=-coverprofile=…`, read back with `go tool cover -func`
    Node   `NODE_V8_COVERAGE=…`, which V8 writes per function with a hit count

Measured before any of this was written, with the same A/B the Python demo
uses -- a test that asserts on the function's source text, and a test that
calls it. Both pass, both exit 0, and the toolchain separates them:

    Go     A: Target 0.0%              B: Target 100.0%
    Node   A: target 執行次數 = 0       B: target 執行次數 = 1

The Rust half is deliberately absent: `-C instrument-coverage` needs the
`llvm-tools` component, which is not the toolchain being present, and a
mechanism that works on some installs is the shape this framework refuses.
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

from kernel import redgreen  # noqa: E402

HAVE_GO = shutil.which("go") is not None
HAVE_NODE = shutil.which("node") is not None
GO_ABSENT = "go is not on PATH; this asks the Go toolchain a question"
NODE_ABSENT = "node is not on PATH; this asks V8 a question"


class WhatThePythonTracerAnswers(unittest.TestCase):
    """The path that already existed, kept here so the other two have a floor."""

    MOD = "def target(n):\n    return n * 2\n"
    READS_SOURCE = ("import inspect, unittest\nimport mod\n\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_a(self):\n"
                    "        self.assertIn('n * 2', inspect.getsource(mod.target))\n")
    CALLS_IT = ("import unittest\nimport mod\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_b(self):\n"
                "        self.assertEqual(mod.target(21), 42)\n")

    def _run(self, test_src):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "mod.py").write_text(self.MOD)
        (work / "test_it.py").write_text(test_src)
        return redgreen._run_traced(
            work, [sys.executable, "-m", "unittest", "test_it"],
            "mod.py", "target")

    def test_a_test_that_reads_the_source_never_entered_it(self):
        code, executed, calls, _out = self._run(self.READS_SOURCE)
        self.assertEqual(code, 0, "the test itself has to pass")
        self.assertIs(executed, False)
        self.assertEqual(calls, 0)

    def test_and_one_that_calls_it_did(self):
        code, executed, calls, _out = self._run(self.CALLS_IT)
        self.assertEqual(code, 0)
        self.assertIs(executed, True)
        self.assertGreaterEqual(calls, 1)


@unittest.skipUnless(HAVE_GO, GO_ABSENT)
class WhatTheGoToolchainAnswers(unittest.TestCase):
    """`go test -coverprofile` reports `0.0%` for a function nothing entered.

    Before this, a Go repo got `None` here -- nothing observed -- and a review
    finding could not be closed by a test at all, because "unknown" is not
    proof. The flag goes through `GOFLAGS` rather than onto the command,
    because the command belongs to the repo.
    """

    MOD = "module ex\n\ngo 1.21\n"
    SRC = "package ex\n\nfunc Target(n int) int {\n\treturn n * 2\n}\n"
    READS_SOURCE = (
        'package ex\n\nimport (\n\t"os"\n\t"strings"\n\t"testing"\n)\n\n'
        'func TestA(t *testing.T) {\n'
        '\tb, err := os.ReadFile("target.go")\n'
        '\tif err != nil {\n\t\tt.Fatal(err)\n\t}\n'
        '\tif !strings.Contains(string(b), "n * 2") {\n'
        '\t\tt.Fatal("source changed")\n\t}\n}\n')
    CALLS_IT = ('package ex\n\nimport "testing"\n\n'
                'func TestB(t *testing.T) {\n'
                '\tif Target(21) != 42 {\n\t\tt.Fatal("wrong")\n\t}\n}\n')

    def _run(self, test_src):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "go.mod").write_text(self.MOD)
        (work / "target.go").write_text(self.SRC)
        (work / "x_test.go").write_text(test_src)
        return redgreen._run_traced(work, ["go", "test", "./..."],
                                    "target.go", "Target")

    def test_a_go_test_that_reads_the_source_never_entered_it(self):
        code, executed, _calls, out = self._run(self.READS_SOURCE)
        self.assertEqual(code, 0, out[-400:])
        self.assertIs(executed, False,
                      "0.0% coverage on a named function is a real False")

    def test_and_one_that_calls_it_did(self):
        code, executed, calls, out = self._run(self.CALLS_IT)
        self.assertEqual(code, 0, out[-400:])
        self.assertIs(executed, True)
        self.assertGreaterEqual(calls, 1)


    def test_a_repo_that_asks_for_its_own_profile_keeps_it(self):
        """`GOFLAGS` is appended to, never replaced.

        A repo that already writes a coverage profile is a repo with a reason,
        and taking the flag off it to answer this question would be the
        framework deciding what its test run is. It answers `None` instead,
        which is what "nothing observed" is for.
        """
        import os
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "go.mod").write_text(self.MOD)
        (work / "target.go").write_text(self.SRC)
        (work / "x_test.go").write_text(self.CALLS_IT)
        theirs = work / "theirs.out"
        old = os.environ.get("GOFLAGS")
        os.environ["GOFLAGS"] = f"-coverprofile={theirs}"
        try:
            _code, executed, _calls, _out = redgreen._run_traced(
                work, ["go", "test", "./..."], "target.go", "Target")
        finally:
            if old is None:
                os.environ.pop("GOFLAGS", None)
            else:
                os.environ["GOFLAGS"] = old
        self.assertTrue(theirs.is_file(), "the repo's own profile was not written")
        self.assertIsNone(executed, "it should not claim an answer it did not get")


@unittest.skipUnless(HAVE_NODE, NODE_ABSENT)
class WhatV8Answers(unittest.TestCase):
    """`NODE_V8_COVERAGE` carries a per-function hit count, so `calls` is real."""

    SRC = "export function target(n) { return n * 2; }\n"
    READS_SOURCE = (
        "import { readFileSync } from 'node:fs';\n"
        "import './t.mjs';\n"
        "const src = readFileSync(new URL('./t.mjs', import.meta.url), 'utf8');\n"
        "if (!src.includes('n * 2')) { process.exit(1); }\n")
    CALLS_IT = ("import { target } from './t.mjs';\n"
                "if (target(21) !== 42) { process.exit(1); }\n")

    def _run(self, runner_src):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "t.mjs").write_text(self.SRC)
        (work / "run.mjs").write_text(runner_src)
        return redgreen._run_traced(work, ["node", "run.mjs"], "t.mjs", "target")

    def test_a_runner_that_reads_the_source_never_entered_it(self):
        code, executed, calls, out = self._run(self.READS_SOURCE)
        self.assertEqual(code, 0, out[-400:])
        self.assertIs(executed, False)
        self.assertEqual(calls, 0)

    def test_and_one_that_calls_it_did(self):
        code, executed, calls, out = self._run(self.CALLS_IT)
        self.assertEqual(code, 0, out[-400:])
        self.assertIs(executed, True)
        self.assertGreaterEqual(calls, 1)

    def test_a_completed_python_child_does_not_mask_js_execution(self):
        source = ("import {execFileSync} from 'node:child_process';\n"
                  f"execFileSync({json.dumps(sys.executable)}, ['-c', 'pass']);\n")
        code, executed, calls, out = self._run(source + self.CALLS_IT)
        self.assertEqual(code, 0, out[-400:])
        self.assertIs(executed, True)
        self.assertGreaterEqual(calls, 1)

    def test_an_import_only_process_does_not_hide_a_child_that_calls_it(self):
        source = ("import './t.mjs';\n"
                  "import {spawnSync} from 'node:child_process';\n"
                  "const child = spawnSync(process.execPath, ['--input-type=module', '-e', "
                  + json.dumps(self.CALLS_IT) + "], {encoding:'utf8'});\n"
                  "if (child.status !== 0) throw new Error(child.stderr);\n")
        code, executed, calls, out = self._run(source)
        self.assertEqual(code, 0, out[-400:])
        self.assertIs(executed, True)
        self.assertGreaterEqual(calls, 1)


class NothingObservedIsStillNothingObserved(unittest.TestCase):
    """The answer that must not become an accusation.

    A command none of the three can instrument leaves no Python trace file, no
    Go profile and no V8 output -- and the answer is `None`. Reading that as
    `False` would tell a worker their test never ran the code, when what
    happened is that nobody watched.
    """

    def test_a_command_none_of_them_instrument_answers_none(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        (work / "thing.rb").write_text("puts 'hi'\n")
        code, executed, calls, _out = redgreen._run_traced(
            work, ["/bin/echo", "not a test runner"], "thing.rb", "target")
        self.assertEqual(code, 0)
        self.assertIsNone(executed)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
