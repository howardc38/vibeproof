"""Three gates that did not cover themselves.

    python3 -m unittest tests.test_what_guards_the_guards -v

  * the export seals a segment when it has *already* outgrown the limit, because
    the size is asked before a rewrite that has no bound. The one sealed segment
    on disk here is 49,233,070 bytes against a `SEAL_AT` of 25,000,000 -- 1.97x,
    while the constant's comment says sealing "keeps every file well under" the
    50 MiB it cites
  * `hooks/**` was in no protected glob, so weakening the programs that
    *enforce* cost a `scope widen` sentence where weakening the programs that
    *decide* costs a signature
  * the third proposed widening `run_without_silent_skips.ENVIRONMENT`. Tried
    and reverted: this repo had already decided, and pinned the decision with
    its argument -- an opt-out that reads as "the world was smaller" makes the
    sole oracle for every `test` claim red forever, for a question nobody
    withheld. The case below now pins the decision rather than the change.
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
sys.path.insert(0, str(ROOT / "tests"))

import run_without_silent_skips as runner_mod                   # noqa: E402
from kernel import ledger as ledger_mod                         # noqa: E402
from kernel.analysis.subject_files import matches               # noqa: E402


class WhatTheSealPromises(unittest.TestCase):
    """A sweep read `SEAL_AT`'s comment as a guarantee that every segment is
    well under 50 MiB. The mechanism does not promise that, and this pins what
    it does promise -- because the repair was tried here and reverted."""

    def test_the_size_is_asked_before_an_unbounded_write(self):
        """Which is why the sealed segment on disk is 1.97x the number: a file
        under the limit passes, then the rewrite adds every row since the last
        seal."""
        src = (ROOT / "kernel" / "ledger.py").read_text(encoding="utf-8")
        body = src.split("def export_jsonl")[1].split("\ndef ")[0]
        self.assertLess(body.index("_seal_if_full"), body.index('open("w"'),
                        "the check has to come before the write, or the skip "
                        "counts it reads are gone")

    def _export(self, first_row=None, rows=(), pad=0):
        """An export file on disk, shaped the way `_seal_if_full` reads one."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = tmp / "ledger_export.jsonl"
        lines = ([json.dumps(first_row)] if first_row else []) + [
            json.dumps(r) for r in rows]
        if pad:
            lines.append(json.dumps({"_table": "pad", "x": "y" * pad}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_and_the_open_file_carries_what_a_reader_needs(self):
        """The reason sealing after a write is a format change, not a one-line
        fix: `skip` and `starts_after` live in the open file's first row, so
        renaming it away leaves the next export re-exporting everything.

        Put to `_seal_if_full` rather than read out of its source. It took a
        `seal_at` from the start, so a small one asks the real question."""
        head = {"_table": ledger_mod.SEGMENT_TABLE,
                "skip": {"attempt": 7}, "starts_after": "abc123"}
        p = self._export(first_row=head, rows=[{"_table": "attempt"}])
        skip, starts_after = ledger_mod._seal_if_full(p, seal_at=10_000_000)
        self.assertEqual(skip, {"attempt": 7},
                         "the counts earlier segments hold were dropped")
        self.assertEqual(starts_after, "abc123",
                         "the head those segments ended on was dropped")
        self.assertTrue(p.is_file(), "a file under the limit was sealed")

    def test_a_file_over_the_limit_is_sealed_and_its_rows_counted(self):
        """The other half: renamed away, and what it held carried forward, so
        the next export writes the rest rather than all of it again."""
        p = self._export(rows=[{"_table": "attempt"}, {"_table": "attempt"},
                               {"_table": "claim"},
                               {"_table": ledger_mod.ANCHOR_TABLE,
                                "head_hash": "deadbeef"}],
                         pad=4000)
        skip, starts_after = ledger_mod._seal_if_full(p, seal_at=1_000)
        self.assertFalse(p.is_file(), "the full file was left open")
        self.assertEqual(skip.get("attempt"), 2)
        self.assertEqual(skip.get("claim"), 1)
        self.assertEqual(starts_after, "deadbeef")
        self.assertTrue((p.parent / (p.name + ".0001")).is_file(),
                        "sealed under a name the segment readers do not find")

    def test_no_file_at_all_is_not_an_error(self):
        """`if not p.is_file()` -- the first export in a repo's life. Read out
        of the source before; asked here."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        skip, starts_after = ledger_mod._seal_if_full(
            tmp / "never_written.jsonl", seal_at=1_000)
        self.assertEqual(skip, {})
        self.assertEqual(starts_after, ledger_mod.GENESIS)

    def test_what_it_does_promise_still_holds(self):
        """One export, one seal, and the rows survive it."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                  ["git", "config", "user.name", "t"]):
            subprocess.run(a, cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        conn = ledger_mod.connect(tmp)
        when = "2026-09-05T00:00:00+00:00"
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "task", id="t", request="r" * 2000,
                              scope_globs=["**"], base_commit="",
                              created_at=when)
        out = tmp / ".v4" / "export.jsonl"
        first = ledger_mod.export_jsonl(conn, out, tmp)
        self.assertGreater(first, 0)
        ledger_mod.export_jsonl(conn, out, tmp, seal_at=1)
        # One seal, and the rows are in the sealed file rather than lost: the
        # second export wrote nothing new, so everything the first wrote has to
        # be findable in the segment it was renamed into.
        segs = ledger_mod.segment_files(out)
        self.assertEqual(len(segs), 1)
        rows = sum(1 for line in segs[0].read_text(encoding="utf-8").splitlines()
                   if line.strip())
        self.assertGreaterEqual(rows, first)


class TheProgramsThatEnforce(unittest.TestCase):
    def test_hooks_are_protected(self):
        globs = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8")
        )["protected_paths"]
        self.assertTrue(matches("hooks/bash_guard.py", globs))
        self.assertTrue(matches("hooks/_framework.py", globs))

    def test_and_the_programs_that_decide_still_are(self):
        globs = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8")
        )["protected_paths"]
        for p in ("checkers/scope.py", "detectors/fail_closed.py",
                  ".v4/config.json", ".github/workflows/v4.yml"):
            self.assertTrue(matches(p, globs), p)

    def test_and_ordinary_code_is_not(self):
        """A protected list that covers everything is a list nobody can widen
        past, which is the same as no list."""
        globs = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8")
        )["protected_paths"]
        for p in ("kernel/cli.py", "tests/test_kernel.py", "docs/SPEC.md"):
            self.assertFalse(matches(p, globs), p)


