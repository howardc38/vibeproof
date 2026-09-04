"""The checks that read the documents as documents.  SPEC.md §0.

Most of `spec_coverage` resolves a name in prose against the repo: a command, a
path, a kind. These four do something different -- they read a claim the
document makes about itself, or about a file the repo can count. Each was
written after a defect of its class shipped, and each is tested here against
that defect rather than against a synthetic one.

The cases below are the real wordings, cut down. A checker that only ever runs
against the current documents is a checker that passes because the documents
were fixed by hand, and this file is the difference.
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import spec_coverage as sc                # noqa: E402


#: The checker's own walk, not a copy of it. The first version of this file
#: reimplemented the loop, and the copy skipped a line the real one examined --
#: a test passing on a walk nothing runs.
scan = sc.lead_in_findings


class LeadInCounts(unittest.TestCase):
    """`四條全部要過:` above a block listing three."""

    def test_fenced_block_short_of_its_lead_in(self):
        # The defect verbatim: the registration gate lost `bypass/`, the colour
        # that found thirteen real evasions the first time it ran, and the
        # number above the block still said four.
        found = scan("Kernel 親自跑,四條全部要過:\n\n"
                     "```\nred/\ngreen/\n同一份輸入跑兩次\n```\n")
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][2], 3)

    def test_ordered_list_longer_than_its_lead_in(self):
        found = scan("三條規矩:\n\n1. a\n2. b\n3. c\n4. d\n5. e\n")
        self.assertEqual([f[2] for f in found], [5])

    def test_table_disagreeing_with_its_lead_in(self):
        found = scan("帶 engagement 嘅七個:\n\n| a | b |\n|---|---|\n"
                     "| 1 | 2 |\n| 3 | 4 |\n")
        self.assertEqual([f[2] for f in found], [2])

    def test_a_lead_in_that_agrees_says_nothing(self):
        self.assertEqual(scan("五樣,任何一樣變:\n\n```\na\nb\nc\nd\ne\n```\n"), [])

    def test_blank_header_row_is_not_a_separator(self):
        # `| | |` is this document's two-column table with no headings. Reading
        # it as the ---|--- separator lost a row from every such table, which
        # made correct lead-ins look wrong.
        self.assertEqual(
            scan("兩個守衛:\n\n| | |\n|---|---|\n| a | b |\n| c | d |\n"), [])

    def test_the_article_is_not_a_count(self):
        # `一個` introduces one example far more often than it counts one thing.
        self.assertEqual(
            scan("目錄型 fixture 可以有一個 `fixture.json`:\n\n"
                 "```\n{\"params\": {}}\n{\"committed\": []}\n```\n"), [])

    def test_a_number_far_from_the_colon_is_about_something_else(self):
        self.assertEqual(
            scan("三個 package 生態,而條 MUST 今日用一堆方式被違反:\n\n"
                 "| a | b |\n|---|---|\n|1|2|\n|3|4|\n|5|6|\n"), [])

    def test_exemption_requires_a_reason(self):
        body = "\n\n| a | b |\n|---|---|\n|1|2|\n|3|4|\n|5|6|\n|7|8|\n|9|0|\n"
        self.assertEqual(scan("四種方式:" " <!-- count-exempt: 一行係對照組 -->" + body), [])
        self.assertEqual(len(scan("四種方式:" " <!-- count-exempt: -->" + body)), 1)


class SectionsAscend(unittest.TestCase):
    def test_a_section_out_of_order_is_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            docs = Path(td) / "docs"
            docs.mkdir()
            (docs / "X.md").write_text("## 10. a\n\n## 12. b\n\n## 11. c\n")
            problems = sc.sections_ascend(Path(td))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("§11", problems[0])

    def test_this_repo_reads_in_order(self):
        self.assertEqual(sc.sections_ascend(ROOT), [])


class EngagedKindsExplained(unittest.TestCase):
    def test_every_engaged_kind_has_a_row(self):
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        self.assertEqual(sc.engaged_kinds_explained(ROOT, spec), [])

    def test_a_kind_with_no_row_is_reported(self):
        # `bundle-secret` carried a rule and had no row, so it stopped work for
        # a reason nobody had written. The marker line was right, so nothing saw
        # it.
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        kinds = json.loads((ROOT / ".v4/claim_kinds.json").read_text())
        engaged = [k for k, v in kinds.items() if v.get("engagement")]
        self.assertTrue(engaged)
        without = spec.replace(f"| `{engaged[0]}` |", "| REMOVED |")
        self.assertEqual(len(sc.engaged_kinds_explained(ROOT, without)), 1)


class DispositionsAddUp(unittest.TestCase):
    def test_the_table_matches_the_register(self):
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        self.assertEqual(sc.dispositions_add_up(ROOT, spec), [])

    def test_a_wrong_count_is_reported(self):
        """Keyed on the landed value, not on the prose beside it.

        This used to break the row by replacing `(層 ④) | 19 |`, and that is a
        sentence in one language: translating the table left the substitution
        matching nothing, so the test asserted a wrong count is reported while
        handing the checker an unmodified document. `assertNotEqual` is what
        caught it, and the repair is to key off the same thing
        `dispositions_add_up` keys off."""
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        rules = json.loads(
            (ROOT / ".v4/rule_dispositions.json").read_text())["rules"]
        n = sum(1 for r in rules if r.get("landed") == ".v4/claim_kinds.json")
        broken, hits = re.subn(
            r"^(\|[^|\n]*\.v4/claim_kinds\.json[^|\n]*\|)\s*%d\s*\|$" % n,
            r"\1 %d |" % (n - 4), spec, flags=re.M)
        self.assertEqual(hits, 1)
        self.assertNotEqual(broken, spec)
        self.assertTrue(sc.dispositions_add_up(ROOT, broken))


class GateColoursNamed(unittest.TestCase):
    def test_the_spec_names_every_colour_the_gate_enforces(self):
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        self.assertEqual(sc.gate_colours_named(ROOT, spec), [])

    def test_a_colour_the_spec_omits_is_reported(self):
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        without = spec.replace("bypass/", "BYPASS-REMOVED")
        problems = sc.gate_colours_named(ROOT, without)
        self.assertTrue(problems)
        self.assertTrue(any("bypass" in p for p in problems))


class PinsAreCountedPerDocument(unittest.TestCase):
    def test_a_row_about_one_document_is_settled_against_that_document(self):
        # README's table names SPEC.md on one row and RATIONALE.md on the next.
        # Counting SPEC's pins for both reported the correct RATIONALE figure as
        # wrong.
        spec = sc._pins_in(ROOT, "SPEC.md")
        rat = sc._pins_in(ROOT, "RATIONALE.md")
        self.assertTrue(spec and rat)
        self.assertNotEqual(spec, rat)
        self.assertIsNone(sc._pins_in(ROOT, "NoSuchDocument.md"))


class TheAdopterNameForAPathThatIsHere(unittest.TestCase):
    """`.v4/fixtures/` is what `v4 install` calls `tests/fixtures/`.

    The spec's ownership table names the adopter-side path, which is correct
    prose about a directory this repo has under its framework-side name. The
    checker had no rule for that; it was satisfied by accident, because one
    fixture case carried a nested `.v4/fixtures/` of its own and the
    fixture-case branch matched it. Deleting sixteen unrelated fixture
    directories in a trim removed that accident, and a true sentence in the
    spec started reading as a path that is not there.

    Fails against dadac9e: `_exists` returned False for `.v4/fixtures/`.
    """

    def test_the_adopter_fixtures_root_resolves_to_the_framework_one(self):
        self.assertTrue(sc._exists(ROOT, ".v4/fixtures/"))
        self.assertTrue(sc._exists(ROOT, ".v4/fixtures"))

    def test_a_named_case_under_it_resolves_too(self):
        self.assertTrue(sc._exists(ROOT, ".v4/fixtures/scope"))

    def test_and_a_case_that_is_not_there_still_reads_as_missing(self):
        """Otherwise the mapping would answer yes for anything under the root."""
        self.assertFalse(sc._exists(ROOT, ".v4/fixtures/no_such_case_here"))

    def test_it_does_not_answer_for_other_v4_paths(self):
        self.assertFalse(sc._exists(ROOT, ".v4/no_such_file.json"))


class APathTheSpecNamesThatShipWrites(unittest.TestCase):
    """A repo that has installed and not shipped does not have every file yet.

    The spec names `.v4/chain_head.json` and `.v4/ledger_export.jsonl`, and
    `v4 ship` is what writes them -- `kernel/install.py` mentions neither. So a
    fresh adopter running `v4 accept` before any work read as a spec describing
    two things the repo does not have. Measured on a freshly exported tree:
    6/7, on these two and nothing else.

    The pair of tests below is the point. One shows the exemption works; the
    other shows the rule it exempts from still catches things, because an
    exemption that quietly answered yes for anything absent would pass the first
    test while destroying the check.
    """

    def _fresh(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        return tmp

    def test_neither_reads_as_missing_in_a_tree_that_never_shipped(self):
        tmp = self._fresh()
        for rel in (".v4/chain_head.json", ".v4/ledger_export.jsonl"):
            self.assertFalse((tmp / rel).exists(), "the fixture must not have it")
            self.assertTrue(sc._exists(tmp, rel), rel)

    def test_and_any_other_absent_path_the_spec_names_still_does(self):
        tmp = self._fresh()
        for rel in (".v4/config.json", ".v4/claim_kinds.json", "kernel/cli.py"):
            self.assertFalse(sc._exists(tmp, rel), rel)


if __name__ == "__main__":
    unittest.main()
