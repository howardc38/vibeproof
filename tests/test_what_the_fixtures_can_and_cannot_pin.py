"""Eleven red cases cannot pin 170 names, and the gate should say so.

    python3 -m unittest tests.test_what_the_fixtures_can_and_cannot_pin -v

`fail-closed`'s rule is a table: 12 tables, 170 names. Its fixture set is 11
red-and-bypass cases and 14 green. Measured here rather than argued:

* removing any single name leaves every fixture green in 168 of the 170 cases;
* ten of the twelve tables can be emptied whole and the gate still passes;
* emptying all twelve at once is what the gate finally catches.

That is arithmetic, not an oversight -- pinning every name would need a fixture
per name. What was missing is that the verdict said `registrable` and left a
reader to assume it covered the rule. This measures the gap and keeps it from
getting quietly worse.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import fail_closed as fc  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "fail_closed"
ROWS = json.loads((ROOT / "kernel" / "analysis" / "fail_closed.json")
                  .read_text())["vocabulary"]


def _cases(colour):
    d = FIXTURES / colour
    return sorted(d.glob("*.py")) if d.is_dir() else []


RED = _cases("red") + _cases("bypass")
GREEN = _cases("green")


def _fires(vocab):
    """(red-and-bypass cases that still produce a finding, green that do)"""
    red = sum(1 for p in RED
              if fc.analyse_source(p.read_text(), path=str(p), vocab=vocab))
    green = sum(1 for p in GREEN
                if fc.analyse_source(p.read_text(), path=str(p), vocab=vocab))
    return red, green


class WhatTheFixturesActuallyPin(unittest.TestCase):
    def test_every_red_case_fires_on_the_shipped_table(self):
        red, green = _fires(fc.SHIPPED)
        self.assertEqual(red, len(RED))
        self.assertEqual(green, 0)

    def test_emptying_every_table_is_caught(self):
        """The floor: a rule with no vocabulary judges nothing, and the gate
        does see that."""
        red, _green = _fires(fc.Vocabulary.of({k: [] for k in ROWS}))
        self.assertEqual(red, 0)

    def test_how_much_of_the_table_the_fixtures_reach(self):
        """The number this file exists to keep honest.

        A name is 'reached' when removing it changes what a fixture reports.
        Two of 170 are, and both are named here so that a change in either
        direction is a change to this test rather than a silent drift.
        """
        base_red, _ = _fires(fc.SHIPPED)
        reached = []
        for table, names in ROWS.items():
            for name in names:
                thin = {k: [x for x in v if not (k == table and x == name)]
                        for k, v in ROWS.items()}
                red, _ = _fires(fc.Vocabulary.of(thin))
                if red < base_red:
                    reached.append(f"{table}:{name}")
        total = sum(len(v) for v in ROWS.values())
        self.assertEqual(total, 170, "the table changed size; re-measure")
        self.assertEqual(sorted(reached),
                         ["auth_words:secret", "outbound_tails_strong:post"],
                         f"{len(reached)} of {total} names are pinned by a "
                         f"fixture; this test records which, and a change here "
                         f"means the coverage moved")

    def test_and_which_whole_tables_can_go_unnoticed(self):
        base_red, _ = _fires(fc.SHIPPED)
        unnoticed = []
        for table in ROWS:
            thin = {k: ([] if k == table else v) for k, v in ROWS.items()}
            red, _ = _fires(fc.Vocabulary.of(thin))
            if red == base_red:
                unnoticed.append(table)
        self.assertEqual(len(unnoticed), 10,
                         f"10 of 12 tables can be emptied with every fixture "
                         f"still green; this run says {len(unnoticed)}: "
                         f"{unnoticed}")


class WideningIsTheSafeDirectionAndCostsNoise(unittest.TestCase):
    """More names means more to answer for, so nothing is lost -- but the cost
    lands as noise, and noise has no gate. SPEC.md §10 names that failure: a
    gate that fires on a config default is one people learn to ignore."""

    def test_a_plausible_addition_disturbs_nothing(self):
        _red, green = _fires(fc.SHIPPED.union({"outbound_roots": ["mycompany_sdk"]}))
        self.assertEqual(green, 0)

    def test_a_reckless_one_does(self):
        wide = fc.SHIPPED.union({"outbound_tails_strong": [
            "get", "set", "add", "run", "call", "log", "make", "load", "read"]})
        _red, green = _fires(wide)
        self.assertGreater(green, 0, "green fixtures are the only thing that "
                                     "notices a table widened past sense")


class AnAdopterCanWidenAndCannotNarrow(unittest.TestCase):
    """The direction that matters for a repo that is not this one.

    `union` adds and never removes, so the table an adopter is judged by is
    always at least the shipped one. Narrowing takes a change to the kernel,
    which is a code review and -- since the table joined `program_sha` -- an
    expiry of every answer it gave.
    """

    def test_declaring_an_empty_table_removes_nothing(self):
        thinned = fc.SHIPPED.union({"outbound_roots": [], "auth_words": []})
        self.assertEqual(thinned.outbound_roots, fc.SHIPPED.outbound_roots)
        self.assertEqual(thinned.auth_words, fc.SHIPPED.auth_words)

    def test_and_naming_one_of_its_own_adds_it(self):
        wide = fc.SHIPPED.union({"auth_words": ["entitlement"]})
        self.assertIn("entitlement", wide.auth_words)
        self.assertTrue(set(fc.SHIPPED.auth_words) <= set(wide.auth_words))


if __name__ == "__main__":
    unittest.main(verbosity=2)