class ASkipThisRepoAlreadyDecidedAbout(unittest.TestCase):
    """The sweep proposed widening `ENVIRONMENT` to catch three more reasons.
    Tried, and reverted: this repo had decided, and the decision is pinned in
    `test_which_repo_which_task_which_environment` with its argument -- an
    opt-out that reads as "the world was smaller" makes the sole oracle red
    forever for a question nobody withheld."""

    def test_the_opt_out_stays_quiet(self):
        for reason in ("V4_ADOPTER_REPO names no checkout",
                       "no facts table can be identified in a checkout named x",
                       "commit a9ae5fb not in adopter_a any more"):
            self.assertIsNone(runner_mod.ENVIRONMENT.search(reason), reason)

    def test_and_a_real_absence_is_still_loud(self):
        for reason in ("go is not installed", "no such binary",
                       "requires a network"):
            self.assertTrue(runner_mod.ENVIRONMENT.search(reason), reason)

    # `test_the_decision_is_written_beside_the_pattern` was here and asserted
    # that the phrase "chose not to ask" appears in `run_without_silent_skips`.
    # It pinned a wording and nothing else: the decision it is about is enforced
    # by the two cases above, which run `ENVIRONMENT` against the three reasons
    # this repo chose not to ask and against three it cannot. What goes with it
    # is the guarantee that the sentence stays -- and a sentence is not what
    # kept the oracle green; the pattern is, and that is asked by running it.
    #
    # The other source read in this file, at `test_the_size_is_asked_before_an_
    # unbounded_write`, stays: it is accepted in `.v4/test-shape_baseline.json`
    # with its reason, which is that nothing executable separates the two
    # orderings until an export exceeds 25 MB.


if __name__ == "__main__":
    unittest.main()
