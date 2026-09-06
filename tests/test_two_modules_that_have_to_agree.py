"""Assertions about the text of the code, kept apart so they cannot poison it.

    python3 -m unittest tests.test_two_modules_that_have_to_agree -v

These read source rather than run it, and each says why in its own case. They
live in a file of their own for a mechanical reason, not a tidy one:
`redgreen.verify` parses the *whole* closing-test file through
`test_shape.source_assertions`, and one match anywhere sets `symbol_executed`
to False. So a file carrying a single source assertion can never close a review
finding, however behavioural the rest of it is -- measured by offering
`tests/test_the_brief_a_reviewer_can_follow.py` as the closure for a finding it
was written to repair and getting "asserts on the source text of the code it is
closing", exit 1.

That is the right rule and the right refusal: a test that calls a symbol once
and then reads its source satisfies both halves of red-green and proves
nothing. It just means the two kinds of check cannot share a file. The
behavioural half stays where it was and can close findings; this half is here,
in the baseline, with its reasons.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TheWriterAndTheReaderOfALensRow(unittest.TestCase):
    """`record_lens_reviewed` writes it, `sweep` reads it, and they have to
    agree on which value means "no task". Behaviour cannot see the agreement:
    each side answers correctly on its own, and they only disagree about a row
    written by one and queried by the other."""

    def test_the_writer_names_the_kind_the_reader_selects(self):
        review = (ROOT / "kernel" / "review.py").read_text(encoding="utf-8")
        sweep = (ROOT / "kernel" / "sweep.py").read_text(encoding="utf-8")
        self.assertIn("LENS_REVIEWED_KIND", review)
        self.assertIn("task_id IS NULL", sweep)

    def test_both_writers_normalise_an_empty_task(self):
        """The behavioural cases in the sibling file prove each writer stores
        NULL. This one is that neither has been left behind: two functions,
        one rule."""
        review = (ROOT / "kernel" / "review.py").read_text(encoding="utf-8")
        self.assertEqual(review.count("task_id=task_id or None"), 2)


class TheGuardThatWasNarrowedOnce(unittest.TestCase):
    def test_the_lens_check_still_needs_a_corpus(self):
        """The first spelling refused every finding in a repo with no lens
        files at all, and the existing suite caught it. In a repo that has
        lenses the two spellings behave identically, so what pins the
        correction is the condition itself."""
        src = (ROOT / "kernel" / "review.py").read_text(encoding="utf-8")
        self.assertIn("if lens and usable and lens not in usable", src)


class TheHalvesThatHadToStayInStep(unittest.TestCase):
    """Four claims that a fact has one copy. Behaviour cannot make them:
    two copies that both work answer identically, which is the state this
    repo was in until one of each pair was found doing something else. The
    behavioural halves are in tests/test_the_half_that_was_silent.py, which
    is kept free of source assertions so it can close findings."""

    def test_both_hooks_ask_the_owner(self):
        for name in ("write_block", "stop_gate"):
            src = (ROOT / "hooks" / f"{name}.py").read_text(encoding="utf-8")
            body = src.split("def _is_open")[1].split("\ndef ")[0]
            self.assertIn("_framework.is_open", body, name)
            self.assertNotIn("sqlite3.connect", body, name)

    def test_the_stand_in_carries_what_the_hook_reaches_for(self):
        from types import SimpleNamespace
        for hook in ("bash_guard", "write_block"):
            src = (ROOT / "hooks" / f"{hook}.py").read_text(encoding="utf-8")
            block = src.split("SimpleNamespace(")[1].split(")")[0]
            self.assertIn("record_seen", block, hook)
            self.assertIn("is_open", block, hook)

    def test_no_second_literal_survives_in_the_run_paths(self):
        src = (ROOT / "kernel" / "accept.py").read_text(encoding="utf-8")
        body = src.split("def _run(")[1].split("\ndef ")[0]
        self.assertNotIn("1800", body)

    def test_a_detector_that_wrote_nothing_adds_no_key(self):
        """An absent key is cheaper to read than a null on every row, and a
        detector with no `--out` has nothing to say rather than nothing."""
        src = (ROOT / "kernel" / "derive.py").read_text(encoding="utf-8")
        self.assertIn("if det_out is not None else {}", src)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()


class ADoctorCheckReadsEverythingItComputes(unittest.TestCase):
    """`_check_a_registered_checker_that_has_never_executed` computed a
    `DISTINCT` query into a local nothing read, under five lines of comment
    explaining what it decided -- the exclusion it describes is a subquery two
    lines below it.

    Source, because a local nobody reads is invisible to behaviour, and that is
    exactly why it is worth refusing: the reader cannot tell it from live code
    and the query runs on every `v4 doctor`. The behavioural half is beside it
    in `tests/test_checks_that_answered_from_nothing.py`, which asks the check
    for a verdict.
    """

    def test_it(self):
        import ast

        src = (ROOT / "kernel" / "doctor.py").read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name != "_check_a_registered_checker_that_has_never_executed":
                continue
            assigned, used = {}, set()
            for n in ast.walk(fn):
                if isinstance(n, ast.Name):
                    if isinstance(n.ctx, ast.Store):
                        assigned.setdefault(n.id, n.lineno)
                    else:
                        used.add(n.id)
            dead = {k: v for k, v in assigned.items() if k not in used}
            self.assertEqual(dead, {}, f"assigned and never read: {dead}")
            return
        self.fail("the function this is about is not in doctor.py")
