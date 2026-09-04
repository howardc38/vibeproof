"""Five defects an adopter reported after a day of parallel work.

    python3 -m unittest tests.test_what_an_adopter_hit_running_eight_worktrees -v

Each was reproduced here against the framework before anything was changed, and
two of the eight they reported are not in this file: one was already repaired
upstream and they were reading a copy eighteen files behind, and one is that
copy being behind, which `v4 install --check` now reports.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import layout                                   # noqa: E402
from kernel.analysis import fail_closed, pysource, route_auth  # noqa: E402


class WhatThisRepoIsCalledIsNotWhereItIsCheckedOut(unittest.TestCase):
    """D1. A worktree is a different directory holding the same repo.

    Measured before the fix: a worktree of this repo at `…/wt-d1` ran
    `v4 doctrine` -- which writes when given no flag -- and rewrote CLAUDE.md's
    title from the repo's name to `wt-d1`, leaving the file modified in git and
    `registry-consistency` failing afterwards. Three agents hit it in one day;
    the adopter worked around it by naming all eight worktrees after the repo.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "the-repo"
        self.main.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=self.main, check=True)
        (self.main / "a.txt").write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=self.main, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.main, check=True)

    def test_in_the_main_worktree(self):
        self.assertEqual(layout.repo_name(self.main), "the-repo")

    def test_and_in_a_linked_one_under_another_name(self):
        wt = self.tmp / "wt-g1"
        subprocess.run(["git", "worktree", "add", str(wt), "-d", "-q"],
                       cwd=self.main, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        self.assertEqual(wt.name, "wt-g1")
        self.assertEqual(layout.repo_name(wt), "the-repo",
                         "the checkout's basename is not the repo's name")

    def test_every_place_that_names_the_repo_asks_the_same_question(self):
        """The first repair reached three call sites and missed four.

        `doctrine`, `facts` and `config` were fixed; `install.write_facts`,
        two rows of `doctor` and one line of `cli` went on using the checkout's
        basename. A worktree would have been handed a draft under its own
        directory name -- a table the loader does not prefer and `doctor`
        reports as being under the wrong name.

        Asserted against the tree rather than against a list, so a fifth site
        added later fails here.
        """
        import re
        offenders = []
        for f in sorted(ROOT.glob("kernel/*.py")):
            if f.name == "layout.py":
                continue
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if re.search(r"\b(root|dst)\.name\b", line) and "facts" in line:
                    offenders.append(f"{f.name}:{i}")
        self.assertEqual(offenders, [],
                         "the repo's name comes from `layout.repo_name`, which "
                         "asks git; a checkout's basename is the directory it "
                         "happens to sit in")

    def test_and_a_directory_git_cannot_answer_about_keeps_its_own_name(self):
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        self.assertEqual(layout.repo_name(plain), "not-a-repo")


class ASymbolHandedToACallIsASymbolThatGetsCalled(unittest.TestCase):
    """D2. One blindness, two checkers, and the repair belongs in one place.

    `Depends(resolve_brand_from_slug)` and `asyncio.to_thread(do_the_write, x)`
    both put the symbol that runs in `args` and a dispatcher in `func`. A scan
    reading `call.func` sees `Depends` and `asyncio.to_thread`.

    Measured on the reference adopter: 22 of 61 FastAPI handlers passed on
    `Depends` being in the auth table rather than on the function it names --
    and the facts table had grown three pre-auth patterns registered as auth to
    compensate, which the doctrine forbids in as many words. 39 chat
    handlers were hidden behind `to_thread`.

    Two of the tests here asserted the auth half of that, through
    `route_auth._touches`. `a9ae5fb` cut the `route-auth` kind and both programs
    that reached `_touches`, and this task removed the function; the two tests
    went with it. What they covered -- `handed_over` wired into a scan that
    reads a declared table, in both directions -- is what the two `fail_closed`
    tests below cover, on the checker that still runs. The rule itself is
    asserted directly in the first two tests, on `pysource.handed_over`, which
    is where `a9ae5fb`'s own reasoning put it.
    """

    def _call(self, src):
        return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call))

    def test_the_rule_itself(self):
        got = [n.id for n in pysource.handed_over(
            self._call("Depends(resolve_brand_from_slug)"))]
        self.assertEqual(got, ["resolve_brand_from_slug"])

    def test_keywords_count_and_literals_do_not(self):
        names = [getattr(n, "id", None) for n in pysource.handed_over(
            self._call("run_in_executor(None, do_the_write, timeout=30, cb=on_done)"))]
        self.assertEqual(names, ["do_the_write", "on_done"])

    def test_an_outbound_call_handed_to_a_thread_is_seen(self):
        src = ("import asyncio\n"
               "async def handler(x):\n"
               "    try:\n"
               "        await asyncio.to_thread(requests_post, x)\n"
               "    except Exception:\n"
               "        pass\n")
        vocab = fail_closed.SHIPPED.union(
            {"outbound_tails_strong": ["requests_post"]})
        found = fail_closed.analyse_source(src, path="m.py", vocab=vocab)
        self.assertTrue(found, "the write is in args and the handler swallows it")
        self.assertIn("requests_post", found[0].detail)

    def test_and_a_handler_that_hands_over_nothing_risky_is_left_alone(self):
        src = ("import asyncio\n"
               "async def handler(x):\n"
               "    try:\n"
               "        await asyncio.to_thread(add_two_numbers, x)\n"
               "    except Exception:\n"
               "        pass\n")
        self.assertEqual(fail_closed.analyse_source(src, path="m.py"), [])


