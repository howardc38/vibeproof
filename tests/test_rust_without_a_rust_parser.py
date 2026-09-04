"""What the Rust half answers, called directly.

    python3 -m unittest tests.test_rust_without_a_rust_parser -v

Every Rust repair is proven by a checker fixture, and a fixture runs `v4 verify`
in a subprocess -- the right proof for the checker and no proof at all for the
symbol: nothing in this process ever calls it. These call the analysis
functions and assert what they returned.

Each case is a shape counted in the five-repo corpus rather than invented, and
the count is in the docstring. A test that pins a behaviour nobody writes is a
test that goes the first time it is inconvenient.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import rssource, test_expectation, test_weakened  # noqa: E402
from kernel.analysis.subject_files import is_test                      # noqa: E402


class TheMaskKeepsItsLength(unittest.TestCase):
    """`rssource.rs_masked` -- an offset taken from the mask indexes the source."""

    def test_a_comment_becomes_spaces_and_the_file_does_not_shrink(self):
        src = "let a = 1; // note\nlet b = 2;\n"
        self.assertEqual(len(rssource.rs_masked(src)), len(src))

    def test_a_newline_inside_a_block_comment_survives(self):
        src = "/* one\ntwo */\nfn live() {}\n"
        self.assertEqual(rssource.rs_masked(src).count("\n"), src.count("\n"))


class AnApostropheIsUsuallyALifetime(unittest.TestCase):
    """8,138 lifetimes against 1,594 char literals, so this is the hot path.

    A char-literal pattern reads `'a` as an opening quote and swallows source
    to the next apostrophe -- in a doc comment, several lines away.
    """

    def test_code_after_a_lifetime_is_still_code(self):
        src = "fn g<'a>(v: &'a str) -> &'a str { v }\nfn after() {}\n"
        self.assertIn("fn after", rssource.rs_masked(src))

    def test_a_char_literal_still_has_its_body_blanked(self):
        self.assertNotIn("x", rssource.rs_masked("let c = 'x';\n"))


class ARawStringPicksItsOwnTerminator(unittest.TestCase):
    """1,910 in the corpus, and `r#"…"#` ends where the writer says."""

    def test_a_quote_inside_a_raw_string_does_not_end_it(self):
        src = 'let s = r#"he said "hi""#;\nfn after() {}\n'
        self.assertIn("fn after", rssource.rs_masked(src))

    def test_a_declaration_inside_one_is_not_a_declaration(self):
        masked = rssource.rs_masked('let s = r#"fn hidden() {}"#;\nfn after() {}\n')
        self.assertNotIn("hidden", masked)
        self.assertIn("after", masked)


class BlockCommentsNest(unittest.TestCase):
    """Three in the corpus, and the first `*/` ends none of them."""

    def test_the_inner_close_does_not_end_the_outer_comment(self):
        masked = rssource.rs_masked("/* a /* b */ fn eaten() {} */\nfn live() {}\n")
        self.assertNotIn("eaten", masked)
        self.assertIn("live", masked)


class AnAttributeReachesPastWhatIsBetweenIt(unittest.TestCase):
    """30 corpus tests have a multi-line attribute between `#[test]` and the
    function, and 12 more have a comment there."""

    def test_a_multi_line_attribute_does_not_hide_the_one_above_it(self):
        src = ('#[test]\n#[should_panic(\n    expected = "no"\n)]\n'
               "fn t() { panic!(); }\n")
        self.assertEqual(test_weakened.rs_count(src), 1)

    def test_a_comment_between_the_attribute_and_the_function(self):
        src = ("#[test]\n// https://example.invalid/issues/1\n"
               "fn t() { assert!(true); }\n")
        self.assertEqual(test_weakened.rs_count(src), 1)


class ATestThatStoppedJudgingIsNotATest(unittest.TestCase):
    """`test_weakened.rs_count` -- listed and not run is the move it catches."""

    def test_a_live_one_counts(self):
        self.assertEqual(
            test_weakened.rs_count("#[test]\nfn t() { assert!(true); }\n"), 1)

    def test_ignore_takes_it_out(self):
        self.assertEqual(test_weakened.rs_count(
            "#[test]\n#[ignore]\nfn t() { assert!(true); }\n"), 0)

    def test_an_emptied_body_takes_it_out(self):
        self.assertEqual(test_weakened.rs_count("#[test]\nfn t() {}\n"), 0)

    def test_should_panic_alone_is_not_a_test(self):
        """It says how a test ends, not that a function is one: with no
        `#[test]` above it nothing ever runs this."""
        self.assertEqual(test_weakened.rs_count(
            "#[should_panic]\nfn t() { panic!(); }\n"), 0)


class OneTestCaseRowIsOneTest(unittest.TestCase):
    """99 attributes on 12 functions in the corpus. Counting the function once
    let 45 of one function's 46 cases go without the count moving."""

    THREE = ('#[test_case(1 ; "a")]\n#[test_case(2 ; "b")]\n#[test_case(3 ; "c")]\n'
             "fn t(n: u8) { assert!(n > 0); }\n")

    def test_three_rows_are_three_tests(self):
        self.assertEqual(test_weakened.rs_count(self.THREE), 3)

    def test_removing_a_row_removes_a_test(self):
        two = self.THREE.replace('#[test_case(2 ; "b")]\n', "")
        self.assertEqual(test_weakened.rs_count(two), 2)


