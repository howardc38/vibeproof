"""What `v4 review add` accepts, and what a kind's held-back reason says.

    python3 -m unittest tests.test_coordinates_a_finding_can_be_closed_under -v

A finding closes by executing its symbol, so the moment to refuse coordinates
no test can reach is when they are written down -- not four hours later, when
the only exit left is a signature. This repo paid that: three findings name a
symbol in a JSON table, and one of them is the claim this test was written for.

Both fail against 0ad6b61.
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

from kernel import install, review  # noqa: E402


def _repo(case, files):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp


class ASymbolNoFrameCanBeNamedAfter(unittest.TestCase):
    """The refusal only applied to Python.

    Any string at all was accepted for a document, a JSON table or a
    non-Python source, and the claim that came back could never be closed by
    red-green -- `redgreen` compares `code.co_name`, and nothing in a JSON
    file is ever a frame. Three are in this ledger, two of them naming
    functions that live in `.py` files somewhere else entirely.
    """

    def test_a_json_table_takes_no_symbol(self):
        root = _repo(self, {".v4/claim_kinds.json": json.dumps({"scope": {}})})
        with self.assertRaises(review.BadCoordinates) as caught:
            review.resolve_symbol(root, ".v4/claim_kinds.json", "<module>")
        self.assertIn("text closure", str(caught.exception))

    def test_nor_does_a_document(self):
        root = _repo(self, {"docs/SPEC.md": "# a document\n"})
        with self.assertRaises(review.BadCoordinates):
            review.resolve_symbol(root, "docs/SPEC.md", "raise_finding")

    def test_but_a_finding_about_one_is_still_legitimate(self):
        """It closes by text closure, which needs no frame -- so the symbol is
        refused and the finding is not."""
        root = _repo(self, {"docs/SPEC.md": "# a document\n"})
        self.assertEqual(review.resolve_symbol(root, "docs/SPEC.md", ""), "")

    def test_and_python_still_resolves_the_way_it_did(self):
        root = _repo(self, {"pkg/thing.py": "class C:\n    def go(self):\n"
                                            "        return 1\n"})
        self.assertEqual(review.resolve_symbol(root, "pkg/thing.py", "C.go"),
                         "go")


class WhatAHeldBackKindSaysItCosts(unittest.TestCase):
    """`applies_to_why` was read only where `applies_to == framework`.

    The `needs`-declared branch built its own sentence and ignored the field,
    so the two kinds that reach it stated a reason nothing would ever print.
    """

    def _kinds(self, spec):
        src = _repo(self, {
            ".v4/claim_kinds.json": json.dumps({"probe": spec}),
            ".v4/detectors.json": "{}",
            ".v4/checkers.json": json.dumps(
                {"probe": {"path": "checkers/probe.py", "kinds": ["probe"],
                           "reads": ["**/*.py"], "sha256": "x"}}),
            "checkers/probe.py": "def main():\n    return 0\n",
        })
        (src / "detectors").mkdir(exist_ok=True)
        dst = _repo(self, {".v4/config.json": json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}),
            "app.py": "x = 1\n"})
        subprocess.run(["git", "init", "-q"], cwd=dst, capture_output=True)
        _keep, held = install.installable_kinds(src, dst)
        return dict(held)

    def test_the_reason_the_kind_gives_reaches_the_adopter(self):
        held = self._kinds({"checker": "probe", "staleness": "repo",
                            "applies_to": install.NEEDS_DECLARED,
                            "needs": ".v4/layers.json",
                            "applies_to_why": "a repo with no browser suite "
                                              "has nothing for this to run"})
        self.assertIn("probe", held)
        self.assertIn("no browser suite", held["probe"])

    def test_and_the_generic_half_is_still_there(self):
        held = self._kinds({"checker": "probe", "staleness": "repo",
                            "applies_to": install.NEEDS_DECLARED,
                            "needs": ".v4/layers.json",
                            "applies_to_why": "a repo with no browser suite "
                                              "has nothing for this to run"})
        self.assertIn("v4 install", held["probe"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