class ATaskCanBeJudgedAgainstSomethingOtherThanHead(unittest.TestCase):
    """D6. Open a task after committing and the diff is empty.

    Every gate on a task is a delta gate, so an empty diff means `scope` reads
    nothing and passes and `lint` finds no delta. Measured on an adopter: the
    only way out was `git reset --soft` and abandoning the task.

    An older base cannot weaken anything -- it only makes the diff bigger --
    which is why the flag is safe to offer at all.
    """

    def setUp(self):
        from kernel import config, ledger, lifecycle
        self.lifecycle, self.ledger = lifecycle, ledger
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=self.tmp, check=True)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "one"], cwd=self.tmp, check=True)
        self.first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp,
                                    capture_output=True, text=True).stdout.strip()
        (self.tmp / "a.py").write_text("x = 2\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "two"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.tmp)

    def test_without_the_flag_it_is_head_and_the_diff_is_empty(self):
        from kernel.analysis import subject_files
        base = self.lifecycle.open_task(self.conn, self.cfg, task_id="t-head",
                                        request="r", scope_globs=["**"])
        self.assertEqual(subject_files.changed_since(self.tmp, base), frozenset())

    def test_naming_the_commit_before_the_work_puts_it_back_in_the_diff(self):
        from kernel.analysis import subject_files
        base = self.lifecycle.open_task(self.conn, self.cfg, task_id="t-base",
                                        request="r", scope_globs=["**"],
                                        base=self.first)
        self.assertEqual(base, self.first)
        self.assertIn("a.py", subject_files.changed_since(self.tmp, base))

    def test_and_the_base_it_was_judged_against_is_on_the_record(self):
        self.lifecycle.open_task(self.conn, self.cfg, task_id="t-rec",
                                 request="r", scope_globs=["**"], base=self.first)
        row = self.conn.execute("SELECT base_commit FROM task WHERE id = ?",
                                ("t-rec",)).fetchone()
        self.assertEqual(row["base_commit"], self.first)


class NoClaimsIsNotEveryClaimAnswered(unittest.TestCase):
    """D8. A task that derived nothing took the all-terminal branch.

    `open_claims` is empty for an empty list in the way every statement about
    an empty set is true, so the hook told the worker every claim was answered
    and it should ship -- on work nothing had ever asked a question about.

    Run, not read. The other test of this hook records why: an assertion
    against the source held with both live payloads changed to `allow`.
    """

    HOOK = ROOT / "hooks" / "stop_gate.py"

    def _repo(self, claims):
        import os
        from kernel import ledger
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        (td / ".v4").mkdir()
        for args in (["init", "-q", "."], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=td, check=True)
        (td / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=td, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=td, check=True)
        (td / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (td / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"probe": {"question_template": "q", "checker": "probe",
                       "staleness": "repo"}}))
        (td / ".v4" / "checkers.json").write_text("{}")
        conn = ledger.connect(td)
        with ledger.writing(conn):
            ledger.insert(conn, "task", id="t-empty", request="r",
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
            for cid in claims:
                ledger.insert(conn, "claim", id=cid, task_id="t-empty",
                              kind="probe", question="q", subject_refs="[]",
                              checker="probe", origin="derive",
                              created_at="2026")
        conn.close()
        env = dict(os.environ, V4_REPO=str(td), V4_TASK="t-empty",
                   PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, str(self.HOOK)],
                           input=json.dumps({"hook_event_name": "Stop"}),
                           capture_output=True, text=True, env=env,
                           cwd=str(td), timeout=120)
        return json.loads(r.stdout or "{}"), r

    def test_a_task_with_no_claims_is_told_so(self):
        out, r = self._repo([])
        self.assertEqual(out.get("decision"), "block", r.stdout + r.stderr)
        reason = out.get("reason", "")
        self.assertIn("no claims at all", reason)
        self.assertIn("derive", reason)
        self.assertNotIn("is answered and it has not shipped", reason,
                         "an empty set makes that sentence true and useless")

    def test_a_task_whose_claims_are_all_answered_still_gets_the_other_one(self):
        """The branch this must not have taken over. One claim, no attempt, so
        it is OPEN -- which is the third message, and proves the empty check
        did not swallow the populated path."""
        out, r = self._repo(["c1"])
        self.assertEqual(out.get("decision"), "block", r.stdout + r.stderr)
        self.assertIn("still open", out.get("reason", ""))


