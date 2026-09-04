"""Which languages a closing test may be written in, asked of the one thing
that knows.

    python3 -m unittest tests.test_a_language_the_tracer_can_read -v

`kernel/redgreen.py` grew a Go tracer and a Node tracer, and the two places that
decide whether a symbol can be traced were not told. Both spelled it
`suffix != ".py"`, both explained themselves by describing a `sitecustomize` on
PYTHONPATH, and both refused -- before `verify` was ever called -- cases the
module behind them answers. Measured on a real Vitest suite before writing any
of this: `tier_of` in a `.ts` module, `executed=True`, `calls=37`.

The third class is the one that makes the other two safe. A tracer that does not
attach returns `None`, `redgreen` argues at length that "Unknown is not proof.
It is just not an allegation" -- and then the only reader of that result printed
"has not earned the right to close this finding" for it. That is exit 1 telling
a worker their repair is unproven when nothing looked at it, and it is what a
`.ts` test under Vitest's default pool would have got the moment the suffix gate
opened.
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

from kernel import redgreen                                     # noqa: E402
from kernel import review                                       # noqa: E402


class TheOneOwnerOfWhichLanguagesCanBeTraced(unittest.TestCase):
    def test_the_three_toolchains_this_module_carries(self):
        for suffix in (".py", ".go", ".ts", ".tsx", ".js", ".mjs"):
            self.assertTrue(redgreen.traceable(f"a{suffix}"), suffix)

    def test_and_a_file_no_tracer_can_enter(self):
        """The refusal has to survive, or the gate is an off switch. A finding
        about a document or a JSON table closes by text closure instead."""
        for suffix in (".md", ".json", ".yml", ".toml", ""):
            self.assertFalse(redgreen.traceable(f"a{suffix}"), suffix)

    def test_the_closing_gate_asks_rather_than_spelling_it(self):
        """`checkers/review_finding.py` carried the fact as a literal. A copy of
        a fact is the copy that goes stale, and this one did."""
        src = (ROOT / "checkers" / "review_finding.py").read_text(encoding="utf-8")
        self.assertIn("redgreen.traceable(", src)
        body = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("#"))
        self.assertNotIn('endswith(".py")', body)

    def test_and_so_does_the_command_that_refuses_a_symbol(self):
        """The second copy. `resolve_symbol` still reads `.py` one line further
        down -- for whether to run a Python AST over it, which is a different
        question and stays."""
        src = (ROOT / "kernel" / "review.py").read_text(encoding="utf-8")
        self.assertIn("redgreen.traceable(", src)


class ASymbolInAFileTheTracerCanEnter(unittest.TestCase):
    """`resolve_symbol` refused a `--symbol` on anything but Python, on the
    grounds that "nothing in it can be entered by a test"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name, text=""):
        (self.tmp / name).write_text(text, encoding="utf-8")
        return name

    def test_a_go_function_can_be_named(self):
        f = self._write("app.go", "package main\n\nfunc Thing() int { return 1 }\n")
        self.assertEqual(review.resolve_symbol(self.tmp, f, "Thing"), "Thing")

    def test_a_typescript_function_can_be_named(self):
        f = self._write("app.ts", "export function tier_of(a: string) { return a }\n")
        self.assertEqual(review.resolve_symbol(self.tmp, f, "tier_of"), "tier_of")

    def test_a_document_still_cannot(self):
        f = self._write("NOTES.md", "# a heading\n")
        with self.assertRaises(review.BadCoordinates):
            review.resolve_symbol(self.tmp, f, "whatever")

    def test_a_json_table_still_cannot(self):
        """The measured case: `.v4/facts.vibeproof.json::writing` is in a real
        ledger, naming a function that lives in a `.py` file elsewhere."""
        f = self._write("table.json", "{}\n")
        with self.assertRaises(review.BadCoordinates):
            review.resolve_symbol(self.tmp, f, "writing")

    def test_python_normalisation_is_untouched(self):
        f = self._write("app.py", "class C:\n    def method(self):\n        return 1\n")
        self.assertEqual(review.resolve_symbol(self.tmp, f, "C.method"), "method")

    def test_and_a_module_level_constant_is_still_refused(self):
        f = self._write("app.py", "WAIT_REASONS = frozenset({'a'})\n")
        with self.assertRaises(review.BadCoordinates):
            review.resolve_symbol(self.tmp, f, "WAIT_REASONS")


