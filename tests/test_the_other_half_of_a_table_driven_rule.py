"""A repo can widen the rule it is judged by; the answers have to expire.

    python3 -m unittest tests.test_the_other_half_of_a_table_driven_rule -v

`kernel/analysis/fail_closed.py` reads two tables: the one it ships beside
itself, and the one the repo being judged writes under `.v4/`. Only the first
was in `program_sha`. So a repo could add a transport to its own table --
`union` only ever adds, and a handler that passed because its call was not
recognised as a transport fails once it is -- and every claim already answered
under the narrower table stayed answered. `fail-closed` is subject-scoped, so
the working-tree digest does not cover it either.

Measured before the fix: writing `.v4/fail_closed_vocabulary.json` moved
`program_sha` not at all.

The fix is one name on both sides (`analysis.tables.shipped` /
`analysis.tables.own`), so the module and the hash find the same two files by the
same rule rather than by a list.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import hashing, layout                      # noqa: E402
from kernel.analysis import fail_closed, secret_patterns, tables  # noqa: E402

TABLE_DRIVEN = sorted(p.stem for p in (ROOT / "kernel" / "analysis").glob("*.json")
                      if p.with_suffix(".py").is_file())


def _repo(d: Path):
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    (d / ".v4").mkdir(parents=True, exist_ok=True)


class OneNameReadFromBothSides(unittest.TestCase):
    def test_the_modules_that_ship_a_table(self):
        """Not a hand-written list: a `.json` beside a module is the marker."""
        self.assertEqual(TABLE_DRIVEN, ["fail_closed", "secret_patterns"])

    def test_the_two_spellings_of_the_directory_agree(self):
        """`analysis` may not import `kernel`, so `.v4` is written twice. The
        rule that forbids the import is why; this is what keeps the two from
        drifting apart in silence."""
        self.assertEqual(tables.DIR, layout.DIR)

    def test_shipped_is_beside_the_module_and_own_is_under_dot_v4(self):
        for stem in TABLE_DRIVEN:
            mod = ROOT / "kernel" / "analysis" / f"{stem}.py"
            self.assertEqual(tables.shipped(mod).name, f"{stem}.json")
            self.assertEqual(tables.own("/repo", mod),
                             Path("/repo/.v4") / f"{stem}.json")

    def test_and_each_module_really_reads_the_path_that_names_it(self):
        """Measured through the union, not asserted from the source text."""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / ".v4").mkdir()
            tables.own(d, fail_closed.__file__).write_text(
                json.dumps({"vocabulary": {"outbound_roots": ["mycompany_sdk"]}}))
            tables.own(d, secret_patterns.__file__).write_text(
                json.dumps({"patterns": [{"id": "corp-token",
                                          "label": "Corp API token",
                                          "kind": "provider",
                                          "regex": "CORP-[0-9a-f]{8}",
                                          "order": 90}]}))
            self.assertIn("mycompany_sdk",
                          fail_closed.vocabulary_for(d).outbound_roots)
            self.assertIn("corp-token",
                          [p.id for p in secret_patterns.table_for(d)])

    def test_a_module_with_no_shipped_table_claims_nothing(self):
        """`kernel/config.py` must not claim `.v4/config.json` -- that is a
        different fact, with a `config` field of its own in the key. The rule is
        structural: no table beside the module, no table under `.v4/`."""
        self.assertFalse(tables.shipped(ROOT / "kernel" / "config.py").is_file())
        self.assertTrue((ROOT / ".v4" / "config.json").is_file())
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _repo(d)
            (d / ".v4" / "config.json").write_text("{}")
            before = hashing.program_sha(d, ROOT / "checkers" / "fail_closed.py")
            (d / ".v4" / "config.json").write_text('{"test_command": "x"}')
            self.assertEqual(before,
                             hashing.program_sha(d, ROOT / "checkers" / "fail_closed.py"))


class WritingTheTableExpiresWhatTheOldOneAnswered(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        _repo(self.d)
        (self.d / ".v4" / "home").write_text(str(ROOT))
        self.addCleanup(self.tmp.cleanup)

    def sha(self, checker="fail_closed"):
        return hashing.program_sha(self.d, ROOT / "checkers" / f"{checker}.py")

    def test_creating_editing_and_removing_it_all_move_the_hash(self):
        own = tables.own(self.d, fail_closed.__file__)
        absent = self.sha()
        own.write_text(json.dumps({"vocabulary": {"outbound_roots": ["sdk_a"]}}))
        created = self.sha()
        own.write_text(json.dumps({"vocabulary": {"outbound_roots": ["sdk_b"]}}))
        edited = self.sha()
        own.unlink()
        self.assertNotEqual(absent, created, "widening the rule left every answer "
                                             "given under the narrow one standing")
        self.assertNotEqual(created, edited)
        self.assertEqual(absent, self.sha(), "removing it must come back to where "
                                             "it started, or the key drifts")

    def test_and_it_expires_that_checker_only(self):
        others = {c: self.sha(c) for c in ("scope", "secret_scan")}
        tables.own(self.d, fail_closed.__file__).write_text(
            json.dumps({"vocabulary": {"outbound_roots": ["sdk_a"]}}))
        self.assertEqual(others, {c: self.sha(c) for c in others},
                         "a table edit that expires unrelated claims is noise, "
                         "and noise is what a gate gets ignored for")

    def test_the_framework_half_counts_from_an_adopter_too(self):
        """`kernel/` is pointed at, never copied. A shipped-table edit has to
        reach a repo that only holds the thin wrapper."""
        shipped = tables.shipped(fail_closed.__file__)
        keep = shipped.read_text()
        before = self.sha()
        try:
            rows = json.loads(keep)
            rows["vocabulary"]["outbound_roots"] = \
                list(rows["vocabulary"]["outbound_roots"]) + ["sdk_from_kernel"]
            shipped.write_text(json.dumps(rows))
            self.assertNotEqual(before, self.sha())
        finally:
            shipped.write_text(keep)
        self.assertEqual(before, self.sha())


if __name__ == "__main__":
    unittest.main(verbosity=2)