class ASignatureHasToCoverTheClaimItWasWrittenFor(unittest.TestCase):
    """The key is built in one place and compared in another.

    `risk.accept` and `claim_state` both call `_staleness_key`, with four
    injections each. `reads_of` was added to the reading side and not to the
    writing side, so a repo-scoped signature was keyed on the whole tree and
    read back keyed on the paths the checker declared -- two values, never
    equal, and a signature that covered nothing.

    Measured on the reference adopter: 11 of its 19 repo-scoped kinds had a
    `reads` narrow enough to differ, `secret-chain` and `bundle-secret` among
    them. (`secret` declares `**`, whose digest equals the whole tree's, so it
    was signable throughout -- the report that found this said otherwise.)

    Signed and read back, not compared field by field: this is the assertion
    that fails whenever a fifth injection is added to one side only.
    """

    def _repo(self, reads):
        from kernel import config, ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, check=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"probe": {"question_template": "q", "checker": "probe",
                       "staleness": "repo"}}))
        (tmp / ".v4" / "checkers.json").write_text(json.dumps(
            {"probe": {"path": "checkers/probe.py", "fixtures": "f",
                       "kinds": ["probe"], "sha256": "x", "reads": reads}}))
        (tmp / "a.py").write_text("x = 1\n")
        (tmp / "notes.md").write_text("prose\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, check=True)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        cfg = config.RepoConfig(tmp)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="probe",
                      question="q", subject_refs=[], checker="probe",
                      origin="derive", created_at="2026")
        return tmp, conn, cfg

    def _state(self, tmp, conn, cfg):
        from kernel import state
        row = conn.execute("SELECT * FROM claim WHERE id='c1'").fetchone()
        return state.claim_state(
            conn, tmp, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
            checker_sha_of=lambda c: cfg.checker_sha_on_disk(c),
            facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for)

    WHY = "there is no oracle for this one and the reason runs past forty chars"

    def _sign(self, conn, cfg):
        from kernel import risk, state
        risk.accept(conn, cfg, claim_id="c1", kind="unprovable", why=self.WHY,
                    require_tty=False)
        return state

    def test_a_narrow_reads_still_gets_a_signature_that_covers(self):
        tmp, conn, cfg = self._repo(["**/*.py"])
        st = self._sign(conn, cfg)
        self.assertEqual(self._state(tmp, conn, cfg), st.RISK_ACCEPTED)

    def test_and_it_lapses_when_a_path_that_checker_reads_moves(self):
        tmp, conn, cfg = self._repo(["**/*.py"])
        self._sign(conn, cfg)
        (tmp / "b.py").write_text("y = 2\n")
        self.assertEqual(self._state(tmp, conn, cfg), "OPEN")

    def test_and_not_when_something_it_never_reads_moves(self):
        """The whole reason `reads` was narrowed: the suite is 300-470 seconds
        and was being re-run for edits its oracle could not see."""
        tmp, conn, cfg = self._repo(["**/*.py"])
        st = self._sign(conn, cfg)
        (tmp / "notes.md").write_text("different prose\n")
        self.assertEqual(self._state(tmp, conn, cfg), st.RISK_ACCEPTED)

    def test_a_whole_tree_reads_covers_too(self):
        """The case that hid this: where `reads` digests the same as no `reads`
        at all, both sides agreed and the signature worked."""
        tmp, conn, cfg = self._repo(["**/*"])
        st = self._sign(conn, cfg)
        self.assertEqual(self._state(tmp, conn, cfg), st.RISK_ACCEPTED)


