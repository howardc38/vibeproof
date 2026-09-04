"""The two counters, in the other language these documents may be written in.

`lead_in_findings` catches a document that says four above a block of three.
Measured before this: the identical mistake in English returned `[]`, in both
the sentence form and the heading form. `docs/SPEC.md` alone carries 343 lead-in
shapes, so translating it would have taken all of them to zero while `v4 accept`
went on printing the same green -- a check that silently covers nothing, which
is the one outcome this repo rates worse than a red.

The cases come in pairs on purpose. Each says a mistake is caught, and one
beside it says the near-miss is not, because a pattern loose enough to catch
everything would pass the first half of every pair while destroying the rule.

`counted_claims` is the second counter and loses the same way. It reads `N 個 X`,
and a count with no quantifier is only settled when it names its population --
so `21 個 kind` was checked and `21 kinds` is prose. The repair is a name, not a
looser pattern: the populations §13.5's table states carry an English key whose
first word is a qualifier, and the table is written in those. The first word has
to be a qualifier and not a population of its own, which is what the second
class here is about -- keying the lens checks under `lens` settles every bare
`11 lens(es)` in the corpus, two of which are sample CLI output.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import spec_coverage as sc                          # noqa: E402

#: The checker's own walk, not a copy of it. `tests/test_spec_coverage.py`
#: records why: a reimplemented loop skipped a line the real one examined, and
#: the test passed on a walk nothing runs.
scan = sc.lead_in_findings

THREE = "\n\n```\na\nb\nc\n```\n"
FIVE = "\n\n```\na\nb\nc\nd\ne\n```\n"


class AnEnglishLeadIn(unittest.TestCase):
    def test_a_sentence_that_says_four_above_three(self):
        found = scan("All four must pass:" + THREE)
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][2], 3)

    def test_a_heading_that_says_four_above_three(self):
        """A heading is a lead-in without the colon, in either language."""
        found = scan("### Four rules" + THREE)
        self.assertEqual([f[2] for f in found], [3])

    def test_a_table_disagreeing_with_its_lead_in(self):
        found = scan("The three colours:\n\n| a | b |\n|---|---|\n"
                     "| 1 | 2 |\n| 3 | 4 |\n")
        self.assertEqual([f[2] for f in found], [2])

    def test_an_ordered_list_longer_than_its_lead_in(self):
        self.assertEqual([f[2] for f in scan("Three rules:\n\n1. a\n2. b\n"
                                             "3. c\n4. d\n5. e\n")], [5])

    def test_a_lead_in_that_agrees_says_nothing(self):
        self.assertEqual(scan("Three rules:" + THREE), [])


class AndTheThingsItMustNotRead(unittest.TestCase):
    def test_a_number_far_from_the_colon_is_about_the_sentence(self):
        """The Chinese limit is fourteen characters, which is two or three
        English words -- so the unit had to change, not the question."""
        self.assertEqual(scan(
            "Three package ecosystems, and the rule is broken in a great many "
            "ways across the tree today:" + FIVE), [])

    def test_but_a_number_near_it_is_about_the_block(self):
        """The other side of the same limit: six words is where it sits."""
        self.assertEqual(
            [f[2] for f in scan("The gate enforces four colours in total:" + THREE)],
            [3])

    def test_one_is_an_article_more_often_than_a_count(self):
        """`一個` is left out of the Chinese table for this reason and `one`
        carries it into English unchanged."""
        self.assertEqual(scan("One example follows:" + THREE), [])

    def test_a_line_about_a_past_run_is_not_a_claim_about_now(self):
        """`當時` and `前身` were the whole exemption and both are Chinese, so a
        translated document would report its own history as a wrong count."""
        self.assertEqual(scan("At the time there were four:" + THREE), [])
        self.assertEqual(scan("Measured on the reference repo, four:" + THREE), [])

    def test_a_blockquote_is_still_exempt(self):
        self.assertEqual(scan("> All four must pass:" + THREE), [])

    def test_and_the_exemption_still_costs_a_sentence(self):
        body = "\n\n| a | b |\n|---|---|\n|1|2|\n|3|4|\n|5|6|\n"
        self.assertEqual(scan("Four ways:" " <!-- count-exempt: one is a control -->"
                              + body), [])
        self.assertEqual(len(scan("Four ways:" " <!-- count-exempt: -->" + body)), 1)


class TheChineseHalfIsUnchanged(unittest.TestCase):
    """Adding a second language must not move the first. Each of these is a
    case `tests/test_spec_coverage.py` already pins, run again here because the
    matching order is what this change altered."""

    def test_the_sentence_form(self):
        self.assertEqual([f[2] for f in scan("四條全部要過:" + THREE)], [3])

    def test_the_article_is_still_not_a_count(self):
        self.assertEqual(scan("可以有一個 fixture.json:" + THREE), [])

    def test_the_ordinal_is_still_not_a_count(self):
        self.assertEqual(scan("## 第三層 —— lens sweep" + THREE), [])

    def test_a_past_run_is_still_exempt(self):
        self.assertEqual(scan("當時有四條:" + THREE), [])

    def test_and_this_repo_own_documents_still_settle(self):
        """The corpus that would show a loosened pattern inventing findings."""
        for name in ("SPEC.md", "RATIONALE.md", "USING.md", "FACTS.md",
                     "EVIDENCE.md", "DOGFOOD_LOG.md", "README.md"):
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            self.assertEqual(scan(text), [], name)


class ACountInEnglishThatNamesItsPopulation(unittest.TestCase):
    """§13.5's table states six numbers, and translating it took the four with
    a Chinese quantifier out of reach of the thing that settles them."""

    def _says(self, sentence):
        import json
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "docs").mkdir()
        (tmp / "docs" / "d.md").write_text(sentence + "\n", encoding="utf-8")
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "checkers.json").write_text(json.dumps(
            {"a": {}, "b": {}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"x": {}, "y": {}, "z": {}}))
        (tmp / ".v4" / "lenses").mkdir()
        (tmp / ".v4" / "lenses" / "one.json").write_text(json.dumps(
            {"checks": ["a", "b", "c", "d"]}))
        return sc.counted_claims(tmp)

    def test_the_kind_count(self):
        self.assertEqual(self._says("There are 3 claim kinds"), [])
        self.assertIn("says 9 claim kind, and there are 3",
                      "".join(self._says("There are 9 claim kinds")))

    def test_the_checker_count(self):
        self.assertEqual(self._says("2 registered checkers"), [])
        self.assertEqual(len(self._says("7 registered checkers")), 1)

    def test_the_lens_counts(self):
        self.assertEqual(self._says("1 reviewer lens · 4 reviewer checks"), [])
        self.assertEqual(len(self._says("1 reviewer lens · 9 reviewer checks")), 1)

    def test_the_pin_count(self):
        """How much of SPEC is held to the code. Settled per document, so the
        key's own value is never the answer -- but without the key the sentence
        is not read as a claim at all."""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "docs").mkdir()
        (tmp / "docs" / "SPEC.md").write_text(
            "## a\n<!-- pinned: kernel/x.py::f -->\n"
            "## b\n<!-- pinned: kernel/x.py::g -->\n", encoding="utf-8")
        (tmp / "docs" / "d.md").write_text(
            "SPEC.md carries 3 design pins\n", encoding="utf-8")
        said = sc.counted_claims(tmp)
        self.assertEqual(len(said), 1, said)
        self.assertIn("says 3 design pin, and there are 2", said[0])

    def test_and_the_chinese_spelling_still_settles_the_same_number(self):
        """Aliases, not replacements. A repo part-way through a translation
        carries both spellings, and both are the same claim."""
        self.assertEqual(len(self._says("有 9 個 kind")), 1)


class TheFirstWordOfAKeyIsAQualifier(unittest.TestCase):
    """What makes a bare count readable is the first word of a key, so a key
    whose first word is itself a population turns every mention of it into a
    claim about a number. Both of these were written and both were withdrawn
    against this repo's own `docs/`."""

    def test_the_lens_checks_are_not_keyed_under_lens(self):
        truth = sc._reality(ROOT)
        self.assertNotIn("lens check", truth)
        self.assertEqual(truth["reviewer check"], truth["check"])

    def test_the_detector_files_are_not_keyed_under_detector(self):
        self.assertNotIn("detector file", sc._reality(ROOT))

    def test_what_that_would_have_reported(self):
        """The measurement, run as an assertion: with `lens` and `detector` in
        the narrowing set, this repo's documents produce findings against
        sample CLI output (`11 lens(es), 214 finding(s)`) and against a
        thousands separator (`3,502 detector runs` reading as 502)."""
        truth = sc._reality(ROOT)
        widened = dict(truth, **{"lens check": truth["check"],
                                 "detector file": truth["detector"]})
        count_re = sc._count_re(widened)
        narrowing = {k.split(" ", 1)[0] for k in widened if " " in k}
        hits = 0
        for doc in sorted((ROOT / "docs").glob("*.md")):
            for line in doc.read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith(">") or sc._about_the_past(line):
                    continue
                for n, quant, qual, noun in count_re.findall(line):
                    if quant or not sc._names_its_population(
                            quant, qual, noun, narrowing):
                        continue
                    what = f"{qual.strip()} {noun}" if qual.strip() else noun
                    if what in ("lens", "detector"):
                        hits += 1
        self.assertGreater(hits, 0)


