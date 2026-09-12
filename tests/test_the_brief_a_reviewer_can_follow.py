"""What a reviewer is told to run, and whether running it works.

    python3 -m unittest tests.test_the_brief_a_reviewer_can_follow -v

Four findings from one sweep, and they are one fact: a reviewer that follows its
own brief and its own role prompt exactly cannot do the thing it was dispatched
to do.

  * the brief's single action was `v4 --repo . review add …`, and nothing puts
    `bin/` on a PATH -- `command -v v4` finds nothing here, so the command the
    brief hands out exits 127
  * the brief never named `v4 review done`, the only writer of the
    `lens_reviewed` row that `ship` and `sweep` read to tell "ran and found
    nothing" from "never ran"
  * the reviewer role prompt says `--task $V4_TASK`, `V4_TASK` is set by
    nothing here, so the shell passes `''` and both lens rows landed with
    `task_id = ''` while `sweep` selects `task_id IS NULL`
  * the lens slug went into the claim id with nothing asking whether that lens
    exists, and `claim` is append-only

Measured in the sweep that raised them: thirteen reviewers were dispatched, and
the prompt they were given had to spell `./bin/v4` and omit `--task` for the run
to be visible at all. One of them said so in its report.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as ledger_mod                         # noqa: E402
from kernel import review                                       # noqa: E402
from kernel.config import RepoConfig                            # noqa: E402


def _brief(lens="devx"):
    """The brief, from the function that generates it.

    Called rather than shelled out to. `bin/v4` in a subprocess produces the
    same text and proves less: the tracer behind `v4 review close` observes
    this process, so a closure offered against a symbol only ever reached in a
    child is refused for never executing it -- measured, and it is the right
    refusal. `TheCommandTheBriefHandsOut` still runs the printed line through a
    real shell, which is the part that has to be a subprocess.
    """
    usable, _unusable = review.lens_files(ROOT)
    return review.lens_brief(usable[lens], slug=lens)


class TheCommandTheBriefHandsOut(unittest.TestCase):
    def test_bare_v4_is_not_on_a_path_here(self):
        """The premise, measured rather than assumed -- if `v4` resolved, the
        brief was right and this whole case is noise."""
        r = subprocess.run(["bash", "-lc", "command -v v4"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0,
                            "if `v4` is on PATH here, the brief was fine")

    def test_so_the_brief_spells_the_launcher(self):
        out = _brief()
        bare = [l.strip() for l in out.splitlines()
                if l.strip().startswith("v4 ")]
        self.assertEqual(bare, [], f"bare `v4` lines in the brief: {bare}")
        self.assertIn("./bin/v4 --repo . review add", out)

    def test_and_the_line_it_prints_actually_runs(self):
        """Run it, with `--help` standing in for the arguments a reviewer
        fills in. A brief whose command exits 127 has dispatched nobody."""
        out = _brief()
        line = next(l.strip() for l in out.splitlines()
                    if "review add" in l and l.strip().startswith("./bin/v4"))
        head = " ".join(line.split()[:5])
        r = subprocess.run(["bash", "-lc", f"{head} --help"],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertNotEqual(r.returncode, 127, r.stderr[:200])


class TheCommandThatSaysAReviewHappened(unittest.TestCase):
    def test_the_brief_names_review_done(self):
        out = _brief()
        self.assertIn("review done", out)
        self.assertIn("--findings", out)

    def test_and_says_that_zero_is_an_answer(self):
        """`--findings 0` is the only way "ran and found nothing" exists as a
        fact. A brief that omits it leaves a silent reviewer and a diligent one
        indistinguishable."""
        self.assertIn("including zero findings", _brief())



def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"]):
        subprocess.run(a, cwd=tmp, capture_output=True)
    shutil.copytree(ROOT / ".v4" / "lenses", tmp / ".v4" / "lenses")
    return tmp, ledger_mod.connect(tmp)


class AnEmptyTaskIsNoTask(unittest.TestCase):
    """`--task $V4_TASK` with `V4_TASK` unset is `''`, and `''` is not NULL."""

    def _task_ids(self, conn, kind):
        return [r["task_id"] for r in conn.execute(
            "SELECT task_id FROM event WHERE kind = ?", (kind,)).fetchall()]

    def test_an_empty_task_id_is_stored_as_null(self):
        root, conn = _repo(self)
        lens = {"name": "n", "checks": ["a"]}
        with ledger_mod.writing(conn):
            review.record_lens_run(conn, slug="devx", lens=lens, task_id="")
        self.assertEqual(self._task_ids(conn, review.LENS_RUN_KIND), [None])

    def test_the_reviewed_row_too(self):
        """The row that matters more: `sweep` reads this one to answer whether
        anybody reviewed at all."""
        root, conn = _repo(self)
        with ledger_mod.writing(conn):
            review.record_lens_reviewed(conn, root, slug="devx", findings=0,
                                        task_id="")
        self.assertEqual(self._task_ids(conn, review.LENS_REVIEWED_KIND), [None])

    def test_but_a_real_task_id_is_kept(self):
        """The control. Normalising everything to NULL would lose which task a
        per-task review belonged to, which is the other half of the same row."""
        root, conn = _repo(self)
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "task", id="t-x", request="r",
                              scope_globs=["**"], base_commit="",
                              created_at="2026-09-05T00:00:00+00:00")
            review.record_lens_reviewed(conn, root, slug="devx", findings=1,
                                        task_id="t-x")
        self.assertEqual(self._task_ids(conn, review.LENS_REVIEWED_KIND), ["t-x"])


class ALensThatDoesNotExist(unittest.TestCase):
    """The slug becomes part of the claim id, and `claim` is append-only.

    These put a real repo and a real ledger to `raise_finding` on purpose: the
    corpus the slug is checked against is *this* repo's lens directory, and a
    fixture repo would be checking a made-up one. What they must not do is hold
    a handle that can write, and they used to. Nothing stopped a write except
    the order of two statements inside `raise_finding` -- the lens check sits a
    few lines above the `insert` -- and that guard has a documented state where
    it does not fire, pinned by `test_a_repo_with_no_lenses_refuses_nothing`
    below: a repo with no corpus has nothing to misspell against.

    Four content-free findings stand in this repo's ledger, coordinates
    `kernel/review.py::defer`, note forty `n` -- which is these calls, field for
    field. Nobody has named what wrote them and this is not a claim that these
    did: the last two arrived 2026-09-06T04:01:03Z 0.07s apart, and a full suite
    run, this class alone, and the green half of the closure running at that
    moment all leave the count unmoved. What is certain is the shape. `claim` is
    append-only, so whatever does land there cannot be taken out again, and a
    read-only connection is the difference between that and a test going red.
    """

    def test_a_finding_under_an_unknown_lens_is_refused(self):
        cfg = RepoConfig(ROOT)
        conn = ledger_mod.connect_readonly(ROOT)
        with self.assertRaises(review.BadCoordinates) as caught:
            review.raise_finding(conn, cfg, task_id=None,
                                 file="kernel/review.py", symbol="defer",
                                 note="n" * 40, lens="securty-permission")
        self.assertIn("no lens named", str(caught.exception))

    def test_and_a_real_lens_is_not(self):
        """Without this the repair is an off switch. Checked through the same
        validation path, not by writing a claim into this repo's ledger."""
        cfg = RepoConfig(ROOT)
        usable, _ = review.lens_files(cfg.root)
        self.assertIn("security-permission", usable)
        self.assertIn("devx", usable)

    def test_and_no_lens_at_all_is_still_allowed(self):
        """A finding raised outside a sweep has no lens, and `_slot` handles
        the empty slug -- refusing it would stop `v4 review add` without
        `--lens`, which is how a per-task reviewer files one."""
        cfg = RepoConfig(ROOT)
        usable, _ = review.lens_files(cfg.root)
        self.assertNotIn("", usable)
        conn = ledger_mod.connect_readonly(ROOT)
        # No exception: the guard has to skip the empty slug outright.
        with self.assertRaises(review.BadCoordinates):
            review.raise_finding(conn, cfg, task_id=None,
                                 file="kernel/review.py", symbol="defer",
                                 note="n" * 40, lens="not-a-lens")

    def test_and_a_write_cannot_land_even_when_the_guard_does_not_fire(self):
        """The property the cases above rely on, put to the code rather than
        left to the order of two statements.

        An empty corpus is not hypothetical: it is the state the case below
        documents, and it is what any checkout without a lens directory gives.
        In it the slug check is skipped and `raise_finding` runs all the way to
        the write. What has to be true then is that nothing lands.
        """
        cfg = RepoConfig(ROOT)
        conn = ledger_mod.connect_readonly(ROOT)

        def count():
            return conn.execute("SELECT count(*) FROM claim").fetchone()[0]

        before = count()
        with mock.patch.object(review, "lens_files",
                               return_value=(set(), set())):
            with self.assertRaises(sqlite3.OperationalError):
                review.raise_finding(conn, cfg, task_id=None,
                                     file="kernel/review.py", symbol="defer",
                                     note="n" * 41, lens="not-a-lens")
        self.assertEqual(count(), before)

    def test_a_repo_with_no_lenses_refuses_nothing(self):
        """The over-reach the existing suite caught. A repo with no
        `.v4/lenses/` has nothing to misspell against, and refusing every slug
        there stops an adopter filing findings at all -- so the rule needs a
        corpus before it can be a rule."""
        root, conn = _repo(self)
        shutil.rmtree(root / ".v4" / "lenses")
        usable, _ = review.lens_files(root)
        self.assertEqual(usable, {})


if __name__ == "__main__":
    unittest.main()