class ATestFileIsDecidedByItsContents(unittest.TestCase):
    """377 of the corpus's 455 files holding tests are ordinary implementation
    files under `#[cfg(test)]`, so a name decides nothing."""

    def test_a_source_file_carrying_a_test_module_is_a_test_file(self):
        self.assertTrue(is_test(
            "src/parse.rs",
            "#[cfg(test)]\nmod tests {\n    #[test]\n"
            "    fn t() { assert!(true); }\n}\n"))

    def test_a_source_file_with_no_tests_is_not(self):
        self.assertFalse(is_test("src/parse.rs",
                                 "pub fn parse() -> bool { true }\n"))

    def test_the_root_is_used_when_one_is_given(self):
        """`is_test` was handed repo-relative paths and resolved them against
        the process directory. Python hid it by deciding on the name first;
        Rust cannot, so five fixtures came back exit 0 -- a green that meant
        nobody looked."""
        self.assertTrue(
            is_test("tests/test_rust_without_a_rust_parser.py", root=ROOT))


class TheExpectationIsTheSecondArgument(unittest.TestCase):
    """Measured: of 2,367 corpus `assert_eq!` calls, exactly one side is a
    literal in 1,219 -- the right one 1,177 times against the left's 42."""

    BEFORE = '#[test]\nfn t() { assert_eq!(width("a"), 42); }\n'

    def test_it_reads_the_expected_value(self):
        self.assertEqual(test_expectation.rs_test_functions(self.BEFORE),
                         {"t": ["42"]})

    def test_changing_the_input_is_not_a_moved_expectation(self):
        after = '#[test]\nfn t() { assert_eq!(width("b"), 42); }\n'
        self.assertEqual(test_expectation.rs_changed(self.BEFORE, after), [])

    def test_changing_the_expectation_is(self):
        after = '#[test]\nfn t() { assert_eq!(width("a"), 43); }\n'
        self.assertEqual(test_expectation.rs_changed(self.BEFORE, after),
                         [("t", ["42"], ["43"])])

    def test_reordering_two_assertions_is_not(self):
        """Sorted, for the reason every other half here is: a rule that calls a
        reorder a changed expectation spends its credibility on formatting."""
        before = "#[test]\nfn t() { assert_eq!(a(), 1); assert_eq!(b(), 2); }\n"
        after = "#[test]\nfn t() { assert_eq!(b(), 2); assert_eq!(a(), 1); }\n"
        self.assertEqual(test_expectation.rs_changed(before, after), [])


class AConstructionCountsWhileEverythingInsideItDoes(unittest.TestCase):
    """Rust writes expected values of types with no literal syntax as
    `Position::new(10, 50)`. Rejecting is the safe direction: a value wrongly
    taken for an expectation becomes a FAIL on a test nobody weakened."""

    def test_all_literal_arguments_make_it_an_expectation(self):
        self.assertTrue(
            test_expectation._rs_is_expectation("Position::new(10, 50)"))

    def test_a_variable_argument_does_not(self):
        self.assertFalse(
            test_expectation._rs_is_expectation("Position::new(line, col)"))

    def test_a_bare_path_is_one(self):
        self.assertTrue(test_expectation._rs_is_expectation("Keyword::Async"))

    def test_the_unit_value_is_one(self):
        self.assertTrue(test_expectation._rs_is_expectation("Ok(())"))


class NothingHereReachesIntoThePackage(unittest.TestCase):
    """`rssource` is imported by `subject_files`, which is imported by checkers
    that have never read a line of Go. While the mask lived in `symbols` --
    which reaches `gosource` to answer for Go -- `secret_scan`'s `program_sha`
    moved with the Go emitter.

    Asked of `program_sha` rather than by reading `rssource.py` and looking for
    import statements. The text is the symptom; what matters is what the
    fingerprint covers, so the test asks the mechanism that computes it.
    `test-shape` reported the source-reading version and was right to: a test
    that greps for `from .` passes the day someone reaches the emitter through
    a module that does not spell it that way.
    """

    def test_a_checker_that_reads_neither_rust_nor_go_is_unmoved(self):
        from kernel import hashing
        entry = ROOT / "checkers" / "secret_scan.py"
        emitter = sorted((ROOT / "kernel" / "analysis" / "_go").glob("*.go"))[0]
        original = emitter.read_text(encoding="utf-8")
        self.addCleanup(emitter.write_text, original)
        before = hashing.program_sha(ROOT, entry)
        emitter.write_text(original + "\n// probe\n")
        self.assertEqual(
            hashing.program_sha(ROOT, entry), before,
            "`subject_files` reads Rust now, and a checker that reads neither "
            "Rust nor Go must not have picked up the Go emitter on the way")


if __name__ == "__main__":
    unittest.main(verbosity=2)