class TheDoctrineRuleCount(unittest.TestCase):
    """§13.5's table has a row for layer ①, and it said 88 while the constant
    held 96 -- the one cell in the table whose number nothing could read."""

    def test_it_matches_the_constant_this_process_imports(self):
        from kernel import doctrine
        self.assertEqual(sc._reality(ROOT)["doctrine rule"],
                         sum(len(rules) for _, rules in doctrine.DOCTRINE))

    def test_it_is_read_from_the_root_it_was_given(self):
        """Not from `kernel.doctrine`. A checker may be run against a repo that
        is not the one it was started from, and importing answers about the
        wrong tree without saying so."""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "kernel").mkdir()
        (tmp / "kernel" / "doctrine.py").write_text(
            'DOCTRINE = [("a", ["one", "two"]), ("b", ["three"])]\n',
            encoding="utf-8")
        self.assertEqual(sc._doctrine_rules(tmp), {"doctrine rule": 3})

    def test_a_repo_with_no_doctrine_module_settles_nothing(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertEqual(sc._doctrine_rules(tmp), {})

    def test_and_a_doctrine_that_is_not_a_literal_settles_nothing(self):
        """Rather than executing it. `literal_eval` refusing is the answer, not
        an import that would run whatever the file happens to say."""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "kernel").mkdir()
        (tmp / "kernel" / "doctrine.py").write_text(
            "DOCTRINE = build_it()\n", encoding="utf-8")
        self.assertEqual(sc._doctrine_rules(tmp), {})


if __name__ == "__main__":
    unittest.main()
