"""Four commands that stop the reader guessing.  A2, A5 and A3.

    python3 -m unittest tests.test_the_answer_before_a_signature -v

Three questions this framework could not answer about itself:

  *Is what judges this repo what the framework ships?*  `.v4/installed.json`
  records the bytes that were shipped, so it tells an edit from an untouched
  copy and says nothing about the framework having moved on -- which is how an
  adopter actually goes stale.  Measured on the reference adopter: 99 current,
  18 behind, 2 missing, 4 edited there.

  *Where is this open task?*  One ledger serves every worktree, so several
  tasks are open in several directories; nothing recorded which.  Measured:
  eight worktrees, five open tasks, and the pairing lived in a naming
  convention nothing reads.

  *Does this claim actually want a signature?*  `risk waiting` listed every
  non-terminal claim under a heading that said a signature would unblock them.
  Most want a re-run.
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

from kernel import config, install, ledger, lifecycle, risk, state  # noqa: E402


def _git(d: Path):
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)


class WhatInstallWouldBringOver(unittest.TestCase):
    """`v4 install --check`: four answers, and each one a different act."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src, self.dst = self.tmp / "fw", self.tmp / "repo"
        for d in (self.src, self.dst):
            (d / ".v4").mkdir(parents=True)
        _git(self.dst)
        (self.src / "checkers").mkdir()
        (self.src / ".v4" / "checkers.json").write_text(json.dumps({
            "alpha": {"path": "checkers/alpha.py"},
            "beta": {"path": "checkers/beta.py"},
            "gamma": {"path": "checkers/gamma.py"},
            "delta": {"path": "checkers/delta.py"},
        }))
        for name in ("alpha", "beta", "gamma", "delta"):
            (self.src / "checkers" / f"{name}.py").write_text(f"# {name} v2\n")
        self.kinds = {n: {"checker": n} for n in
                      ("alpha", "beta", "gamma", "delta")}
        (self.src / ".v4" / "claim_kinds.json").write_text(json.dumps(self.kinds))
        (self.src / ".v4" / "detectors.json").write_text("{}")
        (self.src / "detectors").mkdir()
        (self.src / "hooks").mkdir()
        (self.src / ".v4" / "lenses").mkdir()
        (self.dst / "checkers").mkdir()

    def _report(self):
        return dict(install.copy_files(self.src, self.dst, self.kinds, dry=True))

    def test_the_four_answers(self):
        # current: same bytes as the framework, and recorded as shipped.
        (self.dst / "checkers" / "alpha.py").write_text("# alpha v2\n")
        # behind: what was shipped, and the framework has moved on since.
        (self.dst / "checkers" / "beta.py").write_text("# beta v1\n")
        # yours: not what was shipped -- somebody here changed it.
        (self.dst / "checkers" / "gamma.py").write_text("# gamma, ours\n")
        # delta: absent.
        # The manifest holds what was *shipped*, which for `beta` is the old
        # bytes still on disk here -- that is what "behind" means. `gamma`'s
        # entry is what was shipped and is not what is there now, which is what
        # "yours" means.
        from kernel import hashing
        (self.dst / ".v4" / "installed.json").write_text(json.dumps({
            "checkers/alpha.py": hashing.file_sha(self.dst / "checkers/alpha.py"),
            "checkers/beta.py": hashing.file_sha(self.dst / "checkers/beta.py"),
            "checkers/gamma.py": hashing.file_sha(self.src / "checkers/gamma.py"),
        }))
        got = self._report()
        self.assertEqual(got["checkers/alpha.py"], install.CURRENT)
        self.assertEqual(got["checkers/beta.py"], install.BEHIND)
        self.assertEqual(got["checkers/gamma.py"], install.YOURS)
        self.assertEqual(got["checkers/delta.py"], install.MISSING)

    def test_an_edited_file_that_is_also_behind_reads_as_edited(self):
        """Both are true and only one is actionable: `install` will not touch
        it either way, so the answer a reader needs is why it is being left."""
        (self.dst / "checkers" / "beta.py").write_text("# beta, ours\n")
        (self.dst / ".v4" / "installed.json").write_text(
            json.dumps({"checkers/beta.py": "not the sha of anything here"}))
        self.assertEqual(self._report()["checkers/beta.py"], install.YOURS)

    def test_dry_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.dst.rglob("*") if p.is_file()}
        self._report()
        after = {p: p.read_bytes() for p in self.dst.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def _stranded(self):
        """A program this framework shipped once and no longer ships.

        `epsilon` is in the adopter's manifest with the bytes that are on disk
        there, and is in neither `.v4/checkers.json` nor the kind table -- the
        state a kind trim leaves behind.
        """
        from kernel import hashing
        (self.dst / "checkers" / "epsilon.py").write_text("# epsilon v1\n")
        (self.dst / ".v4" / "installed.json").write_text(json.dumps({
            "checkers/epsilon.py": hashing.file_sha(
                self.dst / "checkers" / "epsilon.py")}))

    def test_a_program_the_framework_stopped_shipping_is_named(self):
        """It had no status at all. `install` only added and updated, and the
        carry-forward re-recorded the file every run, so the manifest went on
        saying this framework owns a file it would never send again."""
        self._stranded()
        self.assertEqual(self._report()["checkers/epsilon.py"], install.RETIRED)

    def test_the_others_are_not_swept_up_with_it(self):
        """The first version of this read `now`, which `put` fills only on the
        writing path -- so under `--check` every program came back retired.
        Measured against a real adopter manifest: 20 detectors reported, 5 true.
        """
        self._stranded()
        got = self._report()
        for name in ("alpha", "beta", "gamma", "delta"):
            self.assertNotEqual(got.get(f"checkers/{name}.py"), install.RETIRED,
                                name)

    def test_a_program_this_repo_edited_is_left_alone(self):
        """`yours` outranks it. Retiring a file somebody changed would delete
        their work on the strength of a decision made somewhere else."""
        (self.dst / "checkers" / "epsilon.py").write_text("# epsilon, ours\n")
        (self.dst / ".v4" / "installed.json").write_text(json.dumps(
            {"checkers/epsilon.py": "the sha of what we shipped, not this"}))
        self.assertNotIn("checkers/epsilon.py", self._report())

    def test_a_detector_for_a_framework_only_kind_is_retired(self):
        """Its source is still here, and an adopter is still never getting it.
        `applies_to: framework` is a decision about who, not about what this
        repo happens to contain today."""
        from kernel import hashing
        (self.src / "detectors" / "always_wiring.py").write_text(
            'print("V4-CLAIM: kind=dead-wiring")\n')
        (self.src / ".v4" / "claim_kinds.json").write_text(json.dumps(
            dict(self.kinds, **{"dead-wiring": {"checker": "dead-wiring",
                                                "applies_to": "framework"}})))
        (self.dst / "detectors").mkdir()
        shutil.copy2(self.src / "detectors" / "always_wiring.py",
                     self.dst / "detectors" / "always_wiring.py")
        (self.dst / ".v4" / "installed.json").write_text(json.dumps({
            "detectors/always_wiring.py": hashing.file_sha(
                self.dst / "detectors" / "always_wiring.py")}))
        self.assertEqual(self._report()["detectors/always_wiring.py"],
                         install.RETIRED)

    def test_a_program_held_back_only_for_now_is_not(self):
        """A kind held back because this repo has no file its checker reads
        comes back the day that changes, and `test_a_kind_held_back_does_not_
        lose_its_checker_s_record` is about that file keeping its record. The
        first version of retirement deleted it, trading one repair for another.
        """
        from kernel import hashing
        (self.dst / "checkers" / "delta.py").write_text("# delta v2\n")
        (self.dst / ".v4" / "installed.json").write_text(json.dumps({
            "checkers/delta.py": hashing.file_sha(
                self.dst / "checkers" / "delta.py")}))
        # `delta` is in the framework and not among the kinds this run installs.
        rows = dict(install.copy_files(
            self.src, self.dst, {k: v for k, v in self.kinds.items()
                                 if k != "delta"}, dry=True))
        self.assertNotEqual(rows.get("checkers/delta.py"), install.RETIRED)

    def test_retiring_removes_the_file_and_the_claim_on_it(self):
        """The wet path: the file goes, and the manifest stops naming it."""
        self._stranded()
        out = dict(install.copy_files(self.src, self.dst, self.kinds))
        self.assertEqual(out["checkers/epsilon.py"], "retired")
        self.assertFalse((self.dst / "checkers" / "epsilon.py").exists())
        manifest = json.loads(
            (self.dst / ".v4" / "installed.json").read_text())
        self.assertNotIn("checkers/epsilon.py", manifest)


class BothNewCommandsReachAnAdopter(unittest.TestCase):
    """A command that stays in this repo is a command the adopter has to
    reinvent. `install` walks `.claude/commands/*.md`, so this holds for any
    future one too -- it is asserted against the directory, not a list."""

    def test_every_command_this_repo_has(self):
        want = {f".claude/commands/{p.name}"
                for p in (ROOT / ".claude" / "commands").glob("*.md")}
        self.assertIn(".claude/commands/sweep.md", want)
        self.assertIn(".claude/commands/wave.md", want)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir(parents=True)
        _git(tmp)
        got = {rel for rel, _how in install.copy_files(ROOT, tmp, {}, dry=True)}
        self.assertTrue(want <= got, want - got)


class WhichTreeThisTaskIsIn(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.tmp)

    def test_opening_a_task_records_where(self):
        lifecycle.open_task(self.conn, self.cfg, task_id="t-1", request="r",
                            scope_globs=["**"])
        self.assertEqual(lifecycle.worktree_of(self.conn, "t-1"),
                         str(self.tmp.resolve()))

    def test_a_task_opened_before_this_existed_says_so(self):
        """`?` rather than the current tree's path: a guess printed beside four
        task ids reads as a fact about all four."""
        ledger.insert(self.conn, "task", id="t-old", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")
        self.assertIsNone(lifecycle.worktree_of(self.conn, "t-old"))


class WhatToDoAboutAClaimThatIsNotTerminal(unittest.TestCase):
    """`risk waiting` offered one exit -- the expensive one -- for every state."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"test": {"question_template": "q", "checker": "test",
                      "staleness": "repo"},
             "review-finding": {"question_template": "q",
                                "checker": "review-finding",
                                "staleness": "subject"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "mod.py").write_text("WAIT = frozenset()\n\n\ndef run():\n    return 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.tmp)
        ledger.insert(self.conn, "task", id="t-live", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")

    def _claim(self, cid, *, kind="review-finding", file=None, symbol=None,
               task="t-live"):
        ledger.insert(self.conn, "claim", id=cid, task_id=task, kind=kind,
                      question="q", subject_refs=[], checker=kind,
                      origin="review", file=file, symbol=symbol,
                      created_at="2026")
        return self.conn.execute("SELECT * FROM claim WHERE id = ?",
                                 (cid,)).fetchone()

    def test_stale_is_a_re_run_and_says_a_signature_would_mean_nothing(self):
        what, why = risk.route(self.conn, self.cfg, self._claim("c1"), state.STALE)
        self.assertEqual(what, risk.RERUN)
        self.assertIn("v4 check", why)

    def test_a_checker_that_crashed_is_not_a_verdict_to_sign(self):
        for st in (state.CHECKER_ERROR, state.CHECKER_TAMPERED,
                   state.SUBJECT_MOVED, state.TIMEOUT):
            what, _why = risk.route(self.conn, self.cfg,
                                    self._claim(f"c-{st}"), st)
            self.assertEqual(what, risk.FIX_FIRST, st)

    def test_unsupported_is_the_one_a_repo_signature_is_for(self):
        what, why = risk.route(self.conn, self.cfg, self._claim("c2"),
                               state.UNSUPPORTED)
        self.assertEqual(what, risk.SIGN_REPO)
        self.assertIn("lapses", why)

    def test_a_symbol_no_frame_can_be_named_after_is_still_not_a_red_green(self):
        """And is not a signature either, which is what this used to say.

        The expectation moved because the code did: `SIGN_ONLY` for this row
        was written on 2026-08-20, eleven days after `review.bind_text_change`
        landed, and it never asked whether the cheaper exit existed. It does --
        `checkers/review_finding._text_closure` closes a finding whose repair
        is a sentence and needs no frame at all -- so "signature is the only
        exit" was false for the whole population text closure was built for,
        on the one constant that promises to offer a signature only where it
        is the only one.
        """
        row = self._claim("c3", file="mod.py", symbol="WAIT")
        what, why = risk.route(self.conn, self.cfg, row, state.OPEN)
        self.assertEqual(what, risk.CLOSE_TEXT)
        self.assertIn("redgreen", why)
        self.assertIn("--gone", why)

    def test_a_file_this_repo_does_not_carry_still_leaves_only_a_signature(self):
        """The floor under the line above. `_text_closure` reads the file at
        the parent commit and at HEAD, so a finding naming a path that is not
        here has no text to move and really is down to a signature."""
        row = self._claim("c3b", file="gone/away.md", symbol="thing")
        what, _why = risk.route(self.conn, self.cfg, row, state.OPEN)
        self.assertEqual(what, risk.SIGN_ONLY)

    def test_a_symbol_a_test_can_enter_is_a_red_green(self):
        row = self._claim("c4", file="mod.py", symbol="run")
        self.assertEqual(risk.route(self.conn, self.cfg, row, state.OPEN)[0],
                         risk.CLOSE_REDGREEN)

    def test_a_claim_about_the_tree_has_no_symbol_to_execute(self):
        row = self._claim("c5", kind="test")
        what, why = risk.route(self.conn, self.cfg, row, state.OPEN)
        self.assertEqual(what, risk.MAKE_IT_PASS)
        self.assertIn("names no symbol", why)

    def test_an_ended_task_cannot_be_worked_on_however_good_the_advice(self):
        ledger.insert(self.conn, "task", id="t-dead", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")
        ledger.insert(self.conn, "event", task_id="t-dead", claim_id=None,
                      kind="abandoned", actor="human", payload={},
                      created_at="2026")
        row = self._claim("c6", file="mod.py", symbol="run", task="t-dead")
        self.assertEqual(risk.route(self.conn, self.cfg, row, state.OPEN)[0],
                         risk.TASK_ENDED)

    def test_one_already_signed_is_not_asked_for_a_second_signature(self):
        """A task-scoped signature covers the bytes it saw, so it lapses when
        the tree moves and the claim reads OPEN again. On a task that has
        ended, nothing re-asks it -- so this read as 'sign it' about a decision
        already in the chain."""
        row = self._claim("c7", file="mod.py", symbol="run")
        risk.accept(self.conn, self.cfg, claim_id="c7", kind="unprovable",
                    why="there is no oracle for this and the reason is long enough",
                    require_tty=False)
        what, why = risk.route(self.conn, self.cfg, row, state.OPEN)
        self.assertEqual(what, risk.ALREADY_SIGNED)
        self.assertIn(".v4/risks/c7.json", why)


class TheListDefaultsToWorkSomebodyCanStillDo(unittest.TestCase):
    """553 claims, 234 of them on tasks abandoned in bring-up. A list that long
    is one nobody opens -- the failure `doctor` was fixed for twice."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"test": {"question_template": "q", "checker": "test",
                      "staleness": "repo"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.tmp)
        for tid, ended in (("t-live", False), ("t-gone", True)):
            ledger.insert(self.conn, "task", id=tid, request="r",
                          scope_globs=["**"], base_commit="x", created_at="2026")
            if ended:
                ledger.insert(self.conn, "event", task_id=tid, claim_id=None,
                              kind="shipped", actor="human", payload={},
                              created_at="2026")
            ledger.insert(self.conn, "claim", id=f"c-{tid}", task_id=tid,
                          kind="test", question="q", subject_refs=[],
                          checker="test", origin="derive", created_at="2026")

    def test_by_default_only_the_ones_still_open(self):
        got = {r["id"] for r, _st in risk.waiting(self.conn, self.cfg)}
        self.assertEqual(got, {"c-t-live"})

    def test_and_the_ended_ones_are_there_when_asked_for(self):
        got = {r["id"] for r, _st in risk.waiting(self.conn, self.cfg, ended=True)}
        self.assertEqual(got, {"c-t-live", "c-t-gone"})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheTestThatClosedAFindingStillBeingThere(unittest.TestCase):
    """A rename breaks the binding, and three rows all look somewhere else.

    Measured here: `tests/test_sweep_repairs.py` became
    `tests/test_repairs_that_arrived_together.py` in the same commit that closed
    195 findings -- same classes, same test names, new path -- and four findings
    went on naming the old one. Their next run was exit 5. `review findings`
    excludes anything with a passing attempt and these had one; `outlived
    findings` counts exit 1; `deferrals` counts deferrals.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "tests").mkdir()
        (self.tmp / "here.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        ledger.insert(self.conn, "task", id="repo-review", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")

    def _finding(self, cid, test_path):
        from kernel import review
        ledger.insert(self.conn, "claim", id=cid, task_id="repo-review",
                      kind="review-finding", question="q", subject_refs=[],
                      checker="review-finding", origin="review",
                      file="here.py", symbol="run", created_at="2026")
        review.bind_closing_test(self.conn, claim_id=cid, test_path=test_path,
                                 command="python3 {path}", parent_commit="abc1234")

    def test_a_binding_that_names_a_file_that_is_here_is_not_reported(self):
        from kernel import review
        (self.tmp / "tests" / "t_ok.py").write_text("# a test\n")
        self._finding("c-ok", "tests/t_ok.py")
        self.assertEqual(review.closing_tests_that_are_gone(self.conn, self.tmp), [])

    def test_one_that_names_a_file_that_is_gone_is(self):
        from kernel import review
        self._finding("c-gone", "tests/t_renamed.py")
        got = review.closing_tests_that_are_gone(self.conn, self.tmp)
        self.assertEqual([(c, w, t) for c, w, t, _why in got],
                         [("c-gone", "here.py::run", "tests/t_renamed.py")])
        self.assertEqual(got[0][3], "not a file in this repo")

    def test_re_binding_to_where_it_lives_now_settles_it(self):
        """The repair, and the reason only the latest binding is read: an
        earlier dangling one is history, not an open problem."""
        from kernel import review
        self._finding("c-fixed", "tests/t_renamed.py")
        (self.tmp / "tests" / "t_new_home.py").write_text("# a test\n")
        review.bind_closing_test(self.conn, claim_id="c-fixed",
                                 test_path="tests/t_new_home.py",
                                 command="python3 {path}", parent_commit="abc1234")
        self.assertEqual(review.closing_tests_that_are_gone(self.conn, self.tmp), [])

    def test_a_finding_closed_by_text_names_no_test_and_is_not_counted(self):
        """`review close --gone/--now` is the other closure and binds no path."""
        from kernel import review
        ledger.insert(self.conn, "claim", id="c-text", task_id="repo-review",
                      kind="review-finding", question="q", subject_refs=[],
                      checker="review-finding", origin="review",
                      file="docs/SPEC.md", symbol=None, created_at="2026")
        review.bind_text_change(self.conn, claim_id="c-text",
                                gone="a sentence long enough to be quoted back",
                                now="the sentence that replaces it, also long",
                                parent_commit="abc1234")
        self.assertEqual(review.closing_tests_that_are_gone(self.conn, self.tmp), [])


class TheTreeUnderTestIsTheOneAboutToBeCommitted(unittest.TestCase):
    """`v4 accept` archives the index, not HEAD.

    Found by breaking it: a new `.claude/commands/*.md` was staged, `accept
    --docs` passed, and the same file failed CI on `spec-coverage`. With `HEAD`
    the work being accepted is the work already committed, which is the one
    thing that does not need accepting.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "myrepo"
        self.repo.mkdir()
        _git(self.repo)
        (self.repo / "committed.txt").write_text("in HEAD\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.repo, check=True)

    def _archive(self):
        from kernel import accept
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        return accept.archive(self.repo, out)

    def test_a_staged_file_is_in_the_tree_under_test(self):
        (self.repo / "staged.txt").write_text("not committed yet\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=self.repo, check=True)
        tree = self._archive()
        self.assertTrue((tree / "staged.txt").is_file(),
                        "accepting HEAD accepts what was already accepted")
        self.assertTrue((tree / "committed.txt").is_file())

    def test_and_a_file_only_in_the_editor_is_not(self):
        """Stated rather than fixed: `git add -A` is the step before a commit
        anyway, and copying the dirty tree would make the answer depend on
        whatever a checker happens to leave behind."""
        (self.repo / "untracked.txt").write_text("never staged\n")
        self.assertFalse((self._archive() / "untracked.txt").is_file())

    def test_the_directory_is_named_after_the_repo(self):
        """`doctrine.render` titles the generated CLAUDE.md after the directory
        and `.v4/facts.<repo>.json` is found by it. Unpacked into `tmp/acc`,
        `registry-consistency` failed with 'CLAUDE.md is not what `v4 doctrine`
        generates' -- true of a directory with the wrong name, and nothing
        about the tree."""
        self.assertEqual(self._archive().name, "myrepo")

    def test_the_archive_is_a_repo_git_can_answer_about(self):
        """Checkers ask git for the diff. A directory of files is not a repo,
        and `scope` there reports every file as a change."""
        tree = self._archive()
        r = subprocess.run(["git", "status", "--porcelain"], cwd=tree,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "", "the archive must start clean")


class RecordingThatSeveralFindingsAreOneFact(unittest.TestCase):
    """`v4 review group`. It records a judgement; it does not make or check one.

    Measured on the sweep it exists for: 224 findings, 209 real, closed as 26
    groups -- 7.4 each, largest 13. And the groups span 3.2 files each, only 9
    of 26 live in one file, and 19 of 63 files carry findings from more than one
    group -- so no split by path, directory or lens reproduces them.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.tmp)
        ledger.insert(self.conn, "task", id="repo-review", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")
        for c in ("c1", "c2", "c3"):
            ledger.insert(self.conn, "claim", id=c, task_id="repo-review",
                          kind="review-finding", question="q", subject_refs=[],
                          checker="review-finding", origin="review",
                          file="a.py", symbol="run", created_at="2026")

    WHY = "the same table is read three times and spelled differently each time"

    def test_it_records_the_claims_it_was_given(self):
        from kernel import review
        got = review.group(self.conn, self.cfg, name="one table, three spellings",
                           claim_ids=["c1", "c2", "c3"], why=self.WHY)
        self.assertEqual(got, ["c1", "c2", "c3"])
        self.assertEqual(review.group_of(self.conn),
                         {c: "one table, three spellings" for c in ("c1", "c2", "c3")})

    def test_a_group_of_one_is_a_finding(self):
        from kernel import review
        with self.assertRaises(review.BadCoordinates):
            review.group(self.conn, self.cfg, name="n", claim_ids=["c1"],
                         why=self.WHY)

    def test_a_reason_under_the_floor_is_refused(self):
        """The same bar `engage` and `risk accept` answer to: the value of a
        grouping is entirely in whether the next person agrees with it."""
        from kernel import review
        with self.assertRaises(review.BadCoordinates):
            review.group(self.conn, self.cfg, name="one table",
                         claim_ids=["c1", "c2"], why="same thing")

    def test_a_claim_that_is_not_here_is_refused(self):
        from kernel import review
        with self.assertRaises(review.BadCoordinates) as caught:
            review.group(self.conn, self.cfg, name="one table",
                         claim_ids=["c1", "nope"], why=self.WHY)
        self.assertIn("nope", str(caught.exception))

    def test_regrouping_is_how_a_judgement_is_revised(self):
        """An append-only ledger keeps both, and readers take the last. This is
        not hypothetical: the first grouping written in this repo carried a
        reason that turned out to be false about two of its three claims."""
        from kernel import review
        review.group(self.conn, self.cfg, name="first read",
                     claim_ids=["c1", "c2"], why=self.WHY)
        review.group(self.conn, self.cfg, name="second read",
                     claim_ids=["c1", "c2", "c3"],
                     why="having read the three of them, it is one fact and not two")
        self.assertEqual(len(review.groups(self.conn)), 2)
        self.assertEqual(set(review.group_of(self.conn).values()), {"second read"})