class TwoWaysAPathCanNotBeAFile(unittest.TestCase):
    """F1. `tests/x.py::the_test` is how a person writes down a test, and how
    pytest addresses one. `redgreen` runs a file.

    Measured on the reference adopter: six findings closed against values
    carrying `::`, every `.py` file on disk, and `doctor` reporting all six as
    "closed by a test that is no longer here" -- six true-sounding sentences,
    none of them true.
    """

    def setUp(self):
        from kernel import ledger
        self.ledger = ledger
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "t_here.py").write_text("# a test\n")

    def test_a_plain_path_that_is_here_is_fine(self):
        from kernel import review
        self.assertEqual(review.why_not_a_test_file(self.tmp, "tests/t_here.py"), "")

    def test_a_pytest_address_says_which_half_is_the_file(self):
        from kernel import review
        why = review.why_not_a_test_file(self.tmp, "tests/t_here.py::the_test")
        self.assertIn("names a test, not a file", why)
        self.assertIn("that file is here", why)

    def test_and_says_so_when_neither_half_is_here(self):
        from kernel import review
        why = review.why_not_a_test_file(self.tmp, "tests/gone.py::the_test")
        self.assertIn("names a test, not a file", why)
        self.assertIn("not here either", why)

    def test_a_path_that_is_simply_absent_reads_differently(self):
        from kernel import review
        self.assertEqual(review.why_not_a_test_file(self.tmp, "tests/gone.py"),
                         "not a file in this repo")

    def test_binding_one_is_refused_at_the_command(self):
        """Refused where it is written, not four hours later as an ERROR from
        the checker -- the rule `resolve_symbol` already states for the other
        coordinate."""
        from kernel import ledger, review
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.tmp, check=True)
        conn = ledger.connect(self.tmp)
        self.addCleanup(conn.close)
        with self.assertRaises(review.BadCoordinates):
            review.bind_closing_test(conn, claim_id="c1",
                                     test_path="tests/t_here.py::the_test",
                                     command="python3 {path}",
                                     parent_commit="abc1234", root=self.tmp)
        review.bind_closing_test(conn, claim_id="c1",
                                 test_path="tests/t_here.py",
                                 command="python3 {path}",
                                 parent_commit="abc1234", root=self.tmp)
        self.assertEqual(review.closing_tests_that_are_gone(conn, self.tmp), [])


class TheWalkIsSharedAndTheVerdictIsNot(unittest.TestCase):
    """F3, after looking. Four modules render a dotted name and all four walk
    `ast.Attribute` identically; what each does at the head is different and
    load-bearing. Only the walk is shared."""

    def _head(self, src):
        return pysource.attribute_chain(
            next(n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Attribute)))

    def test_the_walk(self):
        parts, head = self._head("a.b.c")
        self.assertEqual(parts, ["c", "b"])
        self.assertEqual(getattr(head, "id", None), "a")

    def test_the_four_verdicts_stay_four(self):
        from kernel.analysis import external_write, test_shape
        call = "f().post"
        self.assertEqual(fail_closed.dotted_name(
            next(n for n in ast.walk(ast.parse(call))
                 if isinstance(n, ast.Attribute))), "().post")
        self.assertEqual(external_write.dotted_name(
            next(n for n in ast.walk(ast.parse(call))
                 if isinstance(n, ast.Attribute))), "?.post")
        self.assertEqual(route_auth._dotted(
            next(n for n in ast.walk(ast.parse(call))
                 if isinstance(n, ast.Attribute))), "")
        self.assertEqual(test_shape._dotted(
            next(n for n in ast.walk(ast.parse(call))
                 if isinstance(n, ast.Attribute))), "post")


class ADiffNobodyCouldTakeIsNotACleanTree(unittest.TestCase):
    """F4. `changed_since` returned an empty set where git failed, which is the
    failure `checkers/scope.py` refuses in as many words -- and it would have
    carried that into every caller that adopted it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_directory_git_cannot_answer_about_raises(self):
        from kernel.analysis import subject_files
        with self.assertRaises(subject_files.DiffUnreadable):
            subject_files.changed_since(self.tmp, "HEAD")

    def test_a_base_this_checkout_does_not_have_raises(self):
        from kernel.analysis import subject_files
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.tmp, check=True)
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        with self.assertRaises(subject_files.DiffUnreadable):
            subject_files.changed_since(self.tmp, "0" * 40)

    def test_and_a_real_base_answers(self):
        from kernel.analysis import subject_files
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.tmp, check=True)
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        (self.tmp / "b.py").write_text("y = 2\n")
        self.assertEqual(subject_files.changed_since(self.tmp, "HEAD"),
                         frozenset({"b.py"}))