def _repo_where_the_tracer_cannot_attach(case, *, passes_at_head=True):
    """A repo whose closing test is real, and whose runner is a shell script.

    Red-green holds -- the script greps for a marker that only HEAD carries --
    and no tracer attaches to `sh`, so `symbol_executed` is `None`. That is the
    exact shape a Vitest run under the default pool produces, without needing
    node_modules in a test.
    """
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

    def run(*a):
        return subprocess.run(a, cwd=tmp, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tmp / "app.js").write_text("function thing() { return 0 }\n")
    (tmp / "t_it.js").write_text("// the closing test\n")
    (tmp / "runner.sh").write_text(
        "#!/bin/sh\ngrep -q REPAIRED app.js\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "before")
    parent = run("git", "rev-parse", "HEAD").stdout.strip()

    marker = "REPAIRED" if passes_at_head else "NOT-YET"
    (tmp / "app.js").write_text(f"function thing() {{ return 1 /* {marker} */ }}\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "after")
    return tmp, parent


def _verdict(case, root, parent):
    """Run the checker the way the kernel does, and return its exit code."""
    subject = root / "subject.json"
    subject.write_text(json.dumps({
        "repo_root": str(root),
        "file": "app.js",
        "symbol": "thing",
        "params": {
            "closing_test": "t_it.js",
            "parent_commit": parent,
            "test_one_file_command": "sh runner.sh",
        },
    }))
    r = subprocess.run(
        [sys.executable, str(ROOT / "checkers" / "review_finding.py"),
         "--subject", str(subject)],
        capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


class NothingObservedIsNotAnAllegation(unittest.TestCase):
    def test_red_green_holds_and_the_tracer_saw_nothing(self):
        """Exit 4. The claim still does not close and still needs a signature --
        what changes is that the sentence is true."""
        root, parent = _repo_where_the_tracer_cannot_attach(self)
        code, out = _verdict(self, root, parent)
        self.assertEqual(code, 4, out)
        self.assertIn("UNSUPPORTED", out)
        self.assertIn("nothing observed", out.lower())

    def test_but_a_closing_test_that_does_not_pass_is_still_a_finding(self):
        """The control, and the reason the branch reads three fields rather than
        one. `symbol_executed is None` alone would turn "your closing test is
        broken" into "unsupported", which is the same mistake pointing the other
        way."""
        root, parent = _repo_where_the_tracer_cannot_attach(
            self, passes_at_head=False)
        code, out = _verdict(self, root, parent)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)

    def test_a_suffix_no_tracer_covers_never_reaches_the_runner(self):
        """Still exit 4, and now the sentence names what this module does
        instrument instead of claiming a limitation it does not have."""
        root, parent = _repo_where_the_tracer_cannot_attach(self)
        (root / "NOTES.md").write_text("not a test\n")
        subject = root / "subject.json"
        subject.write_text(json.dumps({
            "repo_root": str(root),
            "file": "app.js",
            "symbol": "thing",
            "params": {
                "closing_test": "NOTES.md",
                "parent_commit": parent,
                "test_one_file_command": "sh runner.sh",
            },
        }))
        r = subprocess.run(
            [sys.executable, str(ROOT / "checkers" / "review_finding.py"),
             "--subject", str(subject)],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("no tracer here can observe", r.stdout + r.stderr)
        self.assertNotIn("sitecustomize", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
