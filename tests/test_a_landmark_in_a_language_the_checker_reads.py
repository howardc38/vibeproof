"""Two checks that find a section by the words it is written in.

`lead_in_findings` needed a grammar in the other language. These two need
something narrower: they look for a landmark -- the sentence that assigns a
rule to layer 1, and the marks that say a line is about a past run -- and both
landmarks were spelled only in Chinese. Translating a document moves the
landmark, and a check that cannot find its landmark reports nothing while
reporting success.

`user_facing_docs` was on the list and is not here: its only Chinese is a
sentence inside a docstring, quoted from §12.5 to explain why `.claude/` is in
the corpus. Nothing in its body reads it.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import spec_coverage as sc                          # noqa: E402


class TheSentenceThatAssignsARuleAHome(unittest.TestCase):
    """§9 argues a rule cannot be layer 4, therefore it is layer 1. Both the
    landmark and the quotes around the rule were Chinese."""

    def _find(self, text):
        return sc.layer_one_has_what_it_was_assigned(ROOT, text)

    def test_english_assigns_a_rule_the_doctrine_does_not_carry(self):
        found = self._find(
            "This rule belongs to layer 1, not layer 4 cannot: `flurb`\n")
        self.assertEqual(len(found), 1, found)
        self.assertIn("flurb", found[0])

    def test_and_a_rule_the_doctrine_does_carry_is_fine(self):
        self.assertEqual(
            self._find("This rule belongs to layer 1: `stopgap`\n"), [])

    def test_a_table_row_is_not_a_sentence(self):
        """Cells are separate fields. §9's engaged-kinds table names a kind in
        one column and cites `CLAUDE.md layer ① Verification` as its source in
        another, and reading across them invents an assignment nobody wrote."""
        self.assertEqual(self._find(
            "| `flurb` | no mechanical answer | CLAUDE.md layer ① Verification |\n"),
            [])

    def test_but_a_sentence_still_assigns(self):
        """The narrowing is the table, not the backtick."""
        self.assertEqual(len(self._find(
            "This rule belongs to layer 1: `flurb`\n")), 1)

    def test_a_path_in_backticks_is_not_a_rule_being_assigned(self):
        """`kernel/doctrine.py` is a file. A rule is named in a word, so the
        backtick form is deliberately narrow -- otherwise every symbol on a
        layer-1 line becomes an assignment the doctrine must answer for."""
        self.assertEqual(self._find(
            "Layer 1 is generated from `kernel/doctrine.py`\n"), [])

    def test_the_chinese_form_still_finds_it(self):
        found = self._find("呢條規則屬層 ①,唔係層 ④ 加唔到:「唔好用 flurb」\n")
        self.assertEqual(len(found), 1, found)

    def test_and_this_repo_own_spec_still_settles(self):
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        self.assertEqual(self._find(spec), [])


class ALineAboutAPastRun(unittest.TestCase):
    """Three sites spelled this inline, two of them carried `V3` and `rev 1`
    and the third did not -- which is what a rule with no owner looks like from
    the inside."""

    def test_the_chinese_marks(self):
        for line in ("當時有四條", "前身量到 12 個", "V3 有 27 個", "rev 1 記低 5 個"):
            self.assertTrue(sc._about_the_past(line), line)

    def test_the_english_marks(self):
        for line in ("At the time there were four",
                     "as measured on the reference repo",
                     "Measured on adopter_a, 27 of them"):
            self.assertTrue(sc._about_the_past(line), line)

    def test_and_a_claim_about_now_is_not_exempt(self):
        """Otherwise the exemption is an off switch: everything reads as
        history and nothing is ever settled."""
        for line in ("there are 27 checkers",
                     "the gate enforces four colours",
                     "每個 task 跑 21 個 checker"):
            self.assertFalse(sc._about_the_past(line), line)

    def test_every_site_reads_the_same_owner(self):
        """The three call sites, by source: none may spell a mark of its own."""
        src = (ROOT / "kernel" / "spec_coverage.py").read_text(encoding="utf-8")
        body = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("#"))
        for mark in ('"當時" in', '"前身" in', '"V3" in', '"rev 1" in'):
            self.assertNotIn(mark, body, mark)
        self.assertGreaterEqual(body.count("_about_the_past("), 3)


if __name__ == "__main__":
    unittest.main()
