"""Repairs in `kernel/review.py`, called directly.

    python3 -m unittest tests.test_what_a_reviewer_is_handed_and_what_comes_back -v

Layer 3 is the one layer with no program behind its judgement, so everything
mechanical about it -- the brief a reviewer is handed, the coordinates a
finding comes back with, the note a reader gets -- is the whole of what can be
checked. Each of these was a place where the mechanical half was wrong.

All of them fail against 0ad6b61.
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

from kernel import config, ledger, review  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


class AnAmendedNoteReachesItsReader(unittest.TestCase):
    """The amendment was written as an event nothing read.

    `raise_finding` appended `finding_note_amended` and every reader of a
    finding went on selecting `claim.note` -- the ledger is append-only, so the
    superseded text is what `status --json` served to the orchestrator the
    amendment was for. The write happened and the read did not follow.
    """

    def _claim(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="review-finding",
                      question="q", subject_refs="[]", checker="review-finding",
                      origin="review", created_at="2026", note="the first note")
        return conn

    def _cfg(self, root):
        return config.RepoConfig(root)

    def test_an_amendment_goes_through_the_writer_that_names_it(self):
        """Through a writer in this module, not around one.

        This used to be `raise_finding` called twice with the same coordinates
        and a different note. That inference is gone -- it could not tell a
        correction from a second finding, and on 2026-08-27 it resolved six of
        those the destructive way -- so the amendment has a function of its own
        and the same fact is pinned against it.
        """
        root = _repo(self)
        (root / "mod.py").write_text("def helper():\n    return 1\n")
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding",
                                "question_template": "q", "staleness": "subject"}}))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        cid, created, _siblings = review.raise_finding(
            conn, self._cfg(root), task_id=None, file="mod.py",
            symbol="helper", note="the first note", lens="l")
        self.assertTrue(created)
        was, changed = review.amend_note(conn, claim_id=cid, actor="agent",
                                         note="the corrected note")
        self.assertTrue(changed, "the correction recorded nothing")
        self.assertEqual(was, "the first note")
        self.assertEqual(review.current_note(conn, cid, "the first note"),
                         "the corrected note")

    def test_and_re_filing_a_different_note_is_a_different_finding(self):
        """The other half of the same decision: what `add` does now.

        `raise_finding` no longer resolves an unrecognised note by overwriting
        the one that is there. It opens the next slot, and says which findings
        already stand at those coordinates -- the sentence that would have
        stopped the six.
        """
        root = _repo(self)
        (root / "mod.py").write_text("def helper():\n    return 1\n")
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding",
                                "question_template": "q", "staleness": "subject"}}))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        first, _made, _sib = review.raise_finding(
            conn, self._cfg(root), task_id=None, file="mod.py",
            symbol="helper", note="the first note", lens="l")
        second, made, siblings = review.raise_finding(
            conn, self._cfg(root), task_id=None, file="mod.py",
            symbol="helper", note="a second thing entirely", lens="l")
        self.assertTrue(made)
        self.assertNotEqual(second, first)
        self.assertEqual(siblings, [first])
        self.assertEqual(review.current_note(conn, first, "the first note"),
                         "the first note")

    def test_with_no_amendment_the_stored_note_is_the_note(self):
        conn = self._claim()
        self.assertEqual(review.current_note(conn, "c1", "the first note"),
                         "the first note")

    def test_an_amendment_supersedes_it(self):
        conn = self._claim()
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id=None, claim_id="c1",
                          kind=review.AMENDED_KIND, actor="reviewer",
                          payload={"now": "the corrected note"},
                          created_at="2026")
        self.assertEqual(review.current_note(conn, "c1", "the first note"),
                         "the corrected note")


class ADeferralFloorIsThisReposFloor(unittest.TestCase):
    """`DEFER_MIN` was bound to the default at import.

    So a repo that sets `thresholds.min_chars` in `.v4/config.json` was still
    held to the number this framework ships with -- the one knob the repo is
    invited to turn, read from somewhere it cannot reach.
    """

    def test_the_default_is_used_when_nothing_is_declared(self):
        self.assertEqual(review.defer_min(),
                         config.DEFAULT_THRESHOLDS["min_chars"])

    def test_a_repo_that_declares_one_gets_its_own(self):
        root = _repo(self)
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 200}}))
        self.assertEqual(review.defer_min(config.RepoConfig(root)), 200)

    def test_defer_holds_a_reason_to_this_repos_floor(self):
        """Through `defer` itself: the floor is read where the refusal is
        made, not bound at import."""
        root = _repo(self)
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 200}}))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="review-finding",
                      question="q", subject_refs="[]", checker="review-finding",
                      origin="review", created_at="2026")
        short = "x" * 100                      # over the default, under 200
        with self.assertRaises(ValueError):
            review.defer(conn, root, claim_id="c1", why=short,
                         target="t-42", cfg=config.RepoConfig(root))


class ABlankAntiPatternReachesTheReviewer(unittest.TestCase):
    """`unusable` read `checks` and never the entries of `anti_patterns`.

    `lens_brief` prints every one of them verbatim, so an empty string arrived
    as a bullet with nothing after the dash -- under "shapes worth flagging on
    sight", which reads as a truncated list. The half meant to stop a reviewer
    reporting the wrong thing was the half nothing checked.
    """

    GOOD = {"name": "n", "source": "s", "checks": ["ask this"],
            "anti_patterns": ["not this"]}

    def test_a_usable_lens_is_usable(self):
        self.assertEqual(review.unusable(self.GOOD), "")

    def test_a_blank_anti_pattern_is_not(self):
        lens = dict(self.GOOD, anti_patterns=["not this", ""])
        said = review.unusable(lens)
        self.assertIn("blank", said)

    def test_and_it_says_which_one(self):
        lens = dict(self.GOOD, anti_patterns=["a", "", "c", ""])
        self.assertIn("[1, 3]", review.unusable(lens))


class TheBriefPrintsACommandThatRuns(unittest.TestCase):
    """`lens_brief` printed `--task $V4_TASK` and omitted `--lens`.

    A periodic sweep has no task by design -- `ledger.REVIEW_TASK` exists for
    that -- so the command it handed every reviewer died with "argument
    --task: expected one argument". And the claim id is derived from what
    `--lens` is given, so omitting it collapsed two lenses' findings about one
    site into one claim.
    """

    LENS = {"name": "architecture fit", "source": "ARCH.md",
            "checks": ["does the boundary hold"],
            "anti_patterns": ["naming a file with no argument"]}

    def test_it_does_not_print_a_task_placeholder(self):
        self.assertNotIn("$V4_TASK", review.lens_brief(self.LENS, "architecture-fit"))

    def test_it_names_the_lens(self):
        self.assertIn("--lens architecture-fit",
                      review.lens_brief(self.LENS, "architecture-fit"))


class CoordinatesAreRefusedWhereTheyAreWritten(unittest.TestCase):
    """`resolve_symbol` checked Python and let everything else through.

    `.github/monitor/PROMPT.md` states the refusal as unconditional. It applied
    to `.py` only, so a symbol against a JSON table or a document was accepted
    and the claim that came back could never be closed by red-green -- three
    are in this ledger, two of them naming functions that live in `.py` files
    somewhere else entirely.
    """

    def _root(self):
        root = _repo(self)
        (root / "mod.py").write_text("class C:\n    def method(self):\n        pass\n")
        (root / "table.json").write_text("{}")
        return root

    def test_a_dotted_python_name_is_normalised(self):
        self.assertEqual(review.resolve_symbol(self._root(), "mod.py", "C.method"),
                         "method")

    def test_a_symbol_against_a_json_table_is_refused(self):
        """The refusal stands; the reason it gives had to move.

        This asserted the message says "not Python", which was the rule while
        the tracer was Python and nothing else. It is not the rule now -- a
        `.go` file is not Python either and its symbols are accepted -- so the
        old expectation would hold a true sentence to a false reason. What is
        asserted instead is the part that is still the rule: this file is one
        nothing can enter, and the message names it."""
        with self.assertRaises(review.BadCoordinates) as caught:
            review.resolve_symbol(self._root(), "table.json", "writing")
        said = str(caught.exception)
        self.assertIn("table.json", said)
        self.assertIn("no tracer here can enter", said)
        self.assertNotIn("not Python", said)

    def test_a_finding_about_that_table_with_no_symbol_is_fine(self):
        """The refusal is of the symbol, not of the finding: a finding about a
        document closes by text closure, which needs no frame."""
        self.assertEqual(review.resolve_symbol(self._root(), "table.json", ""), "")


class TheLensCorpusIsNotDescribedByAStaleNumber(unittest.TestCase):
    """`check_text`'s docstring counted a corpus of nine.

    It said "seven of the nine hold plain strings" and "130 of the 280 checks"
    about a corpus that is neither, and no file is all-string at all. The
    numbers moved to `spec_coverage._reality`, which counts them.
    """

    def test_the_corpus_is_counted_by_something(self):
        """The numbers left the docstring for a function that reads the files.

        A count in a comment about a directory is the one count nothing
        settles; `spec_coverage._reality` reads `.v4/lenses/` and the documents
        that state those numbers are held to them by `counted_claims`.
        """
        # The judgement, not the CLI: these rules moved to `kernel/analysis/`
        # where SPEC §12 step 1 puts them, so this is an ordinary import.
        from kernel import spec_coverage
        truth = spec_coverage._reality(ROOT)
        for key in ("lens", "check", "dict-shaped check", "string-shaped check"):
            self.assertIn(key, truth)
        self.assertEqual(truth["dict-shaped check"] + truth["string-shaped check"],
                         truth["check"])

    def test_a_document_that_states_the_wrong_one_is_reported(self):
        """The control: counting them and telling nobody would pass the test
        above."""
        # The judgement, not the CLI: these rules moved to `kernel/analysis/`
        # where SPEC §12 step 1 puts them, so this is an ordinary import.
        from kernel import spec_coverage
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "docs").mkdir()
        (tmp / ".v4" / "lenses").mkdir(parents=True)
        (tmp / ".v4" / "lenses" / "one.json").write_text(json.dumps(
            {"name": "n", "source": "s", "checks": ["a", "b"],
             "anti_patterns": ["x"]}))
        (tmp / "docs" / "d.md").write_text("呢個 repo 有 9 條 check\n", encoding="utf-8")
        said = spec_coverage.counted_claims(tmp)
        self.assertTrue(any("check" in p for p in said), said)

    def test_check_text_still_reads_both_shapes(self):
        self.assertEqual(review.check_text("a plain string"), "a plain string")
        got = review.check_text({"check": "an object", "why_not_a_checker": "r"})
        self.assertIn("an object", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
