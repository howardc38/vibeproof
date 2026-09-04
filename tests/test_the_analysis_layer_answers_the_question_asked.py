"""Targeted tests for the R11 repairs, one symbol each.

    python3 -m unittest tests.test_the_analysis_layer_answers_the_question_asked -v

Each repair in that group was proven by a checker fixture, and a fixture runs
`v4 verify` in a subprocess -- which is the right proof for the checker and no
proof at all for the symbol: nothing in this process ever calls it. These call
the analysis function directly and assert what it returned, so the claim each
one closes has a test that enters the symbol it names.

Every one of them fails against the code at 0ad6b61.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import pysource, signature_change           # noqa: E402


def _fn(src: str):
    return [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)][0]


class ControlReachesAStatementOrItIsText(unittest.TestCase):
    """`pysource.reachable_nodes` -- dead code inside a live branch."""

    DEAD_IN_A_LIVE_IF = (
        "def f(body, store, nonce):\n"
        "    if body:\n"
        "        return process(body)\n"
        "        if store.seen_before(nonce):\n"
        "            return 'dup'\n"
        "    return None\n")

    def test_a_statement_after_an_unconditional_return_is_not_reachable(self):
        names = [n.attr for n in pysource.reachable_nodes(_fn(self.DEAD_IN_A_LIVE_IF))
                 if isinstance(n, ast.Attribute)]
        self.assertNotIn("seen_before", names)

    def test_the_live_half_of_the_same_branch_is(self):
        """The control: excluding the whole `if` would pass the test above."""
        names = [n.id for n in pysource.reachable_nodes(_fn(self.DEAD_IN_A_LIVE_IF))
                 if isinstance(n, ast.Name)]
        self.assertIn("process", names)


class ARelativeImportNamesAModule(unittest.TestCase):
    """`pysource.imported_modules` -- `from ..x import y` was dropped."""

    SRC = "from ..loader import load\nfrom . import sibling\nimport os\n"

    def test_a_caller_that_knows_its_package_gets_the_names(self):
        got = pysource.imported_modules(self.SRC, "pkg.analysis")
        self.assertIn("pkg.loader", got)
        self.assertIn("pkg.analysis.sibling", got)

    def test_a_caller_that_does_not_know_still_gets_the_absolute_ones(self):
        """Skipping is right when the package is unknown -- resolving would be
        a guess, and the answer feeds a rule about which layer a name is in."""
        got = pysource.imported_modules(self.SRC)
        self.assertEqual(got, {"os"})


class AKeywordOnlyParameterIsRequired(unittest.TestCase):
    """`signature_change.required_params` -- counted positional only."""

    def test_it_counts(self):
        self.assertEqual(
            signature_change.required_params(_fn("def f(a, *, token):\n    pass\n")), 2)

    def test_one_with_a_default_is_not_required(self):
        self.assertEqual(
            signature_change.required_params(_fn("def f(a, *, token=None):\n    pass\n")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
