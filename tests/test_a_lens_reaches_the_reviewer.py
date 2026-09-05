"""What a reviewer is actually handed.

    python3 -m unittest tests.test_a_lens_reaches_the_reviewer -v

`request-fidelity` was written, passed `unusable()`, and was still broken twice
over the first time a reviewer ran it: the field carrying "here is how to fetch
the request" was never printed, and the line above the checks told the reviewer
not to read the diff that all six of them are about.

Neither showed up in the contract check, because both are about what `lens_brief`
does with a lens rather than what a lens carries. So these call the brief and
assert what came back.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import review   # noqa: E402


def lens(**over):
    base = {"name": "L", "source": "S", "checks": ["c one"],
            "anti_patterns": ["a one"]}
    base.update(over)
    return base


class TheWhyFieldReachesTheReviewer(unittest.TestCase):
    """It sat in five of the twelve files and was printed in none of them."""

    def test_it_is_printed_when_present(self):
        out = review.lens_brief(lens(why="讀個 request 先"))
        self.assertIn("讀個 request 先", out)

    def test_a_lens_without_one_is_unchanged(self):
        out = review.lens_brief(lens())
        self.assertNotIn("None", out)
        self.assertIn("## Checks", out)

    def test_it_is_not_required(self):
        """Seven lenses have none and want none -- `unusable` must not start
        asking for one."""
        self.assertEqual(review.unusable(lens()), "")


class TheOpeningLineMatchesTheMode(unittest.TestCase):
    """Two callers: a periodic sweep with no task, and a task-scoped run with a
    diff. The sweep's instruction printed to the second is two contradicting
    orders on one screen."""

    def test_a_task_scoped_run_is_told_to_read_the_diff(self):
        out = review.lens_brief(lens(), slug="l", task="fw-rust")
        self.assertIn("Read the diff for task `fw-rust`", out)
        self.assertNotIn("not a\ndiff review", out)

    def test_a_sweep_keeps_the_after_gate_wording(self):
        out = review.lens_brief(lens(), slug="l")
        self.assertIn("this is the after-gate", out)
        self.assertNotIn("Read the diff for task", out)


class TheShippedLensesStillLoad(unittest.TestCase):
    """`lens_files` splits usable from skipped, and a skipped lens is one no
    reviewer will ever be handed."""

    def test_none_of_them_is_skipped(self):
        usable, skipped = review.lens_files(ROOT)
        self.assertEqual(skipped, {})
        self.assertGreaterEqual(len(usable), 12)

    def test_request_fidelity_is_among_them(self):
        usable, _ = review.lens_files(ROOT)
        self.assertIn("request-fidelity", usable)


class TheRequestFidelityLensSaysWhereToGetTheRequest(unittest.TestCase):
    """The whole design rests on one command reaching the reviewer: the brief
    does not carry the request, so the lens has to say how to fetch it. That
    sentence lives in `why`, which is exactly the field nothing printed."""

    def setUp(self):
        self.lens = json.loads(
            (ROOT / ".v4" / "lenses" / "request-fidelity.json").read_text())
        self.brief = review.lens_brief(self.lens, slug="request-fidelity",
                                       task="some-task")

    def test_the_command_is_in_the_brief(self):
        self.assertIn("v4 --repo . cover --task $V4_TASK --show", self.brief)

    def test_it_says_the_request_is_readable_and_the_worker_is_not(self):
        """Blind reading has a boundary and it is not "read nothing": the
        request is the requester's words. A reviewer who reads the worker's
        engagement sentences samples along that explanation."""
        self.assertIn("engagement", self.brief)

    def test_every_check_carries_why_it_is_not_a_checker(self):
        """SPEC §4.7 forbids the checker. A check that cannot say why it is not
        one is a check that belongs in a checker."""
        for c in self.lens["checks"]:
            self.assertIsInstance(c, dict)
            self.assertTrue(c.get("why_not_a_checker", "").strip(),
                            f"{c.get('check', '')[:40]} has no reason")

    def test_it_refuses_to_report_doing_too_much(self):
        """One direction only. Doing too much is `scope`, and it already asks
        once per task -- a second mechanism asking it is noise with a quorum."""
        joined = " ".join(self.lens["anti_patterns"])
        self.assertIn("scope", joined)


class NoCheckIsNamedByItsPosition(unittest.TestCase):
    """`lens_brief` renders `checks` as unnumbered bullets.

    So a check that says "check 5 wins" asks the reader to count, and the count
    is not in front of them. It survived one revision of request-fidelity and
    then broke: inserting a check at the top moved all six, and six references
    across the `why` and one other check pointed one place to the left.

    Every one of the twelve shipped lenses passes this today, so it costs
    nothing and holds the door.
    """

    #: `check 5`, `checks 2-3`, `第三條`. Deliberately not a bare digit: checks
    #: quote real numbers -- "88 條之中 34 條", "113 題入面 5 條" -- and a rule
    #: that refuses those is a rule about arithmetic, not about references.
    ORDINAL = re.compile(r"check\s*#?\s*\d|第\s*[一二三四五六七八九十\d]+\s*條\s*check")

    def test_no_shipped_lens_points_at_a_position(self):
        """Asserted against what `lens_brief` prints, not against the file.

        The reference is only wrong because of how it renders: the JSON is an
        ordered list, so "check 5" is true of the data and false on the page.
        Reading the file would pass a lens that numbered its own bullets.
        """
        good, _ = review.lens_files(ROOT)
        self.assertTrue(good)
        for slug, l in good.items():
            brief = review.lens_brief(l, slug=slug, task="t")
            hit = self.ORDINAL.search(brief)
            self.assertIsNone(
                hit, f"{slug} names a check by position: "
                     f"{hit.group(0) if hit else ''!r}")



class TheEvidenceALensCitesIsReal(unittest.TestCase):
    """The public lens cites a reproducible demo, not unpublished history.

    A commit hash alone is insufficient: the cited snapshot must also contain
    the measured source and evidence files, with matching implementation hashes.
    """

    LENS = "near-miss"

    #: Bounded to `commit`: claim IDs use the same alphabet but are not objects.
    CITED = re.compile(r"commit\s+([0-9a-f]{7,40})")

    def setUp(self):
        good, _ = review.lens_files(ROOT)
        self.assertIn(self.LENS, good, "the lens is not loadable")
        self.lens = good[self.LENS]

    def test_every_commit_it_cites_resolves(self):
        blob = json.dumps(self.lens, ensure_ascii=False)
        cited = sorted(set(self.CITED.findall(blob)))
        self.assertTrue(cited, "the evidence cites no commit at all")

        for sha in cited:
            r = subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                               cwd=ROOT, capture_output=True)
            self.assertEqual(r.returncode, 0,
                             f"{self.LENS} cites commit {sha}, which this repo "
                             f"does not have")

    def test_public_evidence_matches_the_cited_implementation(self):
        evidence = self.lens["public_evidence"]
        revision = evidence["commit"]

        def at_revision(path):
            result = subprocess.run(["git", "show", f"{revision}:{path}"],
                                    cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0,
                             f"the cited snapshot does not contain {path}")
            return result.stdout

        self.assertTrue(at_revision(evidence["source"]))
        self.assertTrue(at_revision(evidence["transcript"]))
        measured = json.loads(at_revision(evidence["manifest"]))
        self.assertIs(measured["verified"], True)
        self.assertIn(evidence["source"], measured["implementation_sha256"])
        for path, expected in measured["implementation_sha256"].items():
            self.assertEqual(hashlib.sha256(at_revision(path)).hexdigest(), expected,
                             f"recorded evidence does not match {path}")

    def test_each_check_says_why_it_is_not_a_checker(self):
        """Same bar as request-fidelity: SPEC §4.7 forbids the checker, so a
        check that cannot say why it is not one belongs in a checker."""
        for c in self.lens["checks"]:
            self.assertIsInstance(c, dict)
            for field in ("why_not_a_checker", "from"):
                self.assertTrue(c.get(field, "").strip(),
                                f"{c.get('check', '')[:40]} has no {field}")


class AParkedCheckMovedRatherThanVanished(unittest.TestCase):
    """`prevention` held `Request fit` under `why_here: 出處文件冇對應嘅 lens`.

    Retiring it from a 115-check lens and building a 5-check one is a coverage
    change, and the way that goes wrong is the check quietly leaving. So both
    ends are asserted: gone from where it was parked, and named as the source
    of where it went.
    """

    def test_it_is_no_longer_parked_in_prevention(self):
        good, _ = review.lens_files(ROOT)
        parked = [c for c in good["prevention"]["checks"]
                  if isinstance(c, dict)
                  and c.get("check", "").startswith("Request fit:")]
        self.assertEqual(parked, [], "still parked, so it is asked twice")

    def test_the_lens_it_moved_to_names_it(self):
        good, _ = review.lens_files(ROOT)
        self.assertIn("R-eacb12dc", good["near-miss"]["source"],
                      "a check that moved with no forwarding address is a "
                      "check that was deleted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
